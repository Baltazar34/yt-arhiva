#!/usr/bin/env python3
"""yt-arhiva panel — lokalni web panel: link -> probe -> PREUZIMANJE -> (odmah ili kasnije) OBRADA.

  python3 panel.py            # http://127.0.0.1:8790
  YTA_PORT=9000 YTA_DATA=/tmp/yta python3 panel.py

Svaki preuzeti video je stavka ARHIVE (folder sa .yta/manifest.json):
  <stem> [id].source.mkv     izvor: video + sve izabrane audio trake (sa jezikom/nazivom)
  subs/*.srt                 svi preuzeti titlovi (rucni CC, auto, whisper) + prevodi
  <stem> [id].sr.mp4 / .mkv  izlazi obrade (burn / meki titlovi)
Obrada (izbor titla, prevod, audio trake, enkodiranje) moze odmah posle preuzimanja
ili kasnije iz Arhive, koliko god puta, sa razlicitim izborima.

Paralelno: vise preuzimanja; titlovi+prevod teku dok se video skida.
Globalni limiteri: preuzimanja, 1 whisper, prevodi, enkodiranja.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import yt_arhiva as core  # noqa: E402
import memory as memlib  # noqa: E402  memorija ispravki (docs/memory-contract.md)

DATA = Path(os.environ.get("YTA_DATA") or ROOT / "data")
DATA.mkdir(parents=True, exist_ok=True)
SETTINGS_P = DATA / "settings.json"
JOBS_P = DATA / "jobs.json"
ROOTS_P = DATA / "library_roots.json"
WHISPER_DIR = core.whisper_models_dir()  # YTA_WHISPER_DIR > ~/Library/Application Support/YT Arhiva/whisper
INTERNAL_MODELS = Path.home() / ".ollama" / "models"
PORT = int(os.environ.get("YTA_PORT", "8790"))

DEFAULTS = {
    "browser": "chrome",          # cookies iz browsera (tvoj YouTube nalog)
    "cookies_file": "",           # alternativa: cookies.txt
    "out_dir": str(core.DEFAULT_OUT),
    "parallel_downloads": 3,
    "fragments": 8,               # paralelni fragmenti unutar jednog preuzimanja
    "parallel_encodes": 2,
    "parallel_translate": 1,      # ollama lokalno: 1 (GPU je jedan)
    "engine": "synergy",          # Kvalitetno: TranslateGemma + Gemma (C); ollama = Brzo; gemini; cloudflare; claude
    "model": "gemma4:12b-it-qat",
    "cf_account": "",             # Cloudflare Account ID (token je u Keychain-u: yt-arhiva-cf-token)
    "script": "lat",
    "output": "none",           # obrada je opciona; burn samo kad se izabere
    "encoder": "hevc",
    "quality": 65,
    "keep_original": True,        # zadrzi preuzeti izvor posle obrade
    "whisper_model": "large-v3-turbo-q5_0",
    "auto_fetch": True,           # Ollama, whisper-cpp i modeli se preuzimaju sami pri prvom koriscenju
    "whisper_quality_model": "large-v3-q5_0",   # "kvalitet" rezim (grcki/srpski): tacniji, 3-4x sporiji
    "whisper_quality_langs": "el,sr",           # jezici za koje se automatski uzima kvalitet model ("" = nikad)
    "whisper_vad": True,                        # Silero VAD: manje halucinacija na muzici/tisini
    "whisper_prompt": True,                     # prompt po jeziku (pismo, imena iz naslova)
    "mem_tr": True,               # memorija ispravki u prevodu i lekturi (glosar, naucena pravila, primeri, TM)
    "mem_stt": True,              # memorija u whisper-u (termini u promptu, zamene) i u auto titlu pre prevoda
    "dl_audio": "all",            # podrazumevano: sve audio trake (auto = samo originalna)
    "dl_subs": "all",             # podrazumevano: svi CC + auto original (auto = samo jedan)
    "proc_when": "later",         # podrazumevano: samo preuzmi; obrada (prevod/burn) samo na klik
    "recipe": "samo",             # podrazumevani izbor u kartici Preuzmi (vidi recipes()); samo = bez obrade
    "auto_translate": True,       # recepti Arhiva / Arhiva+video / Samo titlovi prevode na srpski
    "embed_subs": True,           # YouTube titlovi i kao trake u MKV izvoru (-c copy), ne samo u subs/
}

LANG_NAMES = {
    "en": "English", "sr": "Srpski", "sh": "Srpskohrvatski", "hr": "Hrvatski", "bs": "Bosanski",
    "de": "Deutsch", "es": "Español", "it": "Italiano", "fr": "Français", "el": "Ελληνικά",
    "pt": "Português", "ru": "Русский", "uk": "Українська", "pl": "Polski", "nl": "Nederlands",
    "tr": "Türkçe", "ar": "العربية", "hi": "हिन्दी", "ja": "日本語", "ko": "한국어", "zh": "中文",
    "id": "Bahasa Indonesia", "sv": "Svenska", "cs": "Čeština", "hu": "Magyar", "ro": "Română",
    "bg": "Български", "mk": "Македонски", "sl": "Slovenščina", "bn": "বাংলা", "th": "ไทย",
    "vi": "Tiếng Việt", "und": "nepoznat",
}
ISO3 = {"en": "eng", "sr": "srp", "sh": "hbs", "hr": "hrv", "bs": "bos", "de": "deu", "es": "spa",
        "it": "ita", "fr": "fra", "el": "ell", "pt": "por", "ru": "rus", "uk": "ukr", "pl": "pol",
        "nl": "nld", "tr": "tur", "ar": "ara", "hi": "hin", "ja": "jpn", "ko": "kor", "zh": "zho",
        "id": "ind", "sv": "swe", "cs": "ces", "hu": "hun", "ro": "ron", "bg": "bul", "mk": "mkd",
        "sl": "slv", "bn": "ben", "th": "tha", "vi": "vie", "ms": "msa", "fa": "fas", "he": "heb",
        "iw": "heb", "ta": "tam", "te": "tel", "ur": "urd", "fil": "fil", "tl": "tgl", "da": "dan",
        "no": "nor", "nb": "nob", "fi": "fin", "sk": "slk", "lt": "lit", "lv": "lav", "et": "est",
        "ka": "kat", "hy": "hye", "sq": "sqi", "mr": "mar", "gu": "guj", "kn": "kan", "ml": "mal",
        "pa": "pan", "sw": "swa", "af": "afr", "ca": "cat", "eu": "eus", "gl": "glg"}

ISO3_REV = {v: k for k, v in ISO3.items()}  # eng -> en

_lock = threading.RLock()
MLOCK = threading.RLock()  # manifest upisi (download i titl niti pisu istovremeno)


def base_lang(code: str | None) -> str:
    b = (code or "und").split("-")[0].lower()
    return ISO3_REV.get(b, b) if len(b) == 3 else b


def lang_label(code: str | None) -> str:
    b = base_lang(code)
    return LANG_NAMES.get(b, code or "?")


def iso3(code: str | None) -> str:
    return ISO3.get(base_lang(code), base_lang(code) or "und")


# ================================================================ settings
def load_settings() -> dict:
    s = dict(DEFAULTS)
    if SETTINGS_P.exists():
        try:
            s.update(json.loads(SETTINGS_P.read_text()))
        except Exception:  # noqa: BLE001
            pass
    return migrate_plan(s)


def migrate_plan(s: dict) -> dict:
    """Jednokratno (3.2): stari podrazumevani recept "arhiva" (sa prevodom) -> "samo" (samo preuzimanje)."""
    if s.get("plan_migrated"):
        return s
    s["plan_migrated"] = True  # nova instalacija: upisuje se uz prvo cuvanje podesavanja
    if not SETTINGS_P.exists():
        return s
    try:
        raw = json.loads(SETTINGS_P.read_text())
        if not isinstance(raw, dict):
            return s
        if raw.get("recipe") == "arhiva":
            s["recipe"] = raw["recipe"] = "samo"
        raw["plan_migrated"] = True
        tmp = SETTINGS_P.with_suffix(".tmp")
        tmp.write_text(json.dumps(raw, indent=2, ensure_ascii=False))
        tmp.replace(SETTINGS_P)
    except Exception as e:  # noqa: BLE001  neispravan fajl: ne prepisuj ga
        print(f"migracija podešavanja nije upisana: {e}", file=sys.stderr)
    return s


SETTINGS = load_settings()
core.CF_ACCOUNT = SETTINGS.get("cf_account") or core.CF_ACCOUNT


def as_bool(v) -> bool:
    # "false" / "0" / "ne" iz forme (select, string) je False; bool("false") bi bio True
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "da", "yes", "on")
    return bool(v)


def save_settings(new: dict) -> dict:
    with _lock:
        for k, v in new.items():
            # "custom" = ⚙ Prilagođeno kao podrazumevani izbor (UI ga nudi u Podešavanjima)
            if (k == "recipe" and v not in RECIPE_IDS + ("custom",)) or (k == "script" and v not in ("lat", "cyr")) \
                    or (k == "engine" and v not in ENGINES):
                continue
            if k in DEFAULTS:
                SETTINGS[k] = type(DEFAULTS[k])(v) if not isinstance(DEFAULTS[k], bool) else as_bool(v)
        SETTINGS_P.write_text(json.dumps(SETTINGS, indent=2, ensure_ascii=False))
        core.CF_ACCOUNT = SETTINGS.get("cf_account") or core.CF_ACCOUNT
        LIM["download"].set(SETTINGS["parallel_downloads"])
        LIM["encode"].set(SETTINGS["parallel_encodes"])
        LIM["translate"].set(SETTINGS["parallel_translate"])
    return SETTINGS


def cookie_args(s: dict | None = None) -> SimpleNamespace:
    s = s or SETTINGS
    cf = s.get("cookies_file") or ""
    return SimpleNamespace(cookies=Path(cf).expanduser() if cf else None,
                           browser=s.get("browser") or "none")


ENGINES = ("synergy", "ollama", "gemini", "cloudflare", "claude", "tg")
TARGET_NAME = {"lat": "srpski latinica", "cyr": "srpski ćirilica", "el": "grčki"}


def engine_label(engine: str | None, model: str | None = None) -> str:
    e = engine or SETTINGS.get("engine") or "synergy"
    m = model or SETTINGS.get("model") or ""
    return {"synergy": "Kvalitetno: TranslateGemma + Gemma", "ollama": f"Brzo: {m}", "tg": "TranslateGemma",
            "gemini": f"Gemini {m.replace('gemini-', '')}" if m.startswith("gemini") else "Gemini",
            "cloudflare": "Cloudflare Gemma 4", "claude": f"Claude {m}"}.get(e, f"{e} {m}")


def engine_model(engine: str | None, model: str | None) -> tuple[str, str]:
    """Model koji ide uz prevodioca (UI salje npr. gemini|gemini-3.5-flash-lite)."""
    e = engine or SETTINGS["engine"]
    m = model or ""
    if e in ("synergy", "ollama") and (not m or m.startswith(("gemini", "@cf/")) or m in ("sonnet", "opus")):
        m = SETTINGS["model"] if not (SETTINGS["model"] or "").startswith(("gemini", "@cf/")) else core.GEMMA_MODEL
    elif e in core.CLOUD_DEFAULT and e != "ollama" and not m.startswith("gemini" if e == "gemini" else "@cf/"):
        m = core.CLOUD_DEFAULT[e]
    elif e == "claude" and m not in ("sonnet", "opus"):
        m = "sonnet"
    elif e == "tg":
        m = core.TG_MODEL
    return e, m


_KEYS_AT = {"t": 0.0, "v": {}}


def cloud_keys() -> dict:
    """Koji cloud prevodioci imaju kljuc (Keychain), kes 60 s; vrednosti se nikad ne salju."""
    if time.time() - _KEYS_AT["t"] > 60:
        _KEYS_AT["v"] = {"gemini": bool(core.keychain(core.KEY_SERVICES["gemini"], fresh=True)),
                         "cloudflare": bool(core.keychain(core.KEY_SERVICES["cloudflare"], fresh=True)
                                            and (SETTINGS.get("cf_account") or core.CF_ACCOUNT))}
        _KEYS_AT["t"] = time.time()
    return _KEYS_AT["v"]


# ================================================================ recepti (kartica Preuzmi)
RECIPE_IDS = ("samo", "arhiva", "arhiva_video", "muzika", "titlovi")


def recipes(s: dict | None = None) -> list[dict]:
    """Gotovi izbori preuzimanja + obrade. proc None = samo preuzimanje (obrada kasnije iz Biblioteke)."""
    s = s or SETTINGS
    script = s.get("script") if s.get("script") in ("lat", "cyr") else "lat"
    tr = as_bool(s.get("auto_translate", True))
    tgt = script if tr else "none"
    who = engine_label(s.get("engine"), s.get("model"))
    sr = f"prevod: srpski {'ćirilica' if script == 'cyr' else 'latinica'} ({who})" if tr else "bez prevoda"
    video_dl = {"video_fmt": "auto:best", "audio_fmt": "all", "subs": "all"}
    base = {"sub_source": "auto-best", "target": tgt}
    knob_sr = ["script", "engine"] if tr else []
    emb = " (i u MKV izvoru)" if as_bool(s.get("embed_subs", True)) else ""
    out = [
        {"id": "samo", "icon": "⬇", "label": "Samo preuzmi",
         "desc": f"Ceo sadržaj: najbolji video · sve audio trake · svi titlovi{emb} · bez prevoda i rekodiranja",
         "dl": dict(video_dl), "proc": None, "knobs": ["quality"]},
        {"id": "arhiva", "icon": "📦", "label": "Arhiva",
         "desc": f"Najbolji video · sve audio trake · svi titlovi · {sr}{' → SRT u arhivi' if tr else ''}"
                 " · bez rekodiranja",
         "dl": dict(video_dl),
         "proc": {**base, "output": "none", "keep_source": True} if tr else None,
         "knobs": knob_sr + ["quality"]},
        {"id": "arhiva_video", "icon": "🎬", "label": "Arhiva + video sa titlom",
         "desc": f"Isto kao Arhiva + MP4 za gledanje: HEVC (VideoToolbox), upečen "
                 f"{'srpski' if tr else 'originalni'} titl, originalni zvuk · {sr}",
         "dl": dict(video_dl),
         "proc": {**base, "output": "burn", "encoder": "hevc", "vq": 65, "scale": "src", "audio_out": "orig",
                  "audio_enc": "aac192", "subs_out": "sr+orig", "keep_source": True},
         "knobs": knob_sr + ["quality"]},
        {"id": "muzika", "icon": "🎵", "label": "Muzika / podkast",
         "desc": "Samo audio (MP3 320), omot i tagovi · bez titlova i prevoda",
         "dl": {"video_fmt": "audio", "audio_fmt": "auto", "audio_conv": "mp3", "subs": "none"},
         "proc": None, "knobs": ["audio_conv"]},
        {"id": "titlovi", "icon": "💬", "label": "Samo titlovi",
         "desc": f"Svi titlovi · {sr} → SRT fajl · bez videa",
         "dl": {"video_fmt": "none", "subs": "all"},
         "proc": {**base, "output": "srt"}, "knobs": knob_sr},
    ]
    for r in out:
        r["default"] = r["id"] == (s.get("recipe") or "samo")
    return out


def deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def item_opts(common: dict, own: dict | None) -> dict:
    """Zajednicke opcije batch-a + opcije jedne stavke (override); dl i proc se spajaju posebno.
    own {"proc": None} = ta stavka bez obrade."""
    o = json.loads(json.dumps(common or {}))
    own = json.loads(json.dumps({k: v for k, v in (own or {}).items() if not str(k).startswith("_")}))
    if not own:
        return o
    if not o:  # bez zajednickih opcija: kao da su opcije stavke poslate za ceo batch
        return own  # (normalize_opts({}) bi dodao proc {"keep_source"} -> nezatrazen prevod)
    if "dl" in o or "dl" in own or "proc" in own:
        o = normalize_opts(o)
        for k in ("dl", "proc", "after"):
            if k in own:
                v = own[k]
                o[k] = deep_merge(o[k], v) if isinstance(v, dict) and isinstance(o.get(k), dict) else v
        return o
    return deep_merge(o, own)


# ================================================================ limiter
class Cancelled(Exception):
    pass


class Limiter:
    def __init__(self, n: int):
        self.n, self.used, self.cv = max(1, int(n)), 0, threading.Condition()

    def set(self, n: int):
        with self.cv:
            self.n = max(1, int(n))
            self.cv.notify_all()

    def acquire(self, job: "Job"):
        with self.cv:
            while self.used >= self.n:
                if job.cancel.is_set():
                    raise Cancelled()
                self.cv.wait(0.5)
            self.used += 1

    def release(self):
        with self.cv:
            self.used -= 1
            self.cv.notify_all()


LIM = {
    "download": Limiter(SETTINGS["parallel_downloads"]),
    "whisper": Limiter(1),
    "translate": Limiter(SETTINGS["parallel_translate"]),
    "encode": Limiter(SETTINGS["parallel_encodes"]),
    "probe": Limiter(4),
}
SLOT_STAGE = {"download": "download", "whisper": "subs", "translate": "translate", "encode": "output",
              "probe": "download"}


class slot:
    def __init__(self, name: str, job: "Job"):
        self.name, self.job = name, job

    def __enter__(self):
        self.job.set_stage(SLOT_STAGE[self.name], "waiting", info="čeka slobodan slot…")
        LIM[self.name].acquire(self.job)

    def __exit__(self, *a):
        LIM[self.name].release()


# ================================================================ probe
PROBES: dict[str, dict] = {}  # url -> info


def ytdlp_cmd() -> list[str]:
    return core.ytdlp_base(cookie_args())


def probe_url(url: str) -> dict:
    p = subprocess.run(ytdlp_cmd() + ["-J", "--no-playlist", url],
                       capture_output=True, text=True, timeout=180)
    if p.returncode != 0:
        err = "\n".join(p.stderr.strip().splitlines()[-6:]) or "yt-dlp greska"
        if "could not find chrome cookies database" in err.lower() or "operation not permitted" in err.lower():
            err += ("\n→ macOS ne dozvoljava ovom programu da čita Chrome. Trajno rešenje: System Settings → Privacy & "
                    "Security → Full Disk Access → + Terminal (uključi), pa panel pokreni iz Terminal.app "
                    "(YT Arhiva.app ili installer/launcher.sh). Kad pita za Keychain 'Chrome Safe Storage': lozinka za Mac + Always Allow.")
        elif "cookies" in err.lower():
            err += ("\n→ Panel mora da radi iz tvog Terminala (YT Arhiva.app ili installer/launcher.sh). Kad macOS pita za "
                    "Keychain 'Chrome Safe Storage' klikni Always Allow. Ili u Podešavanjima izaberi "
                    "drugi browser / cookies.txt.")
        raise RuntimeError(err)
    info = json.loads(p.stdout)
    PROBES[url] = info
    return info


LIST_TABS = {"videos": "videos", "shorts": "shorts", "live": "streams", "podcasts": "podcasts",
             "releases": "releases"}


def is_list_url(url: str) -> bool:
    u = url.lower()
    if "list=" in u and "v=" not in u and "youtu.be/" not in u:
        return True
    if "/playlist" in u:
        return True
    return bool(re.search(r"youtube\.com/(@[^/?#]+|channel/[^/?#]+|c/[^/?#]+|user/[^/?#]+)(/(videos|shorts|streams|podcasts|releases|featured))?/?(\?|$)", u))


def _flat(url: str) -> dict:
    p = subprocess.run(ytdlp_cmd() + ["--flat-playlist", "-J", url], capture_output=True, text=True, timeout=600)
    if p.returncode != 0 or not p.stdout.strip() or p.stdout.strip() == "null":
        raise RuntimeError("\n".join(p.stderr.strip().splitlines()[-4:]) or "yt-dlp: plejlista nije dostupna")
    return json.loads(p.stdout)


def probe_list(url: str) -> dict:
    """Plejlista / kanal -> ravna lista stavki (bez probe svakog videa). Tabovi kanala se razvijaju."""
    d = _flat(url)
    cat = catalog_index()
    entries, tabs = [], []

    def add(e: dict, tab: str | None):
        vid = e.get("id")
        if not vid:
            return
        entries.append({
            "id": vid, "url": e.get("url") if (e.get("url") or "").startswith("http") else f"https://www.youtube.com/watch?v={vid}",
            "title": e.get("title") or vid, "duration": e.get("duration"), "channel": e.get("channel") or e.get("uploader"),
            "thumbnail": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg", "live_status": e.get("live_status"),
            "tab": tab, "have": cat_short(cat.get(vid)),
        })
    for e in d.get("entries") or []:
        if not e:
            continue
        if e.get("_type") == "playlist":  # tab kanala ili ugnjezdena plejlista -> jedan nivo dublje
            name = (e.get("title") or "").rsplit(" - ", 1)[-1]
            sub_url = e.get("url") if (e.get("url") or "").startswith("http") else None
            if not sub_url and LIST_TABS.get(name.lower()):
                sub_url = url.split("?")[0].rstrip("/") + "/" + LIST_TABS[name.lower()]
            if not sub_url:
                continue
            try:
                sd = _flat(sub_url)
            except Exception as ex:  # noqa: BLE001
                tabs.append({"name": name, "count": 0, "error": str(ex)[:200]})
                continue
            n0 = len(entries)
            for se in sd.get("entries") or []:
                if se and se.get("_type") != "playlist":
                    add(se, name)
            tabs.append({"name": name, "count": len(entries) - n0})
        else:
            add(e, None)
    res = {"kind": "playlist", "url": url, "id": d.get("id"), "title": d.get("title") or url,
           "uploader": d.get("uploader") or d.get("channel"), "count": len(entries), "tabs": tabs,
           "entries": entries, "have_count": sum(1 for e in entries if e["have"] and e["have"]["media"])}
    try:  # snimak plejliste: novi / sklonjeni u odnosu na prethodnu proveru
        prev_ids, removed = snapshot_playlist(res, partial=any(t.get("error") for t in tabs))
    except Exception as ex:  # noqa: BLE001
        print(f"snimak plejliste nije upisan: {ex}", file=sys.stderr)
        prev_ids, removed = None, []
    for e in entries:
        e["new"] = prev_ids is not None and e["id"] not in prev_ids
        e["state"] = "have" if e["have"] and e["have"]["media"] else "new" if e["new"] else "missing"
    res["removed"] = removed
    res["new_count"] = sum(1 for e in entries if e["new"])
    return res


def catalog_index() -> dict[str, list[dict]]:
    idx: dict[str, list[dict]] = {}
    for it in library():
        if it.get("id"):
            idx.setdefault(it["id"], []).append(it)
    return idx


def cat_short(items: list[dict] | None) -> dict | None:
    """Sazetak onoga sto imamo za jedan video (za kartice i plejliste)."""
    if not items:
        return None
    it = max(items, key=lambda i: (bool(i.get("source")), i.get("updated", 0)))
    return {"folder": it["folder"], "media": bool(it.get("source")), "kind": it.get("kind"),
            "video": it.get("video"), "audio": len(it.get("audio") or []), "has_audio": it.get("has_audio", True),
            "subs": len([x for x in it.get("subs", []) if x["kind"] in ("manual", "auto", "whisper")]),
            "tr": len([x for x in it.get("subs", []) if x["kind"] == "sr"]), "sr": it.get("sr") or [],
            "outputs": it.get("outputs", []), "size": it.get("size", 0), "copies": len(items),
            # tacno stanje prevoda/transkripta (isti oblik kao u library())
            "tr_state": it.get("tr_state") or "none", "translated": it.get("translated") or [],
            "transcripts": it.get("transcripts") or [], "sr_cc": bool(it.get("sr_cc"))}


def track_name(k: str, tracks: list | None) -> str:
    if tracks and tracks[0].get("name"):
        return tracks[0]["name"]
    return lang_label(k)


def _size(f: dict) -> int:
    return int(f.get("filesize") or f.get("filesize_approx") or 0)


def _is_audio_only(f: dict) -> bool:
    return f.get("vcodec") in (None, "none") and f.get("acodec") not in (None, "none")


def audio_role(f: dict, orig: bool = False) -> str:
    """original | dub | default | descriptive (YouTube language_preference: 10 / 5 / -10, format_note)."""
    note = (f.get("format_note") or "").lower()
    try:
        lp = int(f.get("language_preference"))
    except (TypeError, ValueError):
        lp = None
    if (lp is not None and lp >= 10) or "original" in note:
        return "original"
    if lp == -10 or "descriptive" in note:
        return "descriptive"
    if "dub" in note:
        return "dub"
    if lp == 5 or "default" in note:
        return "default"
    return "original" if orig else "default"


def audio_groups(info: dict) -> list[dict]:
    """Najbolji audio format po (jezik, uloga) — za opciju 'sve audio trake'.
    Uloga je u kljucu: engleski "descriptive" ne nestaje uz engleski default; isti zvuk u drugom kodeku/DRC
    (ista uloga) se preskace."""
    groups: dict[tuple, tuple] = {}
    for f in info.get("formats") or []:
        if not _is_audio_only(f):
            continue
        note = (f.get("format_note") or "").lower()
        fid = f.get("format_id") or ""
        lang = f.get("language") or "und"
        orig = "original" in note or int(f.get("language_preference") or -1) >= 10
        variant = "orig" if orig else ("dub" if "dub" in note else "")
        drc = "drc" in fid.lower() or "drc" in note
        score = (not drc, (f.get("acodec") or "").startswith("opus"),
                 float(f.get("abr") or f.get("tbr") or 0))
        key = (lang, audio_role(f, orig))
        if key not in groups or score > groups[key][0]:
            groups[key] = (score, f, variant)
    out = []
    for (lang, role), (_, f, variant) in groups.items():
        out.append({"id": f["format_id"], "lang": lang, "variant": variant, "original": variant == "orig",
                    "role": role,
                    "acodec": (f.get("acodec") or "").split(".")[0],
                    "abr": round(float(f.get("abr") or f.get("tbr") or 0)), "size": _size(f) or None})
    order = {"original": 0, "default": 1, "dub": 2, "descriptive": 3}
    out.sort(key=lambda a: (not a["original"], a["lang"], order.get(a["role"], 9)))
    return out


def auto_orig_keys(info: dict) -> list[str]:
    auto = info.get("automatic_captions") or {}
    keys = [k for k in auto if k.endswith("-orig")]
    ol = core.orig_lang(info)
    if not keys and ol and ol in auto:
        keys = [ol]
    return keys


def summarize(info: dict, url: str) -> dict:
    fmts = info.get("formats") or []
    vids = [f for f in fmts if f.get("vcodec") not in (None, "none") and f.get("height")
            and f.get("ext") != "mhtml"]
    auds = [f for f in fmts if _is_audio_only(f)]
    best_a = max(auds, key=lambda f: (int(f.get("language_preference") or -1),
                                      float(f.get("abr") or f.get("tbr") or 0)), default=None)
    a_size = _size(best_a or {})
    video_formats = []
    for f in sorted(vids, key=lambda f: (int(f.get("height") or 0), int(f.get("fps") or 0),
                                         float(f.get("tbr") or 0)), reverse=True):
        note = (f.get("format_note") or "")
        video_formats.append({
            "id": f["format_id"], "height": f.get("height"), "width": f.get("width"),
            "fps": int(f.get("fps") or 0), "vcodec": (f.get("vcodec") or "").split(".")[0],
            "hdr": (f.get("dynamic_range") or "SDR") != "SDR",
            "dr": f.get("dynamic_range") or "SDR",
            "premium": "premium" in note.lower(), "note": note,
            "mbps": round(float(f.get("tbr") or 0) / 1000, 1) or None,
            "size": (_size(f) + (0 if f.get("acodec") not in (None, "none") else a_size)) or None,
            "has_audio": f.get("acodec") not in (None, "none"),
            "proto": f.get("protocol"), "ext": f.get("ext"),
        })
    audio_formats = []
    for f in sorted(auds, key=lambda f: (int(f.get("language_preference") or -1),
                                         float(f.get("abr") or f.get("tbr") or 0)), reverse=True):
        note = f.get("format_note") or ""
        audio_formats.append({
            "id": f["format_id"], "lang": f.get("language"), "note": note,
            "acodec": (f.get("acodec") or "").split(".")[0], "abr": round(float(f.get("abr") or f.get("tbr") or 0)),
            "ch": f.get("audio_channels"), "size": _size(f) or None,
            "original": "original" in note.lower() or int(f.get("language_preference") or -1) >= 10,
            "role": audio_role(f),
            "drc": "drc" in (f.get("format_id") or "").lower() or "drc" in note.lower(),
        })
    groups = audio_groups(info)
    heights = sorted({v["height"] for v in video_formats}, reverse=True)
    manual = {k: v for k, v in (info.get("subtitles") or {}).items() if k != "live_chat"}
    ol = core.orig_lang(info)
    auto_orig = [k for k in auto_orig_keys(info) if k not in manual]
    sub_tracks = []
    for k in sorted(manual, key=lambda k: (base_lang(k) not in ("sr", "sh", "en", base_lang(ol)), k)):
        sub_tracks.append({"value": f"manual:{k}", "kind": "manual", "lang": k,
                           "label": f"Ručni CC — {track_name(k, manual[k])} ({k})"})
    for k in auto_orig:
        sub_tracks.append({"value": f"auto:{k}", "kind": "auto", "lang": k.replace("-orig", ""),
                           "label": f"Auto-generated — {lang_label(k.replace('-orig', ''))} ({k.replace('-orig', '')})"})
    live = info.get("live_status")
    return {
        "url": url, "id": info.get("id"), "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "duration": info.get("duration"), "upload_date": info.get("upload_date"),
        "thumbnail": info.get("thumbnail"), "live_status": live,
        "language": ol,
        "video_formats": video_formats,
        "audio_formats": audio_formats,
        "audio_groups": groups,
        "heights": heights,
        "audio_langs": sorted({a["lang"] for a in audio_formats if a["lang"]}),
        "premium_formats": any(v["premium"] for v in video_formats),
        "hdr": any(v["hdr"] for v in video_formats),
        "subs": {
            "manual": [{"key": k, "name": track_name(k, v)} for k, v in manual.items()],
            "auto_orig": auto_orig,
            "has_sr": any(base_lang(k) in ("sr", "sh") for k in manual),
        },
        "sub_tracks": sub_tracks,
        "archive": cat_short(catalog_index().get(info.get("id"))),
        "sub_note": ("Live u toku: YT titl postaje dostupan tek posle kraja streama — "
                     "ako ga ne bude, ide whisper." if live in ("is_live", "is_upcoming") else None),
    }


# ================================================================ arhiva (manifest)
def item_title(info: dict) -> str:
    # live naslov ima vreme probe na kraju; fulltitle je stabilan
    return info.get("fulltitle") or info.get("title") or "video"


def item_dir(info: dict, out_dir: str) -> Path:
    """<datum> - <naslov> [<id>]; dug naslov se skraćuje, ali [id] ostaje (inace dva videa sa istim pocetkom
    naslova dele folder). Folder po starom imenu (id odsecen) se i dalje koristi ako je u njemu isti video."""
    date = info.get("upload_date") or info.get("release_date") or time.strftime("%Y%m%d")
    base, vid = Path(out_dir).expanduser(), core.safe_name(str(info.get("id") or "video"), 40)
    new = base / f"{core.safe_name(f'{date} - {item_title(info)}', 90 - len(vid) - 3)} [{vid}]"
    old = base / core.safe_name(f"{date} - {item_title(info)} [{info.get('id')}]")
    if old != new and (load_manifest(old) or {}).get("id") == info.get("id"):
        return old
    return new


FOLDER_LOCKS: dict[str, threading.Lock] = {}


class folder_lock:
    """Jedan posao po stavci arhive u isto vreme (deljeni .yta/dl, raw, izlazi)."""
    def __init__(self, folder: Path, job: "Job"):
        self.key, self.job = str(Path(folder).resolve()), job

    def __enter__(self):
        with _lock:
            self.lk = FOLDER_LOCKS.setdefault(self.key, threading.Lock())
        said = False
        while not self.lk.acquire(timeout=0.5):
            if not said:
                self.job.say("čeka drugi posao na istoj stavci arhive…")
                said = True
            self.job.check()

    def __exit__(self, *a):
        self.lk.release()


def mdir(folder: Path) -> Path:
    return folder / ".yta"


def load_manifest(folder: Path) -> dict | None:
    p = mdir(folder) / "manifest.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return None


def save_manifest(folder: Path, m: dict):
    m["updated"] = time.time()
    p = mdir(folder) / "manifest.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(m, ensure_ascii=False, indent=1))
    tmp.replace(p)


def update_manifest(folder: Path, fn) -> dict:
    with MLOCK:
        m = load_manifest(folder) or {}
        fn(m)
        save_manifest(folder, m)
        return m


def init_manifest(folder: Path, info: dict, url: str) -> dict:
    mdir(folder).mkdir(parents=True, exist_ok=True)
    (folder / "subs").mkdir(exist_ok=True)
    (mdir(folder) / "info.json").write_text(json.dumps(info, ensure_ascii=False))

    def fn(m):
        m.setdefault("version", 1)
        m.setdefault("source", None)
        m.setdefault("video", None)
        m.setdefault("audio", [])
        m.setdefault("subs", [])
        m.setdefault("outputs", [])
        m.setdefault("playlists", [])
        m.update(url=url, id=info.get("id"), title=item_title(info),
                 channel=info.get("channel") or info.get("uploader"),
                 duration=info.get("duration"), thumbnail=info.get("thumbnail"),
                 upload_date=info.get("upload_date"), language=core.orig_lang(info),
                 live_status=info.get("live_status"))
    return update_manifest(folder, fn)


def item_stem(m: dict) -> str:
    return f"{core.safe_name(m.get('title') or 'video')} [{m.get('id')}]"


def item_info(folder: Path) -> dict:
    p = mdir(folder) / "info.json"
    return json.loads(p.read_text()) if p.exists() else {}


def upsert_sub(folder: Path, entry: dict):
    def fn(m):
        m["subs"] = [s for s in m.get("subs", []) if s["key"] != entry["key"]] + [entry]
    update_manifest(folder, fn)


def add_output(folder: Path, name: str):
    def fn(m):
        if name not in m.setdefault("outputs", []):
            m["outputs"].append(name)
    update_manifest(folder, fn)


def lib_roots() -> list[Path]:
    roots = [Path(SETTINGS["out_dir"]).expanduser()]
    try:
        roots += [Path(p) for p in json.loads(ROOTS_P.read_text())]
    except Exception:  # noqa: BLE001
        pass
    out, seen = [], set()
    for r in roots:
        r = r.expanduser().resolve()
        if str(r) not in seen:
            seen.add(str(r))
            out.append(r)
    return out


def remember_root(out_dir: str):
    r = str(Path(out_dir).expanduser().resolve())
    with _lock:
        try:
            roots = json.loads(ROOTS_P.read_text())
        except Exception:  # noqa: BLE001
            roots = []
        if r not in roots:
            roots.append(r)
            ROOTS_P.write_text(json.dumps(roots, ensure_ascii=False, indent=1))


def under_roots(p: Path) -> bool:
    p = p.expanduser().resolve()
    return any(p == r or r in p.parents for r in lib_roots())


def library() -> list[dict]:
    items = []
    for r in lib_roots():
        if not r.is_dir():
            continue
        for d in r.iterdir():
            if not d.is_dir():
                continue
            m = load_manifest(d)
            if not m:
                continue
            src = d / m["source"] if m.get("source") else None
            outs = [o for o in m.get("outputs", []) if (d / o).exists()]
            size = (src.stat().st_size if src and src.exists() else 0) + sum((d / o).stat().st_size for o in outs)
            subs = [s for s in m.get("subs", []) if (d / s["file"]).exists()]
            items.append({
                **tr_summary(d, subs),  # tr_state / translated / transcripts
                "sr": sorted({sr_target(s) for s in subs} - {None}),  # postojeci prevodi: lat / cyr
                "tr": sorted({s.get("lang") for s in subs if s.get("kind") == "tr"} - {None}),  # npr. el
                "sr_cc": any(is_sr_cc(s) for s in subs),            # rucni srpski CC sa YouTube-a
                "has_video": item_has_video(d, m),
                "has_audio": not ("audio" in m and not m.get("audio")),  # kao lib_transcribe: [] = izvor bez zvuka
                "kind": ("audio" if m.get("kind") == "audio" else "video") if src and src.exists() else "none",
                "size": size, "playlists": m.get("playlists", []),
                "thumb_local": (d / ".yta" / "thumb.jpg").exists(),
                "folder": str(d), "title": m.get("title"), "id": m.get("id"), "url": m.get("url"),
                "thumbnail": m.get("thumbnail"), "channel": m.get("channel"),
                "duration": m.get("duration"), "upload_date": m.get("upload_date"),
                "language": m.get("language"),
                "source": m.get("source") if src and src.exists() else None,
                "source_size": src.stat().st_size if src and src.exists() else 0,
                "video": m.get("video"), "audio": [with_role(a) for a in m.get("audio") or []],
                "subs": subs,
                "outputs": outs,
                "updated": m.get("updated", 0),
            })
    items.sort(key=lambda i: i["updated"], reverse=True)
    return items


def sub_date(folder: Path, s: dict) -> str | None:
    """Datum titla (YYYY-MM-DD): upisan pri prevodu, inace vreme fajla."""
    if s.get("date"):
        return str(s["date"])[:10]
    try:
        return time.strftime("%Y-%m-%d", time.localtime((folder / s["file"]).stat().st_mtime))
    except OSError:
        return None


def tr_summary(folder: Path, subs: list[dict]) -> dict:
    """Stanje stavke iz manifest subs (fajlovi postoje; bez ffprobe):
    translated = bar jedan prevod (sr/tr) ili srpski rucni CC; transcript = titl/transkript bez prevoda; none."""
    translated, transcripts = [], []
    for s in subs:
        k = s.get("kind")
        if k in ("sr", "tr"):
            eng, mdl = key_engine_model(s)
            translated.append({"target": (sr_target(s) or "lat") if k == "sr" else s.get("lang"),
                               "engine": eng, "model": mdl, "date": sub_date(folder, s),
                               "lektura": is_lektura(s), "file": s.get("file"), "name": s.get("name")})
        elif k in ("manual", "auto", "whisper"):
            transcripts.append({"kind": k, "lang": s.get("lang"), "file": s.get("file")})
    translated.sort(key=lambda t: t["date"] or "", reverse=True)  # najnoviji prvi
    state = ("translated" if translated or any(is_sr_cc(s) for s in subs) else
             "transcript" if transcripts else "none")
    return {"tr_state": state, "translated": translated, "transcripts": transcripts}


def with_role(a: dict) -> dict:
    """Stari manifesti nemaju audio[].role -> izvedi iz variant/original (bez upisa)."""
    if not isinstance(a, dict) or a.get("role"):
        return a
    v = a.get("variant") or ""
    return {**a, "role": "original" if a.get("original") or v == "original" else "dub" if v == "dub" else "default"}


def item_source(folder: Path, m: dict) -> Path | None:
    src = folder / m["source"] if m.get("source") else None
    return src if src and src.is_file() else None


def item_has_video(folder: Path, m: dict) -> bool:
    # stari manifesti nemaju "video" kljuc -> izvor se racuna kao video
    return bool(item_source(folder, m)) and m.get("kind") != "audio" and \
        (m.get("video") is not None or "video" not in m)


def sr_target(s: dict) -> str | None:
    """Pismo prevoda iz kljuca 'sr:<lat|cyr>:<model>:...'."""
    p = (s.get("key") or "").split(":")
    return p[1] if s.get("kind") == "sr" and len(p) > 1 and p[1] in ("lat", "cyr") else None


def is_sr_cc(s: dict) -> bool:
    return s.get("kind") == "manual" and base_lang(s.get("lang")) in ("sr", "sh")


def sr_files(folder: Path, target: str, m: dict | None = None) -> list[Path]:
    """Postojeci prevodi za `target` (lat/cyr: subs/sr-<target>*.srt; el: kind tr), najnoviji prvi."""
    m = m if m is not None else (load_manifest(folder) or {})
    if target not in ("lat", "cyr"):
        fs = {folder / s["file"] for s in m.get("subs", []) if s.get("kind") == "tr" and s.get("lang") == target}
        return sorted((p for p in fs if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
    fs = {folder / s["file"] for s in m.get("subs", []) if sr_target(s) == target}
    fs |= set((folder / "subs").glob(f"sr-{target}*.srt"))
    return sorted((p for p in fs if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)


# ================================================================ jobs
JOBS: dict[str, "Job"] = {}
STAGES = ["download", "subs", "translate", "output"]


def normalize_opts(o: dict) -> dict:
    """Stari format (ravne opcije) -> {dl, proc}."""
    if "dl" in o:
        return o
    dl = {"video_fmt": o.get("video_fmt") or "auto:best", "audio_fmt": o.get("audio_fmt") or "auto",
          "subs": "auto", "out_dir": o.get("out_dir")}
    proc = {k: o[k] for k in ("sub_source", "target", "engine", "model", "review", "output", "encoder",
                              "vq", "scale", "audio_enc") if k in o}
    proc["keep_source"] = bool(o.get("keep_original", True))
    return {"dl": dl, "proc": proc, **{k: v for k, v in o.items() if k.startswith("_") or k == "after"}}


class Job:
    def __init__(self, url: str, opts: dict, jid: str | None = None, kind: str = "download",
                 folder: str | None = None):
        self.id = jid or uuid.uuid4().hex[:10]
        self.kind = kind
        self.url, self.opts = url, (normalize_opts(opts) if kind == "download" else opts)
        self.title = opts.get("_title") or url
        self.thumb = opts.get("_thumb")
        self.vid = opts.get("_id")
        self.live = opts.get("_live")
        self.state = "queued"
        self.error = None
        self.folder: str | None = folder
        self.outputs: list[str] = []
        self.created = time.time()
        self.finished = None
        self.stages = {s: {"status": "pending", "pct": None, "info": ""} for s in STAGES}
        self.log = deque(maxlen=400)
        self.procs: set[subprocess.Popen] = set()
        self.cancel = threading.Event()
        self.stop_live = threading.Event()
        self.dl_thread: threading.Thread | None = None
        self.main_file: str | None = None
        self.after_jobs: list[str] = []  # poslovi pokrenuti posle preuzimanja (opts.after)
        self.after_skipped: list[dict] = []  # preskoceni koraci posle preuzimanja [{step, label, reason}]

    def busy(self) -> bool:
        return self.state in ("running", "queued") or bool(self.dl_thread and self.dl_thread.is_alive())

    def say(self, msg: str):
        self.log.append(time.strftime("%H:%M:%S ") + msg)

    def set_stage(self, name: str, status: str, pct=None, info=None):
        st = self.stages[name]
        st["status"] = status
        if pct is not None or status in ("done", "skipped"):
            st["pct"] = 100 if status == "done" else pct
        if info is not None:
            st["info"] = info
        persist_soon()

    def check(self):
        if self.cancel.is_set():
            raise Cancelled()

    def to_dict(self, log=False) -> dict:
        d = {k: getattr(self, k) for k in ("id", "kind", "url", "title", "thumb", "vid", "live", "state",
                                           "error", "folder", "outputs", "created", "finished",
                                           "stages", "opts", "after_jobs", "after_skipped")}
        if log:
            d["log"] = list(self.log)
        else:
            d["log_tail"] = self.log[-1] if self.log else ""
        return d

    def run(self, cmd: list[str], on_line=None, cwd=None) -> int:
        self.check()
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             bufsize=1, cwd=cwd, errors="replace", start_new_session=True)
        self.procs.add(p)
        try:
            for line in p.stdout:
                line = line.rstrip("\r\n")
                if not line:
                    continue
                if on_line and on_line(line):
                    continue
                self.say(line[:300])
            return p.wait()
        finally:
            self.procs.discard(p)
            self.check()

    def signal_all(self, sig):
        for p in list(self.procs):
            try:
                os.killpg(p.pid, sig)
            except Exception:  # noqa: BLE001
                pass


_persist_flag = threading.Event()


def persist_soon():
    _persist_flag.set()


def _persist_loop():
    while True:
        _persist_flag.wait()
        time.sleep(1.0)
        _persist_flag.clear()
        try:
            with _lock:
                data = [j.to_dict(log=True) for j in JOBS.values()]
            JOBS_P.write_text(json.dumps(data, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            pass


def load_jobs():
    if not JOBS_P.exists():
        return
    try:
        for d in json.loads(JOBS_P.read_text()):
            j = Job(d["url"], d.get("opts") or {}, d["id"], d.get("kind") or "download", d.get("folder"))
            for k in ("title", "thumb", "vid", "live", "state", "error", "folder", "outputs",
                      "created", "finished", "stages", "after_jobs", "after_skipped"):
                if k in d:
                    setattr(j, k, d[k])
            j.log.extend(d.get("log") or [])
            if j.state in ("queued", "running"):
                j.state, j.error = "error", "prekinuto (panel restartovan) — klikni Ponovi"
            JOBS[j.id] = j
    except Exception:  # noqa: BLE001
        traceback.print_exc()


# ================================================================ preuzimanje
def build_selector(dl: dict, info: dict) -> tuple[str, bool]:
    """yt-dlp -f selektor + da li treba --audio-multistreams."""
    v = dl.get("video_fmt") or "auto:best"
    a = dl.get("audio_fmt") or "auto"
    fmts = {f.get("format_id"): f for f in info.get("formats") or []}
    if v.startswith("id:"):
        vsel = v[3:]
        if fmts.get(vsel, {}).get("acodec") not in (None, "none"):  # kombinovani format vec ima zvuk
            if a == "auto":
                return f"{vsel}/bv*+ba/b", False
            extra = ([g["id"] for g in audio_groups(info)] if a == "all" else [a[3:]] if a.startswith("id:") else [])
            if extra:
                return f"{vsel}+{'+'.join(extra)}/{vsel}/bv*+ba/b", True
            return f"{vsel}/bv*+ba/b", False
        vfb = "bv*+ba/b"
    else:
        h = v.split(":", 1)[1] if ":" in v else "best"
        c = "" if h == "best" else f"[height<={int(h)}]"
        vsel, vfb = f"bv*{c}", f"bv*{c}+ba/b{c}/bv*+ba/b"
    if a == "all":
        ids = [g["id"] for g in audio_groups(info)]
        if len(ids) > 1:
            return f"{vsel}+{'+'.join(ids)}/{vsel}+ba/{vfb}", True
        a = "auto"
    aa = a[3:] if a.startswith("id:") else "ba"
    return f"{vsel}+{aa}/{vsel}+ba/{vfb}", False


AUDIO_EXTS = ("opus", "m4a", "mp3", "flac", "ogg", "mka", "mkv", "webm", "aac", "wav")


def find_audio(work: Path) -> Path | None:
    return next((work / f"audio.{e}" for e in AUDIO_EXTS if (work / f"audio.{e}").exists()), None)


def find_video(work: Path) -> Path | None:
    # samo zavrsen fajl; video.temp.mkv (prekinut merge) i video.fNNN.* nisu izvor
    return next((work / n for n in ("video.mkv", "video.mp4", "video.webm") if (work / n).exists()), None)


def ffprobe_bin(ffmpeg: str) -> str:
    return str(Path(ffmpeg).with_name("ffprobe"))


def salvage(job: "Job", work: Path, ffmpeg: str) -> Path | None:
    """Posle prekida live snimanja spoji delimicne video/audio fajlove."""
    parts = sorted((p for p in work.glob("video.f*")
                    if not p.name.endswith((".ytdl", ".temp")) and "-Frag" not in p.name),
                   key=lambda p: p.stat().st_size, reverse=True)
    if not parts:
        return None
    vids, auds = [], []
    for p in parts:
        r = subprocess.run([ffprobe_bin(ffmpeg), "-v", "error", "-show_entries", "stream=codec_type",
                            "-of", "csv=p=0", str(p)], capture_output=True, text=True)
        (vids if "video" in r.stdout else auds if "audio" in r.stdout else []).append(p)
    if not vids:
        return None
    out = work / "video.mkv"
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(vids[0])]
    maps = ["-map", "0:v:0"]
    for i, a in enumerate(auds, 1):
        cmd += ["-i", str(a)]
        maps += ["-map", f"{i}:a:0"]
    cmd += maps + ["-c", "copy", str(out)]
    job.say("Spajam delimično snimljen live (salvage)…")
    subprocess.run(cmd)
    return out if out.exists() and out.stat().st_size > 0 else None


def probe_audio_streams(ffmpeg: str, path: Path) -> list[dict]:
    p = subprocess.run([ffprobe_bin(ffmpeg), "-v", "error", "-select_streams", "a", "-show_entries",
                        "stream=index,codec_name,channels,bit_rate:stream_tags=language,title",
                        "-of", "json", str(path)], capture_output=True, text=True)
    return (json.loads(p.stdout or "{}").get("streams")) or []


def probe_video(ffmpeg: str, path: Path) -> dict:
    p = subprocess.run([ffprobe_bin(ffmpeg), "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=codec_name,width,height,pix_fmt,color_primaries,color_transfer,color_space"
                        ":format=duration", "-of", "json", str(path)], capture_output=True, text=True)
    d = json.loads(p.stdout or "{}")
    st = (d.get("streams") or [{}])[0]
    st["duration"] = float((d.get("format") or {}).get("duration") or 0)
    return st


def finalize_source(job: "Job", folder: Path, work: Path, v: Path, info: dict, ffmpeg: str,
                    audio_only: bool = False) -> Path:
    """Upisi naziv/jezik svake audio trake, premesti izvor u stavku arhive, azuriraj manifest."""
    m = load_manifest(folder) or {}
    dst = uniq_path(folder / f"{item_stem(m)}.{'audio' if audio_only else 'source'}{v.suffix}")
    thumb = next((work / f"thumb.{e}" for e in ("jpg", "webp", "png") if (work / f"thumb.{e}").exists()), None)
    if thumb:  # lokalna slicica za katalog (radi i offline)
        shutil.move(str(thumb), mdir(folder) / "thumb.jpg")
    fmts = {f.get("format_id"): f for f in info.get("formats") or []}
    fid_p = mdir(folder) / "format_id.txt"
    fids = fid_p.read_text().strip().splitlines()[-1].split("+") if fid_p.exists() and fid_p.read_text().strip() else []
    a_ids = [i for i in fids if fmts.get(i, {}).get("acodec") not in (None, "none")]
    streams = probe_audio_streams(ffmpeg, v)
    audio = []
    for n, st in enumerate(streams):
        f = fmts.get(a_ids[n], {}) if len(a_ids) == len(streams) else {}
        tags = st.get("tags") or {}
        # jezik iz YouTube formata je pouzdan; oznaka u fajlu (yt-dlp merge) cesto je ista za sve trake
        lang = base_lang(f.get("language") or tags.get("language") or "und")
        raw_note = f.get("format_note") or ""
        note = raw_note.lower()
        orig = ("original" in note or int(f.get("language_preference") or -1) >= 10
                or len(streams) == 1)
        var = "original" if orig else ("dub" if "dub" in note else "")
        st_abr = round(int(st.get("bit_rate") or 0) / 1000) or None
        # posle konverzije (samo audio -x) stvarni bitrate je u fajlu, ne u YouTube formatu
        abr = (st_abr or round(float(f.get("abr") or 0)) or None) if audio_only else \
            (round(float(f.get("abr") or 0)) or st_abr)
        yt_name = re.sub(r"\s*(original|\(default\)|dubbed.*)", "", raw_note.split(",")[0], flags=re.I).strip()
        name = "Audio" if lang == "und" and len(streams) == 1 else (LANG_NAMES.get(lang) or yt_name or lang)
        role = audio_role(f, orig)
        if len(streams) == 1 and role != "descriptive":
            role = "original"
        # opisna traka (descriptive) se u plejeru razlikuje od obicne iste jezicke
        title = f"{name} — audio opis" if role == "descriptive" and len(streams) > 1 else \
            f"{name}{' — ' + var if var else ''}"
        audio.append({"index": n, "lang": lang, "original": orig, "variant": var, "role": role,
                      "codec": st.get("codec_name"), "channels": st.get("channels"), "abr": abr,
                      "format_id": f.get("format_id"), "title": title,
                      "label": f"{title} · {st.get('codec_name')}{f' {abr}k' if abr else ''}"})
    if audio and not any(a["original"] for a in audio):
        audio[0]["original"] = True
        if audio[0]["role"] != "descriptive":
            audio[0]["role"] = "original"
    if len(streams) > 1:  # upisi nazive traka (plejeri ih prikazuju pri izboru zvuka)
        job.set_stage("download", "running", 99, f"upisujem nazive {len(streams)} audio traka…")
        try:  # omot ostaje prilog (sa '-map 0' bi postao druga video traka)
            maps, attach = copy_maps(ffmpeg, v, work / "omot")
        except Exception as e:  # noqa: BLE001
            job.say(f"⚠ omot nije izdvojen ({e}) — remux sa -map 0")
            maps, attach = ["-map", "0"], []
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(v), *maps, *attach, "-c", "copy"]
        for a in audio:
            cmd += [f"-metadata:s:a:{a['index']}", f"title={a['title']}",
                    f"-metadata:s:a:{a['index']}", f"language={iso3(a['lang'])}"]
        cmd.append(str(dst))
        if job.run(cmd) != 0 or not dst.exists():
            raise RuntimeError("upis naziva audio traka nije uspeo")
        v.unlink(missing_ok=True)
    else:
        shutil.move(str(v), dst)
    vs = probe_video(ffmpeg, dst)
    vfid = next((i for i in fids if fmts.get(i, {}).get("vcodec") not in (None, "none")), None)
    vf = fmts.get(vfid, {}) if vfid else {}

    def fn(mm):
        mm["source"] = dst.name
        mm["kind"] = "audio" if audio_only else "video"
        mm["video"] = None if audio_only or not vs.get("width") else {"format_id": vfid, "width": vs.get("width"), "height": vs.get("height"),
                       "codec": vs.get("codec_name"), "fps": vf.get("fps"),
                       "hdr": (vf.get("dynamic_range") or "SDR") != "SDR",
                       "premium": "premium" in (vf.get("format_note") or "").lower(),
                       "duration": vs.get("duration")}
        mm["audio"] = audio
        mm["source_subs"] = []  # nov izvor: titlovi jos nisu ubaceni (embed_source_subs)
    update_manifest(folder, fn)
    shutil.rmtree(work, ignore_errors=True)
    return dst


PROG_RE = re.compile(r"^\[P\] (.*)$")


AUDIO_CONV = {  # dl.audio_conv -> yt-dlp -x argumenti
    "best": ["--audio-format", "best"],                          # original bez rekodiranja (opus/m4a)
    "mp3": ["--audio-format", "mp3", "--audio-quality", "320K"],
    "mp3v0": ["--audio-format", "mp3", "--audio-quality", "0"],
    "m4a": ["--audio-format", "m4a", "--audio-quality", "256K"],
    "opus": ["--audio-format", "opus", "--audio-quality", "160K"],
    "flac": ["--audio-format", "flac"],
}


def do_download(job: "Job", info: dict, folder: Path, dl: dict, ffmpeg: str) -> Path:
    audio_only = (dl.get("video_fmt") or "") == "audio"
    m = load_manifest(folder) or {}
    have = m.get("source") and (folder / m["source"]).exists()
    same_kind = (m.get("kind") or "video") == ("audio" if audio_only else "video")
    if have and same_kind and not dl.get("force"):
        job.set_stage("download", "done", info=f"već u arhivi: {m['source']}")
        return folder / m["source"]
    work = mdir(folder) / ("dl-audio" if audio_only else "dl")
    work.mkdir(parents=True, exist_ok=True)
    v = find_audio(work) if audio_only else find_video(work)
    if v:  # preuzeto ranije, a nije finalizovano
        return finalize_source(job, folder, work, v, info, ffmpeg, audio_only)
    with slot("download", job):
        job.set_stage("download", "running", 0, "pokrećem yt-dlp…")
        for tmp in work.glob("video.temp.*"):  # prekinut merge -> yt-dlp ga ponovi iz fNNN fajlova
            tmp.unlink(missing_ok=True)
        for tmp in work.glob("audio.temp.*"):
            tmp.unlink(missing_ok=True)
        live = info.get("live_status")
        if audio_only:
            a = dl.get("audio_fmt") or "auto"
            ids = [g["id"] for g in audio_groups(info)] if a == "all" else []
            if len(ids) > 1:  # vise audio traka -> jedan mkv (bez konverzije)
                fmt, multi, xargs = "+".join(ids) + "/ba/b", True, []
            else:
                fmt, multi = (a[3:] if a.startswith("id:") else "ba") + "/ba/b", False
                xargs = ["-x", *AUDIO_CONV.get(dl.get("audio_conv") or "best", AUDIO_CONV["best"])]
        else:
            fmt, multi = build_selector(dl, info)
            xargs = ["--embed-chapters"]
        tmpl = ("download:[P] %(progress.status)s|%(progress._percent_str)s|%(progress._speed_str)s|"
                "%(progress._eta_str)s|%(progress.downloaded_bytes)s|%(progress.fragment_index)s|"
                "%(progress.fragment_count)s|%(info.format_id)s|%(info.height)s|%(info.acodec)s|"
                "%(info.vcodec)s|%(info.language)s")
        base = "audio" if audio_only else "video"
        cmd = ytdlp_cmd() + ["--no-playlist", "-f", fmt, "--merge-output-format", "mkv",
                             "-N", str(SETTINGS["fragments"]), "--newline", "--progress-template", tmpl,
                             "--print-to-file", "after_move:%(format_id)s", str(mdir(folder) / "format_id.txt"),
                             "--embed-metadata", "--embed-thumbnail", "--write-thumbnail", "--convert-thumbnails", "jpg",
                             "-o", f"thumbnail:{work / 'thumb.%(ext)s'}", *xargs,
                             "-o", str(work / f"{base}.%(ext)s")]
        if multi:
            cmd.append("--audio-multistreams")
        if live == "is_live":
            cmd += ["--live-from-start"]
        elif live == "is_upcoming":
            cmd += ["--wait-for-video", "30", "--live-from-start"]
        cmd.append(job.url)
        job.say(f"format: {fmt}")
        streams: list[str] = []

        def on_line(line: str) -> bool:
            mm = PROG_RE.match(line)
            if not mm:
                if line.startswith("[Merger]"):
                    job.set_stage("download", "running", 99, "spajam video + audio trake…")
                return False
            f = [x.strip() for x in mm.group(1).split("|")]
            status, pct_s, speed, eta, dlb, fi, fc, fid, h, ac, vc, lg = (f + [""] * 12)[:12]
            if job.live == "is_upcoming":  # stream je poceo
                job.live = "is_live"
            if fid not in streams:
                streams.append(fid)
            kind = "video" if vc not in ("none", "NA", "") else "audio"
            what = (f"{h}p" if kind == "video" and h not in ("NA", "") else
                    (lg if kind == "audio" and lg not in ("NA", "") else ""))
            label = f"{kind} {what} ({fid})".replace("  ", " ")
            try:
                pct = float(pct_s.replace("%", ""))
            except ValueError:
                pct = None
            if live in ("is_live", "is_upcoming") or pct is None:
                mb = int(dlb) / 1048576 if dlb.isdigit() else 0
                frag = f" · frag {fi}" + (f"/{fc}" if fc not in ("NA", "") else "") if fi not in ("NA", "") else ""
                job.set_stage("download", "running", None, f"● snimam {label}: {mb:,.0f} MB · {speed}{frag}")
            else:
                job.set_stage("download", "running", pct,
                              f"{label} [{len(streams)}]: {pct:.1f}% · {speed} · ETA {eta}")
            return True

        rc = job.run(cmd, on_line)
        v = find_audio(work) if audio_only else find_video(work)
        if not v and not audio_only and (job.stop_live.is_set() or live in ("is_live", "is_upcoming")):
            v = salvage(job, work, ffmpeg)  # samo live: spoji ono sto je snimljeno
        if not v:
            raise RuntimeError(f"preuzimanje nije uspelo (yt-dlp rc={rc}) — Ponovi nastavlja gde je stalo; vidi log")
        dst = finalize_source(job, folder, work, v, info, ffmpeg, audio_only)
        m = load_manifest(folder) or {}
        na = len(m.get("audio") or [])
        vh = (m.get("video") or {}).get("height")
        job.set_stage("download", "done",
                      info=f"{'samo audio ' + dst.suffix[1:] if audio_only else f'{vh}p'} · {na} audio "
                           f"{'traka' if na == 1 else 'trake'} · {dst.stat().st_size / 1048576:,.0f} MB")
        return dst


# ================================================================ titlovi
def sub_keys_for(dl_subs: str, info: dict, proc: dict | None) -> list[str]:
    manual = [k for k in (info.get("subtitles") or {}) if k != "live_chat"]
    auto = info.get("automatic_captions") or {}
    keys: list[str] = []
    if dl_subs in ("all", "all+tr"):
        keys = [f"manual:{k}" for k in manual] + [f"auto:{k}" for k in auto_orig_keys(info) if k not in manual]
        if dl_subs == "all+tr":  # + YouTube automatski prevodi na sve jezike (mnogo fajlova, YouTube ume 429)
            # "xx" pored "xx-orig" je isti ASR (i isti fajl subs/xx.auto.srt) -> preskoci
            keys += [f"auto:{k}" for k in auto if k not in manual and f"{k}-orig" not in auto]
    elif dl_subs == "auto":
        tr = core.pick_track(info, None)
        if tr:
            keys = [f"{tr[0]}:{tr[1]}"]
    elif dl_subs.startswith(("manual:", "auto:")):
        keys = [dl_subs]
    if proc:  # obrada trazi odredjeni titl -> obavezno ga preuzmi
        s = proc.get("sub_source") or "auto-best"
        if s.startswith("manual:") and s[7:] in manual and s not in keys:
            keys.append(s)
        elif s.startswith("auto:") and s[5:] in auto and s not in keys:
            keys.append(s)
        elif s == "auto-best" and not keys:
            tr = core.pick_track(info, None)
            if tr:
                keys.append(f"{tr[0]}:{tr[1]}")
    return list(dict.fromkeys(keys))


def sub_file_for(kind: str, lang: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_-]+", "_", lang)
    return f"subs/{clean}.srt" if kind == "manual" else f"subs/{clean.replace('-orig', '')}.auto.srt"


def fetch_subs(job: "Job", folder: Path, keys: list[str], info: dict) -> list[str]:
    """Preuzmi titlove (jedan yt-dlp poziv), konvertuj u srt, upisi u manifest. Vraca preuzete kljuceve."""
    m = load_manifest(folder) or {}
    have = {s["key"] for s in m.get("subs", []) if (folder / s["file"]).exists()}
    todo = [k for k in keys if k not in have]
    if not todo:
        return []
    raw = mdir(folder) / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for f in raw.glob("s.*"):
        f.unlink()
    man = [k.split(":", 1)[1] for k in todo if k.startswith("manual:")]
    aut = [k.split(":", 1)[1] for k in todo if k.startswith("auto:")]
    job.set_stage("subs", "running", None, f"preuzimam {len(todo)} {'titl' if len(todo) == 1 else 'titlova'}…")
    cmd = ytdlp_cmd() + ["--no-playlist", "--skip-download", "--sub-format", "json3",
                         "--sub-langs", ",".join(man + aut), "-o", str(raw / "s.%(ext)s")]
    if man:
        cmd.append("--write-subs")
    if aut:
        cmd.append("--write-auto-subs")
    if len(todo) > 6:
        cmd += ["--sleep-subtitles", "1"]
    cmd.append(job.url)
    job.run(cmd)
    names = {k: track_name(k, v) for k, v in (info.get("subtitles") or {}).items()}
    got = []
    for k in todo:
        kind, lang = k.split(":", 1)
        f = raw / f"s.{lang}.json3"
        if not f.exists():
            job.say(f"titl {k} nije preuzet")
            continue
        cues = core.cues_from_json3(f, kind)
        if not cues:
            continue
        rel = sub_file_for(kind, lang)
        core.write_srt(cues, folder / rel)
        clean = lang.replace("-orig", "")
        upsert_sub(folder, {"key": k, "kind": kind, "lang": clean, "file": rel, "count": len(cues),
                            "name": (names.get(lang) if kind == "manual" else f"{lang_label(clean)} (auto)")})
        got.append(k)
    return got


def embed_candidates(folder: Path, m: dict) -> list[dict]:
    """Titlovi za MKV izvor: rucni CC + automatski na originalnom jeziku (YT automatski prevodi ostaju u subs/)."""
    ol = base_lang(m.get("language"))
    out = []
    for s in m.get("subs") or []:
        if s.get("kind") not in ("manual", "auto") or not s.get("file") or not (folder / s["file"]).is_file():
            continue
        if s["kind"] == "auto" and not (str(s.get("key")).endswith("-orig") or base_lang(s.get("lang")) == ol):
            continue
        out.append(s)
    return out


def embed_title(s: dict) -> str:
    """"English (CC)" / "English (auto)"; regionalna varijanta dobija kod (npr. "English en-GB (CC)")."""
    lg = s.get("lang") or "und"
    name = LANG_NAMES.get(base_lang(lg)) if base_lang(lg) != "und" else None
    name = name or (s.get("name") if s.get("kind") == "manual" else None) or lg
    if lg.lower() != base_lang(lg) and lg.split("-")[0].lower() == base_lang(lg):
        name += f" {lg}"
    return f"{name} ({'CC' if s.get('kind') == 'manual' else 'auto'})"


def sub_lang3(s: dict) -> str:
    l3 = iso3(s.get("lang"))
    return l3 if len(l3) == 3 else "und"


def probe_streams(ffmpeg: str, path: Path) -> list[dict]:
    """Sve trake (ffprobe): index, codec_type, codec_name, disposition attached_pic, tagovi."""
    p = subprocess.run([ffprobe_bin(ffmpeg), "-v", "error", "-show_entries",
                        "stream=index,codec_type,codec_name:stream_disposition=attached_pic"
                        ":stream_tags=language,title,filename,mimetype", "-of", "json", str(path)],
                       capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe ne čita {path.name}")
    return json.loads(p.stdout or "{}").get("streams") or []


def stream_list(ffmpeg: str, path: Path) -> list[tuple]:
    """[(codec_type, language, title, attached_pic)] svih traka (ffprobe); omot = video sa attached_pic 1."""
    return [(st.get("codec_type"), (st.get("tags") or {}).get("language"), (st.get("tags") or {}).get("title"),
             int((st.get("disposition") or {}).get("attached_pic") or 0)) for st in probe_streams(ffmpeg, path)]


PIC_EXT = {"mjpeg": ("jpg", "image/jpeg"), "png": ("png", "image/png"), "webp": ("webp", "image/webp"),
           "bmp": ("bmp", "image/bmp"), "gif": ("gif", "image/gif")}


def copy_maps(ffmpeg: str, src: Path, work: Path) -> tuple[list[str], list[str]]:
    """Mapiranje za remux -c copy koji cuva MKV omot: ffmpeg omot (prilog-slika) cita kao video sa attached_pic,
    a '-map 0' bi ga upisao kao obicnu drugu video traku. Zato 0:V/a/s/t, a omot se izdvoji i vrati kao prilog.
    Vraca (mapiranja ulaza 0, izlazni -attach argumenti); van MKV-a ostaje '-map 0' (mp4 cuva omot sam)."""
    if src.suffix.lower() not in (".mkv", ".mka"):
        return ["-map", "0"], []
    streams = probe_streams(ffmpeg, src)
    n_att = sum(1 for st in streams if st.get("codec_type") == "attachment")  # postojeci prilozi (npr. fontovi)
    att: list[str] = []
    pics = [st for st in streams if (st.get("disposition") or {}).get("attached_pic")]
    for j, st in enumerate(pics):
        tags = {k.lower(): v for k, v in (st.get("tags") or {}).items()}
        ext, mime = PIC_EXT.get(st.get("codec_name") or "", ("img", "application/octet-stream"))
        work.mkdir(parents=True, exist_ok=True)
        k = n_att + j  # -attach prilozi dolaze posle mapiranih (0:t)
        pic = work / f"cover{j}.{ext}"
        p = subprocess.run([ffmpeg, "-hide_banner", "-v", "error", "-nostdin", "-y", "-i", str(src),
                            "-map", f"0:{st['index']}", "-c", "copy", "-frames:v", "1", "-update", "1",
                            "-f", "image2", str(pic)], capture_output=True, text=True, timeout=300)
        if p.returncode != 0 or not pic.is_file() or not pic.stat().st_size:
            raise RuntimeError(f"omot iz {src.name} nije izdvojen: {(p.stderr or '').strip()[-200:]}")
        att += ["-attach", str(pic), f"-metadata:s:t:{k}", f"mimetype={tags.get('mimetype') or mime}",
                f"-metadata:s:t:{k}", f"filename={tags.get('filename') or 'cover.' + ext}"]
    return ["-map", "0:V?", "-map", "0:a?", "-map", "0:s?", "-map", "0:t?"], att


def embed_source_subs(job: "Job", folder: Path, ffmpeg: str) -> int:
    """Titlovi iz subs/ i kao trake u MKV izvoru (ffmpeg -c copy, bez rekodiranja; omot, poglavlja, metapodaci
    i postojece trake ostaju). Ubacuju se samo titlovi kojih nema u manifest source_subs (bez dupliranja).
    Privremeni fajl u .yta/work pa os.replace; neuspeh = upozorenje u logu, izvor netaknut. Vraca broj ubacenih."""
    if not as_bool(SETTINGS.get("embed_subs", True)):
        return 0
    m = load_manifest(folder) or {}
    src = item_source(folder, m)
    if not src or src.suffix.lower() != ".mkv":  # npr. samo audio m4a/mp3 -> preskoci
        return 0
    have = {x.get("key") for x in m.get("source_subs") or []}
    new = [x for x in embed_candidates(folder, m) if x.get("key") not in have]
    if not new:
        return 0
    work = mdir(folder) / "work" / f"{job.id}-embed"
    tmp = work / src.name
    prev = dict(job.stages["subs"])
    ok = False

    def record(done: list[dict]):
        got = [{"key": x["key"], "lang": x.get("lang"), "kind": x["kind"]} for x in done]

        def fn(mm):
            keys = {g["key"] for g in got}
            mm["source_subs"] = [x for x in mm.get("source_subs") or [] if x.get("key") not in keys] + got
        update_manifest(folder, fn)
    try:
        before = stream_list(ffmpeg, src)
        # traka sa istim jezikom i nazivom je vec u fajlu (npr. manifest obnovljen bez source_subs) -> ne dupliraj
        inside = {(lg, t) for typ, lg, t, _ in before if typ == "subtitle"}
        known = [x for x in new if (sub_lang3(x), embed_title(x)) in inside]
        new = [x for x in new if x not in known]
        if known:
            record(known)
        if not new:
            return 0
        work.mkdir(parents=True, exist_ok=True)
        n0 = len([1 for x in before if x[0] == "subtitle"])
        maps, attach = copy_maps(ffmpeg, src, work)  # omot ostaje prilog (ne druga video traka)
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(src)]
        for x in new:
            cmd += ["-i", str(folder / x["file"])]
        cmd += [*maps, *sum((["-map", f"{n + 1}:0"] for n in range(len(new))), []), *attach,
                "-map_metadata", "0", "-map_chapters", "0", "-c", "copy",
                "-default_mode", "infer_no_subs"]  # nijedan titl nije podrazumevan
        for n, x in enumerate(new):
            k = n0 + n
            cmd += [f"-metadata:s:s:{k}", f"language={sub_lang3(x)}",
                    f"-metadata:s:s:{k}", f"title={embed_title(x)}", f"-disposition:s:{k}", "0"]
        cmd.append(str(tmp))
        job.set_stage("subs", "running", None,
                      f"ubacujem {len(new)} {'titl' if len(new) == 1 else 'titlova'} u MKV izvor…")
        rc = job.run(cmd)
        after = stream_list(ffmpeg, tmp) if rc == 0 and tmp.is_file() and tmp.stat().st_size > 0 else None
        # provera pre zamene: iste ne-titl trake (video, audio, omot kao prilog attached_pic) + tacno n novih titlova
        if after is None or len([1 for x in after if x[0] == "subtitle"]) != n0 + len(new) or \
                sorted((x[0], x[3]) for x in after if x[0] != "subtitle") != \
                sorted((x[0], x[3]) for x in before if x[0] != "subtitle"):
            job.say(f"⚠ titlovi nisu ubačeni u MKV izvor (ffmpeg rc={rc}) — izvor nije menjan, titlovi su u subs/")
            return 0
        os.replace(tmp, src)
        record(new)
        ok = True
        job.say(f"titlovi u MKV izvoru: +{len(new)} ({', '.join(embed_title(x) for x in new)})")
        return len(new)
    except Cancelled:
        raise
    except Exception as e:  # noqa: BLE001
        job.say(f"⚠ ubacivanje titlova u MKV izvor nije uspelo: {e} — izvor nije menjan, titlovi su u subs/")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)
        job.stages["subs"] = prev
        if ok:
            prev["info"] = (prev.get("info") or "titlovi") + " · i u MKV izvoru"
        persist_soon()


def sub_label(e: dict | None) -> str:
    if not e:
        return "bez titla"
    return {"manual": "ručni CC", "auto": "auto", "whisper": "whisper", "sr": "prevod"}.get(e["kind"], e["kind"]) \
        + f" [{e.get('lang')}]"


def choose_sub(folder: Path, want: str) -> dict | None:
    m = load_manifest(folder) or {}
    subs = [s for s in m.get("subs", []) if (folder / s["file"]).exists()]
    by = {s["key"]: s for s in subs}
    if want in by:
        return by[want]
    cand = [s for s in subs if s["kind"] in ("manual", "auto", "whisper")]
    man = [s for s in cand if s["kind"] == "manual"]
    ol = base_lang(m.get("language"))
    for s in man:
        if base_lang(s["lang"]) in ("sr", "sh"):
            return s
    for s in man:
        if base_lang(s["lang"]) == ol:
            return s
    if man:
        return sorted(man, key=lambda s: (base_lang(s["lang"]) != "en", s["lang"]))[0]
    auto = [s for s in cand if s["kind"] == "auto"]
    if auto:
        return auto[0]
    return by.get("whisper")


def parse_srt(path: Path) -> list[dict]:
    def t(s):
        h, mnt, rest = s.split(":")
        sec, ms = rest.replace(".", ",").split(",")
        return ((int(h) * 60 + int(mnt)) * 60 + int(sec)) * 1000 + int(ms)
    cues = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8", errors="replace").strip()):
        lines = block.strip().splitlines()
        for i, ln in enumerate(lines):
            if "-->" in ln:
                a, b = [x.strip() for x in ln.split("-->")]
                txt = " ".join(x.strip() for x in lines[i + 1:]).strip()
                if txt:
                    cues.append({"start": t(a), "end": t(b.split()[0]), "text": txt})
                break
    return core.fix_overlaps(cues)


WHISPER_PROMPTS = {  # pocetni prompt fiksira pismo/stil i pomaze sa imenima; {names} = imena iz naslova
    "sr": "Transkript na srpskom jeziku, latinica, sa interpunkcijom. {names}",
    "el": "Απομαγνητοφώνηση στα ελληνικά, με σωστή ορθογραφία και στίξη. {names}",
    "en": "Transcript in English with punctuation. {names}",
    "es": "Transcripción en español con puntuación. {names}",
    "it": "Trascrizione in italiano con punteggiatura. {names}",
    "de": "Transkript auf Deutsch mit Zeichensetzung. {names}",
}
VAD_MODEL = "silero-v5.1.2"
VAD_URL = "https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v5.1.2.bin"
WHISPER_PROMPT_MAX = 600       # ceo prompt (whisper uzima ~224 tokena)
WHISPER_TERMS_MAX = {"el": 250}  # termini iz memorije; ostali jezici (latinica) 400
_CYR_RE = re.compile(r"[\u0400-\u04FF]")


# ================================================================ memorija ispravki (memory.py)
def mem_warn(job: "Job | None", e: Exception):
    if job:
        job.say(f"⚠ memorija ispravki nije primenjena ({type(e).__name__}: {str(e)[:200]}) — nastavljam bez nje")


class SafeMem:
    """MemView omotac za poslove: greska u memoriji nikad ne obara posao (upozorenje u log, neutralan rezultat)."""
    _NEUTRAL = {"examples": list, "glossary_for": list, "hints": list, "exact": lambda: None,
                "whisper_terms": str}

    def __init__(self, view, job: "Job | None" = None):
        self._v, self._job, self._warned = view, job, False

    def __getattr__(self, name):
        a = getattr(self._v, name)
        if not callable(a):
            return a

        def call(*args, **kw):
            try:
                return a(*args, **kw)
            except Exception as e:  # noqa: BLE001
                if not self._warned:
                    self._warned = True
                    mem_warn(self._job, e)
                if name == "apply_rules":
                    return (args[0] if args else kw.get("text")), 0
                return self._NEUTRAL.get(name, lambda: None)()
        return call


def mem_view(job: "Job | None", kind: str, lang: str | None, channel: str | None = None) -> SafeMem | None:
    """Pogled memorije za posao ili None (iskljuceno u podesavanjima, nepoznat jezik, prazno ili greska)."""
    if not as_bool(SETTINGS.get(f"mem_{kind}", True)) or not lang or base_lang(lang) == "und":
        return None
    try:
        v = memlib.Memory.load(DATA).view(kind, lang, channel or None)
        return None if v.empty else SafeMem(v, job)
    except Exception as e:  # noqa: BLE001
        mem_warn(job, e)
        return None


_LAT_RE = re.compile(r"[A-Za-zÀ-ɏ]")
_LETTERS = re.compile(r"[^\W\d_]+")


def is_cyr_line(text: str) -> bool:
    """Red je cirilicni tek kad ima vise cirilicnih nego latinicnih slova (zalutalo slovo ne menja pismo reda)."""
    return len(_CYR_RE.findall(text or "")) > len(_LAT_RE.findall(text or ""))


def cyr_keep(texts: list[str]) -> set[str]:
    """Imena iz celog transkripta koja u cirilicnim redovima stoje latinicom (npr. "Siemens" u cirilici)."""
    return {w for t in texts if is_cyr_line(t) for w in _LETTERS.findall(t)
            if len(w) >= 3 and w[0].isupper() and not _CYR_RE.search(w)}


def mem_apply(v, text: str, keep: set[str] | None = None) -> tuple[str, int]:
    """apply_rules; srpski cirilicni red (vecina slova cirilica) se radi u latinici i vraca u cirilicu, samo ako je
    nesto zamenjeno. Latinicne reci reda i imena iz celog transkripta (keep) ostaju latinicom; latinicni red sa
    zalutalim cirilicnim slovom ostaje latinicni."""
    if v.lang == "sr" and is_cyr_line(text):
        lat = core.normalize_script(text, "lat")
        new, n = v.apply_rules(lat)
        if not n:
            return text, 0
        k = set(keep or ()) | {w for w in _LETTERS.findall(text) if not _CYR_RE.search(w)}
        old = set(_LETTERS.findall(lat))
        k |= {w for w in _LETTERS.findall(new) if w not in old and any(c.isupper() for c in w[1:])}  # iPhone
        return core.to_cyr(new, k), n
    return v.apply_rules(text)


def mem_apply_cues(job: "Job | None", v, cues: list[dict], key: str = "text") -> int:
    """Pravila pogleda na sve titlove (u mestu); broj zamena. Greska -> upozorenje, titlovi ostaju kakvi su."""
    if v is None or not cues:
        return 0
    try:
        keep = cyr_keep([c[key] for c in cues]) if v.lang == "sr" else None
        new = [mem_apply(v, c[key], keep) for c in cues]
    except Exception as e:  # noqa: BLE001
        mem_warn(job, e)
        return 0
    for c, (t, _n) in zip(cues, new):
        c[key] = t
    return sum(n for _t, n in new)


def mem_attach(job: "Job", args: SimpleNamespace, target: str, channel: str | None):
    """args.mem (+ args.mem_report) za prevod/lekturu kad je mem_tr ukljucen i pogled nije prazan."""
    v = mem_view(job, "tr", "sr" if target in ("lat", "cyr") else target, channel)
    if v is not None:
        args.mem, args.mem_report = v, {}
    return v


def mem_say(job: "Job", args: SimpleNamespace, what: str):
    """Kratak izvestaj iz args.mem_report posle prevoda/lekture."""
    rep = getattr(args, "mem_report", None)
    if getattr(args, "mem", None) is None or not isinstance(rep, dict):
        return
    try:
        n = {k: int(rep.get(k) or 0) for k in ("examples", "glossary", "hints", "exact", "rules")}
    except (TypeError, ValueError):
        return
    b = n["examples"]
    blk = "bloku" if b % 10 == 1 and b % 100 != 11 else "bloka" if b % 10 in (2, 3, 4) and b % 100 not in (12, 13, 14) \
        else "blokova"
    parts = [f"primeri u {b} {blk}" if b else "",
             f"glosar {n['glossary']}" if n["glossary"] else "",
             f"naučena pravila u promptu {n['hints']}" if n["hints"] else "",
             f"prevodna memorija {n['exact']}" if n["exact"] else "",
             f"zamena {n['rules']}" if n["rules"] else ""]
    job.say(f"memorija ({what}): " + (" · ".join(p for p in parts if p) or "ništa nije primenjeno"))


def _whisper_prompt(lang: str | None, title: str = "", channel: str | None = None,
                    job: "Job | None" = None) -> tuple[str | None, int]:
    """(prompt, broj termina iz memorije) — vidi whisper_prompt_for."""
    base = ""
    if as_bool(SETTINGS.get("whisper_prompt", True)) and lang in WHISPER_PROMPTS:
        names = " ".join(w for w in re.findall(r"[A-ZΑ-Ω][^\W\d_]{2,}", title or "") if w.lower() not in
                         ("the", "and", "with", "from", "for", "you", "your", "how", "what", "why"))[:200]
        base = WHISPER_PROMPTS[lang].format(names=names).strip()
    terms = ""
    v = mem_view(job, "stt", lang, channel)
    if v is not None:
        room = WHISPER_PROMPT_MAX - (len(base) + 1 if base else 0)
        terms = v.whisper_terms(min(WHISPER_TERMS_MAX.get(base_lang(lang), 400), room)) or ""
        if len(terms) > room:  # zastita: nikad preko ukupnog limita
            terms = ""
    n = len([t for t in terms.split(", ") if t.strip()]) if terms else 0
    return (" ".join(x for x in (base, terms) if x) or None), n


def whisper_prompt_for(lang: str | None, title: str = "", channel: str | None = None) -> str | None:
    """Whisper --prompt: prompt po jeziku (ako je whisper_prompt ukljucen) + termini iz memorije (mem_stt;
    <= 400 znakova latinica, 250 grcki; ukupno <= 600). Bez prompta i bez termina -> None."""
    return _whisper_prompt(lang, title, channel)[0]


def _fetch_model(job: "Job", path: Path, url: str, min_size: int, what: str):
    if path.exists():
        return
    WHISPER_DIR.mkdir(parents=True, exist_ok=True)
    job.set_stage("subs", "running", 0, f"preuzimam {what} (jednom)…")
    part = Path(str(path) + ".part")
    rc = job.run(["curl", "-L", "--fail", "-sS", "-C", "-", "-o", str(part), url])
    if rc != 0 or not part.exists() or part.stat().st_size < min_size:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"preuzimanje ({what}) nije uspelo (curl rc={rc}) — Ponovi nastavlja")
    part.rename(path)


def detect_lang(cli: str, model: Path, wav: Path, job: "Job") -> str | None:
    """Brza detekcija jezika (samo prvih 30 s) pre izbora modela/prompta."""
    p = subprocess.run([cli, "-m", str(model), "-f", str(wav), "-dl", "-t", "8"], capture_output=True,
                       text=True, timeout=120)
    m = re.search(r"auto-detected language:\s*([a-z]{2})", p.stdout + p.stderr)
    return m.group(1) if m else None


def whisper_media(job: "Job", media: Path, work: Path, ffmpeg: str, audio_n: int = 0,
                  lang: str | None = None, title: str = "", channel: str | None = None) -> tuple[list[dict], str]:
    """Transkripcija audio trake (whisper.cpp na Metal-u, rezerva: python whisper). Vraca (cues, jezik).
    lang: forsiran jezik ('sr', 'el'...) ili None = automatski; title: za imena u promptu;
    channel: kanal za memoriju ispravki (termini u promptu + naucene zamene posle transkripcije)."""
    work.mkdir(parents=True, exist_ok=True)
    n_terms = 0
    with slot("whisper", job):
        job.set_stage("subs", "running", 0, "whisper: vadim audio…")
        wav = work / "audio16k.wav"
        if not wav.exists():  # .part pa rename: prekinuto vadjenje ne ostavlja "gotov" wav
            part = work / "audio16k.part.wav"
            if job.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(media),
                        "-map", f"0:a:{audio_n}", "-vn", "-ac", "1", "-ar", "16000", str(part)]) != 0:
                part.unlink(missing_ok=True)
                raise RuntimeError("vađenje audia za whisper nije uspelo")
            part.rename(wav)
        cli = shutil.which("whisper-cli")
        if not cli:  # prvo koriscenje: whisper.cpp preko Homebrew-a
            try:
                brew_install(job, "whisper-cpp", "whisper-cli", "whisper.cpp (prepoznavanje govora)", "subs")
            except RuntimeError as e:
                job.say(f"{e} — probam python whisper")
            cli = shutil.which("whisper-cli")
        name = SETTINGS["whisper_model"]
        model = WHISPER_DIR / f"ggml-{name}.bin"
        hf = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{}.bin"
        if cli:
            _fetch_model(job, model, hf.format(name), 50_000_000, f"whisper model {name}")
        cues = None
        if cli and model.exists():
            if not lang:  # prvo jezik, pa izbor modela i prompta
                lang = detect_lang(cli, model, wav, job)
                job.say(f"whisper: prepoznat jezik {lang or '?'}")
            qlangs = {x.strip() for x in str(SETTINGS.get("whisper_quality_langs") or "").split(",") if x.strip()}
            qname = SETTINGS.get("whisper_quality_model") or ""
            if lang in qlangs and qname and qname != name:  # grcki/srpski: tacniji (sporiji) model
                qmodel = WHISPER_DIR / f"ggml-{qname}.bin"
                try:
                    _fetch_model(job, qmodel, hf.format(qname), 50_000_000, f"whisper model {qname}")
                    model, name = qmodel, qname
                except RuntimeError as e:
                    job.say(f"kvalitet model nije dostupan ({e}) — koristim {name}")
            extra = ["-l", lang or "auto"]
            if SETTINGS.get("whisper_vad", True):
                vad = WHISPER_DIR / f"ggml-{VAD_MODEL}.bin"
                try:
                    _fetch_model(job, vad, VAD_URL, 500_000, "VAD model")
                    extra += ["--vad", "-vm", str(vad)]
                except RuntimeError as e:
                    job.say(f"VAD nije dostupan ({e}) — nastavljam bez VAD-a")
            prompt, n_terms = _whisper_prompt(lang, title, channel, job)
            if prompt:
                extra += ["--prompt", prompt]
            job.set_stage("subs", "running", 0, f"whisper.cpp ({name}, {lang or 'auto'}, Metal)…")
            det = {}

            def on_line(line):
                mm = re.search(r"progress\s*=\s*(\d+)%", line)
                if mm:
                    job.set_stage("subs", "running", int(mm.group(1)), f"whisper.cpp: {mm.group(1)}%")
                    return True
                ml = re.search(r"auto-detected language:\s*([a-z]{2})", line)
                if ml:
                    det["lang"] = ml.group(1)
                # u log ide samo bitno: jezik, greske, ukupno vreme; ostalo je whisper.cpp buka
                keep = ("auto-detected" in line or "error" in line.lower() or "total time" in line
                        or "total duration of speech" in line)
                return not keep
            rc = job.run([cli, "-m", str(model), "-f", str(wav), *extra, "-t", "8",
                          "-osrt", "-of", str(work / "whisper"), "-ml", "80", "-sow", "-pp"], on_line)
            srt = work / "whisper.srt"
            if rc == 0 and srt.exists():
                cues, lang = parse_srt(srt), lang or det.get("lang", "und")
            else:
                job.say("whisper.cpp nije uspeo, probam python whisper…")
        if cues is None:
            job.set_stage("subs", "running", None, "python whisper (sporije)…")
            cues = core.whisper_cues(SimpleNamespace(whisper_model="turbo"), media, work, ffmpeg, runner=job.run)
            lang = lang or "und"
        wav.unlink(missing_ok=True)
    n_rep = mem_apply_cues(job, mem_view(job, "stt", lang, channel), cues)
    if n_rep or n_terms:
        job.say(f"memorija: {n_rep} zamena u transkriptu, {n_terms} termina u promptu")
    return cues, lang or "und"


def do_whisper(job: "Job", folder: Path, source: Path, ffmpeg: str, lang: str | None = None) -> dict:
    m = load_manifest(folder) or {}
    if not lang:
        yl = base_lang(m.get("language") or "")
        lang = yl if yl and yl != "und" else None
    cues, lang = whisper_media(job, source, mdir(folder) / "work" / job.id, ffmpeg, lang=lang,
                               title=m.get("title") or "", channel=m.get("channel"))
    shutil.rmtree(mdir(folder) / "work" / job.id, ignore_errors=True)
    if not cues:
        raise RuntimeError("whisper nije našao govor u audio traci")
    rel = "subs/whisper.srt"
    core.write_srt(cues, folder / rel)
    e = {"key": "whisper", "kind": "whisper", "lang": lang, "file": rel, "count": len(cues),
         "name": f"Whisper ({lang_label(lang)})"}
    upsert_sub(folder, e)
    return e


def get_cues(job: "Job", folder: Path, proc: dict, ffmpeg: str, allow_whisper: bool):
    """(cues, entry). cues=None znaci: treba whisper, a jos nije dozvoljen (video se skida)."""
    want = proc.get("sub_source") or "auto-best"
    if want == "none":
        job.set_stage("subs", "skipped", info="bez titla")
        return [], None
    if want != "whisper:run":
        e = choose_sub(folder, want)
        if e:
            if want not in ("auto-best", e["key"]):
                job.say(f"izabrani titl {want} nije u arhivi — koristim {e['key']}")
            job.set_stage("subs", "done", info=f"za obradu: {e['name']} · {e['count']} titlova")
            return parse_srt(folder / e["file"]), e
    if not allow_whisper:
        return None, None
    m = load_manifest(folder) or {}
    src = folder / m["source"] if m.get("source") else None
    if not src or not src.exists():
        if want == "auto-best":  # npr. recept "Samo titlovi": nema titla ni izvora -> preskoci, nije greska
            if core.pick_track(item_info(folder), None):  # YT ima titl, a nije u arhivi (npr. 429) -> greska
                raise RuntimeError("YouTube ima titl, ali nije preuzet u arhivu (vidi log) — "
                                   "preuzmi ponovo (Ponovi / recept Samo titlovi)")
            job.say("nema titla, a izvor za whisper nije u arhivi — prevod i izlaz preskočeni")
            job.set_stage("subs", "skipped", info="YouTube nema titl (nema izvora za whisper)")
            return [], None
        raise RuntimeError("nema titla, a whisper traži preuzet video (izvor nije u arhivi)")
    e = do_whisper(job, folder, src, ffmpeg, lang=(proc.get("whisper_lang") or None))
    job.set_stage("subs", "done", info=f"za obradu: {e['name']} · {e['count']} titlova")
    return parse_srt(folder / e["file"]), e


def translate_cues(job: "Job", cues: list[dict], target: str, engine: str, model: str, review: bool,
                   cache: Path, ctx: str, src_lang: str = "", channel: str | None = None):
    """Prevedi cues (c['sr'] = prevod na `target`: lat | cyr | el), kes u `cache`, progres u 'translate'.
    channel: kanal stavke za memoriju ispravki (args.mem kad je mem_tr ukljucen)."""
    engine, model = engine_model(engine, model)
    if engine == "tg" and target in ("lat", "cyr"):  # TranslateGemma sam ne radi srpski JSON pipeline -> C
        engine, model = engine_model("synergy", None)
    if engine == "ollama":
        ensure_ollama(model, job)
    elif engine == "synergy":
        ensure_ollama(model, job)
        ensure_ollama(core.TG_MODEL, job)
    elif engine == "tg" or (target not in ("lat", "cyr") and engine in ("gemini", "cloudflare")):
        if ollama_tags() and core.TG_MODEL in (ollama_tags() or []):
            pass  # TranslateGemma je lokalna rezerva za ne-srpske jezike
        elif engine == "tg":
            ensure_ollama(core.TG_MODEL, job)
    local = engine in ("ollama", "synergy", "tg")
    args = SimpleNamespace(engine=engine, model=model, script=target if target in ("lat", "cyr") else "lat",
                           jobs=1 if local or engine in ("gemini", "cloudflare") else 4,
                           batch=30 if local else 60, cancelled=job.cancel.is_set, review=review,
                           src_lang=src_lang, status=lambda t: job.set_stage("translate", "running", None, t),
                           keep_lines=True)  # promena par linija izvora (npr. stt memorija) ne baca ceo kes
    mem_attach(job, args, target, channel)
    cache.mkdir(parents=True, exist_ok=True)
    label = engine_label(engine, model)
    with slot("translate", job):
        job.set_stage("translate", "running", 0, f"{label}: {len(cues)} titlova → {TARGET_NAME.get(target, target)}…")

        def prog(done, total):
            job.set_stage("translate", "running", round(100 * done / max(1, total)),
                          f"{label}: blok {done}/{total} ({len(cues)} titlova)")
        try:
            if target in ("lat", "cyr"):
                core.translate(cues, ctx, args, cache, progress=prog)
            else:
                core.translate_to(cues, ctx, args, cache, target, progress=prog)
                for c in cues:
                    c["sr"] = c["tr"]
        except Exception:
            if job.cancel.is_set():
                raise Cancelled()
            raise
    mem_say(job, args, "prevod")
    for c in cues:
        c["sr"] = core.wrap_lines(c["sr"])


def translate_step(job: "Job", folder: Path, proc: dict, cues: list[dict], entry: dict | None) -> bool:
    """Postavi c['sr']. Vraca True ako je titl na srpskom (preveden ili vec srpski)."""
    target = proc.get("target", "lat")
    job.main_file = entry["file"] if entry else None
    if not cues:
        job.set_stage("translate", "skipped", info="nema titla")
        return False
    job.tr_lang = None
    is_sr = target in ("lat", "cyr", "none") and bool(entry) and (
        entry["kind"] == "sr" or (entry["kind"] == "manual" and base_lang(entry["lang"]) in ("sr", "sh")))
    if target == "none" or is_sr:
        for c in cues:
            c["sr"] = (core.wrap_lines(core.finalize_script(c["text"], target, []))
                       if is_sr and target in ("lat", "cyr") else c["text"])
        job.set_stage("translate", "skipped", info="originalni titl" if target == "none" and not is_sr else
                      f"već srpski{' → ćirilica' if is_sr and target == 'cyr' else ''}")
        return is_sr
    engine, model = engine_model(proc.get("engine"), proc.get("model"))
    review = proc.get("review") == "on"
    info = item_info(folder)
    ctx = (info.get("title") or "") + (" — " + (info.get("description") or "")[:600])
    cache = mdir(folder) / "cache" / re.sub(r"[^A-Za-z0-9_-]+", "_", entry["key"])
    src_lang = base_lang(entry.get("lang")) if entry and entry.get("lang") not in (None, "und") else ""
    channel = (load_manifest(folder) or {}).get("channel") or info.get("channel") or info.get("uploader")
    if entry.get("kind") == "auto":  # automatski titl: naucene whisper/stt zamene pre prevoda (fajl ostaje isti)
        n = mem_apply_cues(job, mem_view(job, "stt", entry.get("lang"), channel), cues)
        if n:
            job.say(f"memorija: {n} zamena u automatskom titlu pre prevoda")
    translate_cues(job, cues, target, engine, model, review, cache, ctx, src_lang, channel=channel)
    tag = re.sub(r"[^A-Za-z0-9_-]+", "_", f"{engine}_{model}{'_rev' if review else ''}")
    srck = re.sub(r"[^A-Za-z0-9_-]+", "_", entry["key"])
    label = engine_label(engine, model)
    if target in ("lat", "cyr"):
        rel = f"subs/sr-{target}.{srck}.{tag}.srt"
        e = {"key": f"sr:{target}:{engine}:{model}{':rev' if review else ''}:{entry['key']}", "kind": "sr", "lang": "sr",
             "name": f"Srpski {'ćirilica' if target == 'cyr' else 'latinica'} — {label}"
                     f"{' + lektura' if review else ''} (iz: {entry['name']})"}
    else:
        job.tr_lang = target
        rel = f"subs/{target}.{srck}.{tag}.srt"
        e = {"key": f"tr:{target}:{engine}:{model}:{entry['key']}", "kind": "tr", "lang": target,
             "name": f"{core.TARGETS.get(target, (target, target, target))[2]} — {label} (iz: {entry['name']})"}
    core.write_srt(cues, folder / rel, key="sr")
    job.main_file = rel
    upsert_sub(folder, {**e, "file": rel, "count": len(cues), "src": entry["key"], "engine": engine, "model": model,
                        "date": time.strftime("%Y-%m-%d")})
    job.set_stage("translate", "done", info=f"{label} · {TARGET_NAME.get(target, target)}")
    return True


# ================================================================ izlaz
def ffmpeg_progress(job: "Job", dur: float, label: str):
    state = {"speed": ""}

    def on_line(line: str) -> bool:
        if line.startswith("out_time_us=") and dur > 0:
            try:
                sec = int(line.split("=")[1]) / 1e6
            except ValueError:
                return True
            job.set_stage("output", "running", min(99.9, 100 * sec / dur),
                          f"{label}: {100 * sec / dur:.1f}% · {state['speed']}")
            return True
        if line.startswith("speed="):
            state["speed"] = line.split("=")[1].strip()
            return True
        return bool(re.match(r"^[a-z_0-9]+=", line))
    return on_line


def pick_audio(m: dict, sel: str, job: "Job | None" = None) -> list[int]:
    aud = m.get("audio") or []
    if not aud:
        return []
    orig = next((a["index"] for a in aud if a.get("original")), aud[0]["index"])
    hit = None
    if sel == "all":
        return [orig] + [a["index"] for a in aud if a["index"] != orig]
    if sel.startswith("idx:"):
        hit = [a for a in aud if a["index"] == int(sel[4:])]
    elif sel.startswith("fid:"):  # tacna traka iz probe (razlikuje es-419 / es-ES)
        hit = [a for a in aud if a.get("format_id") == sel[4:]]
    elif sel.startswith("lang:"):
        hit = [a for a in aud if base_lang(a["lang"]) == base_lang(sel[5:])]
    if hit is None:
        return [orig]
    if not hit:
        if job:
            job.say(f"audio {sel} nije u preuzetom izvoru — koristim originalnu traku")
        return [orig]
    return [hit[0]["index"]]


def uniq_path(p: Path) -> Path:
    n = 2
    q = p
    while q.exists():
        q = p.with_name(f"{p.stem} ({n}){p.suffix}")
        n += 1
    return q


TONEMAP_SDR = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
               "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")


def do_output(job: "Job", folder: Path, proc: dict, cues: list[dict], entry: dict | None,
              serbian: bool, ffmpeg: str) -> bool:
    """Napravi izlaz po izboru. Vraca True ako je napravljen video fajl."""
    mode = proc.get("output") or "none"
    m = load_manifest(folder) or {}
    stem = item_stem(m)
    has = bool(cues)
    tgt = proc.get("target", "lat")
    tl = getattr(job, "tr_lang", None)
    if serbian and tl:  # preveden na ne-srpski jezik (npr. grcki)
        tag = tl
    elif serbian:
        if entry and entry["kind"] == "sr" and tgt not in ("lat", "cyr"):
            tag = "sr-" + entry["key"].split(":")[1]
        else:
            tag = f"sr-{tgt}" if tgt in ("lat", "cyr") else "sr"
    else:
        tag = base_lang(entry["lang"]) if entry else "sub"
    if serbian and tl:
        main_lang, main_title = iso3(tl), core.TARGETS.get(tl, (tl, tl, tl))[2]
    else:
        main_lang, main_title = ("srp", "Srpski") if serbian else (iso3(entry["lang"]) if entry else "und",
                                                                  entry["name"] if entry else "Titl")
    if mode == "none":
        job.set_stage("output", "skipped", info="bez izlaza (titl/prevod je u subs/)")
        return False
    if not has:  # bez titla nema ni SRT ni burn/meki titl (ni kad izvor fali) -> preskoci, nije greska
        job.say(f"nema titla — izlaz '{mode}' preskočen")
        job.set_stage("output", "skipped", info="nema titla")
        return False
    if mode == "srt":
        out = uniq_path(folder / f"{stem}.{tag}.srt")
        core.write_srt(cues, out, key="sr")
        job.outputs.append(out.name)
        add_output(folder, out.name)
        job.set_stage("output", "done", info="SRT")
        return False
    src = folder / m["source"] if m.get("source") else None
    if not src or not src.exists():
        raise RuntimeError("izvorni video nije u arhivi (preuzmi ponovo ili izaberi izlaz 'SRT')")
    if m.get("kind") == "audio":
        raise RuntimeError("u arhivi je samo audio — burn/MKV traže video (izaberi izlaz 'SRT')")
    st = probe_video(ffmpeg, src)
    w, h, dur = int(st.get("width") or 1920), int(st.get("height") or 1080), st["duration"]
    ten_bit = "10" in (st.get("pix_fmt") or "")
    trc = st.get("color_transfer") or ""
    hdr = trc in ("smpte2084", "arib-std-b67")
    color = []
    for k, opt in (("color_primaries", "-color_primaries"), ("color_transfer", "-color_trc"),
                   ("color_space", "-colorspace")):
        if st.get(k) and st[k] != "unknown":
            color += [opt, st[k]]
    amaps = pick_audio(m, proc.get("audio_out") or "orig", job)
    amap_args = sum((["-map", f"0:a:{i}"] for i in amaps), []) if amaps else ["-map", "0:a?"]
    variant = [tag]
    ao = proc.get("audio_out") or "orig"
    if ao == "all" and len(amaps) > 1:
        variant.append("audio-sve")
    elif ao != "orig" and amaps:
        a = next((x for x in m.get("audio") or [] if x["index"] == amaps[0]), None)
        if a and not a.get("original"):
            variant.append(f"audio-{base_lang(a['lang'])}")
    work = mdir(folder) / "work" / job.id  # privremeni fajlovi po poslu
    work.mkdir(parents=True, exist_ok=True)
    produced = False
    try:
        core.write_srt(cues, work / "main.srt", key="sr")
        tracks = [(work / "main.srt", main_lang, main_title)]
        so = proc.get("subs_out") or "sr+orig"
        subs_ok = [x for x in m.get("subs", []) if x.get("count") and (folder / x["file"]).exists()
                   and (folder / x["file"]).stat().st_size > 0]
        if so == "sr+orig" and serbian and entry:
            orig = entry if entry["kind"] not in ("sr", "tr") else next((x for x in subs_ok if x["key"] == entry.get("src")), None)
            if orig and orig["kind"] not in ("sr", "tr") and not (orig["kind"] == "manual" and base_lang(orig["lang"]) in ("sr", "sh")):
                tracks.append((folder / orig["file"], iso3(orig["lang"]), f"Original — {orig['name']}"))
        elif so == "sr+all":
            main_file = job.main_file
            for x in subs_ok:
                if x["file"] != main_file:
                    tracks.append((folder / x["file"], iso3(x["lang"]), x["name"]))

        def sub_inputs(default_first: bool):
            args, maps, meta = [], [], []
            for n, (pth, lg, ttl) in enumerate(tracks):
                args += ["-i", str(pth)]
                maps += ["-map", f"{n + 1}:0"]
                meta += [f"-metadata:s:s:{n}", f"language={lg}", f"-metadata:s:s:{n}", f"title={ttl}"]
            # burn: titl je vec u slici -> nijedan meki titl nije podrazumevan (inace dupli titl)
            return args, maps, meta + (["-disposition:s:0", "default"] if default_first else ["-disposition:s", "0"])

        with slot("encode", job):
            if mode in ("soft", "both"):
                out = uniq_path(folder / f"{stem}.{'.'.join(variant)}.mkv")
                si, sm, smeta = sub_inputs(True)
                cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1", "-y",
                       "-i", str(src), *si, "-map", "0:v:0", *amap_args, *sm,
                       "-c:v", "copy", "-c:a", "copy", "-c:s", "srt", *smeta, str(out)]
                job.set_stage("output", "running", 0, "MKV + meki titlovi (bez rekodiranja)…")
                if job.run(cmd, ffmpeg_progress(job, dur, "MKV mux"), cwd=str(work)) != 0:
                    raise RuntimeError("MKV mux nije uspeo — vidi log")
                job.outputs.append(out.name)
                add_output(folder, out.name)
                produced = True
            if mode in ("burn", "both"):
                enc = proc.get("encoder") or SETTINGS["encoder"]
                vq = str(proc.get("vq") or (SETTINGS["quality"] if enc in ("hevc", "h264") else 20))
                pre_vf, primary = "", "&H00FFFFFF"
                if hdr and enc in ("h264", "x264"):  # 8-bit SDR izlaz: tonemap, bt709 tagovi
                    pre_vf = TONEMAP_SDR
                    color = ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]
                    job.say("HDR izvor → tonemap u SDR (H.264)")
                elif hdr:  # HDR ostaje: prigusena bela da titl ne svetli punim HDR vrhom
                    primary = "&H00949494" if trc == "smpte2084" else "&H00BFBFBF"
                core.write_ass(cues, work / "main.ass", w, h, key="sr", primary=primary)
                if enc == "hevc":
                    venc = ["-c:v", "hevc_videotoolbox", "-q:v", vq, "-tag:v", "hvc1"]
                    venc += ["-pix_fmt", "p010le", "-profile:v", "main10"] if ten_bit else ["-pix_fmt", "yuv420p"]
                elif enc == "h264":
                    venc = ["-c:v", "h264_videotoolbox", "-q:v", vq, "-pix_fmt", "yuv420p"]
                elif enc == "x264":
                    venc = ["-c:v", "libx264", "-crf", vq, "-preset", "slow", "-pix_fmt", "yuv420p"]
                else:
                    xp = "log-level=error"
                    if hdr:
                        xp += ":hdr10-opt=1:repeat-headers=1"
                    venc = ["-c:v", "libx265", "-crf", vq, "-preset", "medium", "-tag:v", "hvc1", "-x265-params", xp]
                    venc += ["-pix_fmt", "yuv420p10le"] if ten_bit else ["-pix_fmt", "yuv420p"]
                scale = str(proc.get("scale") or "src")
                scale_f = f"scale=-2:{int(scale)}:flags=lanczos" if scale != "src" and h > int(scale) else ""
                vf = ",".join(x for x in (pre_vf, scale_f, "ass=main.ass") if x)
                aenc = proc.get("audio_enc") or "aac192"
                bvar = variant + ([f"{scale}p"] if scale_f else [])
                if aenc == "copy":
                    out = uniq_path(folder / f"{stem}.{'.'.join(bvar)}.burn.mkv")
                    acodec, scodec = ["-c:a", "copy"], ["-c:s", "srt"]
                else:
                    out = uniq_path(folder / f"{stem}.{'.'.join(bvar)}.mp4")
                    acodec = ["-c:a", "aac", "-b:a", aenc.replace("aac", "") + "k"]
                    scodec = ["-c:s", "mov_text"]
                si, sm, smeta = sub_inputs(False)
                cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1", "-y",
                       "-i", str(src), *si, "-map", "0:v:0", *amap_args, *sm,
                       "-vf", vf, *venc, *color, *acodec, *scodec, *smeta]
                if out.suffix == ".mp4":
                    cmd += ["-movflags", "+faststart"]
                cmd.append(str(out))
                label = f"burn {enc} q{vq}{'' if not scale_f else ' → ' + scale + 'p'}"
                job.set_stage("output", "running", 0, label + "…")
                if job.run(cmd, ffmpeg_progress(job, dur, f"{label} ({w}x{h})"), cwd=str(work)) != 0:
                    raise RuntimeError("burn nije uspeo — vidi log")
                job.outputs.append(out.name)
                add_output(folder, out.name)
                produced = True
    finally:
        shutil.rmtree(work, ignore_errors=True)
    job.set_stage("output", "done", info=", ".join(job.outputs) or "gotovo")
    return produced


def finish_source(job: "Job", folder: Path, proc: dict, produced: bool = False):
    if proc.get("keep_source", True):
        return
    if not produced:
        job.say("izvor zadržan: obrada nije napravila video izlaz")
        return
    m = load_manifest(folder) or {}
    if m.get("source") and (folder / m["source"]).exists():
        (folder / m["source"]).unlink()
        job.say(f"izvor obrisan (po izboru): {m['source']}")
        update_manifest(folder, lambda mm: mm.update(source=None, source_subs=[]))


# ================================================================ poslovi
def run_download_job(job: "Job", ffmpeg: str):
    dl, proc = job.opts.get("dl") or {}, job.opts.get("proc")
    if job.opts.get("after") and proc and not proc.get("keep_source", True):
        # Transkoduj / Izvuci audio rade iz izvora (posle obrade) -> ne brisi ga pre njih
        proc = {**proc, "keep_source": True}
        job.say("izvor se zadržava: koraci posle preuzimanja (Transkoduj / Izvuci audio) rade iz njega")
    info = PROBES.get(job.url)
    if not info:
        with slot("probe", job):
            job.set_stage("download", "running", None, "probe…")
            info = probe_url(job.url)
    job.title, job.thumb, job.vid = item_title(info), info.get("thumbnail"), info.get("id")
    job.live = info.get("live_status")
    out_dir = dl.get("out_dir") or SETTINGS["out_dir"]
    pl = job.opts.get("_playlist")
    if pl and dl.get("subfolder"):  # stavke plejliste u njen podfolder
        out_dir = str(Path(out_dir).expanduser() / core.safe_name(pl.get("title") or pl.get("id") or "plejlista"))
    remember_root(out_dir)
    folder = item_dir(info, out_dir)
    if job.folder:  # Ponovi: isti folder (i kad se naslov live-a promenio)
        prev = load_manifest(Path(job.folder))
        if prev and prev.get("id") == info.get("id"):
            folder = Path(job.folder)
    with folder_lock(folder, job):
        init_manifest(folder, info, job.url)
        if pl:
            def addpl(mm):
                if not any(x.get("id") == pl.get("id") for x in mm.setdefault("playlists", [])):
                    mm["playlists"].append({"id": pl.get("id"), "title": pl.get("title")})
            update_manifest(folder, addpl)
        job.folder = str(folder)
        job.say(f"arhiva: {folder}")
        _download_and_process(job, info, folder, dl, proc, ffmpeg)
    if job.opts.get("after") and not job.cancel.is_set():  # van folder_lock: Toolbox posao uzima isti lock
        run_after_steps(job, folder)


def _download_and_process(job: "Job", info: dict, folder: Path, dl: dict, proc: dict | None, ffmpeg: str):
    is_live = job.live in ("is_live", "is_upcoming")
    box: dict = {}

    def dl_thread():
        try:
            box["video"] = do_download(job, info, folder, dl, ffmpeg)
        except BaseException as e:  # noqa: BLE001
            box["err"] = e
    t = None
    if (dl.get("video_fmt") or "auto:best") != "none":
        t = threading.Thread(target=dl_thread, daemon=True)
        job.dl_thread = t
        t.start()
    else:
        job.set_stage("download", "skipped", info="bez videa (samo titlovi)")

    pre, err = None, None
    want = (proc or {}).get("sub_source") or "auto-best"
    try:
        if not is_live:
            keys = sub_keys_for(dl.get("subs") or "auto", info, proc)
            got = fetch_subs(job, folder, keys, info) if keys else []
            m = load_manifest(folder) or {}
            n = len([s for s in m.get("subs", []) if s["kind"] in ("manual", "auto")])
            job.set_stage("subs", "done" if n else "skipped",
                          info=f"u arhivi {n} {'titl' if n == 1 else 'titlova'}" + (f" (+{len(got)} novih)" if got else "")
                          if n else ("bez titlova (po izboru)" if not keys and dl.get("subs") == "none"
                                     else "YouTube nema titl"))
            if proc and not want.startswith("whisper"):
                cues, entry = get_cues(job, folder, proc, ffmpeg, allow_whisper=False)
                if cues is not None:
                    serbian = translate_step(job, folder, proc, cues, entry)  # dok se video jos skida
                    pre = (cues, entry, serbian)
                elif t:
                    job.set_stage("subs", "waiting", info="nema YT titla → whisper posle preuzimanja")
    except Cancelled:
        raise
    except Exception as e:  # noqa: BLE001  video neka se ipak zavrsi u arhivu
        err = e
        job.say(f"greška u titlu/prevodu: {e} — čekam da se video preuzme u arhivu")
    if t:
        while t.is_alive():
            t.join(0.5)
            job.check()
        if "err" in box:
            raise box["err"]
    if err:
        raise err
    if is_live:  # titlovi se pojave tek posle kraja streama
        try:
            info = probe_url(job.url)
            (mdir(folder) / "info.json").write_text(json.dumps(info, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001
            job.say(f"re-probe posle live-a nije uspeo: {e}")
        keys = sub_keys_for(dl.get("subs") or "auto", info, proc)
        if keys:
            fetch_subs(job, folder, keys, info)
    # titlovi i u MKV izvoru: posle preuzimanja izvora i SVIH YT titlova (i posle live re-probe), a pre obrade
    # (whisper/burn/meki izlaz citaju izvor). Ponovi: samo novi titlovi. Izvor koji ce obrada obrisati se ne dira.
    if not (proc and not proc.get("keep_source", True) and (proc.get("output") or "none") in ("burn", "soft", "both")):
        embed_source_subs(job, folder, ffmpeg)
    if not proc:
        job.set_stage("translate", "skipped", info="kasnije (Arhiva → Obradi)")
        job.set_stage("output", "skipped", info="kasnije (Arhiva → Obradi)")
        if job.stages["subs"]["status"] in ("pending", "waiting", "running"):
            job.set_stage("subs", "done", info="preuzeto")
        return
    if pre is None:
        cues, entry = get_cues(job, folder, proc, ffmpeg, allow_whisper=True)
        serbian = translate_step(job, folder, proc, cues, entry)
    else:
        cues, entry, serbian = pre
    produced = do_output(job, folder, proc, cues, entry, serbian, ffmpeg)
    finish_source(job, folder, proc, produced)


def run_process_job(job: "Job", ffmpeg: str):
    folder = Path(job.folder)
    m = load_manifest(folder)
    if not m:
        raise RuntimeError("stavka arhive nema manifest")
    job.title, job.thumb, job.vid = m.get("title"), m.get("thumbnail"), m.get("id")
    proc = job.opts.get("proc") or {}
    job.set_stage("download", "skipped", info="iz arhive")
    if proc.get("action") in ("lektura", "audio_copy"):
        with folder_lock(folder, job):
            if proc["action"] == "lektura":
                run_lektura(job, folder, proc)
            else:
                run_audio_copy(job, folder, ffmpeg)
        return
    with folder_lock(folder, job):
        cues, entry = get_cues(job, folder, proc, ffmpeg, allow_whisper=True)
        serbian = translate_step(job, folder, proc, cues, entry)
        produced = do_output(job, folder, proc, cues, entry, serbian, ffmpeg)
        finish_source(job, folder, proc, produced)


# ================================================================ toolbox (transkoder)
TB_ANALYSIS: dict[str, tuple[float, dict]] = {}  # path -> (mtime, analysis)


def tb():
    import toolbox  # lenji import: panel radi i dok toolbox.py ne postoji
    return toolbox


def tb_analyze(path: str) -> dict:
    p = Path(path).expanduser()
    if not p.is_file():
        raise RuntimeError(f"fajl ne postoji: {p}")
    mt = p.stat().st_mtime
    hit = TB_ANALYSIS.get(str(p))
    if hit and hit[0] == mt:
        return hit[1]
    a = tb().analyze(str(p), ffprobe_bin(core.find_ffmpeg()))
    TB_ANALYSIS[str(p)] = (mt, a)
    return a


def hms(v: str) -> float | None:
    if not v:
        return None
    parts = [float(x) for x in str(v).replace(",", ".").split(":")]
    sec = 0.0
    for x in parts:
        sec = sec * 60 + x
    return sec


def tb_prepare_subs(job: "Job", src: Path, spec: dict, a: dict, work: Path, ffmpeg: str, out: Path) -> str | None:
    """Pripremi titl za soft/burn (ekstrakcija / spoljni fajl / whisper + opcioni prevod). Vraca sub_file."""
    sub_src = spec.get("sub_src") or "none"
    target = spec.get("translate") or "off"
    mode = spec.get("sub_mode") or "soft"
    if sub_src == "none":
        job.set_stage("subs", "skipped", info="bez dodatnog titla")
        job.set_stage("translate", "skipped", info="—")
        return None
    cues, label, sub_lang_hint = None, "", ""
    if sub_src.startswith("track:"):
        n = int(sub_src.split(":", 1)[1])
        st = next((x for x in a.get("subs", []) if x["n"] == n), None)
        if not st:
            raise RuntimeError(f"titl traka {n} ne postoji u fajlu")
        if not st["text"] or (target == "off" and mode != "burn"):
            # slikovni titl (burn = overlay u plan()) ili meki bez prevoda: plan() radi direktno sa trakom
            job.set_stage("subs", "done", info=f"traka #{n} ({st['codec']}{', ' + st['lang'] if st.get('lang') else ''})")
            job.set_stage("translate", "skipped", info="bez prevoda" if st["text"] else "slikovni titl se ne prevodi")
            return None
        job.set_stage("subs", "running", None, f"izvlačim titl #{n}…")
        if job.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
                    "-map", f"0:s:{n}", str(work / "src.srt")]) != 0:
            raise RuntimeError("izvlačenje titla nije uspelo")
        cues, label = parse_srt(work / "src.srt"), f"traka #{n} {st.get('lang') or ''}".strip()
        sub_lang_hint = base_lang(st.get("lang")) if st.get("lang") not in (None, "und") else ""
    elif sub_src == "file":
        f = Path(spec.get("sub_file") or "").expanduser()
        if not f.is_file():
            raise RuntimeError(f"spoljni titl ne postoji: {f}")
        if target == "off" and mode != "burn":  # plan() dodaje fajl direktno (jezik iz imena fajla)
            job.set_stage("subs", "done", info=f.name)
            job.set_stage("translate", "skipped", info="bez prevoda")
            return None
        job.set_stage("subs", "running", None, f"čitam {f.name}…")
        if job.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(f), str(work / "src.srt")]) != 0:
            raise RuntimeError("spoljni titl nije čitljiv (srt/ass/vtt)")
        cues, label = parse_srt(work / "src.srt"), f.name
    elif sub_src == "whisper":
        at = spec.get("audio_tracks")
        n = at[0] if isinstance(at, list) and at else 0
        wl = str(spec.get("whisper_lang") or "").strip() or None
        cues, lang = whisper_media(job, src, work, ffmpeg, audio_n=n, lang=wl, title=src.stem)
        label = f"whisper ({lang})"
        sub_lang_hint = lang if lang != "und" else ""
    if not cues:
        raise RuntimeError("izabrani titl je prazan")
    job.set_stage("subs", "done", info=f"{label} · {len(cues)} titlova")
    if target in ("lat", "cyr", "el"):
        eng, mdl = engine_model(spec.get("engine"), spec.get("model"))
        translate_cues(job, cues, target, eng, mdl, spec.get("review") == "on", work / "cache", src.stem,
                       sub_lang_hint)
        job.set_stage("translate", "done", info=f"{engine_label(eng, mdl)} · {TARGET_NAME[target]}")
        side = out.with_suffix(".el.srt" if target == "el" else ".sr.srt")  # prevod i pored izlaza
        core.write_srt(cues, side, key="sr")
        job.outputs.append(side.name)
    else:
        for c in cues:
            c["sr"] = c["text"]
        job.set_stage("translate", "skipped", info="bez prevoda")
    if mode == "burn":
        w, h = tb().output_dims(spec, a)
        core.write_ass(cues, work / "burn.ass", w, h, key="sr")
        return str(work / "burn.ass")
    core.write_srt(cues, work / "sub.srt", key="sr")
    return str(work / "sub.srt")


def run_transcode_job(job: "Job", ffmpeg: str):
    lib = job.opts.get("_lib")
    if not lib:
        return _transcode(job, ffmpeg)
    # izvor iz Arhive: jedan posao po stavci (izlazno ime se racuna pod lock-om), izlaz ide u manifest
    folder = Path(lib)
    with folder_lock(folder, job):
        _transcode(job, ffmpeg)
        for name in job.outputs:
            if (folder / name).is_file():
                add_output(folder, name)


def _transcode(job: "Job", ffmpeg: str):
    src = Path(job.opts.get("path") or "").expanduser()
    spec = dict(job.opts.get("spec") or {})
    job.set_stage("download", "running", None, "analiza (ffprobe)…")
    a = tb_analyze(str(src))
    v = a.get("video") or {}
    job.title = job.opts.get("_title") or a.get("name") or src.name
    job.set_stage("download", "done", info=(f"{v.get('codec')} {v.get('width')}x{v.get('height')} · " if v else "")
                  + f"{len(a.get('audio', []))} audio · {len(a.get('subs', []))} titl · {fmt_dur(a.get('duration'))}")
    if spec.get("out_dir") and not job.opts.get("_lib"):  # folder stavke arhive nije novi koren biblioteke
        remember_root(spec["out_dir"])
    out = Path(tb().default_out_path(str(src), spec, a))
    job.folder = str(out.parent)
    work = DATA / "tb" / job.id
    work.mkdir(parents=True, exist_ok=True)
    try:
        if job.opts.get("_sub_name") and spec.get("sub_src") == "file":
            # srpski prevod iz subs/: kopija sa imenom "... .sr.srt" -> traka dobije jezik srp i citljiv naziv
            cp = work / job.opts["_sub_name"]
            sf = Path(spec.get("sub_file") or "").expanduser()
            if job.opts.get("_sub_script") in ("lat", "cyr"):  # srpski CC sa YouTube-a -> izabrano pismo
                cues = parse_srt(sf)
                for c in cues:
                    c["sr"] = core.wrap_lines(core.finalize_script(c["text"], job.opts["_sub_script"], []))
                core.write_srt(cues, cp, key="sr")
            else:
                shutil.copyfile(sf, cp)
            spec["sub_file"] = str(cp)
        sub_file = tb_prepare_subs(job, src, spec, a, work, ffmpeg, out)
        p = tb().plan(str(src), spec, a, ffmpeg, str(out), str(work), sub_file)
        if p.get("errors"):
            raise RuntimeError("; ".join(p["errors"]))
        for w in p.get("warnings", []):
            job.say("upozorenje: " + w)
        job.say("komanda: " + " ".join(p["cmd"])[:1500])
        t0, t1 = hms(spec.get("trim_start")), hms(spec.get("trim_end"))
        dur = (t1 or a.get("duration") or 0) - (t0 or 0)
        label = f"{spec.get('vcodec')} → {spec.get('container')}"
        with slot("encode", job):
            job.set_stage("output", "running", 0, label + "…")
            rc = job.run(p["cmd"], ffmpeg_progress(job, dur, label), cwd=str(work))
        if rc != 0 or not Path(p["out"]).exists():
            raise RuntimeError(f"ffmpeg nije uspeo (rc={rc}) — vidi log")
        job.outputs.insert(0, Path(p["out"]).name)
        job.set_stage("output", "done", info=f"{Path(p['out']).name} · {fmt_size(Path(p['out']).stat().st_size)}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def fmt_dur(sec) -> str:
    if not sec:
        return "?"
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


def fmt_size(b: int) -> str:
    return f"{b / 1073741824:.2f} GB" if b > 1e9 else f"{b / 1048576:.0f} MB"


def choose_files(kind: str) -> list[str]:
    prompt = "Izaberi titl (srt/ass/vtt)" if kind == "sub" else "Izaberi video/audio fajlove"
    script = (f'set fs to choose file with prompt "{prompt}" with multiple selections allowed\n'
              'set out to ""\nrepeat with f in fs\nset out to out & POSIX path of f & linefeed\nend repeat\n'
              'return out')
    p = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return [x for x in p.stdout.splitlines() if x.strip()] if p.returncode == 0 else []


def run_job(job: "Job"):
    job.state, job.error, job.finished = "running", None, None
    job.cancel.clear()
    job.stop_live.clear()
    job.outputs = []
    job.after_jobs = []
    job.after_skipped = []
    for s in STAGES:
        job.stages[s] = {"status": "pending", "pct": None, "info": ""}
    persist_soon()
    try:
        ffmpeg = core.find_ffmpeg()
        {"process": run_process_job, "transcode": run_transcode_job}.get(job.kind, run_download_job)(job, ffmpeg)
        job.state = "done"
        job.say("GOTOVO")
    except Cancelled:
        job.state, job.error = "cancelled", "otkazano"
        for s in STAGES:
            if job.stages[s]["status"] in ("running", "waiting"):
                job.stages[s]["status"] = "cancelled"
    except (Exception, SystemExit) as e:  # noqa: BLE001
        job.state, job.error = "error", str(e)[:500]
        job.say("GRESKA: " + str(e))
        job.say(traceback.format_exc()[-800:])
        for s in STAGES:
            if job.stages[s]["status"] in ("running", "waiting"):
                job.stages[s]["status"] = "error"
    finally:
        job.finished = time.time()
        persist_soon()


def start_job(job: "Job"):
    with _lock:
        JOBS[job.id] = job
    threading.Thread(target=run_job, args=(job,), daemon=True).start()


# ================================================================ biblioteka: batch akcije
LIB_ACTIONS = ("translate", "video", "toolbox", "audio", "open", "lektura", "audio_copy", "transcribe")
AUDIO_PRESETS = ("audio_m4a", "audio_mp3", "audio_flac")
AUDIO_CONTAINERS = ("m4a", "mp3", "flac")
MAX_OPEN = 5


class BadRequest(Exception):
    pass


def open_path(p: Path):
    if os.environ.get("YTA_NO_OPEN"):  # testovi: bez Finder prozora
        return
    subprocess.Popen(["open", str(p)])


def process_job(folder: Path, m: dict, proc: dict) -> "Job":
    return Job(m.get("url") or "", {"proc": proc, "dl": {}, "_title": m.get("title"), "_thumb": m.get("thumbnail"),
                                     "_id": m.get("id")}, kind="process", folder=str(folder))


def lib_item(raw) -> tuple[Path | None, dict | None, str | None]:
    """(folder, manifest, razlog preskakanja)."""
    if not isinstance(raw, str) or not raw.strip():
        return None, None, "prazna putanja"
    f = Path(raw).expanduser()
    if not under_roots(f):
        return None, None, "nije u arhivi"
    m = load_manifest(f)
    if not m:
        return None, None, "nije stavka arhive (nema manifest)"
    return f.resolve(), m, None


def _pick(params: dict, key: str, allowed: tuple, default):
    v = params.get(key)
    v = default if v in (None, "") else v
    if v not in allowed:
        raise BadRequest(f"{key}: nepoznata vrednost {v!r} (dozvoljeno: {', '.join(map(str, allowed))})")
    return v


def _llm_opts(params: dict) -> dict:
    return {k: params[k] for k in ("engine", "model", "review") if params.get(k)}


def lib_translate(folder: Path, m: dict, p: dict) -> "Job | str":
    tgt = p["target"]
    if not p.get("force"):
        if sr_files(folder, tgt, m):
            return (f"već ima srpski prevod ({'ćirilica' if tgt == 'cyr' else 'latinica'})" if tgt in ("lat", "cyr")
                    else f"već ima prevod: {TARGET_NAME.get(tgt, tgt)}")
        if tgt in ("lat", "cyr") and any(is_sr_cc(s) and (folder / s["file"]).is_file() for s in m.get("subs", [])):
            return "već ima srpski titl sa YouTube-a"
    return process_job(folder, m, {"sub_source": "auto-best", "target": tgt, "output": "none",
                                   "keep_source": True, **_llm_opts(p)})


def lib_video(folder: Path, m: dict, p: dict) -> "Job | str":
    if not item_has_video(folder, m):
        src = item_source(folder, m)
        return "nema videa (" + ("izvor nije u arhivi" if not src else "u arhivi je samo audio"
                                 if m.get("kind") == "audio" else "izvor nema video traku") + ")"
    tgt = p["target"]
    proc = {"sub_source": "auto-best", "target": tgt, "output": p["output"], "keep_source": True,
            "encoder": p.get("encoder") or "hevc", "scale": str(p.get("scale") or "src"),
            "audio_out": p.get("audio_out") or "orig", "audio_enc": p.get("audio_enc") or "aac192",
            "subs_out": p.get("subs_out") or "sr+orig", **_llm_opts(p)}
    if p.get("vq"):
        proc["vq"] = int(p["vq"])
    elif proc["encoder"] in ("hevc", "h264"):  # kao do_output: kvalitet iz Podesavanja (default 65)
        proc["vq"] = int(SETTINGS["quality"])
    if tgt in ("lat", "cyr", "el"):  # postojeci prevod se koristi direktno (bez ponovnog prevodjenja)
        have = {folder / s["file"]: s for s in m.get("subs", [])
                if sr_target(s) == tgt or (s.get("kind") == "tr" and s.get("lang") == tgt)}
        e = next((have[f] for f in sr_files(folder, tgt, m) if f in have), None)
        if e:
            proc["sub_source"] = e["key"]
    return process_job(folder, m, proc)


def lib_transcode(folder: Path, m: dict, preset: dict, sub: str, tgt: str | None) -> "Job | str":
    src = item_source(folder, m)
    if not src:
        return "nema izvora u arhivi"
    spec = dict(preset["spec"])
    audio_out = spec.get("vcodec") == "none" or spec.get("container") in AUDIO_CONTAINERS
    if not audio_out and not item_has_video(folder, m):
        return "nema videa (za zvuk izaberi Izvuci audio)"
    spec.update(out_dir=str(folder), suffix="." + preset["id"])
    aud = m.get("audio") or []
    if spec.get("audio_tracks") == "first" and len(aud) > 1:  # originalna traka, ne prva (dub)
        orig = next((a.get("index") for a in aud if a.get("original")), None)
        if isinstance(orig, int) and 0 <= orig < len(aud):
            spec["audio_tracks"] = [orig]
    extra = {}
    if sub != "none" and not audio_out:
        fs = sr_files(folder, tgt, m)
        if not fs:  # srpski CC sa YouTube-a (Prevedi ga preskace) -> koristi se on, u izabranom pismu
            fs = [folder / s["file"] for s in m.get("subs", []) if is_sr_cc(s) and (folder / s["file"]).is_file()]
            if fs:
                extra["_sub_script"] = tgt
        if not fs:
            return f"nema prevoda ({TARGET_NAME.get(tgt, tgt)}) — prvo Prevedi"
        spec.update(sub_src="file", sub_file=str(fs[0]), translate="off", sub_mode=sub)
        extra["_sub_name"] = ("Ελληνικά.el.srt" if tgt == "el" else
                              f"Srpski ({'ćirilica' if tgt == 'cyr' else 'latinica'}).sr.srt")
    return Job("", {"path": str(src), "spec": spec, "_title": f"{m.get('title') or src.name} · {preset['label']}",
                    "_thumb": m.get("thumbnail"), "_id": m.get("id"), "_lib": str(folder), **extra},
               kind="transcode", folder=str(folder))


def lib_transcribe(folder: Path, m: dict, p: dict) -> "Job | str":
    """Transkript (whisper) na originalnom jeziku, bez prevoda: whisper.srt ostaje u subs/."""
    if not p.get("force") and any(s.get("kind") == "whisper" and (folder / s["file"]).is_file()
                                  for s in m.get("subs") or []):
        return "već ima whisper transkript"
    if not item_source(folder, m) or ("audio" in m and not m.get("audio")):
        return "nema izvora zvuka"
    return process_job(folder, m, {"sub_source": "whisper:run", "whisper_lang": p.get("lang"), "target": "none",
                                   "output": "none", "keep_source": True})


def burn_copy_check(preset: dict, sub: str):
    if sub == "burn" and preset["spec"].get("vcodec") == "copy":
        raise BadRequest(f"upečen titl traži rekodiranje videa — preset '{preset['label']}' samo kopira")


# ================================================================ koraci posle preuzimanja (opts.after)
AFTER_STEPS = {"toolbox": ("preset", "sub", "target"), "audio": ("preset",)}
AFTER_LABEL = {"toolbox": "Transkoduj (Toolbox)", "audio": "Izvuci audio"}


def validate_after(after) -> dict | None:
    """opts.after -> {"toolbox": {preset, sub, target}, "audio": {preset}} (samo cekirani) ili None.
    Ista pravila kao library_batch toolbox/audio; greska -> BadRequest (400)."""
    if after in (None, {}, False):
        return None
    if not isinstance(after, dict):
        raise BadRequest("after mora biti objekat")
    bad = [k for k in after if k not in AFTER_STEPS]
    if bad:
        raise BadRequest(f"after: nepoznat ključ {bad[0]!r} (dozvoljeno: {', '.join(AFTER_STEPS)})")
    out: dict = {}
    for step, keys in AFTER_STEPS.items():
        a = after.get(step)
        if a in (None, False, {}):
            continue
        if not isinstance(a, dict):
            raise BadRequest(f"after.{step} mora biti objekat")
        bad = [k for k in a if k not in keys]
        if bad:
            raise BadRequest(f"after.{step}: nepoznat ključ {bad[0]!r} (dozvoljeno: {', '.join(keys)})")
        presets = {x["id"]: x for x in tb().PRESETS}
        try:
            if step == "toolbox":
                pid = _pick(a, "preset", tuple(presets), "arhiva")
                sub = _pick(a, "sub", ("none", "soft", "burn"), "none")
                tgt = _pick(a, "target", ("lat", "cyr", "el"), SETTINGS["script"]) if sub != "none" else None
            else:
                pid, sub, tgt = _pick(a, "preset", AUDIO_PRESETS, "audio_m4a"), "none", None
        except BadRequest as e:
            raise BadRequest(f"after.{step}: {e}") from None
        burn_copy_check(presets[pid], sub)
        out[step] = {"preset": pid, "sub": sub, "target": tgt} if step == "toolbox" else {"preset": pid}
    return out or None


def check_after(opts: dict) -> dict:
    """Normalizuj opts["after"] na mestu (prazno se izbacuje); BadRequest za neispravno."""
    if isinstance(opts, dict) and "after" in opts:
        a = validate_after(opts["after"])
        if a:
            opts["after"] = a
        else:
            opts.pop("after")
    return opts


def run_after_steps(job: "Job", folder: Path):
    """Posle uspesnog preuzimanja: Toolbox pa audio -> novi poslovi (job.after_jobs).
    Preskocen korak ili greska ovde ne obara preuzimanje."""
    after = job.opts.get("after") or {}
    for step in AFTER_STEPS:
        a = after.get(step)
        if not a:
            continue
        try:
            preset = {x["id"]: x for x in tb().PRESETS}.get(a.get("preset"))
            m = load_manifest(folder)
            r = ("nepoznat preset" if not preset else "stavka arhive nema manifest" if not m else
                 lib_transcode(folder, m, preset, (a.get("sub") or "none") if step == "toolbox" else "none",
                               a.get("target")))
            if isinstance(r, Job):
                start_job(r)
                job.after_jobs.append(r.id)
                job.say(f"posle preuzimanja: {preset['label']} → posao {r.id}")
                continue
        except Exception as e:  # noqa: BLE001
            r = str(e)
        job.say(f"{AFTER_LABEL[step]} preskočen: {r}")
        job.after_skipped.append({"step": step, "label": AFTER_LABEL[step], "reason": r})  # vidi se i u Redu
    persist_soon()


def library_batch(body: dict) -> dict:
    """Akcija nad vise stavki Biblioteke -> {"jobs": [id], "skipped": [{"folder", "title", "reason"}]}."""
    act = body.get("action")
    folders = body.get("folders") or []
    p = body.get("params") or {}
    if act not in LIB_ACTIONS:
        raise BadRequest(f"nepoznata akcija {act!r} (dozvoljeno: {', '.join(LIB_ACTIONS)})")
    if not isinstance(folders, list) or not isinstance(p, dict):
        raise BadRequest("folders mora biti lista, params objekat")
    p = dict(p)
    preset = None
    if act == "translate":
        p["target"] = _pick(p, "target", ("lat", "cyr", "el"), SETTINGS["script"])
    elif act == "video":
        p["output"] = _pick(p, "output", ("burn", "soft", "both"), "burn")
        p["target"] = _pick(p, "target", ("lat", "cyr", "el", "none"), SETTINGS["script"])
        _pick(p, "encoder", ("hevc", "h264", "x264", "x265"), "hevc")
        _pick(p, "subs_out", ("sr+orig", "sr", "sr+all"), "sr+orig")
        try:
            int(p.get("vq") or 0)
        except (TypeError, ValueError):
            raise BadRequest("vq mora biti broj") from None
        for k, rx in (("scale", r"src|\d{3,4}"), ("audio_enc", r"copy|aac\d{2,3}")):
            if p.get(k) and not re.fullmatch(rx, str(p[k])):
                raise BadRequest(f"{k}: nepoznata vrednost {p[k]!r}")
    elif act in ("toolbox", "audio"):
        presets = {x["id"]: x for x in tb().PRESETS}
        pid = _pick(p, "preset", tuple(presets) if act == "toolbox" else AUDIO_PRESETS,
                    "arhiva" if act == "toolbox" else "audio_m4a")
        preset = presets[pid]
        p["sub"] = _pick(p, "sub", ("none", "soft", "burn"), "none") if act == "toolbox" else "none"
        # pismo vazi samo uz srpski titl; bez titla (i za audio) se target ne gleda ("none" ne sme dati 400)
        p["target"] = _pick(p, "target", ("lat", "cyr", "el"), SETTINGS["script"]) if p["sub"] != "none" else None
        burn_copy_check(preset, p["sub"])
    elif act == "transcribe":
        lg = str(p.get("lang") or "").strip()
        if lg in ("", "auto"):
            lg = None
        elif not re.fullmatch(r"[a-z]{2,3}", lg):
            raise BadRequest(f"lang: nepoznata vrednost {lg!r} (npr. sr, en, el ili auto)")
        p["lang"], p["force"] = lg, as_bool(p.get("force"))
    elif act == "lektura":
        p["target"] = _pick(p, "target", ("lat", "cyr", "el"), SETTINGS["script"])
        p["engine"] = _pick(p, "engine", ("claude", "gemini"), "claude")
        mdl = p.get("model") or ("sonnet" if p["engine"] == "claude" else core.CLOUD_DEFAULT["gemini"])
        if (p["engine"] == "claude" and mdl not in ("sonnet", "opus")) or \
                (p["engine"] == "gemini" and not re.fullmatch(r"gemini-[A-Za-z0-9._-]+", str(mdl))):
            raise BadRequest(f"model: nepoznata vrednost {mdl!r} za {p['engine']}")
        p["model"] = mdl
    jobs, skipped, opened, seen = [], [], [], set()
    for raw in folders:
        folder, m, why = lib_item(raw)
        if folder is not None:
            if str(folder) in seen:
                continue
            seen.add(str(folder))
        if not why:
            if act == "open":
                if len(opened) >= MAX_OPEN:
                    why = f"najviše {MAX_OPEN} foldera odjednom"
                else:
                    open_path(folder)
                    opened.append(str(folder))
                    continue
            elif act == "translate":
                why = lib_translate(folder, m, p)
            elif act == "video":
                why = lib_video(folder, m, p)
            elif act == "lektura":
                why = lib_lektura(folder, m, p)
            elif act == "audio_copy":
                why = lib_audio_copy(folder, m)
            elif act == "transcribe":
                why = lib_transcribe(folder, m, p)
            else:
                why = lib_transcode(folder, m, preset, p["sub"], p["target"])
        if isinstance(why, Job):
            start_job(why)
            jobs.append(why.id)
        else:
            skipped.append({"folder": str(folder or raw), "title": (m or {}).get("title"), "reason": why})
    out = {"jobs": jobs, "skipped": skipped}
    if act == "open":
        out["opened"] = opened
    return out


# ================================================================ v3: editor titla + ispravke za obuku
CORR_P = DATA / "corrections.jsonl"
PL_DIR = DATA / "playlists"
PROBE_CACHE_P = DATA / "probe_cache.json"
_CORR_LOCK = threading.Lock()
MEDIA_TYPES = {".mkv": "video/x-matroska", ".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm",
               ".mov": "video/quicktime", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".opus": "audio/ogg",
               ".ogg": "audio/ogg", ".flac": "audio/flac", ".mka": "audio/x-matroska", ".wav": "audio/wav",
               ".aac": "audio/aac"}
MAX_EDIT_CUES = 20000
PAIR_TYPE = {"sr": "tr", "tr": "tr", "whisper": "stt", "auto": "stt"}  # kind stavke -> tip para u corrections.jsonl


class NotFound(Exception):
    pass


def item_folder(raw) -> Path:
    """Folder stavke iz zahteva: mora biti u root-ovima biblioteke (vraca resolve-ovanu putanju)."""
    if not isinstance(raw, str) or not raw.strip() or "\0" in raw:
        raise BadRequest("nedostaje folder stavke")
    f = Path(raw).expanduser()
    if not under_roots(f) or not f.is_dir():
        raise BadRequest("folder nije u arhivi")
    f = f.resolve()
    if f in lib_roots():  # stavka je folder UNUTAR root-a, ne sam root (inace cela arhiva kroz jedan "folder")
        raise BadRequest("folder nije stavka arhive")
    return f


def rel_in(folder: Path, rel) -> Path | None:
    """Putanja unutar foldera stavke (bez ../ i apsolutnih izlaza)."""
    if not isinstance(rel, str) or not rel.strip() or "\0" in rel:
        return None
    p = (folder / rel).resolve()
    return p if folder in p.parents else None


def _srt_ms(s: str) -> int:
    h, mnt, rest = s.strip().split(":")
    sec, ms = rest.replace(".", ",").split(",")
    return ((int(h) * 60 + int(mnt)) * 60 + int(sec)) * 1000 + int(ms[:3].ljust(3, "0"))


def srt_raw(path: Path) -> list[dict]:
    """SRT tacno kako je u fajlu (redosled, vreme, prelom reda), za editor: [{i, start, end, text}] (ms)."""
    txt = path.read_text(encoding="utf-8-sig", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    cues = []
    for block in re.split(r"\n\s*\n", txt.strip()):
        lines = block.strip().split("\n")
        for j, ln in enumerate(lines):
            if "-->" in ln:
                a, b = [x.strip() for x in ln.split("-->", 1)]
                try:
                    start, end = _srt_ms(a), _srt_ms(b.split()[0])
                except (ValueError, IndexError):
                    break
                text = "\n".join(x.strip() for x in lines[j + 1:] if x.strip())
                if text:
                    cues.append({"i": len(cues), "start": start, "end": end, "text": text})
                break
    return cues


def _one_line(t: str) -> str:
    return " ".join((t or "").split())


def key_engine_model(e: dict) -> tuple[str | None, str | None]:
    """engine/model prevoda: iz polja stavke, inace iz kljuca 'sr:<t>:<engine>:<model>[:rev|:lektura]:<src>'."""
    if e.get("engine") or e.get("model"):
        return e.get("engine"), e.get("model")
    k = e.get("key") or ""
    if e.get("src") and k.endswith(":" + e["src"]):
        k = k[:-len(e["src"]) - 1]
    parts = k.split(":")[2:] if k.startswith(("sr:", "tr:")) else []
    while parts and parts[-1] in ("rev", "lektura"):
        parts.pop()
    if not parts or parts[0] == "rebuilt":  # manifest obnovljen iz fajlova: model nepoznat
        return None, None
    if parts[0] in ENGINES:
        return parts[0], ":".join(parts[1:]) or None
    return None, ":".join(parts)


def entry_lang(e: dict) -> str:
    t = sr_target(e)
    return f"sr-{t}" if t else (e.get("lang") or "und")


def history_backup(folder: Path, p: Path) -> str:
    """Prethodna verzija titla -> .yta/history/<ime>.<YYYYmmdd-HHMMSS>.srt (rel putanja)."""
    h = mdir(folder) / "history"
    h.mkdir(parents=True, exist_ok=True)
    dst = uniq_path(h / f"{p.stem}.{time.strftime('%Y%m%d-%H%M%S')}.srt")
    shutil.copy2(p, dst)
    return str(dst.relative_to(folder))


def write_srt_atomic(cues: list[dict], p: Path, key: str = "text"):
    tmp = p.with_name(p.name + ".tmp")
    core.write_srt(cues, tmp, key=key)
    os.replace(tmp, p)


def src_cues_for(folder: Path, m: dict, entry: dict | None) -> tuple[list[dict] | None, dict | None]:
    """Titl iz kog je prevod nastao (entry.src), parsiran kao pri prevodu (isti broj titlova)."""
    if not entry or not entry.get("src"):
        return None, None
    se = next((s for s in m.get("subs") or [] if s.get("key") == entry["src"]), None)
    if not se or not (folder / se["file"]).is_file():
        return None, se
    return [{"i": n, "start": c["start"], "end": c["end"], "text": c["text"]}
            for n, c in enumerate(parse_srt(folder / se["file"]))], se


def subs_get(f: str, rel: str) -> dict:
    folder = item_folder(f)
    p = rel_in(folder, rel)
    if not p or p.suffix.lower() != ".srt" or not p.is_file():
        raise NotFound("titl ne postoji")
    m = load_manifest(folder) or {}
    rel_n = str(p.relative_to(folder))
    entry = next((s for s in m.get("subs") or [] if s.get("file") == rel_n), None)
    src, src_entry = src_cues_for(folder, m, entry)
    return {"entry": entry, "cues": srt_raw(p), "src": src, "src_entry": src_entry,
            "media": m.get("source") if item_source(folder, m) else None}


def _cue_in(n: int, c) -> dict:
    if not isinstance(c, dict):
        raise BadRequest(f"titl {n}: očekujem objekat")
    try:
        start, end = int(c.get("start")), int(c.get("end"))
    except (TypeError, ValueError):
        raise BadRequest(f"titl {n}: start/end moraju biti brojevi (ms)") from None
    if start < 0 or end < start:
        raise BadRequest(f"titl {n}: neispravno vreme ({start} → {end})")
    text = c.get("text")
    if not isinstance(text, str):
        raise BadRequest(f"titl {n}: text mora biti tekst")
    i = c.get("i")
    i = int(i) if isinstance(i, int) and not isinstance(i, bool) and i >= 0 else None
    text = "\n".join(x.strip() for x in text.replace("\r", "").split("\n") if x.strip())
    return {"i": i, "start": start, "end": end, "text": text}


def subs_save(body: dict) -> dict:
    folder = item_folder(body.get("folder"))
    if not load_manifest(folder):
        raise BadRequest("nije stavka arhive (nema manifest)")
    p = rel_in(folder, body.get("file"))
    if not p or p.suffix.lower() != ".srt" or not p.is_file():
        raise BadRequest("titl ne postoji")
    raw = body.get("cues")
    if not isinstance(raw, list) or len(raw) > MAX_EDIT_CUES:
        raise BadRequest("cues mora biti lista")
    new = [c for c in (_cue_in(n, c) for n, c in enumerate(raw, 1)) if c["text"]]  # prazan tekst = obrisan titl
    if not new:
        raise BadRequest("titl ne može ostati prazan — bar jedan red mora imati tekst")
    rel_n = str(p.relative_to(folder))
    ck = lambda c: (c["start"], c["end"], c["text"])  # noqa: E731
    with MLOCK:
        m = load_manifest(folder) or {}
        entry = next((s for s in m.get("subs") or [] if s.get("file") == rel_n), None)
        ptype = PAIR_TYPE.get((entry or {}).get("kind"))  # tr: prevod, stt: whisper/auto; ostalo bez para
        old = srt_raw(p)
        if [ck(c) for c in new] == [ck(o) for o in old]:
            return {"ok": True, "changed": 0, "pairs_saved": 0, "backup": None}
        uses: dict[int, int] = {}
        for c in new:
            if c["i"] is not None and c["i"] < len(old):
                uses[c["i"]] = uses.get(c["i"], 0) + 1
        changed = len(old) - len(uses)  # obrisani
        pairs, first = [], set()
        for c in new:
            i = c["i"] if c["i"] is not None and c["i"] < len(old) and c["i"] not in first else None
            o = old[i] if i is not None else None  # ponovljen i (kopija/podela reda) = nov titl
            if i is not None:
                first.add(i)
            if o is None or ck(o) != ck(c):
                changed += 1
            # par za obuku samo za cistu izmenu teksta jedne linije (ne za podelu reda)
            if o is not None and ptype and uses[i] == 1 and _one_line(o["text"]) != _one_line(c["text"]):
                pairs.append((i, _one_line(o["text"]), _one_line(c["text"])))
        changed = max(changed, 1)  # npr. samo promenjen redosled
        src, src_e = src_cues_for(folder, m, entry) if pairs and ptype == "tr" else (None, None)
        src = src or []
        if src and len(src) != len(old):  # posle brisanja titla indeks vise ne pogadja izvor -> po vremenu
            src = align_by_time(src, old)
        backup = history_backup(folder, p)
        write_srt_atomic(new, p)
        if entry:
            def fn(mm):
                for s in mm.get("subs") or []:
                    if s.get("file") == rel_n:
                        s["count"], s["edited"] = len(new), int(time.time())
            update_manifest(folder, fn)
    if pairs:
        if ptype == "tr":
            eng, mdl = key_engine_model(entry)
            lang = entry_lang(entry)
            sl = base_lang((src_e or {}).get("lang"))
            src_lang = sl if src_e and sl != "und" else None
        else:  # stt: transkript / automatski titl koji je korisnik ispravio
            eng, mdl = ("whisper" if entry.get("kind") == "whisper" else None), None
            lang, src_lang = base_lang(entry.get("lang")), None
        ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        rows = [{"ts": ts, "video_id": m.get("id"), "folder": str(folder), "file": rel_n, "lang": lang,
                 "engine": eng, "model": mdl,
                 "src": (_one_line(src[i]["text"]) or None) if ptype == "tr" and i < len(src) else None,
                 "before": b, "after": a, "type": ptype, "channel": m.get("channel") or None,
                 "src_lang": src_lang} for i, b, a in pairs]
        with _CORR_LOCK:
            with CORR_P.open("a", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return {"ok": True, "changed": changed, "pairs_saved": len(pairs), "backup": backup,
            "memory": {"rules_active": mem_rules_active()}}


def mem_rules_active() -> int | None:
    """Broj aktivnih pravila memorije (None ako memorija nije citljiva — cuvanje titla ne pada zbog nje)."""
    try:
        return memlib.Memory.load(DATA).stats()["rules_active"]
    except Exception:  # noqa: BLE001
        return None


def corrections_stats() -> dict:
    total, by = 0, {}
    try:
        with CORR_P.open("rb") as fh:
            for bl in fh:
                try:  # red sa losim UTF-8 bajtom ili pokvaren JSON se preskace (kao u memory.py)
                    d = json.loads(bl.decode("utf-8"))
                except ValueError:
                    continue
                if not isinstance(d, dict):
                    continue
                total += 1
                by[d.get("lang") or "?"] = by.get(d.get("lang") or "?", 0) + 1
    except OSError:
        pass
    return {"total": total, "by_lang": by, "path": str(CORR_P), "rules_active": mem_rules_active()}


def memory_dump(recent) -> dict:
    """GET /api/memory: Memory.dump + podesavanja iz panela (SETTINGS je novije od settings.json)."""
    try:
        recent = 50 if recent in (None, "") else int(recent)
    except (TypeError, ValueError):
        raise ValueError("recent mora biti broj") from None
    d = memlib.Memory.load(DATA).dump(recent)
    d["settings"] = {k: as_bool(SETTINGS.get(k, True)) for k in ("mem_tr", "mem_stt")}
    return d


def memory_post(what: str, body: dict) -> dict:
    """POST /api/memory/<glossary|rule|correction>; ValueError -> 400, KeyError -> 404."""
    mem = memlib.Memory.load(DATA)
    if what == "glossary":
        return {"ok": True, "glossary": mem.glossary_op(body.get("op"), body.get("entry"))}
    rid = body.get("id")
    if not isinstance(rid, str) or not rid.strip():
        raise ValueError("nedostaje id " + ("pravila" if what == "rule" else "ispravke"))
    if what == "rule":
        return {"ok": True, "rule": mem.set_rule_state(rid, body.get("state"))}
    return {"ok": True, "correction": mem.set_correction_disabled(rid, body.get("disabled"))}


def media_file(f: str, rel: str) -> Path:
    folder = item_folder(f)
    p = rel_in(folder, rel)
    if not p or not p.is_file() or p.suffix.lower() not in MEDIA_TYPES:
        raise NotFound("fajl ne postoji")
    return p


# ================================================================ v3: lektura postojeceg prevoda
def is_lektura(s: dict) -> bool:
    return ":lektura:" in (s.get("key") or "")


def target_entries(folder: Path, m: dict, target: str) -> tuple[list[dict], list[dict]]:
    """(prevodi bez lekture, lekture) za target, najnoviji prvi (samo stavke manifesta sa fajlom)."""
    hit = [s for s in m.get("subs") or [] if (folder / s.get("file", "")).is_file() and s.get("file")
           and (sr_target(s) == target if target in ("lat", "cyr") else s.get("kind") == "tr" and s.get("lang") == target)]
    hit.sort(key=lambda s: (folder / s["file"]).stat().st_mtime, reverse=True)
    return [s for s in hit if not is_lektura(s)], [s for s in hit if is_lektura(s)]


def lib_lektura(folder: Path, m: dict, p: dict) -> "Job | str":
    drafts, lek = target_entries(folder, m, p["target"])
    if not drafts and not lek:
        return "nema prevoda za lekturu"
    if lek and not p.get("force"):
        return "već ima lekturu"
    draft = (drafts or lek)[0]
    return process_job(folder, m, {"action": "lektura", "target": p["target"], "engine": p["engine"],
                                   "model": p["model"], "draft": draft["key"]})


def align_by_time(src: list[dict], dst: list[dict]) -> list[dict]:
    """Izvor sa drugacijim brojem titlova: za svaki red prevoda izvor sa najvecim preklapanjem."""
    out = []
    for c in dst:
        best = max(src, key=lambda s: min(s["end"], c["end"]) - max(s["start"], c["start"]), default=None)
        out.append(best if best and min(best["end"], c["end"]) > max(best["start"], c["start"]) else {"text": ""})
    return out


def run_lektura(job: "Job", folder: Path, proc: dict):
    target, engine, model = proc.get("target") or "lat", proc.get("engine") or "claude", proc.get("model") or "sonnet"
    m = load_manifest(folder) or {}
    job.set_stage("subs", "running", None, "učitavam prevod i izvor…")
    by = {s.get("key"): s for s in m.get("subs") or []}
    draft = by.get(proc.get("draft"))
    if not draft or not (folder / draft["file"]).is_file():
        drafts, lek = target_entries(folder, m, target)
        draft = (drafts or lek or [None])[0]
        if not draft:
            raise RuntimeError("nema prevoda za lekturu")
    dcues = parse_srt(folder / draft["file"])
    if not dcues:
        raise RuntimeError(f"prevod je prazan: {draft['file']}")
    src_e = by.get(draft.get("src")) if draft.get("src") else None
    scues = parse_srt(folder / src_e["file"]) if src_e and (folder / src_e["file"]).is_file() else []
    if scues and len(scues) != len(dcues):
        job.say(f"izvor ({len(scues)}) i prevod ({len(dcues)}) nemaju isti broj titlova — poravnanje po vremenu")
        scues = align_by_time(scues, dcues)
    job.set_stage("subs", "done", info=f"{draft.get('name') or draft['file']} · {len(dcues)} titlova"
                                        + (f" · izvor: {src_e.get('name')}" if scues else " · bez izvora"))
    info = item_info(folder)
    title = (m.get("title") or info.get("title") or "") + (" — " + (info.get("description") or "")[:600]
                                                           if info.get("description") else "")
    who = "Claude" if engine == "claude" else "Gemini"
    label = f"{who} {model.replace('gemini-', '')}"
    args = SimpleNamespace(engine=engine, model=model, batch=core.LEKTURA_BLOCK, cancelled=job.cancel.is_set,
                           status=lambda t: job.set_stage("translate", "running", None, f"{label}: {t}"))
    mem_attach(job, args, target, m.get("channel") or info.get("channel") or info.get("uploader"))
    llm = core.make_llm(engine, model)
    with slot("translate", job):
        job.set_stage("translate", "running", 0, f"{label}: lektura {len(dcues)} titlova…")

        def prog(done, total):
            job.set_stage("translate", "running", round(100 * done / max(1, total)), f"{label}: lektura blok {done}/{total}")
        try:
            lines = core.review_translation(scues, dcues, title, target, llm, args, progress=prog)
        except Exception:
            if job.cancel.is_set():
                raise Cancelled() from None
            raise
    mem_say(job, args, "lektura")
    act = llm.active() if callable(getattr(llm, "active", None)) else None  # Gemini lanac: kvota -> CF/Gemma
    fallback = f"{act[0]}:{act[1]}" if act and act[0] != engine else None
    if fallback:
        job.say(f"⚠ {who} nije bio dostupan do kraja — deo lekture uradio je {fallback}")
    changed = sum(1 for c, t in zip(dcues, lines) if _one_line(c["text"]) != _one_line(t))
    out = [{"start": c["start"], "end": c["end"], "text": core.wrap_lines(t)} for c, t in zip(dcues, lines)]
    srckey = draft.get("src") or draft["key"]
    srck = re.sub(r"[^A-Za-z0-9_-]+", "_", srckey)
    mtag = re.sub(r"[^A-Za-z0-9_-]+", "_", model)
    if target in ("lat", "cyr"):
        rel = f"subs/sr-{target}.{srck}.{engine}-lektura_{mtag}.srt"
        e = {"key": f"sr:{target}:{engine}:{model}:lektura:{srckey}", "kind": "sr", "lang": "sr",
             "name": f"Srpski {'ćirilica' if target == 'cyr' else 'latinica'} — {who} lektura"}
    else:
        rel = f"subs/{target}.{srck}.{engine}-lektura_{mtag}.srt"
        e = {"key": f"tr:{target}:{engine}:{model}:lektura:{srckey}", "kind": "tr", "lang": target,
             "name": f"{core.TARGETS.get(target, (target, target, target))[2]} — {who} lektura"}
    with MLOCK:
        if (folder / rel).is_file():
            job.say(f"prethodna lektura sačuvana: {history_backup(folder, folder / rel)}")
        write_srt_atomic(out, folder / rel)
    upsert_sub(folder, {**e, "file": rel, "count": len(out), "src": draft.get("src"), "draft": draft["key"],
                        "engine": engine, "model": model, "changed": changed, "date": time.strftime("%Y-%m-%d"),
                        **({"fallback": fallback} if fallback else {})})
    job.main_file = rel
    job.say(f"lektura: izmenjeno {changed}/{len(dcues)} linija → {rel}")
    job.set_stage("translate", "done", info=f"{label} · izmenjeno {changed}/{len(dcues)} linija"
                                             + (f" · deo: {fallback}" if fallback else ""))
    job.set_stage("output", "skipped", info="lektura je nov prevod u subs/")


# ================================================================ v3: audio iz postojece arhive
def audio_copy_ext(codec: str) -> str:
    return {"aac": "m4a", "opus": "opus"}.get(codec or "", "mka")


def flac_picture_b64(img: Path) -> str:
    """Omot za Ogg Opus: FLAC METADATA_BLOCK_PICTURE (base64) kao vorbis komentar (ffmpeg ogg ne prima sliku)."""
    import base64
    import struct
    data, mime = img.read_bytes(), b"image/jpeg"
    blk = struct.pack(">II", 3, len(mime)) + mime + struct.pack(">IIIIII", 0, 0, 0, 0, 0, len(data)) + data
    return base64.b64encode(blk).decode()


def lib_audio_copy(folder: Path, m: dict) -> "Job | str":
    src = item_source(folder, m)
    if not src:
        return "nema izvora"
    if not m.get("audio") and not probe_audio_streams(core.find_ffmpeg(), src):
        return "izvor nema zvuk"
    return process_job(folder, m, {"action": "audio_copy"})


def run_audio_copy(job: "Job", folder: Path, ffmpeg: str):
    m = load_manifest(folder) or {}
    src = item_source(folder, m)
    if not src:
        raise RuntimeError("nema izvora")
    job.set_stage("subs", "skipped", info="—")
    job.set_stage("translate", "skipped", info="—")
    streams = probe_audio_streams(ffmpeg, src)
    if not streams:
        raise RuntimeError("izvor nema zvuk")
    aud = m.get("audio") or []
    n = next((a.get("index") for a in aud if a.get("original") and isinstance(a.get("index"), int)), 0)
    n = n if 0 <= n < len(streams) else 0
    codec = streams[n].get("codec_name") or ""
    ext = audio_copy_ext(codec)
    stem = item_stem(m)
    out = folder / f"{stem}.audio-orig.{ext}"
    tmp = folder / f"{stem}.audio-orig.part.{ext}"
    lang = next((a.get("lang") for a in aud if a.get("index") == n), None)
    d = m.get("upload_date") or ""
    md = []
    for k, v in (("title", m.get("title")), ("artist", m.get("channel")),
                 ("date", f"{d[:4]}-{d[4:6]}-{d[6:8]}" if re.fullmatch(r"\d{8}", d) else d)):
        if v:
            md += ["-metadata", f"{k}={v}"]
    if lang and lang != "und":
        md += ["-metadata:s:a:0", f"language={iso3(lang)}"]
    base = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src)]
    thumb = mdir(folder) / "thumb.jpg"
    plain = base + ["-map", f"0:a:{n}", "-map_chapters", "-1", "-c", "copy", *md]
    tries = []
    if thumb.is_file() and ext == "m4a":
        tries.append(base + ["-i", str(thumb), "-map", f"0:a:{n}", "-map", "1:v", "-map_chapters", "-1", "-c", "copy",
                             "-disposition:v:0", "attached_pic", *md, "-f", "mp4"])
    elif thumb.is_file() and ext == "opus" and thumb.stat().st_size < 600_000:  # base64 ide kao argument
        tries.append(plain + ["-metadata:s:a:0", f"METADATA_BLOCK_PICTURE={flac_picture_b64(thumb)}"])
    elif thumb.is_file():
        tries.append(plain + ["-attach", str(thumb), "-metadata:s:t", "mimetype=image/jpeg",
                              "-metadata:s:t", "filename=cover.jpg"])
    tries.append(plain)
    job.set_stage("output", "running", None, f"izvlačim originalnu audio traku ({codec} → .{ext}, bez rekodiranja)…")
    for k, cmd in enumerate(tries):
        tmp.unlink(missing_ok=True)
        if job.run(cmd + [str(tmp)]) == 0 and tmp.is_file() and tmp.stat().st_size > 0:
            break
        if k + 1 < len(tries):
            job.say("omot nije podržan u ovom formatu — pravim bez omota")
    else:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("izvlačenje audio trake nije uspelo — vidi log")
    os.replace(tmp, out)
    job.outputs.append(out.name)
    add_output(folder, out.name)
    job.set_stage("output", "done", info=f"{out.name} · {fmt_size(out.stat().st_size)}")


# ================================================================ v3: plejliste (snimak, stanje, album)
def pl_path(pid: str) -> Path:
    return PL_DIR / (re.sub(r"[^A-Za-z0-9_-]+", "_", pid)[:120] + ".json")


def load_playlist(pid: str) -> dict | None:
    p = pl_path(pid)
    d = core.load_json(p) if p.is_file() else {}
    return d if d.get("id") else None


_TAB_URL = re.compile(r"youtube\.com/(?:@[^/?#]+|channel/[^/?#]+|c/[^/?#]+|user/[^/?#]+)"
                      r"/(videos|shorts|streams|podcasts|releases)/?(?:[?#]|$)", re.I)


def snapshot_id(res: dict) -> str | None:
    """Kanal i njegov tab (@x vs @x/videos) imaju isti yt-dlp id -> tab ide u kljuc snimka,
    inace bi provera samo /videos oznacila shorts/live kao sklonjene."""
    pid = res.get("id")
    t = _TAB_URL.search(res.get("url") or "")
    return f"{pid}_{t.group(1).lower()}" if pid and t else pid


def snapshot_playlist(res: dict, partial: bool) -> tuple[set | None, list]:
    """Upisi DATA/playlists/<id>.json; vraca (ID-jevi prethodnog snimka ili None, removed)."""
    pid = snapshot_id(res)
    if not pid:
        return None, []
    PL_DIR.mkdir(parents=True, exist_ok=True)
    with _lock:
        prev = load_playlist(pid) or {}
        prev_ids = {e["id"] for e in prev.get("entries") or [] if e.get("id")} if prev else None
        now = int(time.time())
        seen, entries = set(), []
        for e in res.get("entries") or []:
            if e["id"] not in seen:
                seen.add(e["id"])
                entries.append({"id": e["id"], "title": e.get("title"), "duration": e.get("duration"),
                                "pos": len(entries) + 1})
        removed = [r for r in prev.get("removed") or [] if r.get("id") not in seen]  # vratio se -> nije sklonjen
        if partial and prev:  # tab kanala nije ucitan -> ne menjaj snimak (lazno "sklonjeni")
            return prev_ids, removed
        known = {r.get("id") for r in removed}
        for e in prev.get("entries") or []:
            if e.get("id") and e["id"] not in seen and e["id"] not in known:
                removed.append({"id": e["id"], "title": e.get("title"), "last_seen": prev.get("fetched_at")})
        core.save_json(pl_path(pid), {
            "id": pid, "list_id": res.get("id"), "title": res.get("title"), "url": res.get("url"),
            "uploader": res.get("uploader"),
            "fetched_at": now, "entries": entries,
            "history": (prev.get("history") or [])[-199:] + [{"fetched_at": now, "count": len(entries)}],
            "removed": removed})
    return prev_ids, removed


def has_media(items: list[dict] | None) -> bool:
    return any(i.get("source") for i in items or [])


def playlists_list() -> list[dict]:
    cat = catalog_index()
    out = []
    for p in PL_DIR.glob("*.json") if PL_DIR.is_dir() else []:
        d = core.load_json(p)
        if not d.get("id"):
            continue
        ids = [e.get("id") for e in d.get("entries") or []]
        have = sum(1 for i in ids if has_media(cat.get(i)))
        out.append({"id": d["id"], "title": d.get("title"), "count": len(ids), "have": have,
                    "missing": len(ids) - have, "removed": len(d.get("removed") or []),
                    "fetched_at": d.get("fetched_at"), "url": d.get("url"), "uploader": d.get("uploader")})
    out.sort(key=lambda x: x.get("fetched_at") or 0, reverse=True)
    return out


def batch_opts(body: dict, default_recipe: str | None = None) -> dict:
    """opts iz tela ({"opts":{dl,proc}}) ili iz recepta ({"recipe":"arhiva"})."""
    opts = body.get("opts") or {}
    if not isinstance(opts, dict):
        raise BadRequest("opts mora biti objekat")
    rid = body.get("recipe") or (default_recipe if not opts else None)
    if not opts and rid:
        r = next((x for x in recipes() if x["id"] == rid), None)
        if not r:
            raise BadRequest(f"nepoznat recept {rid!r}")
        opts = {"dl": r["dl"], "proc": r["proc"]}
    return opts


def playlist_missing(pid: str, body: dict) -> dict:
    d = load_playlist(pid)
    if not d:
        raise NotFound("nema snimka plejliste (prvo je proveri u kartici Preuzmi)")
    rid = SETTINGS.get("recipe") if SETTINGS.get("recipe") in RECIPE_IDS else "samo"
    opts = check_after(batch_opts(body, rid))
    cat = catalog_index()
    pl = {"id": d.get("list_id") or d["id"], "title": d.get("title")}
    made = []
    for e in sorted(d.get("entries") or [], key=lambda e: e.get("pos") or 0):
        vid = e.get("id")
        if not vid or has_media(cat.get(vid)):
            continue
        o = item_opts(opts, None)
        o.update(_title=e.get("title") or vid, _thumb=f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg", _id=vid,
                 _live=None, _playlist=pl)
        j = Job(f"https://www.youtube.com/watch?v={vid}", o)
        start_job(j)
        made.append(j.id)
    return {"jobs": made}


def try_folder_lock(folder: Path) -> threading.Lock | None:
    with _lock:
        lk = FOLDER_LOCKS.setdefault(str(Path(folder).resolve()), threading.Lock())
    return lk if lk.acquire(blocking=False) else None


def ff_tags(ffmpeg: str, path: Path) -> dict:
    """Tagovi fajla: format + audio trake (ogg/opus ih drzi po traci)."""
    p = subprocess.run([ffprobe_bin(ffmpeg), "-v", "error", "-select_streams", "a", "-show_entries",
                        "format_tags:stream_tags", "-of", "json", str(path)], capture_output=True, text=True)
    try:
        d = json.loads(p.stdout or "{}")
    except ValueError:
        return {}
    out = {}
    for t in [(d.get("format") or {}).get("tags") or {}] + [s.get("tags") or {} for s in d.get("streams") or []]:
        for k, v in t.items():
            out.setdefault(k.lower(), v)
    return out


def tag_album(ffmpeg: str, folder: Path, src: Path, tags: dict) -> str | None:
    """Tagovi bez rekodiranja: ffmpeg -c copy u privremeni fajl pa zamena. Vraca razlog greske ili None."""
    lk = try_folder_lock(folder)
    if not lk:
        return "stavka je zauzeta (posao u toku)"
    try:
        have = ff_tags(ffmpeg, src)
        md = {k: v for k, v in tags.items() if v and not (k == "artist" and have.get("artist"))}
        meta = sum((["-metadata", f"{k}={v}"] for k, v in md.items()), [])
        if src.suffix.lower() in (".opus", ".ogg"):  # ogg: vorbis komentari su na traci
            meta += sum((["-metadata:s:a:0", f"{k}={v}"] for k, v in md.items()), [])
        tmp = src.with_name(f"{src.stem}.tagtmp{src.suffix}")
        err = ""
        try:  # MKV/MKA: omot ostaje prilog (sa '-map 0' bi postao druga video traka)
            first = copy_maps(ffmpeg, src, mdir(folder) / "work" / "tag")
        except RuntimeError:
            first = (["-map", "0"], [])
        for maps, att in (first, (["-map", "0:a"], [])):  # ogg/opus ne prima omot -> samo zvuk
            tmp.unlink(missing_ok=True)
            p = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), *maps,
                                "-c", "copy", *att, *meta, str(tmp)], capture_output=True, text=True)
            if p.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 0:
                os.replace(tmp, src)
                return None
            err = ((p.stderr or "").strip().splitlines() or [""])[-1]
        tmp.unlink(missing_ok=True)
        return f"upis tagova nije uspeo: {err[:200]}"
    finally:
        shutil.rmtree(mdir(folder) / "work" / "tag", ignore_errors=True)
        lk.release()


def playlist_album(pid: str, body: dict) -> dict:
    d = load_playlist(pid)
    if not d:
        raise NotFound("nema snimka plejliste")
    want_m3u, want_tags = as_bool(body.get("m3u", True)), as_bool(body.get("tags", True))
    ffmpeg = core.find_ffmpeg()
    cat = catalog_index()
    entries = sorted(d.get("entries") or [], key=lambda e: e.get("pos") or 0)
    total = len(entries)
    tagged, skipped, tracks = 0, [], []
    for e in entries:
        items = cat.get(e.get("id")) or []
        it = next((i for i in items if i.get("kind") == "audio" and i.get("source")), None)
        if not it:
            skipped.append({"id": e.get("id"), "title": e.get("title"),
                            "reason": "nije u biblioteci" if not items else
                            "nije samo audio" if has_media(items) else "izvor nije u arhivi"})
            continue
        folder = Path(it["folder"])
        src = folder / it["source"]
        if want_tags:
            why = tag_album(ffmpeg, folder, src, {"album": d.get("title"), "track": f"{e.get('pos')}/{total}",
                                                   "artist": it.get("channel"), "album_artist": d.get("uploader")})
            if why:
                skipped.append({"id": e.get("id"), "title": it.get("title"), "reason": why})
            else:
                tagged += 1
        tracks.append((e, it, src))
    m3u = None
    if want_m3u and tracks:
        parents = {s.parent.parent for _, _, s in tracks}
        base = parents.pop() if len(parents) == 1 else Path(SETTINGS["out_dir"]).expanduser()
        m3u = base / f"{core.safe_name(d.get('title') or d['id'])}.m3u8"
        lines = ["#EXTM3U"]
        for e, it, s in tracks:
            dur = int(round(float(it.get("duration") or e.get("duration") or 0))) or -1
            lines += [f"#EXTINF:{dur},{it.get('title') or e.get('title') or e.get('id')}", os.path.relpath(s, base)]
        m3u.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"tagged": tagged, "m3u": str(m3u) if m3u else None, "skipped": skipped}


# ================================================================ v3: zdravlje kataloga
ID_IN_NAME = re.compile(r"\[([A-Za-z0-9_-]{11})\]")
MEDIA_V = (".mkv", ".mp4", ".webm", ".mov", ".m4v")
MEDIA_A = (".m4a", ".mp3", ".opus", ".ogg", ".flac", ".mka", ".aac", ".wav")
_PROBE_CACHE: dict = {}
_PROBE_LOCK = threading.Lock()


def media_duration(path: Path, ffprobe: str) -> float | None:
    """ffprobe trajanje, kes po putanji+mtime+velicini (DATA/probe_cache.json)."""
    st = path.stat()
    key = f"{path}|{st.st_mtime_ns}|{st.st_size}"
    with _PROBE_LOCK:
        if not _PROBE_CACHE:
            _PROBE_CACHE.update(core.load_json(PROBE_CACHE_P) or {"_": None})
        if key in _PROBE_CACHE:
            return _PROBE_CACHE[key]
    try:
        p = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None  # ne kesira se: sledeca provera pokusa ponovo
    try:
        dur = float(p.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        dur = None
    with _PROBE_LOCK:
        _PROBE_CACHE[key] = dur
    return dur


def _save_probe_cache(keep: set[str]):
    with _PROBE_LOCK:
        for k in [k for k in _PROBE_CACHE if k not in keep and k != "_"]:
            _PROBE_CACHE.pop(k)
        try:
            core.save_json(PROBE_CACHE_P, _PROBE_CACHE)
        except OSError:
            pass


def scan_orphans() -> list[dict]:
    roots = lib_roots()
    rs = {str(r) for r in roots}
    out, seen = [], set()
    for r in roots:
        if not r.is_dir():
            continue
        for d in sorted(r.iterdir()):
            if not d.is_dir() or str(d.resolve()) in rs or str(d.resolve()) in seen:
                continue
            seen.add(str(d.resolve()))
            ids = ID_IN_NAME.findall(d.name)
            if ids and not (mdir(d) / "manifest.json").exists():
                out.append({"folder": str(d), "id": ids[-1]})
    return out


def catalog_health() -> dict:
    ffprobe = ffprobe_bin(core.find_ffmpeg())
    rows, todo, seen = [], [], set()
    for r in lib_roots():
        if not r.is_dir():
            continue
        for d in sorted(r.iterdir()):
            mp = mdir(d) / "manifest.json"
            if not d.is_dir() or not mp.exists() or str(d.resolve()) in seen:
                continue
            seen.add(str(d.resolve()))
            m = load_manifest(d)
            if m is None:
                rows.append({"folder": str(d), "title": d.name, "id": None, "issues": ["manifest je oštećen"]})
                continue
            issues = []
            src = d / m["source"] if m.get("source") else None
            if src and not src.is_file():
                issues.append("izvor nedostaje")
            elif src and float(m.get("duration") or 0) > 0 and m.get("live_status") not in ("is_live", "is_upcoming"):
                todo.append((len(rows), src, float(m["duration"])))
            for s in m.get("subs") or []:
                if s.get("file") and not (d / s["file"]).is_file():
                    issues.append(f"titl nedostaje: {s['file']}")
            for o in m.get("outputs") or []:
                if not (d / o).is_file():
                    issues.append(f"izlaz nedostaje: {o}")
            rows.append({"folder": str(d), "title": m.get("title") or d.name, "id": m.get("id"), "issues": issues})
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(8) as ex:
        durs = list(ex.map(lambda t: media_duration(t[1], ffprobe), todo))
    keep = set()
    for (idx, src, want), got in zip(todo, durs):
        st = src.stat()
        keep.add(f"{src}|{st.st_mtime_ns}|{st.st_size}")
        if got is None:
            rows[idx]["issues"].append("izvor nečitljiv (ffprobe)")
        elif got < 0.98 * want:
            rows[idx]["issues"].append(f"izvor skraćen ({int(100 * got / want)}%)")
    _save_probe_cache(keep)
    return {"items": [r for r in rows if r["issues"]], "orphans": scan_orphans(), "checked_at": int(time.time()),
            "total": len(rows)}


def sub_entry_from_file(f: Path) -> dict:
    stem = f.name[:-4]
    try:
        n = len(parse_srt(f))
    except Exception:  # noqa: BLE001
        n = 0
    rel = f"subs/{f.name}"
    mm = re.match(r"^sr-(lat|cyr)", stem)
    if stem.endswith(".auto"):
        lang = stem[:-5]
        return {"key": f"auto:{lang}", "kind": "auto", "lang": lang, "file": rel, "count": n,
                "name": f"{lang_label(lang)} (auto)"}
    if stem == "whisper":
        return {"key": "whisper", "kind": "whisper", "lang": "und", "file": rel, "count": n, "name": "Whisper"}
    if mm:
        return {"key": f"sr:{mm.group(1)}:rebuilt:{stem}", "kind": "sr", "lang": "sr", "file": rel, "count": n,
                "name": f"Srpski {'ćirilica' if mm.group(1) == 'cyr' else 'latinica'} ({f.name})"}
    lang = stem.split(".")[0]
    return {"key": f"manual:{lang}", "kind": "manual", "lang": lang, "file": rel, "count": n, "name": lang_label(lang)}


def rebuild_manifest(d: Path, vid: str, ffmpeg: str) -> bool:
    mm = re.match(r"^(\d{8}) - (.*?)\s*\[[A-Za-z0-9_-]{11}\]\s*$", d.name)
    date, title = (mm.group(1), mm.group(2)) if mm else (None, ID_IN_NAME.sub("", d.name).strip(" -") or vid)
    files = [f for f in d.iterdir() if f.is_file() and f.suffix.lower() in MEDIA_V + MEDIA_A
             and ".part" not in f.name and ".tagtmp" not in f.name]
    src = max(files, key=lambda f: f.stat().st_size, default=None)
    kind, video, audio, dur = None, None, [], None
    if src:
        vs = probe_video(ffmpeg, src)
        dur = vs.get("duration") or None
        is_audio = src.suffix.lower() in MEDIA_A or ".audio." in src.name or not vs.get("width") \
            or vs.get("codec_name") in ("mjpeg", "png")
        kind = "audio" if is_audio else "video"
        if not is_audio:
            video = {"format_id": None, "width": vs.get("width"), "height": vs.get("height"),
                     "codec": vs.get("codec_name"), "fps": None, "hdr": vs.get("color_transfer") in
                     ("smpte2084", "arib-std-b67"), "premium": False, "duration": dur}
        for n, st in enumerate(probe_audio_streams(ffmpeg, src)):
            lang = base_lang((st.get("tags") or {}).get("language") or "und")
            audio.append({"index": n, "lang": lang, "original": n == 0, "variant": "original" if n == 0 else "",
                          "role": "original" if n == 0 else "default", "codec": st.get("codec_name"),
                          "channels": st.get("channels"), "abr": round(int(st.get("bit_rate") or 0) / 1000) or None,
                          "format_id": None, "title": (st.get("tags") or {}).get("title") or lang_label(lang)})
    subs = [sub_entry_from_file(f) for f in sorted((d / "subs").glob("*.srt"))] if (d / "subs").is_dir() else []
    keys = set()
    for s in subs:  # en.srt + en.x.srt -> isti "manual:en"; upsert_sub bi jedan izgubio
        if s["key"] in keys:
            s["key"] += ":" + s["file"][5:-4]
        keys.add(s["key"])
    outs = sorted(f.name for f in files if f != src)
    mdir(d).mkdir(parents=True, exist_ok=True)
    thumb = next((f for f in sorted(d.glob("*.jpg"))), None)
    if thumb and not (mdir(d) / "thumb.jpg").exists():
        shutil.copyfile(thumb, mdir(d) / "thumb.jpg")
    m = {"version": 1, "url": f"https://www.youtube.com/watch?v={vid}", "id": vid, "title": title, "channel": None,
         "duration": dur, "thumbnail": None, "upload_date": date, "language": None, "live_status": None,
         "source": src.name if src else None, "kind": kind, "video": video, "audio": audio, "subs": subs,
         "outputs": outs, "playlists": [], "rebuilt": int(time.time())}
    with MLOCK:
        if (mdir(d) / "manifest.json").exists():  # u medjuvremenu napravljen (posao) -> ne diraj
            return False
        save_manifest(d, m)
        return True


def rebuild_orphans() -> dict:
    ffmpeg = core.find_ffmpeg()
    n, errors = 0, []
    for o in scan_orphans():
        try:
            n += rebuild_manifest(Path(o["folder"]), o["id"], ffmpeg)
        except Exception as e:  # noqa: BLE001
            errors.append({"folder": o["folder"], "error": str(e)[:300]})
    return {"rebuilt": n, "errors": errors}


# ================================================================ ollama
OLLAMA_PULL = {"model": None, "status": "", "running": False}


def ollama_tags() -> list[str] | None:
    try:
        with urllib.request.urlopen(core.OLLAMA_URL + "/api/tags", timeout=2) as r:
            return [m["name"] for m in json.loads(r.read()).get("models", [])]
    except Exception:  # noqa: BLE001
        return None


def models_dir_problem() -> str | None:
    d = os.environ.get("OLLAMA_MODELS", "")
    if d.startswith("/Volumes/"):
        vol = "/Volumes/" + d.split("/")[2]
        if not Path(vol).exists():
            return f"disk sa modelima nije povezan ({vol})"
    return None


def start_ollama():
    if ollama_tags() is not None or not shutil.which("ollama"):
        return
    env = dict(os.environ)
    if models_dir_problem():  # eksterni disk nije povezan -> interni folder za modele
        env["OLLAMA_MODELS"] = str(INTERNAL_MODELS)
        INTERNAL_MODELS.mkdir(parents=True, exist_ok=True)
    log = open(DATA / "ollama.log", "ab")
    subprocess.Popen(["ollama", "serve"], stdout=log, stderr=log, start_new_session=True, env=env)
    for _ in range(40):
        time.sleep(0.25)
        if ollama_tags() is not None:
            return


FETCH_LOCK = threading.Lock()  # jedna instalacija/preuzimanje u isto vreme
BREW_BINS = ("/opt/homebrew/bin/brew", "/usr/local/bin/brew")
MODEL_GB = {"gemma4:12b-it-qat": 7.2, "translategemma:12b": 8.1}


def free_gb(path: Path) -> float:
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free / 1e9


def brew_install(job: "Job", formula: str, binary: str, what: str, stage: str):
    """Prvo koriscenje: instaliraj alat preko Homebrew-a (jednom, uz napredak u fazi posla)."""
    if shutil.which(binary):
        return
    hint = f"brew install {formula}"
    if not SETTINGS.get("auto_fetch", True):
        raise RuntimeError(f"{what} nije instaliran — {hint} (ili uključi automatsko preuzimanje u Podešavanjima)")
    brew = next((b for b in BREW_BINS if Path(b).exists()), None) or shutil.which("brew")
    if not brew:
        raise RuntimeError(f"{what} nije instaliran, a nema Homebrew-a (https://brew.sh) — {hint}")
    with FETCH_LOCK:
        if shutil.which(binary):
            return
        job.set_stage(stage, "running", None, f"prvo korišćenje: instaliram {what} ({hint})…")
        job.say(f"prvo korišćenje: {hint}")
        if job.run([brew, "install", formula]) != 0 or not shutil.which(binary):
            raise RuntimeError(f"instalacija nije uspela ({hint}) — vidi log")


def pull_for_job(job: "Job", model: str, stage: str):
    """Prvo koriscenje: preuzmi Ollama model uz proveru prostora i napredak u fazi posla."""
    mdir_ = INTERNAL_MODELS if models_dir_problem() else Path(os.environ.get("OLLAMA_MODELS") or INTERNAL_MODELS)
    need = MODEL_GB.get(model, 8.0)
    free = free_gb(mdir_)
    if free < need + 3:
        raise RuntimeError(f"nema mesta za model {model}: treba ~{need:.1f} GB (+3 GB rezerve), slobodno {free:.1f} GB "
                           f"na {mdir_} — oslobodi prostor ili poveži disk za modele")
    with FETCH_LOCK:
        if model in (ollama_tags() or []):
            return
        job.set_stage(stage, "running", 0, f"prvo korišćenje: preuzimam model {model} (~{need:.1f} GB)…")
        job.say(f"prvo korišćenje: ollama pull {model}")

        def on_line(line):
            clean = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", line).strip()
            m = re.search(r"(\d{1,3})%", clean)
            if m:
                job.set_stage(stage, "running", int(m.group(1)), f"preuzimam model {model}: {clean[-60:]}")
                return True
            return not clean or clean.startswith("pulling")
        if job.run(["ollama", "pull", model], on_line) != 0 or model not in (ollama_tags() or []):
            raise RuntimeError(f"preuzimanje modela {model} nije uspelo — vidi log")


def ensure_ollama(model: str, job: "Job", stage: str = "translate"):
    brew_install(job, "ollama", "ollama", "Ollama (lokalni prevodilac)", stage)
    if ollama_tags() is None:
        job.say("pokrećem ollama serve…")
        start_ollama()
    tags = ollama_tags()
    if tags is None:
        raise RuntimeError("Ollama ne radi (vidi data/ollama.log)")
    if model not in tags and f"{model}:latest" not in tags:
        if not SETTINGS.get("auto_fetch", True):
            raise RuntimeError(f"Ollama model '{model}' nije preuzet — u Podešavanjima klikni 'Preuzmi model'")
        pull_for_job(job, model, stage)


def pull_model(model: str):
    OLLAMA_PULL.update(model=model, status="pokrećem…", running=True)
    try:
        start_ollama()
        p = subprocess.Popen(["ollama", "pull", model], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, errors="replace")
        buf = ""
        while True:
            ch = p.stdout.read(1)
            if not ch:
                break
            if ch in "\r\n":
                if buf.strip():
                    OLLAMA_PULL["status"] = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", buf).strip()[-160:]
                buf = ""
            else:
                buf += ch
        rc = p.wait()
        OLLAMA_PULL["status"] = "gotovo ✓" if rc == 0 else f"greška (rc={rc}): {OLLAMA_PULL['status']}"
    except FileNotFoundError:
        OLLAMA_PULL["status"] = "greška: Ollama nije instaliran (brew install ollama ili prvo pokretanje prevoda)"
    except Exception as e:  # noqa: BLE001  pozadinska nit: greska mora do korisnika
        OLLAMA_PULL["status"] = f"greška: {e}"[:200]
    finally:
        OLLAMA_PULL["running"] = False


# ================================================================ http
def choose_folder(current: str) -> str | None:
    cur = Path(current or SETTINGS["out_dir"]).expanduser()
    while not cur.exists() and cur != cur.parent:
        cur = cur.parent
    script = (f'POSIX path of (choose folder with prompt "Izaberi folder za arhivu" '
              f'default location POSIX file "{cur}")')
    p = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return (p.stdout.strip() or None) if p.returncode == 0 else None


class H(BaseHTTPRequestHandler):
    server_version = "yt-arhiva"

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def _local_ok(self) -> bool:
        hosts = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
        if (self.headers.get("Host") or "").lower() not in hosts:
            return False
        origin = (self.headers.get("Origin") or "").lower()
        return not origin or origin in {f"http://{h}" for h in hosts}

    def do_GET(self):
        path = self.path.split("?")[0]
        if not self._local_ok():
            return self._json({"error": "samo lokalno (Host/Origin)"}, 403)
        if path in ("/", "/index.html"):
            b = (ROOT / "static" / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)
        elif path.startswith("/static/"):
            f = (ROOT / "static" / path[len("/static/"):]).resolve()
            if (ROOT / "static") not in f.parents or not f.is_file():
                return self._json({"error": "not found"}, 404)
            b = f.read_bytes()
            # uputstvo.html i slike ekrana (static/uputstvo/*.png) se prikazuju u pregledacu, ne preuzimaju
            ctype = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                     ".html": "text/html; charset=utf-8", ".png": "image/png"}.get(f.suffix.lower(),
                                                                                   "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)
        elif path == "/api/tb/presets":
            self._json(tb().PRESETS)
        elif path == "/api/settings":
            self._json(SETTINGS)
        elif path == "/api/recipes":
            self._json(recipes())
        elif path == "/api/jobs":
            with _lock:
                jobs = sorted(JOBS.values(), key=lambda j: j.created, reverse=True)
                self._json([j.to_dict() for j in jobs])
        elif path.startswith("/api/jobs/") and path.endswith("/log"):
            j = JOBS.get(path.split("/")[3])
            self._json({"log": list(j.log) if j else []})
        elif path == "/api/library":
            self._json(library())
        elif path in ("/api/subs", "/api/media", "/api/corrections/stats", "/api/playlists", "/api/health"):
            self._v3_get(path)
        elif path == "/api/memory":
            try:
                self._json(memory_dump(self._q("recent")))
            except ValueError as e:
                self._json({"error": str(e)}, 400)
            except Exception as e:  # noqa: BLE001
                self._json({"error": str(e)[:1500]}, 500)
        elif path == "/api/thumb":
            from urllib.parse import parse_qs, urlparse
            f = Path((parse_qs(urlparse(self.path).query).get("f") or [""])[0])
            t = f / ".yta" / "thumb.jpg"
            if not under_roots(f) or not t.is_file():
                return self._json({"error": "nema"}, 404)
            b = t.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "max-age=3600")
            self.end_headers()
            self.wfile.write(b)
        elif path == "/api/status":
            tags = ollama_tags()
            self._json({
                "ollama": {"running": tags is not None, "models": tags or [], "pull": OLLAMA_PULL,
                           "problem": None if tags is not None else models_dir_problem()},
                "limits": {k: {"n": v.n, "used": v.used} for k, v in LIM.items()},
                "whisper_cli": bool(shutil.which("whisper-cli")),
                "whisper_model_ready": (WHISPER_DIR / f"ggml-{SETTINGS['whisper_model']}.bin").exists(),
                "ffmpeg": core.find_ffmpeg(),
                "keys": cloud_keys(),  # samo da li kljuc postoji, nikad vrednost
                "tg_ready": core.TG_MODEL in (tags or []),
            })
        else:
            self._json({"error": "not found"}, 404)

    def _q(self, k: str) -> str:
        from urllib.parse import parse_qs, urlparse
        return (parse_qs(urlparse(self.path).query).get(k) or [""])[0]

    def _v3_get(self, path: str, head: bool = False):
        try:
            if path == "/api/subs":
                return self._json(subs_get(self._q("f"), self._q("file")))
            if path == "/api/media":
                # <video src> sa tudjeg sajta ne salje Origin -> Sec-Fetch-Site (Chrome/Safari/Firefox) ga odaje
                if (self.headers.get("Sec-Fetch-Site") or "same-origin").lower() not in ("same-origin", "none"):
                    return self._json({"error": "samo iz panela"}, 403)
                return self._media(media_file(self._q("f"), self._q("file")), head)
            if path == "/api/corrections/stats":
                return self._json(corrections_stats())
            if path == "/api/playlists":
                return self._json(playlists_list())
            if path == "/api/health":
                return self._json(catalog_health())
        except BadRequest as e:
            return self._json({"error": str(e)}, 400)
        except NotFound as e:
            return self._json({"error": str(e)}, 404)
        except (BrokenPipeError, ConnectionResetError):
            return None
        except Exception as e:  # noqa: BLE001
            return self._json({"error": str(e)[:1500]}, 500)
        return self._json({"error": "not found"}, 404)

    def _media(self, f: Path, head: bool = False):
        """Fajl izvora sa HTTP Range (206) — <video>/<audio> u editoru trazi delove fajla."""
        size = f.stat().st_size
        start, end, code = 0, size - 1, 200
        rng = (self.headers.get("Range") or "").strip()
        mm = re.fullmatch(r"bytes=(\d*)-(\d*)", rng) if rng else None
        if mm and (mm.group(1) or mm.group(2)):  # vise opsega / neispravan zaglavlje -> ceo fajl (200)
            if mm.group(1):
                start = int(mm.group(1))
                end = min(int(mm.group(2)), size - 1) if mm.group(2) else size - 1
            else:
                start = max(0, size - int(mm.group(2)))
            if start >= size or start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
            code = 206
        n = end - start + 1 if size else 0
        fh = None if head or not n else f.open("rb")  # greska pri otvaranju -> JSON 500 pre zaglavlja
        try:
            self.send_response(code)
            self.send_header("Content-Type", MEDIA_TYPES.get(f.suffix.lower(), "application/octet-stream"))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(n))
            if code == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if not fh:
                return None
            fh.seek(start)
            left = n
            while left > 0:
                chunk = fh.read(min(1 << 20, left))
                if not chunk:  # fajl skracen u medjuvremenu -> zatvori vezu (Content-Length nije ispunjen)
                    self.close_connection = True
                    break
                self.wfile.write(chunk)
                left -= len(chunk)
        except OSError:  # zaglavlje je vec poslato: nema JSON greske, samo prekini vezu
            self.close_connection = True
        finally:
            if fh:
                fh.close()
        return None

    def do_HEAD(self):
        path = self.path.split("?")[0]
        if not self._local_ok():
            return self._json({"error": "samo lokalno (Host/Origin)"}, 403)
        if path == "/api/media":
            return self._v3_get(path, head=True)
        self.send_response(405)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        path = self.path.split("?")[0]
        if not self._local_ok():
            return self._json({"error": "samo lokalno (Host/Origin)"}, 403)
        # drugi sajtovi iz browsera ne mogu da posalju application/json bez CORS preflight-a
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            return self._json({"error": "očekujem application/json"}, 415)
        try:
            body = self._body()
        except Exception:  # noqa: BLE001
            return self._json({"error": "los JSON"}, 400)
        if not isinstance(body, dict):
            return self._json({"error": "očekujem JSON objekat"}, 400)
        try:
            if path == "/api/probe":
                url = (body.get("url") or "").strip()
                if not re.match(r"^https?://", url):
                    return self._json({"error": "nije URL"}, 400)
                if body.get("playlist") or is_list_url(url):
                    if body.get("playlist") and "list=" in url:  # watch?v=..&list=.. -> cela plejlista
                        url = "https://www.youtube.com/playlist?list=" + re.search(r"list=([^&#]+)", url).group(1)
                    return self._json(probe_list(url))
                info = probe_url(url)
                out = summarize(info, url)
                out["in_list"] = bool(re.search(r"[?&]list=", url))
                return self._json(out)
            if path == "/api/jobs/batch":
                items, opts = body.get("items") or [], body.get("opts") or {}
                if not opts and body.get("recipe"):  # skracenica: opcije iz recepta
                    r = next((x for x in recipes() if x["id"] == body["recipe"]), None)
                    if not r:
                        return self._json({"error": f"nepoznat recept {body['recipe']!r}"}, 400)
                    opts = {"dl": r["dl"], "proc": r["proc"]}
                if not isinstance(items, list) or not isinstance(opts, dict):
                    return self._json({"error": "items mora biti lista, opts objekat"}, 400)
                try:
                    check_after(opts)
                except BadRequest as e:
                    return self._json({"error": str(e)}, 400)
                merged = []
                for n, it in enumerate(items, 1):  # provera pre ijednog posla (bez polovicnog batch-a)
                    if not isinstance(it, dict) or not re.match(r"^https?://", str(it.get("url") or "")):
                        return self._json({"error": f"stavka {n}: nije URL"}, 400)
                    if not isinstance(it.get("opts") or {}, dict):
                        return self._json({"error": f"stavka {n}: opts mora biti objekat"}, 400)
                    try:  # per-item override (dl, proc i after posebno)
                        merged.append(check_after(item_opts(opts, it.get("opts"))))
                    except BadRequest as e:
                        return self._json({"error": f"stavka {n}: {e}"}, 400)
                pl = body.get("playlist")
                made = []
                for it, o in zip(items, merged):
                    o.update(_title=it.get("title"), _thumb=it.get("thumbnail"), _id=it.get("id"),
                             _live=it.get("live_status"), _playlist=pl)
                    j = Job(it["url"], o)
                    start_job(j)
                    made.append(j.id)
                return self._json({"jobs": made})
            if path == "/api/jobs":
                url = body.get("url")
                opts = body.get("opts") or {}
                if not isinstance(url, str) or not re.match(r"^https?://", url) or not isinstance(opts, dict):
                    return self._json({"error": "nije URL (ili opts nije objekat)"}, 400)  # "--opcija" -> yt-dlp
                try:
                    check_after(opts)
                except BadRequest as e:
                    return self._json({"error": str(e)}, 400)
                info = PROBES.get(url)
                if info:
                    opts.update(_title=info.get("title"), _thumb=info.get("thumbnail"),
                                _id=info.get("id"), _live=info.get("live_status"))
                job = Job(url, opts)
                start_job(job)
                return self._json(job.to_dict())
            if path == "/api/process":
                folder = Path(body.get("folder") or "")
                if not under_roots(folder) or not load_manifest(folder):
                    return self._json({"error": "nepoznata stavka arhive"}, 400)
                job = process_job(folder, load_manifest(folder), body.get("proc") or {})
                start_job(job)
                return self._json(job.to_dict())
            if path == "/api/library/batch":
                try:
                    return self._json(library_batch(body))
                except BadRequest as e:
                    return self._json({"error": str(e)}, 400)
            v3 = re.match(r"^/api/(subs/save|health/rebuild|playlists/([A-Za-z0-9_-]+)/(missing|album))$", path)
            if v3:
                try:
                    if v3.group(1) == "subs/save":
                        return self._json(subs_save(body))
                    if v3.group(1) == "health/rebuild":
                        return self._json(rebuild_orphans())
                    fn = playlist_missing if v3.group(3) == "missing" else playlist_album
                    return self._json(fn(v3.group(2), body))
                except BadRequest as e:
                    return self._json({"error": str(e)}, 400)
                except NotFound as e:
                    return self._json({"error": str(e)}, 404)
            mm = re.match(r"^/api/memory/(glossary|rule|correction)$", path)
            if mm:
                try:
                    return self._json(memory_post(mm.group(1), body))
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
                except KeyError as e:  # poruka je u args[0] (str(KeyError) dodaje navodnike)
                    return self._json({"error": str(e.args[0]) if e.args else "ne postoji"}, 404)
            if path == "/api/tb/choose-files":
                return self._json({"paths": choose_files(body.get("kind") or "media")})
            if path == "/api/tb/analyze":
                return self._json(tb_analyze(body.get("path") or ""))
            if path == "/api/tb/command":
                src = str(Path(body.get("path") or "").expanduser())
                a = tb_analyze(src)
                spec = body.get("spec") or {}
                out = tb().default_out_path(src, spec, a)
                # titl se priprema tek pri pokretanju; plan() u pregledu stavi privremeno ime
                p = tb().plan(src, spec, a, core.find_ffmpeg(), out, str(DATA / "tb" / "preview"), None)
                import shlex
                return self._json({"cmd": shlex.join(p.get("cmd") or []), "out": p.get("out") or out,
                                   "warnings": p.get("warnings", []), "errors": p.get("errors", [])})
            if path == "/api/tb/jobs":
                src = str(Path(body.get("path") or "").expanduser())
                a = tb_analyze(src)
                job = Job("", {"path": src, "spec": body.get("spec") or {}, "_title": a.get("name")},
                          kind="transcode", folder=str(Path(src).parent))
                start_job(job)
                return self._json(job.to_dict())
            if path == "/api/open-path":
                p = Path(body.get("path") or "")
                if not under_roots(p) or not p.exists():
                    return self._json({"error": "putanja nije u arhivi"}, 400)
                subprocess.Popen(["open", str(p)])
                return self._json({"ok": True})
            m = re.match(r"^/api/jobs/([0-9a-f]+)/(cancel|stop|retry|remove|open)$", path)
            if m:
                j = JOBS.get(m.group(1))
                if not j:
                    return self._json({"error": "nema posla"}, 404)
                act = m.group(2)
                if act == "cancel":
                    j.cancel.set()
                    j.signal_all(signal.SIGTERM)
                elif act == "stop":  # zaustavi live snimanje, zadrzi snimljeno, nastavi dalje
                    j.stop_live.set()
                    j.signal_all(signal.SIGINT)
                elif act == "retry":
                    if j.busy():
                        return self._json({"error": "posao (ili njegovo preuzimanje) još radi"}, 409)
                    start_job(j)
                elif act == "remove":
                    if j.busy():
                        return self._json({"error": "prvo otkaži (preuzimanje još radi)"}, 409)
                    with _lock:
                        JOBS.pop(j.id, None)
                    persist_soon()
                elif act == "open" and j.folder:
                    subprocess.Popen(["open", j.folder])
                return self._json({"ok": True})
            if path == "/api/settings":
                return self._json(save_settings(body))
            if path == "/api/choose-folder":
                return self._json({"path": choose_folder(body.get("current") or "")})
            if path == "/api/ollama/start":
                start_ollama()
                return self._json({"running": ollama_tags() is not None})
            if path == "/api/ollama/pull":
                if not OLLAMA_PULL["running"]:
                    threading.Thread(target=pull_model, args=(body.get("model") or SETTINGS["model"],),
                                     daemon=True).start()
                return self._json({"ok": True})
            if path == "/api/open-out":
                d = Path(body.get("path") or SETTINGS["out_dir"]).expanduser()
                if not under_roots(d):
                    return self._json({"error": "folder nije u arhivi"}, 400)
                d.mkdir(parents=True, exist_ok=True)
                subprocess.Popen(["open", str(d)])
                return self._json({"ok": True})
            return self._json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": str(e)[:1500]}, 500)


def main():
    load_jobs()
    threading.Thread(target=_persist_loop, daemon=True).start()
    if SETTINGS["engine"] == "ollama":
        threading.Thread(target=start_ollama, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    srv.daemon_threads = True
    print(f"yt-arhiva panel: http://127.0.0.1:{PORT}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
