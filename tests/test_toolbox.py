#!/usr/bin/env python3
"""Test toolbox.py: sinteticki mediji (lavfi) -> analyze + plan -> IZVRSI ffmpeg -> ffprobe provere.

  python3 tests/test_toolbox.py              # svi testovi (~1-2 min), izlaz != 0 ako nesto padne
  python3 tests/test_toolbox.py apple trim   # samo testovi ciji naziv sadrzi 'apple' ili 'trim'
  TB_KEEP=1 python3 tests/test_toolbox.py    # ne brisi privremeni folder (za pregled izlaza)

Koristi ffmpeg-full (libass, zimg, VideoToolbox): /opt/homebrew/opt/ffmpeg-full/bin.
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import toolbox as tb  # noqa: E402

BIN = Path(os.environ.get("TB_FFMPEG_DIR", "/opt/homebrew/opt/ffmpeg-full/bin"))
FF, FP = str(BIN / "ffmpeg"), str(BIN / "ffprobe")
TMP = Path(tempfile.mkdtemp(prefix="tb-test-"))
MEDIA, WORK, OUT = TMP / "media", TMP / "work", TMP / "out"
for _d in (MEDIA, WORK, OUT):
    _d.mkdir()
IN1, IN2, IN3, IN4 = (str(MEDIA / n) for n in ("in1.mkv", "in2 hdr.mkv", "in3_576i.mkv", "in4.m4a"))
A: dict[str, dict] = {}

SRT = """1
00:00:00,300 --> 00:00:01,400
Hello there

2
00:00:01,600 --> 00:00:02,800
General Kenobi
"""
SRT_SR = SRT.replace("Hello there", "Zdravo").replace("General Kenobi", "Generale Kenobi")
ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Helvetica,90,&H00FFFFFF,&H00FFFFFF,&H00FFFFFF,&H00FFFFFF,0,0,0,0,100,100,0,0,3,40,0,2,20,20,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:03.00,Default,,0,0,0,,BURN TEST BURN TEST
"""
CHAPTERS = """;FFMETADATA1
title=Toolbox test

[CHAPTER]
TIMEBASE=1/1000
START=0
END=1500
title=Prvo

[CHAPTER]
TIMEBASE=1/1000
START=1500
END=3000
title=Drugo
"""
TONE2 = "aevalsrc=0.3*sin(440*2*PI*t)|0.3*sin(554*2*PI*t):c=stereo:s=48000:d=3"
TONE6 = ("aevalsrc=0.2*sin(440*2*PI*t)|0.2*sin(494*2*PI*t)|0.2*sin(523*2*PI*t)|0.1*sin(60*2*PI*t)"
         "|0.2*sin(587*2*PI*t)|0.2*sin(659*2*PI*t):c=5.1(side):s=48000:d=3")


# ================================================================ pomocne
def sh(cmd: list[str], cwd: str | None = None, timeout: int = 300) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise AssertionError(f"komanda nije uspela (rc={p.returncode}): {' '.join(cmd)[:300]}\n"
                             f"{p.stderr[-1200:]}")
    return p


