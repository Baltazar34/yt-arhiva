#!/usr/bin/env python3
"""toolbox — Mac transkoder za yt-arhiva panel (cist modul: bez HTTP-a, niti i importa panela).

  PRESETS / DEFAULT_SPEC     predefinisani specovi (DEFAULT_SPEC = "arhiva")
  analyze(path, ffprobe)     ffprobe -> opis fajla (video / audio / titlovi / poglavlja)
  output_dims(spec, a)       dimenzije posle scale-a (za ASS PlayRes)
  default_out_path(...)      izlazna putanja koja ne gazi postojeci fajl ni izvor
  plan(src, spec, a, ...)    spec -> ffmpeg komanda + upozorenja / greske

Ugovor: docs/toolbox-contract.md. Samo stdlib.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

# ================================================================ presets
_BASE = {
    "container": "mkv", "vcodec": "hevc_vt", "vq": 65, "vbitrate": "", "vpreset": "medium",
    "prores_profile": "hq", "scale": "src", "fps": "src", "depth": "auto", "hdr": "keep",
    "deint": "auto", "audio_tracks": "all", "acodec": "copy", "abitrate": "192k",
    "channels": "keep", "loudnorm": "off", "subs_keep": "all", "sub_src": "none", "sub_file": "",
    "translate": "off", "engine": "ollama", "model": "gemma4:12b-it-qat", "sub_mode": "soft",
    "trim_start": "", "trim_end": "", "chapters": True, "out_dir": "", "suffix": ".transkod",
}


def _p(pid: str, label: str, desc: str, **kw) -> dict:
    return {"id": pid, "label": label, "desc": desc, "spec": {**_BASE, **kw}}


PRESETS: list[dict] = [
    _p("arhiva", "Arhiva",
       "HEVC VideoToolbox q65, 10-bit ako je izvor 10-bit, HDR zadržan, zvuk bez rekodiranja, "
       "svi titlovi i poglavlja, MKV"),
    _p("apple", "Apple (iPhone/iPad/Apple TV)",
       "HEVC VideoToolbox q60 (hvc1), AAC 192k stereo, titlovi kao mov_text, MP4",
       container="mp4", vq=60, acodec="aac", abitrate="192k", channels="2"),
    _p("kompat", "Kompatibilno svuda",
       "H.264 VideoToolbox q60, 8-bit (HDR → SDR), AAC 192k stereo, najviše 1080p, MP4",
       container="mp4", vcodec="h264_vt", vq=60, depth="8", hdr="sdr", scale="1080",
       acodec="aac", abitrate="192k", channels="2"),
    _p("mali", "Manji fajl",
       "x265 CRF 24 preset slow, Opus 128k, MKV (sporije od VideoToolbox-a, manji fajl)",
       vcodec="x265", vq=24, vpreset="slow", acodec="opus", abitrate="128k"),
    _p("av1", "AV1 SVT",
       "SVT-AV1 CRF 30 preset 6, Opus 128k, MKV (najmanji fajl, najsporije)",
       vcodec="av1", vq=30, vpreset="6", acodec="opus", abitrate="128k"),
    _p("prores", "Montaža (ProRes)",
       "ProRes 422 HQ VideoToolbox, PCM zvuk, MOV (veliki fajl, za montažu)",
       container="mov", vcodec="prores_vt", prores_profile="hq", acodec="pcm", subs_keep="none"),
    _p("remux", "Samo remux",
       "Bez rekodiranja: video i zvuk kopirani, titlovi i poglavlja zadržani → MKV",
       vcodec="copy", acodec="copy", deint="off"),
    _p("audio_m4a", "Samo audio — M4A", "AAC 256k, prva audio traka, M4A",
       container="m4a", vcodec="none", acodec="aac", abitrate="256k", audio_tracks="first",
       subs_keep="none"),
    _p("audio_mp3", "Samo audio — MP3", "MP3 320k, prva audio traka",
       container="mp3", vcodec="none", acodec="mp3", abitrate="320k", audio_tracks="first",
       subs_keep="none"),
    _p("audio_flac", "Samo audio — FLAC", "FLAC bez gubitaka, prva audio traka",
       container="flac", vcodec="none", acodec="flac", audio_tracks="first", subs_keep="none"),
]
DEFAULT_SPEC: dict = dict(PRESETS[0]["spec"])

# ================================================================ tabele
ENUMS = {
    "container": ("mkv", "mp4", "mov", "m4a", "mp3", "flac"),
    "vcodec": ("copy", "hevc_vt", "h264_vt", "prores_vt", "x265", "x264", "av1", "none"),
    "prores_profile": ("proxy", "lt", "standard", "hq", "4444"),
    "scale": ("src", "2160", "1440", "1080", "720", "576", "480"),
    "fps": ("src", "60", "50", "30", "25", "24"),
    "depth": ("auto", "8", "10"),
    "hdr": ("keep", "sdr"),
    "deint": ("off", "auto", "yadif", "bwdif"),
    "acodec": ("copy", "aac", "opus", "ac3", "eac3", "flac", "alac", "pcm", "mp3"),
    "channels": ("keep", "2", "6"),
    "loudnorm": ("off", "-16", "-23"),
    "subs_keep": ("all", "none"),
    "translate": ("off", "lat", "cyr", "el"),
    "sub_mode": ("soft", "burn"),
}
FMT = {"mkv": "matroska", "mp4": "mp4", "mov": "mov", "m4a": "ipod", "mp3": "mp3", "flac": "flac"}
AUDIO_ONLY = ("m4a", "mp3", "flac")
MOVLIKE = ("mp4", "mov", "m4a")
VENC = {"hevc_vt": "hevc_videotoolbox", "h264_vt": "h264_videotoolbox",
        "prores_vt": "prores_videotoolbox", "x265": "libx265", "x264": "libx264", "av1": "libsvtav1"}
VOUT = {"hevc_vt": "hevc", "h264_vt": "h264", "prores_vt": "prores", "x265": "hevc", "x264": "h264",
        "av1": "av1"}
AENC = {"aac": "aac", "opus": "libopus", "ac3": "ac3", "eac3": "eac3", "flac": "flac", "alac": "alac",
        "mp3": "libmp3lame"}
LOSSY_A = ("aac", "opus", "ac3", "eac3", "mp3")
# video kodeci (ffprobe ime) koje kontejner prima; None = sve
V_OK = {"mkv": None,
        "mp4": {"h264", "hevc", "av1", "vp9", "mpeg4", "mpeg2video", "mpeg1video", "mjpeg"},
        "mov": {"h264", "hevc", "prores", "mpeg4", "mpeg2video", "mpeg1video", "mjpeg", "dnxhd",
                "png", "qtrle", "rawvideo"}}
# audio kodeci (ffprobe ime); pcm_* posebno (samo mkv/mov)
A_OK = {"mkv": None,
        "mp4": {"aac", "mp3", "mp2", "ac3", "eac3", "alac", "opus", "flac", "dts"},
        "mov": {"aac", "mp3", "mp2", "ac3", "eac3", "alac"},
        "m4a": {"aac", "alac"}, "mp3": {"mp3"}, "flac": {"flac"}}
LOSSLESS_SRC = ("flac", "alac", "truehd", "mlp", "wavpack", "ape", "tta")
TEXT_SUBS = ("subrip", "ass", "ssa", "mov_text", "webvtt", "text")
IMAGE_SUBS = ("hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle")   # overlay (burn) radi samo za ove
MKV_SUBS = ("subrip", "ass", "ssa", "webvtt") + IMAGE_SUBS           # MKV ih prima bez konverzije
# HDR metapodaci koji posle tonemapa u SDR ne smeju da ostanu na frejmovima (i u kontejneru)
HDR_SIDEDATA = ("MASTERING_DISPLAY_METADATA", "CONTENT_LIGHT_LEVEL", "DYNAMIC_HDR_PLUS",
                "DOVI_RPU_BUFFER", "DOVI_METADATA")
X26X_PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower",
                "veryslow", "placebo")
OPUS_LAYOUTS = {"mono", "stereo", "3.0", "quad", "5.0", "5.1", "6.1", "7.1"}
OPUS_BY_CH = {3: "3.0", 4: "quad", 5: "5.0", 6: "5.1", 7: "6.1", 8: "7.1"}
LANG3 = {"en": "eng", "sr": "srp", "hr": "hrv", "bs": "bos", "de": "deu", "es": "spa", "it": "ita",
         "fr": "fra", "ru": "rus", "pt": "por", "el": "ell", "nl": "nld", "pl": "pol", "tr": "tur",
         "sl": "slv", "mk": "mkd", "bg": "bul", "hu": "hun", "ro": "ron", "cs": "ces", "uk": "ukr",
         "ja": "jpn", "ko": "kor", "zh": "zho", "ar": "ara", "sv": "swe", "da": "dan", "fi": "fin",
         "no": "nor", "sh": "hbs", "nb": "nob", "nn": "nno", "he": "heb", "hi": "hin", "th": "tha",
         "vi": "vie", "id": "ind", "ms": "msa", "ca": "cat", "gl": "glg", "eu": "eus", "et": "est",
         "lv": "lav", "lt": "lit", "sk": "slk", "sq": "sqi", "hy": "hye", "ka": "kat", "is": "isl",
         "ga": "gle", "cy": "cym", "fa": "fas", "ur": "urd", "bn": "ben", "ta": "tam", "tl": "tgl",
         "be": "bel", "az": "aze", "kk": "kaz", "mt": "mlt", "lb": "ltz", "af": "afr", "sw": "swa"}
# 3-slovni kodovi koje prihvatamo iz imena fajla (da "film.mkv.srt" ne postane jezik "mkv")
LANG3_OK = set(LANG3.values()) | {"fre", "ger", "dut", "chi", "cze", "gre", "rum", "slo", "alb", "arm",
                                  "baq", "per", "wel", "ice", "mac", "may", "scc", "scr", "fil", "und"}
UNKNOWN = (None, "", "unknown", "unspecified", "reserved")


# ================================================================ analyze
def _int(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _rate(s: str | None) -> float:
    try:
        n, d = (s or "0/0").split("/")
        return round(int(n) / int(d), 3) if int(d) else 0.0
    except ValueError:
        return 0.0


def _tags(st: dict) -> dict:
    return {k.lower(): v for k, v in (st.get("tags") or {}).items()}


def _br(st: dict) -> int | None:
    t = _tags(st)
    return _int(st.get("bit_rate")) or _int(t.get("bps")) or _int(t.get("bps-eng"))


def _bit_depth(st: dict) -> int:
    b = _int(st.get("bits_per_raw_sample"))
    if b and 8 <= b <= 16:
        return b
    m = re.search(r"(\d+)(?:le|be)$", st.get("pix_fmt") or "")
    b = int(m.group(1)) if m else 8
    return b if 8 <= b <= 16 else 8


def _hdr(st: dict) -> str:
    sd = " ".join(x.get("side_data_type") or "" for x in st.get("side_data_list") or [])
    if "DOVI" in sd or (st.get("codec_tag_string") or "") in ("dvh1", "dvhe", "dav1", "dva1"):
        return "dolby_vision"
    t = st.get("color_transfer")
    return "hdr10" if t == "smpte2084" else "hlg" if t == "arib-std-b67" else "sdr"


def _rotation(st: dict) -> int:
    """Rotacija iz display matrix-a (telefoni snimaju landscape + rotaciju) -> 0/90/180/270."""
    r = next((_int(x.get("rotation")) for x in st.get("side_data_list") or []
              if x.get("rotation") is not None), None)
    if r is None:
        r = _int(_tags(st).get("rotate"))
    return (round((r or 0) / 90) * 90) % 360


def _frame_colors(path: str, ffprobe: str, video: dict):
    """Kontejner cesto nema color tagove iako ih bitstream (VUI) ima: procitaj prvi frejm."""
    try:
        p = subprocess.run([ffprobe, "-v", "error", "-select_streams", str(video["index"]),
                            "-read_intervals", "%+#1", "-show_frames", "-of", "json", path],
                           capture_output=True, text=True, timeout=60)
        fr = (json.loads(p.stdout or "{}").get("frames") or [{}])[0]
    except (subprocess.SubprocessError, ValueError, OSError):
        return
    for k in ("color_primaries", "color_transfer", "color_space"):
        if video.get(k) in UNKNOWN and fr.get(k) not in UNKNOWN:
            video[k] = fr[k]
    sd = " ".join(x.get("side_data_type") or "" for x in fr.get("side_data_list") or [])
    if "Dolby Vision" in sd:
        video["hdr"] = "dolby_vision"
    elif video["hdr"] == "sdr":
        video["hdr"] = _hdr({"color_transfer": video.get("color_transfer")})


def analyze(path: str, ffprobe: str) -> dict:
    p = subprocess.run([ffprobe, "-v", "error", "-show_format", "-show_streams", "-show_chapters",
                        "-of", "json", path], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe: {(p.stderr or '').strip()[-300:] or 'nepoznat format'}")
    d = json.loads(p.stdout or "{}")
    fmt = d.get("format") or {}
    streams = d.get("streams") or []
    warns: list[str] = []
    video, audio, subs = None, [], []
    for st in streams:
        typ, disp, t = st.get("codec_type"), st.get("disposition") or {}, _tags(st)
        if typ == "video" and not disp.get("attached_pic"):
            if video is None:
                # width/height = prikazane dimenzije (posle rotacije); ffmpeg autorotira pre filtera
                w, h, rot = _int(st.get("width")) or 0, _int(st.get("height")) or 0, _rotation(st)
                if rot in (90, 270):
                    w, h = h, w
                video = {"index": st.get("index"), "codec": st.get("codec_name"),
                         "profile": st.get("profile"), "width": w, "height": h, "rotation": rot,
                         "fps": _rate(st.get("avg_frame_rate")) or _rate(st.get("r_frame_rate")),
                         "pix_fmt": st.get("pix_fmt"), "bit_depth": _bit_depth(st), "hdr": _hdr(st),
                         "color_primaries": st.get("color_primaries"),
                         "color_transfer": st.get("color_transfer"),
                         "color_space": st.get("color_space"),
                         "interlaced": (st.get("field_order") or "") in ("tt", "bb", "tb", "bt"),
                         "bitrate": _br(st)}
        elif typ == "audio":
            audio.append({"n": len(audio), "index": st.get("index"), "codec": st.get("codec_name"),
                          "channels": _int(st.get("channels")) or 0, "layout": st.get("channel_layout"),
                          "sample_rate": _int(st.get("sample_rate")), "bitrate": _br(st),
                          "lang": t.get("language") or "und", "title": t.get("title") or "",
                          "default": bool(disp.get("default"))})
        elif typ == "subtitle":
            subs.append({"n": len(subs), "index": st.get("index"), "codec": st.get("codec_name"),
                         "lang": t.get("language") or "und", "title": t.get("title") or "",
                         "text": st.get("codec_name") in TEXT_SUBS, "forced": bool(disp.get("forced")),
                         "default": bool(disp.get("default")),
                         "width": _int(st.get("width")), "height": _int(st.get("height"))})
    dur = float(fmt.get("duration") or 0) or max(
        (float(s.get("duration") or 0) for s in streams), default=0.0)
    if video and (video["color_transfer"] in UNKNOWN or video["color_primaries"] in UNKNOWN):
        _frame_colors(path, ffprobe, video)
    if not video and not audio:
        warns.append("fajl nema ni video ni audio strim")
    if video:
        if video["hdr"] == "dolby_vision":
            warns.append("Dolby Vision: pri rekodiranju RPU se gubi (ostaje HDR10/HLG bazni sloj ako postoji)")
        if video["interlaced"]:
            warns.append("video je interlaced — deinterlace 'auto' ga ispravlja")
    if any(s["codec"] in IMAGE_SUBS for s in subs):
        warns.append("slikovni titlovi (PGS/DVD) ne idu u MP4/MOV i ne mogu da se prevedu")
    odd = [f"{s['n']} ({s['codec']})" for s in subs if not s["text"] and s["codec"] not in IMAGE_SUBS]
    if odd:
        warns.append(f"titl {', '.join(odd)} nije podržan za obradu — biće izbačen iz izlaza")
    if not dur:
        warns.append("trajanje nije poznato (napredak neće biti tačan)")
    stat = sorted({k for st in streams for k in st.get("tags") or {}
                   if k.upper().startswith(("BPS", "NUMBER_OF_FRAMES", "NUMBER_OF_BYTES", "_STATISTICS_"))})
    return {"path": path, "name": os.path.basename(path),
            "size": _int(fmt.get("size")) or (os.path.getsize(path) if os.path.exists(path) else 0),
            "container": fmt.get("format_name") or "", "duration": dur, "bitrate": _int(fmt.get("bit_rate")),
            "video": video, "audio": audio, "subs": subs, "chapters": len(d.get("chapters") or []),
            "stat_tags": stat, "warnings": warns}


# ================================================================ pomocne
def _spec(spec: dict | None) -> dict:
    s = {**DEFAULT_SPEC, **(spec or {})}
    s["chapters"] = s["chapters"] not in (False, 0, "0", "false", "False", "off", "no", "")
    for k in ("scale", "fps", "depth", "loudnorm", "channels"):
        s[k] = str(s[k])
    return s


def _secs(v) -> float | None:
    v = str(v if v is not None else "").strip().replace(",", ".")
    if not v:
        return None
    parts = v.split(":")
    if len(parts) > 3 or not all(re.fullmatch(r"\d+(\.\d+)?", x) for x in parts):
        raise ValueError(v)
    t = 0.0
    for x in parts:
        t = t * 60 + float(x)
    return t


def _ts(x: float) -> str:
    return f"{x:.3f}".rstrip("0").rstrip(".") or "0"


def _same(a: str, b: str) -> bool:
    try:
        if os.path.exists(a) and os.path.exists(b):
            return os.path.samefile(a, b)
    except OSError:
        pass
    ra, rb = os.path.realpath(a), os.path.realpath(b)
    return ra.lower() == rb.lower() if sys.platform == "darwin" else ra == rb


def _fesc(s: str) -> str:
    """Escape vrednosti opcije filtera (ime fajla) za -vf / -filter_complex: dva nivoa
    (opcija filtera pa filtergraf); razmak na pocetku/kraju bi ffmpeg inace odsekao."""
    def esc(x: str, chars: str) -> str:
        x = re.sub(f"([{chars}])", r"\\\1", x)
        return re.sub(r"^\s|\s$", lambda m: "\\" + m.group(0), x)
    return esc(esc(s, r"\\':="), r"\\',;\[\]")


def _even(x: float) -> int:
    return max(2, int(x / 2 + 0.5) * 2)


def _scale_target(spec: dict, v: dict | None) -> tuple[int, int] | None:
    """Samo smanjivanje u okvir p x p*16/9 (za portret obrnuto): "1080" nikad nije sire od 1920."""
    if not v or spec.get("scale", "src") == "src" or not str(spec["scale"]).isdigit():
        return None
    w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    if not w or not h:
        return None
    p = int(spec["scale"])
    bw, bh = (_even(p * 16 / 9), p) if w >= h else (p, _even(p * 16 / 9))
    f = min(bw / w, bh / h)
    if f >= 1:
        return None
    return (min(_even(w * f), bw), min(_even(h * f), bh))


def output_dims(spec: dict, a: dict) -> tuple[int, int]:
    v = (a or {}).get("video")
    if not v or not v.get("width") or not v.get("height"):
        return (1920, 1080)
    s = _spec(spec)
    t = None if s["vcodec"] == "copy" else _scale_target(s, v)
    return t or (int(v["width"]), int(v["height"]))


def default_out_path(src: str, spec: dict, a: dict) -> str:
    s = _spec(spec)
    d = os.path.expanduser(str(s.get("out_dir") or "")) or os.path.dirname(os.path.abspath(src))
    d = os.path.abspath(d)  # cmd se pokrece sa cwd=workdir -> relativna putanja bi otisla tamo
    stem = os.path.splitext(os.path.basename(src))[0]
    if stem.endswith(".source"):
        stem = stem[:-7]
    suf = str(s.get("suffix") or "").replace("/", "_")
    ext = s["container"] if s["container"] in FMT else "mkv"
    base = os.path.join(d, stem + suf)
    p, k = f"{base}.{ext}", 2
    while os.path.exists(p) or _same(p, src):
        p, k = f"{base} ({k}).{ext}", k + 1
    return p


def _lang_from_name(path: str) -> str:
    m = re.search(r"\.([a-z]{2,3})(?:-[A-Za-z]+)?\.[^.]+$", os.path.basename(path).lower())
    if not m:
        return "und"
    c = m.group(1)
    return LANG3.get(c, c if c in LANG3_OK else "und")


# ================================================================ plan
def plan(src: str, spec: dict, a: dict, ffmpeg: str, out_path: str, workdir: str,
         sub_file: str | None = None) -> dict:
    s = _spec(spec)
    E: list[str] = []
    W: list[str] = []
    # komanda se pokrece sa cwd=workdir: sve putanje apsolutne (relativne bi pokazivale u workdir)
    src, workdir = os.path.abspath(src), os.path.abspath(workdir)
    out_path = os.path.abspath(os.path.expanduser(out_path)) if out_path else out_path

    def done() -> dict:
        return {"cmd": [] if E else cmd, "out": out_path, "warnings": list(dict.fromkeys(W)),
                "errors": list(dict.fromkeys(E))}

    cmd: list[str] = []
    for k, vals in ENUMS.items():
        if str(s[k]) not in vals:
            E.append(f"nepoznata vrednost {k}={s[k]!r}")
    if E:
        return done()
    if not out_path:
        E.append("nije zadata izlazna putanja")
    elif _same(out_path, src):
        E.append("izlaz je isti fajl kao izvor — izaberi drugo ime ili folder")
    elif not os.path.isdir(os.path.dirname(os.path.abspath(out_path))):
        W.append("izlazni folder ne postoji (biće potreban pre pokretanja)")

    cont, vc, ac = s["container"], s["vcodec"], s["acodec"]
    v = a.get("video")
    auds, subs = a.get("audio") or [], a.get("subs") or []
    dur = float(a.get("duration") or 0)

    # ---------------------------------------------------------- video da/ne
    use_v = False
    if cont in AUDIO_ONLY:
        if vc != "none" and v:
            W.append(f"{cont.upper()} je audio kontejner — video se izbacuje")
    elif vc == "none":
        pass
    elif not v:
        W.append("izvor nema video — izlaz je samo zvuk")
    else:
        use_v = True

    # ---------------------------------------------------------- titl izvor
    ss = str(s.get("sub_src") or "none")
    tr = s["translate"]
    strack = None
    if ss.startswith("track:"):
        n = _int(ss[6:])
        strack = subs[n] if n is not None and 0 <= n < len(subs) else None
        if strack is None:
            E.append(f"titl traka {ss[6:]} ne postoji u izvoru")
    elif ss not in ("none", "file", "whisper"):
        E.append(f"nepoznata vrednost sub_src={ss!r}")
    if ss == "file" and not (sub_file or s.get("sub_file")):
        E.append("izabran je titl iz fajla, ali fajl nije zadat")
    if ss == "whisper" and not a.get("audio"):
        E.append("whisper traži audio traku")
    want_sub = ss != "none" and not E
    if ss == "none" and tr != "off":
        W.append("prevod se ignoriše — nije izabran izvor titla")
    if strack and not strack["text"] and tr != "off":
        E.append("prevod slikovnog titla (PGS/DVD) nije moguć — izaberi tekstualni titl ili whisper")
    burn = want_sub and s["sub_mode"] == "burn"
    if burn and strack and not strack["text"] and strack["codec"] not in IMAGE_SUBS:
        E.append(f"titl {strack['codec']} ne može da se utisne — izaberi tekstualni ili PGS/DVD/DVB titl")
    if want_sub and cont in AUDIO_ONLY:
        if burn:
            E.append("burn titla traži video izlaz (izabran je audio kontejner)")
        else:
            W.append("audio kontejner ne nosi titlove — izabrani titl se ignoriše")
        want_sub = burn = False
    if burn and vc == "copy":
        E.append("burn titla traži rekodiranje videa (video kodek ne sme biti copy)")
    elif burn and not use_v:
        E.append("burn titla traži video (izvor nema video ili je video isključen)")
    image_burn = burn and strack is not None and not strack["text"]
    need_file = want_sub and not image_burn and (
        ss in ("file", "whisper") or tr != "off" or (strack is not None and burn))
    sf = None
    if need_file:
        sf = sub_file
        if not sf and ss == "file" and tr == "off" and not burn:
            sf = os.path.expanduser(str(s.get("sub_file")))
        if not sf:
            sf = os.path.join(workdir, "sub.ass" if burn else "sub.srt")
            W.append("titl se priprema pri pokretanju — u komandi je privremeno ime "
                     f"({os.path.basename(sf)})")
        sf = os.path.abspath(sf)
    ext_img = need_file and any(str(x or "").lower().endswith((".sup", ".idx"))  # spoljni PGS / VobSub
                                for x in (sf, s.get("sub_file") if ss == "file" else ""))
    if ext_img:
        if tr != "off":
            E.append("prevod slikovnog titla (PGS/DVD) nije moguć — izaberi tekstualni titl ili whisper")
        elif burn:
            E.append("spoljni slikovni titl (.sup/.idx) ne može da se utisne — izaberi tekstualni titl")
        elif cont in ("mp4", "mov"):
            E.append(f"slikovni titl (.sup/.idx) ne ide u {cont.upper()} — izaberi MKV")
    rel = None
    if burn and sf:
        rel = os.path.relpath(sf, workdir)
        if rel.startswith(".."):  # npr. /tmp vs /private/tmp
            rel = os.path.relpath(os.path.realpath(sf), os.path.realpath(workdir))
        if rel.startswith("..") or os.path.isabs(rel):
            E.append("titl za burn mora biti u radnom folderu (workdir)")
    if E:
        return done()

    # ---------------------------------------------------------- trim
    try:
        t0, t1 = _secs(s["trim_start"]), _secs(s["trim_end"])
    except ValueError as e:
        E.append(f"neispravno vreme za isecanje: {e} (oblik hh:mm:ss[.ms])")
        return done()
    t0 = t0 or None
    if t0 and dur and t0 >= dur:
        E.append("početak isecanja je posle kraja fajla")
    if t1 is not None and t1 <= (t0 or 0):
        E.append("kraj isecanja mora biti posle početka")
    if t1 is not None and dur and t1 > dur + 0.5:
        W.append("kraj isecanja je posle kraja fajla — ide do kraja")
    tdur = (t1 - (t0 or 0)) if t1 is not None else None
    # -ss uvek PRE -i (brzo i tacno). Burn: filter titla dobija originalni tajming preko setpts.
    # (ffmpeg 9: kod -ss posle -i transkodovani titlovi (mov_text/srt) se ne pomeraju -> kasne.)
    if (t0 or tdur) and use_v and vc == "copy":
        W.append("isecanje uz video copy ide po ključnim frejmovima (neprecizno)")

    # ---------------------------------------------------------- ulazi
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1", "-y"]
    if t0:
        cmd += ["-ss", _ts(t0)]
    cmd += ["-i", src]
    soft_ext = need_file and not burn
    if soft_ext:
        if t0:
            cmd += ["-ss", _ts(t0)]
        cmd += ["-i", sf]

    maps: list[str] = []
    opts: list[str] = []

    # ---------------------------------------------------------- video
    if use_v:
        vi = v["index"]
        if vc == "copy":
            ok = V_OK.get(cont)
            if ok is not None and v["codec"] not in ok:
                E.append(f"video kodek {v['codec']} ne može bez rekodiranja u {cont.upper()}")
            maps += ["-map", f"0:{vi}"]
            opts += ["-c:v", "copy"]
            if v["codec"] == "hevc" and cont in MOVLIKE:
                opts += ["-tag:v", "hvc1"]
            if v["hdr"] == "dolby_vision" and cont in MOVLIKE:
                opts += ["-strict", "unofficial"]
            ign = [n for n, bad in (("rezolucija", s["scale"] != "src"), ("fps", s["fps"] != "src"),
                                    ("HDR → SDR", s["hdr"] == "sdr" and v["hdr"] != "sdr"),
                                    ("bit depth", s["depth"] != "auto"),
                                    ("deinterlace", s["deint"] in ("yadif", "bwdif"))) if bad]
            if ign:
                W.append("uz video copy se ignoriše: " + ", ".join(ign))
        else:
            ok = V_OK.get(cont)
            if ok is not None and VOUT[vc] not in ok:
                E.append(f"{VOUT[vc]} ne ide u {cont.upper()} — izaberi "
                         + ("MOV ili MKV" if vc == "prores_vt" else "MP4 ili MKV"))
            hdr_src = v["hdr"] != "sdr"
            tonemap = s["hdr"] == "sdr" and hdr_src
            if vc == "prores_vt":
                depth = 10
            elif s["depth"] == "auto":
                depth = 10 if v["bit_depth"] >= 10 and vc != "x264" else 8
            else:
                depth = int(s["depth"])
            if vc == "h264_vt" and depth == 10:
                W.append("H.264 VideoToolbox je samo 8-bit — izlaz je 8-bit")
                depth = 8
            if vc == "x264" and depth == 10:
                W.append("H.264 10-bit (High10) slabo podržavaju plejeri i TV")
            if hdr_src and not tonemap:
                if depth == 8:
                    W.append("HDR u 8 bita bez tonemapa → isprane/pogrešne boje; izaberi HDR → SDR")
                if v["hdr"] == "dolby_vision" and vc not in ("x265", "av1"):
                    W.append("Dolby Vision RPU se gubi pri rekodiranju")
            if tonemap and v["hdr"] == "dolby_vision" and v.get("color_transfer") not in (
                    "smpte2084", "arib-std-b67"):
                W.append("Dolby Vision bez HDR10 osnove (profil 5): boje posle tonemapa neće biti tačne")
            pre, post = [], []
            if s["deint"] in ("yadif", "bwdif") or (s["deint"] == "auto" and v["interlaced"]):
                f = "yadif" if s["deint"] == "yadif" else "bwdif"
                pre.append(f"{f}=mode=send_frame:parity=auto:deint=all")
            elif v["interlaced"] and s["deint"] == "off":
                W.append("izvor je interlaced, a deinterlace je isključen")
            if tonemap:
                tin = v.get("color_transfer") if v.get("color_transfer") in (
                    "smpte2084", "arib-std-b67") else "smpte2084"
                mi = v.get("color_space") if v.get("color_space") not in UNKNOWN else "bt2020nc"
                pi = v.get("color_primaries") if v.get("color_primaries") not in UNKNOWN else "bt2020"
                post.append(f"zscale=tin={tin}:min={mi}:pin={pi}:rin=tv:t=linear:npl=100,"
                            "format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
                            "zscale=t=bt709:m=bt709:r=tv,"
                            f"format={'yuv420p10le' if depth == 10 else 'yuv420p'},"
                            # SDR izlaz bez HDR10/DV metapodataka (inace ostaju i u kontejneru)
                            + ",".join(f"sidedata=mode=delete:type={x}" for x in HDR_SIDEDATA))
            if burn and hdr_src and not tonemap:
                W.append("utiskivanje titla u HDR video bez tonemapa: titl će biti presvetao "
                         "— izaberi HDR → SDR")
            p = _scale_target(s, v)
            if p:
                post.append(f"scale={p[0]}:{p[1]}:flags=lanczos")
            if s["fps"] != "src":
                if v["fps"] and v["fps"] > float(s["fps"]) + 0.01:
                    post.append(f"fps={s['fps']}")
                else:
                    W.append(f"fps izvora ({v['fps'] or '?'}) nije veći od {s['fps']} — ne menja se")
            if burn and rel:
                r = _fesc(rel)
                f = f"ass={r}" if rel.lower().endswith((".ass", ".ssa")) else f"subtitles={r}"
                if t0:  # ulaz je pomeren za -ss; titl filter mora da vidi originalni tajming
                    f = f"setpts=PTS+{_ts(t0)}/TB,{f},setpts=PTS-{_ts(t0)}/TB"
                post.append(f)
            if image_burn:
                sin, ov, g = f"[0:s:{strack['n']}]", "overlay=eof_action=pass:format=auto", ""
                sw, sh = strack.get("width"), strack.get("height")
                if sw and sh and (sw, sh) != (v["width"], v["height"]):
                    # platno titla != video: DVD/DVB (SD, anamorfno) -> ceo kadar; PGS (npr. 1080
                    # platno na isecenom 1920x800) -> po sirini, poravnato dole
                    if strack["codec"] == "hdmv_pgs_subtitle":
                        g = f"{sin}scale={v['width']}:-1[sb];"
                        ov = "overlay=x=(W-w)/2:y=H-h:eof_action=pass:format=auto"
                    else:
                        g = f"{sin}scale={v['width']}:{v['height']}[sb];"
                    sin = "[sb]"
                g += f"[0:{vi}]{','.join(pre) or 'null'}[vb];[vb]{sin}{ov}"
                g += f"[vo];[vo]{','.join(post)}[vout]" if post else "[vout]"
                opts += ["-filter_complex", g]
                maps += ["-map", "[vout]"]
            else:
                maps += ["-map", f"0:{vi}"]
                if pre or post:
                    opts += ["-vf", ",".join(pre + post)]
            # enkoder
            enc = ["-c:v", VENC[vc]]
            vq = _int(s["vq"])
            vb = str(s.get("vbitrate") or "").strip()
            if vb and not re.fullmatch(r"\d+(\.\d+)?[kKmM]?", vb):
                E.append(f"neispravan video bitrate {vb!r} (npr. 8M)")
            if vc in ("hevc_vt", "h264_vt"):
                if not vb and not (vq and 1 <= vq <= 100):
                    E.append("kvalitet za VideoToolbox mora biti 1–100")
                enc += ["-b:v", vb] if vb else ["-q:v", str(vq)]
                if vc == "hevc_vt":
                    enc += (["-profile:v", "main10", "-pix_fmt", "p010le"] if depth == 10
                            else ["-profile:v", "main", "-pix_fmt", "yuv420p"])
                else:
                    enc += ["-profile:v", "high", "-pix_fmt", "yuv420p"]
            elif vc == "prores_vt":
                enc += ["-profile:v", s["prores_profile"],
                        "-pix_fmt", "p410le" if s["prores_profile"] == "4444" else "p210le"]
                if vb or s["depth"] != "auto":
                    W.append("ProRes: bitrate i bit depth određuje profil (ignorisano)")
            else:
                top = 63 if vc == "av1" else 51
                if not vb and (vq is None or not 0 <= vq <= top):
                    E.append(f"CRF za {vc} mora biti 0–{top}")
                pr = str(s.get("vpreset") or "")
                if vc == "av1":
                    if not (re.fullmatch(r"-?\d+", pr) and -2 <= int(pr) <= 13):
                        if pr:
                            W.append(f"preset {pr!r} ne važi za SVT-AV1 — koristim 6")
                        pr = "6"
                elif pr not in X26X_PRESETS:
                    if pr:
                        W.append(f"preset {pr!r} ne važi za {vc} — koristim medium")
                    pr = "medium"
                enc += ["-preset", pr] + (["-b:v", vb] if vb else ["-crf", str(vq)])
                enc += ["-pix_fmt", "yuv420p10le" if depth == 10 else "yuv420p"]
                if vc == "x265":
                    xp = ["log-level=error"]
                    if hdr_src and not tonemap and depth == 10:
                        col = [f"{k}={v[f]}" for k, f in (("colorprim", "color_primaries"),
                                                          ("transfer", "color_transfer"),
                                                          ("colormatrix", "color_space"))
                               if v.get(f) not in UNKNOWN]
                        xp += (["hdr10=1", "hdr10-opt=1"] if v.get("color_transfer") == "smpte2084"
                               else []) + ["repeat-headers=1"] + col
                    enc += ["-x265-params", ":".join(xp)]
            if VOUT[vc] == "hevc" and cont in MOVLIKE:
                enc += ["-tag:v", "hvc1"]
            # color tagovi idu sa frejmova (dekoder / zscale); ffmpeg 9 ignorise -color_trc/-color_primaries
            opts += enc

    # ---------------------------------------------------------- audio
    at = s["audio_tracks"]
    if isinstance(at, str) and at not in ("all", "first"):
        at = [x for x in re.split(r"[,\s]+", at) if x]
    if at == "all":
        sel = list(auds)
    elif at == "first":
        sel = auds[:1]
    else:
        want = [_int(x) for x in (at if isinstance(at, (list, tuple)) else [at])]
        bad = [x for x in want if x is None or not 0 <= x < len(auds)]
        if bad:
            W.append("nepostojeće audio trake su preskočene: " + ", ".join(map(str, bad)))
        sel = [auds[x] for x in dict.fromkeys(w for w in want if w is not None and 0 <= w < len(auds))]
    if cont in ("mp3", "flac") and len(sel) > 1:
        W.append(f"{cont.upper()} nosi samo jednu audio traku — uzimam prvu izabranu")
        sel = sel[:1]
    if not sel:
        if cont in AUDIO_ONLY:
            E.append("izvor nema audio traku (ili nijedna nije izabrana)")
        elif auds:
            W.append("nijedna audio traka nije izabrana — izlaz je bez zvuka")
    if not sel and not use_v:
        E.append("izlaz ne bi imao ni video ni audio")
    ln = s["loudnorm"]
    eff = (dur - (t0 or 0)) if dur else None
    if tdur is not None:
        eff = min(eff, tdur) if eff is not None else tdur
    if sel and ln != "off" and ac != "copy" and eff is not None and eff < 3:
        # loudnorm < 3 s radi linearno: tisina -> NaN (aac greska, lame pada), flac: ogroman blok
        W.append("loudnorm traži bar 3 s zvuka — preskočen")
        ln = "off"
    if sel and ln != "off" and ac == "copy":
        E.append("loudnorm ne ide uz audio copy — izaberi kodek za zvuk")
    if sel and s["channels"] != "keep" and ac == "copy":
        W.append("broj kanala se ne menja uz audio copy")
    if ac == "pcm" and cont == "mp4":
        E.append("PCM ne ide u MP4 — izaberi MOV ili MKV (ili ALAC/FLAC)")
    abr = str(s.get("abitrate") or "192k").strip().lower()
    if ac in LOSSY_A and not re.fullmatch(r"\d+k", abr):
        E.append(f"neispravan audio bitrate {abr!r} (npr. 192k)")
    if ac == "mp3" and re.fullmatch(r"\d+k", abr) and int(abr[:-1]) > 320:
        abr = "320k"
    if ac == "ac3" and re.fullmatch(r"\d+k", abr) and int(abr[:-1]) > 640:
        abr = "640k"
    aok = A_OK.get(cont)
    any_default = any(t.get("default") for t in sel)
    for k, t in enumerate(sel):
        maps += ["-map", f"0:a:{t['n']}"]
        if ac == "copy":
            c = t.get("codec") or "?"
            if aok is not None and c not in aok and not (cont == "mov" and c.startswith("pcm_")):
                E.append(f"audio traka {t['n']} ({c}) ne može bez rekodiranja u {cont.upper()} — izaberi kodek")
            opts += [f"-c:a:{k}", "copy"]
            continue
        lossless_src = (t.get("codec") or "").startswith("pcm_") or t.get("codec") in LOSSLESS_SRC
        if ac == "pcm":  # 24 bita samo ako izvor ima vise od 16
            enc = "pcm_s24le" if lossless_src and not re.match(
                r"pcm_[su](8|16)", t.get("codec") or "") else "pcm_s16le"
            if cont not in ("mkv", "mov", "mp4"):
                E.append(f"PCM ne ide u {cont.upper()}")
        else:
            enc = AENC[ac]
            outc = "mp3" if ac == "mp3" else ac
            if aok is not None and outc not in aok:
                E.append(f"{ac.upper()} ne ide u {cont.upper()}")
        opts += [f"-c:a:{k}", enc]
        if ac in LOSSY_A:
            opts += [f"-b:a:{k}", abr]
        if ac in ("flac", "alac", "pcm") and not lossless_src:
            if ac != "pcm":
                opts += [f"-sample_fmt:a:{k}", "s16" if ac == "flac" else "s16p"]
        ch = int(t.get("channels") or 0)
        och = ch
        if s["channels"] == "2" and ch > 2:
            och = 2
        elif s["channels"] == "6" and ch > 6:
            och = 6
        if ac == "mp3" and och > 2:
            W.append(f"MP3 je najviše stereo — traka {t['n']} se miksuje na 2 kanala")
            och = 2
        if ac in ("ac3", "eac3") and och > 6:
            W.append(f"{ac.upper()} je najviše 5.1 — traka {t['n']} se miksuje na 6 kanala")
            och = 6
        flt = []
        if och != ch:
            opts += [f"-ac:a:{k}", str(och)]
            flt.append(f"aformat=channel_layouts={'stereo' if och == 2 else '5.1'}")
        elif ac == "opus" and ch > 2 and (t.get("layout") or "") not in OPUS_LAYOUTS:
            flt.append(f"aformat=channel_layouts={OPUS_BY_CH.get(ch, '5.1')}")
        if ln != "off":
            i = int(ln)
            sr = 48000 if ac == "opus" else (t.get("sample_rate") or 48000)
            flt += [f"loudnorm=I={i}:TP={-1.5 if i > -20 else -1}:LRA=11", f"aresample={sr}"]
        if flt:
            opts += [f"-filter:a:{k}", ",".join(flt)]
    if sel and not any_default and cont not in ("mp3", "flac"):
        opts += ["-disposition:a:0", "+default"]

    # ---------------------------------------------------------- titlovi
    sk = 0
    if cont not in AUDIO_ONLY:
        keep = list(subs) if s["subs_keep"] == "all" else []
        if strack and not burn and tr == "off" and strack not in keep:
            keep.append(strack)
        pick = strack["n"] if strack and not burn and tr == "off" else None
        img_drop, odd_drop, kept_out = [], [], []
        for t in keep:
            if t["text"]:
                c = "mov_text" if cont in ("mp4", "mov") else "copy" if t["codec"] in MKV_SUBS else "srt"
            elif t["codec"] in IMAGE_SUBS and cont == "mkv":
                c = "copy"
            else:  # slikovni u MP4/MOV; ttml/teletext/eia_608… MKV ne prima (ceo posao bi pao)
                (img_drop if t["codec"] in IMAGE_SUBS else odd_drop).append(t)
                continue
            maps += ["-map", f"0:s:{t['n']}"]
            opts += [f"-c:s:{sk}", c]
            kept_out.append(t)
            if pick is not None:
                opts += [f"-disposition:s:{sk}", "+default" if t["n"] == pick else "-default"]
            elif burn:  # utisnut titl + default meki = dupli titl na ekranu
                opts += [f"-disposition:s:{sk}", "-default"]
            sk += 1
        if img_drop:
            W.append(f"slikovni titlovi ({', '.join(str(t['n']) for t in img_drop)}) "
                     f"ne idu u {cont.upper()} — izbačeni")
        if odd_drop:
            names = ", ".join("%s (%s)" % (t["n"], t["codec"]) for t in odd_drop)
            W.append(f"titlovi {names} ne mogu u {cont.upper()} — izbačeni")
        if cont == "mkv" and any(t["codec"] in ("ass", "ssa") for t in keep):
            maps += ["-map", "0:t?"]  # fontovi za ASS
        if soft_ext:
            maps += ["-map", "1:s:0"]  # ne 1:0 — spoljni fajl moze biti kontejner sa videom
            if cont in ("mp4", "mov"):
                c = "mov_text"
            else:
                c = "copy" if ext_img or sf.lower().endswith((".srt", ".ass", ".ssa")) else "srt"
            if tr != "off":
                lang, title = ("ell", "Ελληνικά") if tr == "el" else \
                    ("srp", "Srpski (ćirilica)" if tr == "cyr" else "Srpski (latinica)")
            elif ss == "whisper":
                lang, title = "und", "Whisper"
            elif strack:
                lang, title = strack["lang"], strack["title"] or strack["lang"]
            else:
                lang = _lang_from_name(s.get("sub_file") or sf)
                title = os.path.splitext(os.path.basename(s.get("sub_file") or sf))[0][:60]
            opts += [f"-c:s:{sk}", c, f"-metadata:s:s:{sk}", f"language={lang}",
                     f"-metadata:s:s:{sk}", f"title={title}", f"-disposition:s:{sk}", "+default"]
            for j in range(sk):
                opts += [f"-disposition:s:{j}", "-default"]
            sk += 1
        if kept_out and not any(x.startswith("-disposition:s:") for x in opts):
            # bez ijedne rucne dispozicije ffmpeg sam stavi default na prvi titl kad ih je 2+ a nijedan
            # nije default (titlovi bi se sami ukljucili); no-op dispozicija -> kopira se izvor
            opts += ["-disposition:s:0", "+default" if kept_out[0].get("default") else "-default"]

    # ---------------------------------------------------------- izlaz
    out_opts: list[str] = []
    if t0 and not (use_v and vc == "copy"):
        out_opts += ["-copypriorss", "0"]  # copy trake bez paketa pre pocetka (inace kasni ceo fajl)
    if t0 and sk:
        out_opts += ["-bsf:s", "noise=drop=lt(pts\\,0)"]  # titlovi zavrseni pre pocetka isecanja
    if tdur is not None:
        out_opts += ["-t", _ts(tdur)]
    for k in a.get("stat_tags") or []:  # mkvmerge statistika (BPS…) posle obrade vise ne vazi
        out_opts += ["-metadata:s", f"{k}="]
    out_opts += ["-map_metadata", "0", "-map_chapters", "0" if s["chapters"] else "-1"]
    if cont in MOVLIKE:
        out_opts += ["-movflags", "+faststart"]
    out_opts += ["-max_muxing_queue_size", "4096", "-f", FMT[cont], out_path]
    cmd += maps + opts + out_opts
    return done()


if __name__ == "__main__":  # brzi pregled: python3 toolbox.py FAJL [preset]
    _ff = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
    _a = analyze(sys.argv[1], _ff[:-6] + "ffprobe")
    _s = next((p["spec"] for p in PRESETS if len(sys.argv) > 2 and p["id"] == sys.argv[2]), DEFAULT_SPEC)
    _o = default_out_path(sys.argv[1], _s, _a)
    print(json.dumps(_a, indent=1, ensure_ascii=False))
    print(json.dumps(plan(sys.argv[1], _s, _a, _ff, _o, os.getcwd()), indent=1, ensure_ascii=False))