def gen_media():
    (MEDIA / "in1.srt").write_text(SRT)
    (MEDIA / "chapters.txt").write_text(CHAPTERS)
    base = [FF, "-hide_banner", "-loglevel", "error", "-y"]
    sh(base + ["-f", "lavfi", "-i", "testsrc2=s=1280x720:r=25:d=3", "-f", "lavfi", "-i", TONE2,
               "-f", "lavfi", "-i", TONE6, "-i", str(MEDIA / "in1.srt"), "-i", str(MEDIA / "chapters.txt"),
               "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:s", "-map_chapters", "4",
               "-map_metadata", "4", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
               "-c:a:0", "aac", "-b:a:0", "128k", "-c:a:1", "ac3", "-b:a:1", "384k", "-c:s", "srt",
               "-metadata:s:a:0", "language=eng", "-metadata:s:a:0", "title=English",
               "-metadata:s:a:1", "language=spa", "-metadata:s:a:1", "title=Español 5.1",
               "-metadata:s:s:0", "language=eng", "-metadata:s:s:0", "title=English CC",
               "-disposition:a:0", "default", "-disposition:a:1", "0", IN1])
    sh(base + ["-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=3", "-f", "lavfi", "-i", TONE2,
               "-map", "0:v", "-map", "1:a", "-c:v", "libx265", "-preset", "ultrafast",
               "-pix_fmt", "yuv420p10le", "-x265-params",
               "log-level=error:hdr10=1:repeat-headers=1:colorprim=bt2020:transfer=smpte2084:"
               "colormatrix=bt2020nc:master-display=G(13250,34500)B(7500,3000)R(34000,16000)"
               "WP(15635,16450)L(10000000,1):max-cll=1000,400",
               "-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc",
               "-c:a", "aac", "-b:a", "128k", IN2])
    sh(base + ["-f", "lavfi", "-i", "testsrc2=s=720x576:r=25:d=3,setfield=tff", "-f", "lavfi", "-i", TONE2,
               "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast",
               "-flags", "+ilme+ildct", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", IN3])
    sh(base + ["-f", "lavfi", "-i", TONE2, "-c:a", "aac", "-b:a", "128k", IN4])
    (WORK / "ext.en.srt").write_text(SRT)   # spoljni titl (dodat rucno)
    (WORK / "sr.srt").write_text(SRT_SR)    # "preveden" titl (simulacija prevoda)
    (WORK / "burn.ass").write_text(ASS)
    (WORK / "sub.srt").write_text(SRT_SR)
    for p in (IN1, IN2, IN3, IN4):
        A[p] = tb.analyze(p, FP)


def probe(path: str) -> dict:
    p = sh([FP, "-v", "error", "-show_format", "-show_streams", "-show_chapters", "-of", "json", path])
    d = json.loads(p.stdout)
    d["v"] = [s for s in d["streams"] if s["codec_type"] == "video"
              and not (s.get("disposition") or {}).get("attached_pic")]
    d["a"] = [s for s in d["streams"] if s["codec_type"] == "audio"]
    d["s"] = [s for s in d["streams"] if s["codec_type"] == "subtitle"]
    return d


def spec(preset: str = "arhiva", **kw) -> dict:
    base = next(p["spec"] for p in tb.PRESETS if p["id"] == preset)
    return {**base, "out_dir": str(OUT), **kw}


def mkplan(src: str, sp: dict, sub_file: str | None = None, out: str | None = None,
           a: dict | None = None) -> dict:
    a = a or A.get(src) or A.setdefault(src, tb.analyze(src, FP))
    out = out or tb.default_out_path(src, sp, a)
    return tb.plan(src, sp, a, FF, out, str(WORK), sub_file)


def run(src: str, sp: dict, sub_file: str | None = None) -> tuple[dict, dict]:
    r = mkplan(src, sp, sub_file)
    if r["errors"]:
        raise AssertionError("plan greške: " + "; ".join(r["errors"]))
    assert r["cmd"] and r["cmd"][0] == FF, "cmd prazan ili ne počinje ffmpeg-om"
    sh(r["cmd"], cwd=str(WORK))
    assert os.path.exists(r["out"]), f"izlaz ne postoji: {r['out']}"
    return r, probe(r["out"])


def eq(got, want, what: str):
    if got != want:
        raise AssertionError(f"{what}: očekivano {want!r}, dobijeno {got!r}")


def ok(cond, what: str):
    if not cond:
        raise AssertionError(what)


def codecs(streams: list[dict]) -> list[str]:
    return [s.get("codec_name") for s in streams]


def lang(s: dict) -> str | None:
    return (s.get("tags") or {}).get("language")


def sdur(st: dict) -> float:
    """Trajanje strima (mp4: duration; mkv: tag DURATION)."""
    if st.get("duration"):
        return float(st["duration"])
    h, m, x = ((st.get("tags") or {}).get("DURATION") or "0:0:0").split(":")
    return int(h) * 3600 + int(m) * 60 + float(x)


def sub_times(path: str) -> list[tuple[float, float]]:
    """Efektivni tajming prvog titla u izlazu (izvuceno kao SRT)."""
    p = sh([FF, "-v", "error", "-i", path, "-map", "0:s:0", "-f", "srt", "-"])
    out = []
    for line in p.stdout.splitlines():
        if "-->" in line:
            a, b = (x.strip().replace(",", ".") for x in line.split("-->"))
            out.append(tuple(round(sum(float(v) * 60 ** i for i, v in enumerate(reversed(t.split(":")))), 2)
                             for t in (a, b)))
    return out


def frame_diff(p1: str, p2: str, t: float) -> float:
    f1, f2 = gray_frame(p1, t), gray_frame(p2, t)
    ok(len(f1) == len(f2) == 128 * 72, "frejmovi za poređenje")
    return sum(abs(x - y) for x, y in zip(f1, f2)) / len(f1)


def cmd_str(r: dict) -> str:
    return " ".join(r["cmd"])


def arg_after(cmd: list[str], flag: str) -> str | None:
    return cmd[cmd.index(flag) + 1] if flag in cmd else None


def is10(s: dict) -> bool:
    return "10" in (s.get("pix_fmt") or "")


def gray_frame(path: str, t: float = 1.0) -> bytes:
    p = subprocess.run([FF, "-v", "error", "-ss", str(t), "-i", path, "-frames:v", "1", "-vf", "scale=128:72",
                        "-pix_fmt", "gray", "-f", "rawvideo", "-"], capture_output=True, timeout=60)
    return p.stdout


def gen(name: str, args: list[str]) -> str:
    """Pomocni medij u MEDIA (jednom po pokretanju)."""
    p = str(MEDIA / name)
    if not os.path.exists(p):
        sh([FF, "-hide_banner", "-loglevel", "error", "-y"] + args + [p])
    return p


def pgs_sup(t0: float, t1: float, cw: int = 1280, ch: int = 720) -> bytes:
    """Rucno napravljen PGS (.sup): bela traka 600x80 na (340, 560) platna cw x ch, vidljiva t0-t1."""
    def seg(t, typ, data):
        return b"PG" + struct.pack(">IIBH", int(t * 90000), 0, typ, len(data)) + data
    w, h, x, y = 600, 80, 340, 560
    rle = (b"\x00" + bytes([0xC0 | (w >> 8), w & 0xFF, 1]) + b"\x00\x00") * h
    wds = struct.pack(">BBHHHH", 1, 0, x, y, w, h)
    on = struct.pack(">HHBHBBBB", cw, ch, 0x10, 0, 0x80, 0, 0, 1) + struct.pack(">HBBHH", 0, 0, 0, x, y)
    ods = struct.pack(">HBB", 0, 0, 0xC0) + (len(rle) + 4).to_bytes(3, "big") + struct.pack(">HH", w, h) + rle
    off = struct.pack(">HHBHBBBB", cw, ch, 0x10, 1, 0x00, 0, 0, 0)
    return (seg(t0, 0x16, on) + seg(t0, 0x17, wds) + seg(t0, 0x14, b"\x00\x00\x01\xeb\x80\x80\xff")
            + seg(t0, 0x15, ods) + seg(t0, 0x80, b"") + seg(t1, 0x16, off) + seg(t1, 0x17, wds)
            + seg(t1, 0x80, b""))


def with_pgs(src: str, name: str) -> str:
    """src + PGS traka (1280x720 platno, vidljiva 1.6-2.3 s izvora)."""
    sup = MEDIA / "t.sup"
    if not sup.exists():
        sup.write_bytes(pgs_sup(1.6, 2.3))
    return gen(name, ["-copyts", "-i", src, "-i", str(sup), "-map", "0", "-map", "1", "-c", "copy"])


def side_data(path: str) -> list[str]:
    """Tipovi side data na video strimu (kontejner) i prvom frejmu."""
    p = sh([FP, "-v", "error", "-select_streams", "v:0", "-read_intervals", "%+#1", "-show_frames",
            "-show_streams", "-of", "json", path])
    d = json.loads(p.stdout)
    return [x.get("side_data_type") or "" for o in d["streams"][:1] + d["frames"][:1]
            for x in o.get("side_data_list") or []]


# ================================================================ testovi
def t_analyze_in1():
    a = A[IN1]
    v = a["video"]
    eq((v["codec"], v["width"], v["height"], v["bit_depth"], v["hdr"], v["interlaced"]),
       ("h264", 1280, 720, 8, "sdr", False), "video")
    eq(round(v["fps"]), 25, "fps")
    eq([(x["n"], x["codec"], x["channels"], x["lang"]) for x in a["audio"]],
       [(0, "aac", 2, "eng"), (1, "ac3", 6, "spa")], "audio")
    eq(a["audio"][0]["default"], True, "audio 0 default")
    eq([(x["n"], x["codec"], x["lang"], x["text"]) for x in a["subs"]], [(0, "subrip", "eng", True)], "subs")
    eq(a["chapters"], 2, "chapters")
    ok("matroska" in a["container"], "container")
    ok(2.8 < a["duration"] < 3.3, f"duration {a['duration']}")
    ok(a["size"] > 0 and a["name"] == "in1.mkv", "size/name")
    for k in ("path", "bitrate", "warnings"):
        ok(k in a, f"nedostaje {k}")


def t_analyze_in2_hdr():
    v = A[IN2]["video"]
    eq((v["codec"], v["bit_depth"], v["hdr"], v["color_primaries"], v["color_transfer"]),
       ("hevc", 10, "hdr10", "bt2020", "smpte2084"), "hdr video")


def t_analyze_in3_interlaced():
    v = A[IN3]["video"]
    eq(v["interlaced"], True, "interlaced")
    ok(any("interlaced" in w for w in A[IN3]["warnings"]), "upozorenje za interlaced")


def t_analyze_in4_audio_only():
    a = A[IN4]
    eq(a["video"], None, "video")
    eq(len(a["audio"]), 1, "audio")
    eq(a["subs"], [], "subs")


def t_presets_shape():
    ids = [p["id"] for p in tb.PRESETS]
    eq(ids, ["arhiva", "apple", "kompat", "mali", "av1", "prores", "remux", "audio_m4a", "audio_mp3",
             "audio_flac"], "preset ids")
    eq(tb.DEFAULT_SPEC, tb.PRESETS[0]["spec"], "DEFAULT_SPEC == arhiva")
    keys = set(tb.DEFAULT_SPEC)
    for p in tb.PRESETS:
        ok(p["label"] and p["desc"], f"{p['id']}: label/desc")
        eq(set(p["spec"]), keys, f"{p['id']}: ključevi speca")
        for k, vals in tb.ENUMS.items():
            ok(str(p["spec"][k]) in vals, f"{p['id']}: {k}={p['spec'][k]}")
        r = mkplan(IN1, spec(p["id"]))
        eq(r["errors"], [], f"{p['id']}: plan greške")
    ok(json.loads(json.dumps(tb.PRESETS)) == tb.PRESETS, "PRESETS nije JSON-serijalizabilan")
    c = mkplan(IN1, spec())["cmd"]
    for x in ("-hide_banner", "-nostats", "-y", "-map_metadata"):
        ok(x in c, f"nedostaje {x}")
    eq((arg_after(c, "-loglevel"), arg_after(c, "-progress")), ("error", "pipe:1"), "loglevel/progress")


# ---------------------------------------------------------------- preseti na in1
def t_preset_arhiva():
    r, d = run(IN1, spec("arhiva"))
    ok("matroska" in d["format"]["format_name"], "kontejner mkv")
    eq(codecs(d["v"]), ["hevc"], "video")
    ok(not is10(d["v"][0]), "8-bit izvor ostaje 8-bit")
    eq(codecs(d["a"]), ["aac", "ac3"], "audio copy")
    eq([lang(s) for s in d["a"]], ["eng", "spa"], "audio jezici")
    eq(codecs(d["s"]), ["subrip"], "titl")
    eq(len(d["chapters"]), 2, "poglavlja")
    ok("hevc_videotoolbox" in r["cmd"] and arg_after(r["cmd"], "-q:v") == "65", "hevc_vt q65")
    ok(r["out"].endswith("in1.transkod.mkv"), f"ime izlaza {r['out']}")


def t_preset_apple():
    r, d = run(IN1, spec("apple"))
    ok("mp4" in d["format"]["format_name"] and r["out"].endswith(".mp4"), "kontejner mp4")
    eq(codecs(d["v"]), ["hevc"], "video")
    eq(d["v"][0].get("codec_tag_string"), "hvc1", "hvc1 tag")
    eq(codecs(d["a"]), ["aac", "aac"], "audio")
    eq([s["channels"] for s in d["a"]], [2, 2], "stereo")
    eq(codecs(d["s"]), ["mov_text"], "titl mov_text")
    eq(len(d["chapters"]), 2, "poglavlja")


def t_preset_kompat():
    r, d = run(IN1, spec("kompat"))
    eq(codecs(d["v"]), ["h264"], "video")
    eq(d["v"][0]["pix_fmt"], "yuv420p", "8-bit")
    eq(d["v"][0]["height"], 720, "bez uvećanja (max 1080)")
    eq(codecs(d["a"]), ["aac", "aac"], "audio")
    eq([s["channels"] for s in d["a"]], [2, 2], "stereo")
    eq(codecs(d["s"]), ["mov_text"], "titl")
    ok("scale=" not in cmd_str(r), "720p izvor se ne skalira")


def t_preset_mali():
    r, d = run(IN1, spec("mali"))
    ok("libx265" in r["cmd"] and arg_after(r["cmd"], "-crf") == "24" and arg_after(r["cmd"], "-preset") == "slow",
       "x265 crf24 slow")
    eq(codecs(d["v"]), ["hevc"], "video")
    eq(codecs(d["a"]), ["opus", "opus"], "opus")
    eq([s["channels"] for s in d["a"]], [2, 6], "kanali (5.1(side) -> opus 5.1)")
    eq(codecs(d["s"]), ["subrip"], "titl")


def t_preset_av1():
    r, d = run(IN1, spec("av1"))
    ok("libsvtav1" in r["cmd"] and arg_after(r["cmd"], "-crf") == "30" and arg_after(r["cmd"], "-preset") == "6",
       "svt-av1 crf30 p6")
    eq(codecs(d["v"]), ["av1"], "video")
    eq(codecs(d["a"]), ["opus", "opus"], "opus")


def t_preset_prores():
    r, d = run(IN1, spec("prores"))
    ok(r["out"].endswith(".mov") and "mov" in d["format"]["format_name"], "mov")
    eq(codecs(d["v"]), ["prores"], "video")
    eq(d["v"][0].get("profile"), "HQ", "ProRes 422 HQ")
    eq(codecs(d["a"]), ["pcm_s16le", "pcm_s16le"], "pcm")
    eq(d["s"], [], "bez titlova")


def t_preset_remux():
    r, d = run(IN1, spec("remux"))
    eq(arg_after(r["cmd"], "-c:v"), "copy", "video copy")
    ok("-vf" not in r["cmd"], "nema filtera uz copy")
    eq(codecs(d["v"]), ["h264"], "video")
    eq(codecs(d["a"]), ["aac", "ac3"], "audio")
    eq(codecs(d["s"]), ["subrip"], "titl")
    eq(len(d["chapters"]), 2, "poglavlja")


def t_preset_audio_m4a():
    r, d = run(IN1, spec("audio_m4a"))
    ok(r["out"].endswith(".m4a"), "ekstenzija m4a")
    eq(d["v"], [], "bez videa")
    eq(d["s"], [], "bez titlova")
    eq(codecs(d["a"]), ["aac"], "jedna aac traka")
    eq(lang(d["a"][0]), "eng", "prva traka")


def t_preset_audio_mp3():
    r, d = run(IN1, spec("audio_mp3"))
    eq(d["format"]["format_name"], "mp3", "mp3")
    eq(codecs(d["a"]), ["mp3"], "audio")
    eq((d["v"], d["s"]), ([], []), "bez videa/titlova")
    ok(int(d["a"][0].get("bit_rate") or d["format"].get("bit_rate") or 0) >= 300000, "320k")


def t_preset_audio_flac():
    r, d = run(IN1, spec("audio_flac"))
    eq(d["format"]["format_name"], "flac", "flac")
    eq(codecs(d["a"]), ["flac"], "audio")
    eq(d["v"], [], "bez videa")


# ---------------------------------------------------------------- HDR / 10-bit
def t_hdr_arhiva_10bit():
    r, d = run(IN2, spec("arhiva"))
    v = d["v"][0]
    eq(v["codec_name"], "hevc", "hevc")
    ok(is10(v), f"10-bit zadržan ({v['pix_fmt']})")
    eq(v.get("profile"), "Main 10", "Main 10")
    eq((v.get("color_transfer"), v.get("color_primaries")), ("smpte2084", "bt2020"), "HDR tagovi")
    ok("main10" in r["cmd"] and "p010le" in r["cmd"], "main10 + p010le")


def t_hdr_apple_mp4():
    r, d = run(IN2, spec("apple"))
    v = d["v"][0]
    eq((v.get("codec_tag_string"), is10(v), v.get("color_transfer")), ("hvc1", True, "smpte2084"),
       "hvc1 10-bit HDR u mp4")


def t_hdr_x265_keep():
    r, d = run(IN2, spec("mali"))
    xp = arg_after(r["cmd"], "-x265-params") or ""
    ok("hdr10=1" in xp and "transfer=smpte2084" in xp and "colorprim=bt2020" in xp, f"x265-params: {xp}")
    v = d["v"][0]
    eq((v["pix_fmt"], v.get("color_transfer")), ("yuv420p10le", "smpte2084"), "x265 10-bit HDR")


def t_hdr_sdr_tonemap_hevc():
    r, d = run(IN2, spec("arhiva", hdr="sdr"))
    ok("tonemap=tonemap=hable" in cmd_str(r) and "zscale" in cmd_str(r), "tonemap filter")
    v = d["v"][0]
    eq((v.get("color_transfer"), v.get("color_primaries"), v.get("color_space")),
       ("bt709", "bt709", "bt709"), "BT.709 posle tonemapa")
    ok(is10(v), "auto dubina ostaje 10-bit")


def t_hdr_sdr_kompat_h264():
    r, d = run(IN2, spec("kompat"))
    v = d["v"][0]
    eq((v["codec_name"], v["pix_fmt"], v.get("color_transfer")), ("h264", "yuv420p", "bt709"),
       "h264 8-bit SDR")


def t_hdr_h264_no_tonemap_warns():
    r = mkplan(IN2, spec("kompat", hdr="keep"))
    ok(any("HDR" in w for w in r["warnings"]), f"upozorenje za HDR u 8 bita: {r['warnings']}")


def t_hdr_av1_10bit():
    r, d = run(IN2, spec("av1"))
    v = d["v"][0]
    eq((v["codec_name"], v["pix_fmt"], v.get("color_transfer")), ("av1", "yuv420p10le", "smpte2084"), "av1 HDR")


# ---------------------------------------------------------------- deinterlace
def t_deint_auto_interlaced():
    r, d = run(IN3, spec("arhiva"))
    vf = arg_after(r["cmd"], "-vf") or ""
    ok(vf.startswith("bwdif"), f"bwdif prvi u lancu: {vf}")
    fo = d["v"][0].get("field_order")
    ok(fo in (None, "progressive", "unknown"), f"izlaz progresivan ({fo})")


def t_deint_auto_progressive():
    r = mkplan(IN1, spec("arhiva"))
    ok("bwdif" not in cmd_str(r) and "yadif" not in cmd_str(r), "nema deint na progresivnom")


def t_deint_explicit_and_off():
    r = mkplan(IN1, spec("arhiva", deint="yadif"))
    ok((arg_after(r["cmd"], "-vf") or "").startswith("yadif"), "yadif eksplicitno")
    r = mkplan(IN3, spec("arhiva", deint="off"))
    ok("bwdif" not in cmd_str(r) and any("interlaced" in w for w in r["warnings"]), "off + upozorenje")


# ---------------------------------------------------------------- trim
def t_trim():
    r, d = run(IN1, spec("arhiva", trim_start="00:00:01", trim_end="00:00:02.5"))
    c = r["cmd"]
    ok(c.index("-ss") < c.index("-i"), "-ss pre -i (brzo) bez burna")
    for st in d["v"] + d["a"]:
        ok(abs(sdur(st) - 1.5) < 0.1, f"{st['codec_type']} {st['codec_name']}: trajanje {sdur(st)} ≈ 1.5")
    eq(sub_times(r["out"]), [(0.6, 1.8)], "titl (copy) pomeren za početak isecanja")


def t_trim_only_end():
    r, d = run(IN1, spec("arhiva", trim_end="2"))
    ok("-ss" not in r["cmd"], "bez -ss")
    ok(abs(sdur(d["v"][0]) - 2.0) < 0.1, f"trajanje {sdur(d['v'][0])} ≈ 2.0")


def t_trim_mp4_subs_sync():
    r, d = run(IN1, spec("apple", trim_start="1", trim_end="2.5"))
    eq(sub_times(r["out"]), [(0.6, 1.8)], "mov_text posle isecanja u sinhronu")
    ok(abs(sdur(d["v"][0]) - 1.5) < 0.1, f"video {sdur(d['v'][0])}")
    sp = spec("arhiva", trim_start="1", sub_src="file", sub_file=str(WORK / "ext.en.srt"), translate="lat",
              subs_keep="none")
    r, d = run(IN1, sp, sub_file=str(WORK / "sr.srt"))
    eq(sub_times(r["out"]), [(0.6, 1.8)], "spoljni titl posle isecanja u sinhronu")


def t_trim_audio_only():
    r, d = run(IN4, spec("audio_m4a", trim_start="0.5", trim_end="2"))
    ok(abs(sdur(d["a"][0]) - 1.5) < 0.1, f"audio {sdur(d['a'][0])} ≈ 1.5")


def t_trim_errors():
    eq(bool(mkplan(IN1, spec(trim_start="2", trim_end="1"))["errors"]), True, "kraj pre početka")
    eq(bool(mkplan(IN1, spec(trim_start="1:xx"))["errors"]), True, "loš format")
    eq(bool(mkplan(IN1, spec(trim_start="00:05:00"))["errors"]), True, "početak posle kraja")


# ---------------------------------------------------------------- audio opcije
def t_audio_tracks_select():
    r, d = run(IN1, spec("arhiva", audio_tracks=[1]))
    eq([lang(s) for s in d["a"]], ["spa"], "samo spa")
    eq(codecs(d["a"]), ["ac3"], "ac3 copy")
    eq(d["a"][0]["disposition"]["default"], 1, "jedina traka je default")


def t_channels_downmix():
    r, d = run(IN1, spec("arhiva", acodec="aac", channels="2"))
    eq([s["channels"] for s in d["a"]], [2, 2], "downmix na 2")
    ok("-ac:a:1" in r["cmd"] and "-ac:a:0" not in r["cmd"], "downmix samo za 5.1 traku")


def t_loudnorm():
    r, d = run(IN1, spec("arhiva", acodec="aac", loudnorm="-16"))
    flts = [r["cmd"][i + 1] for i, x in enumerate(r["cmd"]) if x.startswith("-filter:a:")]
    eq(len(flts), 2, "loudnorm po traci")
    ok(all("loudnorm=I=-16" in f for f in flts), f"loudnorm filter: {flts}")
    eq([s["sample_rate"] for s in d["a"]], ["48000", "48000"], "sample rate vraćen na 48k")


def t_loudnorm_copy_error():
    r = mkplan(IN1, spec("arhiva", acodec="copy", loudnorm="-23"))
    ok(r["errors"] and r["cmd"] == [], "loudnorm + copy -> greška i prazan cmd")


def t_pcm_mp4_error():
    r = mkplan(IN1, spec("apple", acodec="pcm"))
    ok(r["errors"] and r["cmd"] == [], "pcm + mp4 -> greška")


def t_mp3_from_51():
    r, d = run(IN1, spec("audio_mp3", audio_tracks=[1]))
    eq(d["a"][0]["channels"], 2, "mp3 5.1 -> stereo")
    ok(any("MP3" in w for w in r["warnings"]), "upozorenje za mp3 downmix")


def t_copy_incompatible_audio_mp4():
    r = mkplan(IN4, spec("apple", acodec="copy"))
    eq(r["errors"], [], "aac copy u mp4 je ok")
    fake = json.loads(json.dumps(A[IN1]))
    fake["audio"][1]["codec"] = "truehd"
    r = tb.plan(IN1, spec("apple", acodec="copy"), fake, FF, str(OUT / "x.mp4"), str(WORK))
    ok(r["errors"], "truehd copy u mp4 -> greška")


# ---------------------------------------------------------------- titlovi
def t_soft_translated_mkv():
    sp = spec("arhiva", sub_src="file", sub_file=str(WORK / "ext.en.srt"), translate="lat", sub_mode="soft")
    r, d = run(IN1, sp, sub_file=str(WORK / "sr.srt"))
    eq(codecs(d["s"]), ["subrip", "subrip"], "postojeći + dodat")
    eq([lang(s) for s in d["s"]], ["eng", "srp"], "jezici")
    eq(d["s"][1]["tags"].get("title"), "Srpski (latinica)", "naslov")
    eq([s["disposition"]["default"] for s in d["s"]], [0, 1], "srp je default")


def t_soft_translated_mp4():
    sp = spec("apple", sub_src="file", sub_file=str(WORK / "ext.en.srt"), translate="cyr")
    r, d = run(IN1, sp, sub_file=str(WORK / "sr.srt"))
    eq(codecs(d["s"]), ["mov_text", "mov_text"], "mov_text u mp4")
    eq(lang(d["s"][1]), "srp", "srp")


def t_soft_file_direct():
    ext = str(MEDIA / "movie.de.srt")
    Path(ext).write_text(SRT)
    r, d = run(IN1, spec("arhiva", sub_src="file", sub_file=ext, subs_keep="none"))
    ok(ext in r["cmd"], "spoljni fajl direktno kao ulaz")
    eq([lang(s) for s in d["s"]], ["deu"], "jezik iz imena fajla")


def t_soft_track_default():
    r, d = run(IN1, spec("arhiva", sub_src="track:0", subs_keep="none"))
    eq(codecs(d["s"]), ["subrip"], "izabrana traka")
    eq(d["s"][0]["disposition"]["default"], 1, "default")


def t_whisper_placeholder():
    r = mkplan(IN1, spec("arhiva", sub_src="whisper"))
    eq(r["errors"], [], "bez grešaka")
    ok(str(WORK / "sub.srt") in r["cmd"], "privremeno ime u workdir")
    ok(any("priprema" in w for w in r["warnings"]), "upozorenje o pripremi")


def t_burn_ass():
    sp = spec("arhiva", sub_src="file", sub_file="/nekde/original.srt", sub_mode="burn")
    r, d = run(IN1, sp, sub_file=str(WORK / "burn.ass"))
    vf = arg_after(r["cmd"], "-vf") or ""
    ok(vf.endswith("ass=burn.ass") and "/" not in vf, f"relativni ass filter: {vf}")
    eq(len(d["s"]), 1, "postojeći titl zadržan kao mek")
    eq(d["s"][0]["disposition"]["default"], 0, "zadržani meki titl nije default (inače dupli titl uz burn)")
    (OUT / "nb").mkdir(exist_ok=True)
    r2, _ = run(IN1, spec("arhiva", out_dir=str(OUT / "nb")))  # isto bez burna, za poredjenje
    diff = frame_diff(r["out"], r2["out"], 1.0)
    ok(diff > 3, f"titl je stvarno utisnut (razlika {diff:.1f})")


def t_burn_srt_translated_trim():
    sp = spec("kompat", sub_src="track:0", translate="lat", sub_mode="burn", trim_start="1", trim_end="2.5")
    r, d = run(IN1, sp, sub_file=str(WORK / "sub.srt"))
    c = r["cmd"]
    ok("subtitles=sub.srt" in (arg_after(c, "-vf") or ""), "subtitles=sub.srt")
    ok(abs(sdur(d["v"][0]) - 1.5) < 0.1, f"trajanje {sdur(d['v'][0])}")
    eq(sub_times(r["out"]), [(0.6, 1.8)], "zadržan mek titl (mov_text) u sinhronu")


def t_burn_trim_timing():
    # titl vidljiv samo 1.6-2.3 s izvora; posle isecanja od 1 s mora biti vidljiv na ~0.6-1.3 s izlaza
    (WORK / "late.ass").write_text(ASS.replace("0:00:00.00,0:00:03.00", "0:00:01.60,0:00:02.30"))
    base = spec("arhiva", trim_start="1", trim_end="2.5", out_dir=str(OUT / "bt"))
    (OUT / "bt").mkdir(exist_ok=True)
    r1, _ = run(IN1, {**base, "sub_src": "file", "sub_file": "/x/late.srt", "sub_mode": "burn"},
                sub_file=str(WORK / "late.ass"))
    vf = arg_after(r1["cmd"], "-vf") or ""
    ok("setpts=PTS+1/TB,ass=late.ass,setpts=PTS-1/TB" in vf, f"ass vidi originalni tajming: {vf}")
    r2, _ = run(IN1, base)
    d_before, d_during = frame_diff(r1["out"], r2["out"], 0.3), frame_diff(r1["out"], r2["out"], 0.9)
    ok(d_before < 2 and d_during > 3, f"tajming burna: pre={d_before:.1f} tokom={d_during:.1f}")


def t_burn_copy_error():
    r = mkplan(IN1, spec("remux", sub_src="file", sub_file=str(WORK / "ext.en.srt"), sub_mode="burn"),
               sub_file=str(WORK / "burn.ass"))
    ok(r["errors"] and r["cmd"] == [], "burn + video copy -> greška")


def t_burn_outside_workdir_error():
    r = mkplan(IN1, spec("arhiva", sub_src="file", sub_file="/tmp/x.srt", sub_mode="burn"),
               sub_file=str(MEDIA / "in1.srt"))
    ok(r["errors"], "burn titl van workdir -> greška")


def t_image_sub_overlay():
    fake = json.loads(json.dumps(A[IN3]))
    fake["subs"] = [{"n": 0, "index": 2, "codec": "dvd_subtitle", "lang": "eng", "title": "", "text": False,
                     "forced": False, "default": False}]
    r = tb.plan(IN3, spec("arhiva", sub_src="track:0", sub_mode="burn", scale="480"), fake, FF,
                str(OUT / "ov.mkv"), str(WORK))
    eq(r["errors"], [], "bez grešaka")
    g = arg_after(r["cmd"], "-filter_complex") or ""
    ok("[0:s:0]overlay" in g, f"overlay iz [0:s:0]: {g}")
    ok(g.index("bwdif") < g.index("overlay") < g.index("scale="), "redosled deint → overlay → scale")
    eq(arg_after(r["cmd"], "-map"), "[vout]", "map [vout]")
    r = tb.plan(IN3, spec("arhiva", sub_src="track:0", translate="lat"), fake, FF, str(OUT / "ov.mkv"), str(WORK))
    ok(r["errors"], "prevod slikovnog titla -> greška")
    r = tb.plan(IN3, spec("apple"), fake, FF, str(OUT / "ov.mp4"), str(WORK))
    ok("0:s:0" not in r["cmd"] and any("slikovni" in w for w in r["warnings"]), "slikovni izbačen iz mp4")


# ---------------------------------------------------------------- izlaz / ostalo
def t_out_eq_src_error():
    r = tb.plan(IN1, spec("remux"), A[IN1], FF, IN1, str(WORK))
    ok(r["errors"] and r["cmd"] == [], "out == src -> greška")
    r = tb.plan(IN1, spec("remux"), A[IN1], FF, IN1.upper() if sys.platform == "darwin" else IN1, str(WORK))
    ok(r["errors"], "out == src (druga velika/mala slova na macOS)")


def t_default_out_path():
    d = OUT / "dop"
    d.mkdir()
    sp = spec("arhiva", out_dir=str(d))
    p1 = tb.default_out_path(IN1, sp, A[IN1])
    eq(p1, str(d / "in1.transkod.mkv"), "prvo ime")
    Path(p1).touch()
    p2 = tb.default_out_path(IN1, sp, A[IN1])
    eq(p2, str(d / "in1.transkod (2).mkv"), "drugo ime")
    Path(p2).touch()
    eq(tb.default_out_path(IN1, sp, A[IN1]), str(d / "in1.transkod (3).mkv"), "treće ime")
    p = tb.default_out_path(IN1, {**sp, "out_dir": "", "suffix": ""}, A[IN1])
    ok(p != IN1 and p.endswith("in1 (2).mkv") and os.path.dirname(p) == str(MEDIA), f"ne gazi izvor: {p}")
    eq(tb.default_out_path(IN4, spec("audio_mp3"), A[IN4]), str(OUT / "in4.transkod.mp3"), "ekstenzija")


def t_output_dims_scale():
    sp = spec("kompat", scale="480")
    eq(tb.output_dims(sp, A[IN1]), (854, 480), "output_dims 720p -> 480")
    r, d = run(IN1, sp)
    eq((d["v"][0]["width"], d["v"][0]["height"]), (854, 480), "stvarne dimenzije == output_dims")
    eq(tb.output_dims(spec("arhiva", scale="1080"), A[IN1]), (1280, 720), "bez uvećanja")
    port = {"video": {"width": 1080, "height": 1920}}
    eq(tb.output_dims(spec("arhiva", scale="720"), port), (720, 1280), "portret")


def t_audio_only_input():
    r, d = run(IN4, spec("arhiva"))
    eq((d["v"], codecs(d["a"])), ([], ["aac"]), "mkv samo zvuk")
    ok(any("nema video" in w for w in r["warnings"]), "upozorenje: nema video")
    r, d = run(IN4, spec("audio_m4a"))
    eq(codecs(d["a"]), ["aac"], "m4a")
    r = mkplan(IN4, spec("arhiva", sub_src="file", sub_file=str(WORK / "ext.en.srt"), sub_mode="burn"),
               sub_file=str(WORK / "burn.ass"))
    ok(r["errors"], "burn bez videa -> greška")


def t_invalid_values():
    for k, val in (("container", "avi"), ("vcodec", "vp9"), ("acodec", "wma"), ("sub_src", "x")):
        ok(mkplan(IN1, spec(**{k: val}))["errors"], f"{k}={val} -> greška")
    ok(mkplan(IN1, spec("mali", vq=65))["errors"], "CRF 65 za x265 -> greška")
    ok(mkplan(IN1, spec("apple", vcodec="prores_vt"))["errors"], "prores u mp4 -> greška")
    r = mkplan(IN1, spec("av1", vpreset="slow"))
    ok(not r["errors"] and arg_after(r["cmd"], "-preset") == "6", "loš preset -> podrazumevani + upozorenje")


def t_combos_execute():
    """Kombinacije koje moraju da prodju kroz ffmpeg bez greske (kodek x kontejner x filteri)."""
    (OUT / "cb").mkdir(exist_ok=True)
    cases = [
        (IN1, dict(container="mkv", acodec="eac3", abitrate="384k")),
        (IN1, dict(container="mkv", acodec="ac3", abitrate="448k", channels="6")),
        (IN1, dict(container="mkv", acodec="flac")),
        (IN1, dict(container="mkv", acodec="alac")),
        (IN1, dict(container="mkv", acodec="pcm")),
        (IN1, dict(container="mp4", acodec="alac")),
        (IN1, dict(container="mp4", acodec="opus")),
        (IN1, dict(container="mp4", acodec="ac3", abitrate="384k")),
        (IN1, dict(container="mov", acodec="eac3", abitrate="384k")),
        (IN1, dict(container="mov", acodec="pcm")),
        (IN1, dict(container="m4a", vcodec="none", acodec="alac", subs_keep="none")),
        (IN1, dict(container="mp4", vcodec="x264", vq=23, vpreset="veryfast", acodec="aac", loudnorm="-16",
                   channels="2")),
        (IN1, dict(container="mkv", vcodec="x264", vq=23, vpreset="veryfast", depth="10")),
        (IN1, dict(container="mp4", vcodec="av1", vq=40, vpreset="10", acodec="aac")),
        (IN1, dict(vbitrate="3M", fps="24", scale="576")),
        (IN1, dict(container="mov", vcodec="prores_vt", prores_profile="4444", acodec="pcm")),
        (IN1, dict(container="mov", vcodec="prores_vt", prores_profile="proxy", acodec="aac")),
        (IN1, dict(vcodec="h264_vt", container="mkv", sub_src="track:0", sub_mode="soft", subs_keep="none")),
        (IN2, dict(vcodec="x265", vq=28, vpreset="ultrafast", hdr="sdr", depth="8")),
        (IN2, dict(deint="yadif", scale="480", fps="24", hdr="sdr", sub_src="file", sub_file="/x/a.srt",
                   sub_mode="burn", trim_start="0.5")),
        (IN3, dict(container="mp4", vcodec="h264_vt", scale="480", acodec="aac", trim_start="0.4",
                   trim_end="2.2")),
        (IN4, dict(container="mp3", vcodec="none", acodec="mp3", abitrate="192k", loudnorm="-23")),
        (IN4, dict(container="flac", vcodec="none", acodec="flac", trim_start="1")),
    ]
    bad = []
    for i, (src, kw) in enumerate(cases):
        sp = spec("arhiva", out_dir=str(OUT / "cb"), suffix=f".c{i}", **kw)
        sf = str(WORK / "burn.ass") if kw.get("sub_mode") == "burn" else None
        try:
            r, d = run(src, sp, sub_file=sf)
            want_v = 0 if kw.get("vcodec") == "none" or src == IN4 else 1
            eq(len(d["v"]), want_v, "broj video strimova")
            ok(d["a"], "ima zvuk")
        except AssertionError as e:
            bad.append(f"#{i} {kw}: {str(e)[:400]}")
    ok(not bad, "\n      ".join(bad))


def t_container_codec_errors():
    for kw in (dict(container="mov", acodec="opus"), dict(container="mov", acodec="flac"),
               dict(container="m4a", vcodec="none", acodec="mp3"), dict(container="mp3", vcodec="none"),
               dict(container="mov", vcodec="av1", vq=30, vpreset="6")):
        r = mkplan(IN1, spec("arhiva", **kw))
        ok(r["errors"] and r["cmd"] == [], f"{kw} -> greška")


# ---------------------------------------------------------------- regresije (nezavisni pregled)
def t_rotation_portrait():
    """Telefon: 1280x720 + rotacija 90 -> prikaz 720x1280; scale i output_dims po prikazu."""
    base = gen("rot0.mov", ["-f", "lavfi", "-i", "testsrc2=s=1280x720:r=25:d=1", "-f", "lavfi", "-i",
                            "sine=d=1", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac"])
    src = gen("rot90.mov", ["-display_rotation:v:0", "90", "-i", base, "-c", "copy"])
    a = tb.analyze(src, FP)
    eq((a["video"]["width"], a["video"]["height"], a["video"]["rotation"]), (720, 1280, 90), "prikazane dimenzije")
    sp = spec("kompat", scale="480")
    eq(tb.output_dims(sp, a), (480, 854), "output_dims portret")
    r, d = run(src, sp)
    eq((d["v"][0]["width"], d["v"][0]["height"]), (480, 854), "stvarne dimenzije (ne 270x480)")


def t_scale_box_scope():
    """'1080'/'720' = okvir 16:9 (npr. 1920x1080): široki (scope) izvor ne sme biti širi od okvira."""
    src = gen("scope.mkv", ["-f", "lavfi", "-i", "testsrc2=s=1920x800:r=25:d=1", "-c:v", "libx264",
                            "-preset", "ultrafast"])
    a = tb.analyze(src, FP)
    eq(tb.output_dims(spec("kompat"), a), (1920, 800), "1920x800 staje u 1080 okvir")
    eq(tb.output_dims(spec("kompat", scale="720"), a), (1280, 534), "720 okvir -> 1280 široko")
    r, d = run(src, spec("kompat", scale="720"))
    eq((d["v"][0]["width"], d["v"][0]["height"]), (1280, 534), "stvarne dimenzije == output_dims")
    fake = {"video": {"width": 3840, "height": 1600}}
    eq(tb.output_dims(spec("kompat"), fake), (1920, 800), "4K scope -> kompat najviše 1920 široko")
    eq(tb.output_dims(spec("remux", scale="480"), A[IN1]), (1280, 720), "video copy -> bez skaliranja")


def t_image_burn_pgs_executes():
    """Slikovni titl (PGS) se stvarno utiskuje: isto platno, trim usred titla, platno != video."""
    (OUT / "ib").mkdir(exist_ok=True)
    src = with_pgs(IN1, "pgs.mkv")
    eq([(s["codec"], s["width"], s["default"]) for s in tb.analyze(src, FP)["subs"]],
       [("subrip", None, True), ("hdmv_pgs_subtitle", 1280, False)], "analyze: PGS + platno; srt je default")
    for i, kw in enumerate((dict(), dict(trim_start="1.8", trim_end="2.5"), dict(container="mp4"))):
        o = dict(out_dir=str(OUT / "ib"), acodec="aac", **kw)
        rb, _ = run(src, spec("arhiva", suffix=f".b{i}", **o))
        r, d = run(src, spec("arhiva", suffix=f".x{i}", sub_src="track:1", sub_mode="burn", **o))
        t0 = float(kw.get("trim_start") or 0)
        ok(frame_diff(r["out"], rb["out"], 1.9 - t0) > 3, f"{kw}: PGS utisnut na 1.9 s")
        if not t0:
            ok(frame_diff(r["out"], rb["out"], 1.0) < 2, f"{kw}: nema titla na 1.0 s")
        if "container" not in kw:  # mp4 muxer uvek ukljuci prvu traku tipa (default=1), ne moze drugacije
            ok(all(s["disposition"]["default"] == 0 for s in d["s"]), f"{kw}: meki titlovi nisu default")
    # PGS platno 1280x720 na videu 640x360: bez skaliranja traka je van kadra
    src = with_pgs(IN2, "pgs_small.mkv")
    rb, _ = run(src, spec("arhiva", suffix=".b9", out_dir=str(OUT / "ib"), hdr="sdr"))
    r, _ = run(src, spec("arhiva", suffix=".x9", out_dir=str(OUT / "ib"), hdr="sdr", sub_src="track:0",
                         sub_mode="burn"))
    ok("scale=640:-1" in (arg_after(r["cmd"], "-filter_complex") or ""), "PGS platno skalirano na video")
    ok(frame_diff(r["out"], rb["out"], 1.9) > 3, "PGS vidljiv i kad platno != video")
    # DVD titl (SD platno, ovde 1280x720) na 720x576 videu -> ceo kadar
    dvd = gen("dvd576.mkv", ["-i", IN3, "-i", with_pgs(IN1, "pgs.mkv"), "-map", "0:v", "-map", "0:a",
                             "-map", "1:s:1", "-c:v", "copy", "-c:a", "copy", "-c:s", "dvdsub"])
    rb, _ = run(dvd, spec("arhiva", suffix=".b8", out_dir=str(OUT / "ib")))
    r, _ = run(dvd, spec("arhiva", suffix=".x8", out_dir=str(OUT / "ib"), sub_src="track:0", sub_mode="burn"))
    ok("scale=720:576" in (arg_after(r["cmd"], "-filter_complex") or ""), "DVD platno -> ceo kadar")
    ok(frame_diff(r["out"], rb["out"], 1.9) > 3, "DVD titl utisnut")


def t_burn_special_names():
    """Ime titla sa [ ] : ' , ; = \\ % i razmakom na početku ne sme da obori filter."""
    src = str(MEDIA / "Test [a]: 'x' (2).mkv")
    shutil.copy(IN1, src)
    a = tb.analyze(src, FP)
    for i, n in enumerate(("Test [a]: 'x' (2).ass", "a=b,c;d\\e%f.ass", " lead.ass")):
        (WORK / n).write_text(ASS)
        sp = spec("kompat", sub_src="file", sub_file="/x/y.srt", sub_mode="burn", trim_start="0.5",
                  trim_end="1.5", suffix=f".n{i}")
        r = tb.plan(src, sp, a, FF, tb.default_out_path(src, sp, a), str(WORK), str(WORK / n))
        eq(r["errors"], [], f"{n!r}: plan")
        sh(r["cmd"], cwd=str(WORK))
        ok(os.path.exists(r["out"]), f"{n!r}: izlaz")


def t_unsupported_sub_codec():
    """TTML (mp4 stpp) ne ide u MKV/MP4: izbaci uz upozorenje umesto da ceo posao padne."""
    src = gen("ttml.mp4", ["-i", IN1, "-map", "0:v", "-map", "0:a:0", "-map", "0:s", "-c:v", "copy",
                           "-c:a", "copy", "-c:s", "ttml"])
    a = tb.analyze(src, FP)
    eq([(s["codec"], s["text"]) for s in a["subs"]], [("ttml", False)], "analyze ttml")
    ok(any("ttml" in w for w in a["warnings"]), "analyze upozorenje")
    for p in ("arhiva", "apple", "remux"):
        r, d = run(src, spec(p, suffix=f".tt{p}"))
        ok(any("ttml" in w for w in r["warnings"]) and d["s"] == [], f"{p}: ttml izbačen")
    r = mkplan(src, spec("arhiva", sub_src="track:0", sub_mode="burn"))
    ok(r["errors"], "burn ttml -> greška (nije slikovni ni tekstualni)")


def t_tonemap_strips_hdr_metadata():
    """HDR -> SDR: izlaz ne sme da nosi HDR10 mastering/CLL (plejer/TV bi ga tretirao kao HDR)."""
    ok(any("Mastering" in x for x in side_data(IN2)), "izvor ima mastering metadata")
    for p, kw in (("kompat", {}), ("mali", dict(hdr="sdr", vpreset="ultrafast"))):
        r, d = run(IN2, spec(p, suffix=".sdr", **kw))
        sd = side_data(r["out"])
        ok(not any("Mastering" in x or "light level" in x for x in sd), f"{p}: HDR metapodaci ostali: {sd}")
        eq(d["v"][0].get("color_transfer"), "bt709", f"{p}: bt709")
    r, _ = run(IN2, spec("arhiva", suffix=".keep"))
    ok(any("Mastering" in x for x in side_data(r["out"])), "HDR keep zadržava mastering metadata")


def t_hdr_burn_warns():
    r = mkplan(IN2, spec("arhiva", sub_src="file", sub_file=str(WORK / "ext.en.srt"), sub_mode="burn"),
               sub_file=str(WORK / "burn.ass"))
    ok(any("presvetao" in w for w in r["warnings"]), f"burn u HDR bez tonemapa -> upozorenje: {r['warnings']}")
    r = mkplan(IN2, spec("kompat", sub_src="file", sub_file=str(WORK / "ext.en.srt"), sub_mode="burn"),
               sub_file=str(WORK / "burn.ass"))
    ok(not any("presvetao" in w for w in r["warnings"]), "uz tonemap nema upozorenja")


def t_loudnorm_short_clip():
    """loudnorm < 3 s: flac pada (blok), tišina daje NaN (aac greška, lame assert) -> preskoči."""
    r, d = run(IN1, spec("audio_flac", loudnorm="-16", trim_start="1", trim_end="2.5"))
    ok(any("loudnorm" in w for w in r["warnings"]) and "loudnorm" not in cmd_str(r), "flac: loudnorm preskočen")
    sil = gen("sil2.m4a", ["-f", "lavfi", "-i", "anullsrc=d=2:r=48000:cl=stereo", "-c:a", "aac"])
    for p in ("audio_m4a", "audio_mp3"):
        run(sil, spec(p, loudnorm="-16"))
    ok("loudnorm" in cmd_str(mkplan(IN1, spec("audio_flac", loudnorm="-16"))), "3 s i više: loudnorm ostaje")


def t_relative_paths():
    """Relativne putanje: komanda se izvršava sa cwd=workdir, pa plan mora da ih pretvori u apsolutne."""
    cwd = os.getcwd()
    os.chdir(MEDIA)
    try:
        a = tb.analyze("in1.mkv", FP)
        r = tb.plan("in1.mkv", spec("remux"), a, FF, "rel_out.mkv", "../work")
        eq(os.path.realpath(r["out"]), os.path.realpath(MEDIA / "rel_out.mkv"), "izlaz apsolutan")
        ok(os.path.isabs(r["out"]) and all(os.path.isabs(x) for x in r["cmd"] if x.endswith(".mkv")),
           "putanje u komandi apsolutne")
    finally:
        os.chdir(cwd)
    sh(r["cmd"], cwd=str(WORK))
    ok((MEDIA / "rel_out.mkv").exists(), "izlaz napravljen pored izvora, ne u workdir")


def t_ext_sub_container():
    """Spoljni titl kao kontejner (video + titl): mapira se titl, ne prvi strim (video)."""
    ext = gen("ext_with_video.mkv", ["-f", "lavfi", "-i", "testsrc2=s=320x240:d=3", "-i", str(WORK / "ext.en.srt"),
                                     "-map", "0", "-map", "1", "-c:v", "libx264", "-preset", "ultrafast",
                                     "-c:s", "srt"])
    r, d = run(IN1, spec("arhiva", sub_src="file", sub_file=ext, subs_keep="none"))
    eq((len(d["v"]), codecs(d["s"])), (1, ["subrip"]), "jedan video + dodat titl")


def t_subs_not_auto_default():
    """2+ titla bez default-a: ffmpeg bi sam stavio default na prvi (titlovi bi se sami uključili)."""
    src = gen("twosubs.mkv", ["-i", IN1, "-i", str(WORK / "sr.srt"), "-map", "0", "-map", "1", "-c", "copy",
                              "-disposition:s:0", "0", "-disposition:s:1", "0"])
    eq([s["default"] for s in tb.analyze(src, FP)["subs"]], [False, False], "izvor: nijedan default")
    for p in ("remux", "arhiva"):
        r, d = run(src, spec(p, suffix=".nd"))
        eq([s["disposition"]["default"] for s in d["s"]], [0, 0], f"{p}: titlovi i dalje nisu default")
        eq([s["disposition"]["default"] for s in d["a"]], [1, 0], f"{p}: audio default zadržan")
    r, d = run(with_pgs(IN1, "pgs.mkv"), spec("remux", suffix=".nd"))
    eq([s["disposition"]["default"] for s in d["s"]], [1, 0], "default titl izvora ostaje default")


def t_stale_mkv_stat_tags():
    """mkvmerge statistika (BPS, NUMBER_OF_BYTES…) posle rekodiranja/isecanja ne sme da ostane."""
    src = gen("stats.mkv", ["-i", IN1, "-map", "0", "-c", "copy", "-metadata:s:v:0", "BPS=99999999",
                            "-metadata:s:v:0", "NUMBER_OF_BYTES=123",
                            "-metadata:s:v:0", "_STATISTICS_TAGS=BPS DURATION NUMBER_OF_FRAMES NUMBER_OF_BYTES"])
    a = tb.analyze(src, FP)
    eq(a["stat_tags"], ["BPS", "NUMBER_OF_BYTES", "_STATISTICS_TAGS"], "analyze stat_tags")
    r, d = run(src, spec("arhiva", suffix=".st"))
    tags = {k.upper() for k in d["v"][0].get("tags") or {}}
    ok(not tags & {"BPS", "NUMBER_OF_BYTES", "_STATISTICS_TAGS"}, f"zastarele oznake ostale: {tags}")
    ok(tb.analyze(r["out"], FP)["video"]["bitrate"] != 99999999, "bitrate izlaza nije bitrate izvora")


def t_ext_image_sub_file():
    """Spoljni .sup: MKV meki (copy) radi; MP4, burn i prevod -> greška (ne pad ffmpeg-a)."""
    sup = MEDIA / "t.sup"
    with_pgs(IN1, "pgs.mkv")
    base = dict(sub_src="file", sub_file=str(sup), subs_keep="none", acodec="aac")
    r, d = run(IN1, spec("arhiva", suffix=".sup", **base))
    eq(codecs(d["s"]), ["hdmv_pgs_subtitle"], "PGS iz .sup kopiran u MKV")
    for kw in (dict(container="mp4"), dict(sub_mode="burn"), dict(translate="lat")):
        r = mkplan(IN1, spec("arhiva", **base, **kw))
        ok(r["errors"] and not r["cmd"], f"{kw}: greška")


def t_lang_from_name():
    for n, want in (("film.de.srt", "deu"), ("film [x].sr-Latn.srt", "srp"), ("film.eng.srt", "eng"),
                    ("film.mkv.srt", "und"), ("The.Web.srt", "und"), ("film.he.srt", "heb")):
        eq(tb._lang_from_name(n), want, n)


def t_pcm_depth():
    for c, want in (("pcm_s16le", "pcm_s16le"), ("pcm_s24le", "pcm_s24le")):
        src = gen(f"{c}.mkv", ["-f", "lavfi", "-i", TONE2, "-c:a", c])
        r = mkplan(src, spec("arhiva", vcodec="none", acodec="pcm"))
        eq(arg_after(r["cmd"], "-c:a:0"), want, f"{c} -> {want}")


def main():
    only = [x.lower() for x in sys.argv[1:]]
    tests = [(n, f) for n, f in globals().items() if n.startswith("t_") and callable(f)]
    if only:
        tests = [(n, f) for n, f in tests if any(o in n.lower() for o in only)]
    t_all = time.time()
    fails = 0
    try:
        gen_media()
        print(f"mediji: {time.time() - t_all:.1f}s  ({TMP})")
        for n, f in tests:
            t = time.time()
            try:
                f()
                print(f"PASS  {n[2:]:<32} {time.time() - t:5.1f}s")
            except Exception as e:  # noqa: BLE001
                fails += 1
                msg = str(e) if isinstance(e, AssertionError) else traceback.format_exc()
                print(f"FAIL  {n[2:]:<32} {time.time() - t:5.1f}s  {msg}")
    finally:
        if not os.environ.get("TB_KEEP"):
            shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{len(tests) - fails}/{len(tests)} prošlo za {time.time() - t_all:.1f}s")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
