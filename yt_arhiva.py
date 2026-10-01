#!/usr/bin/env python3
"""yt-arhiva — YouTube (tvoj nalog) -> video + srpski titl -> burn kroz ffmpeg.

Tok:
  1. yt-dlp sa cookie-jima iz browsera (tvoj ulogovani YouTube nalog; Premium formati ako ih nalog ima)
  2. video (i live u toku, od pocetka: --live-from-start)
  3. titl: rucni CC (orig. jezik) > auto-generated (orig. jezik) > lokalni whisper
     (ako vec postoji rucni srpski titl, koristi se direktno, bez prevoda)
  4. prevod na srpski (lokalno Ollama; opciono `claude -p`)
  5. burn titla kroz ffmpeg-full (libass) + VideoToolbox HEVC

Primer:
  yt-arhiva "https://www.youtube.com/watch?v=XXXX"
  yt-arhiva URL --cyr            # cirilica
  yt-arhiva URL --no-burn        # samo video + .srt fajlovi
  yt-arhiva --resume ~/Movies/YT-Arhiva/<folder>   # nastavi prekinut posao
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from pathlib import Path

HOME = Path.home()
DEFAULT_OUT = HOME / "Movies" / "YT-Arhiva"
FFMPEG_CANDIDATES = [
    "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
    "/usr/local/opt/ffmpeg-full/bin/ffmpeg",
]


def whisper_bin() -> str | None:
    """Rezervni python whisper (openai-whisper CLI): YTA_WHISPER_BIN iz okruzenja (putanja ili ime komande),
    inace `whisper` na PATH-u. None = nije dostupan."""
    v = (os.environ.get("YTA_WHISPER_BIN") or "").strip()
    return shutil.which(os.path.expanduser(v) if v else "whisper")


def whisper_models_dir(env=None, home: Path | None = None) -> Path:
    """Folder whisper.cpp modela (ggml-*.bin): YTA_WHISPER_DIR iz okruzenja (launcher ga prenosi i iz
    yta.env; postojeci folder sa modelima se zadaje ovde), inace ~/Library/Application Support/YT Arhiva/whisper.
    Isti izbor koristi installer (installer/common.sh: yta_whisper_dir)."""
    env = os.environ if env is None else env
    home = HOME if home is None else Path(home)
    v = (env.get("YTA_WHISPER_DIR") or "").strip()
    if v:
        return Path(v).expanduser()
    return home / "Library" / "Application Support" / "YT Arhiva" / "whisper"


# ----------------------------------------------------------------- util
def log(msg: str) -> None:
    print(f"[yt-arhiva {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def die(msg: str, code: int = 1) -> None:
    print(f"[yt-arhiva] GRESKA: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kw)


def find_ffmpeg() -> str:
    for c in FFMPEG_CANDIDATES:
        if Path(c).exists():
            return c
    raise RuntimeError("ffmpeg-full nije instaliran (treba libass za burn). Pokreni: brew install ffmpeg-full")


def safe_name(s: str, maxlen: int = 90) -> str:
    s = re.sub(r'[\\/:*?"<>|\n\r\t]+', " ", s).strip()
    s = s.replace("$", "＄").replace("%", "％")  # yt-dlp inace sirio $VAR / %(..)s u putanji
    s = re.sub(r"\s+", " ", s)
    return s[:maxlen].rstrip(" .") or "video"


def ytdlp_base(args) -> list[str]:
    cmd = ["yt-dlp", "--no-warnings", "--js-runtimes", "deno"]
    if args.cookies:
        cmd += ["--cookies", str(args.cookies)]
    elif args.browser != "none":
        cmd += ["--cookies-from-browser", args.browser]
    return cmd


# ----------------------------------------------------------------- probe
def probe(args, url: str) -> dict:
    log("Citam info o videu (cookies iz browsera: %s)..." % (args.cookies or args.browser))
    p = subprocess.run(ytdlp_base(args) + ["-J", "--no-playlist", url],
                       capture_output=True, text=True)
    if p.returncode != 0:
        die("yt-dlp -J nije uspeo:\n" + p.stderr[-2000:])
    return json.loads(p.stdout)


# ----------------------------------------------------------------- video
def download_video(args, url: str, info: dict, work: Path) -> Path:
    existing = sorted(work.glob("video.*"))
    existing = [e for e in existing if e.suffix in (".mkv", ".mp4", ".webm")]
    if existing:
        log(f"Video vec postoji: {existing[0].name}")
        return existing[0]
    live = info.get("live_status")
    cmd = ytdlp_base(args) + [
        "--no-playlist",
        "-f", args.format,
        "--merge-output-format", "mkv",
        "--concurrent-fragments", "8",
        "-o", str(work / "video.%(ext)s"),
    ]
    if live == "is_live":
        log("Live stream u toku -> snimam OD POCETKA (--live-from-start); traje do kraja streama.")
        cmd += ["--live-from-start"]
    elif live == "is_upcoming":
        log("Stream jos nije poceo -> cekam (--wait-for-video).")
        cmd += ["--wait-for-video", "30", "--live-from-start"]
    else:
        log("Preuzimam video (najbolji kvalitet dostupan tvom nalogu)...")
    cmd.append(url)
    try:
        run(cmd)
    except subprocess.CalledProcessError:
        die("preuzimanje videa nije uspelo")
    except KeyboardInterrupt:
        log("Prekinuto (Ctrl+C). yt-dlp je sacuvao ono sto je skinuto ako je moguce.")
    got = [e for e in sorted(work.glob("video.*")) if e.suffix in (".mkv", ".mp4", ".webm")]
    if not got:
        die("nema preuzetog video fajla")
    return got[0]


# ----------------------------------------------------------------- subs
def orig_lang(info: dict) -> str | None:
    lang = info.get("language")
    if lang:
        return lang
    auto = info.get("automatic_captions") or {}
    for k in auto:
        if k.endswith("-orig"):
            return k[:-5]
    return None


def pick_track(info: dict, want_lang: str | None) -> tuple[str, str, bool] | None:
    """Vraca (kind, lang_key, is_serbian). kind = 'manual' | 'auto'."""
    manual = {k: v for k, v in (info.get("subtitles") or {}).items() if k != "live_chat"}
    auto = info.get("automatic_captions") or {}
    ol = orig_lang(info)

    def base(k: str) -> str:
        return k.split("-")[0].lower()

    if want_lang:
        if want_lang in manual:
            return ("manual", want_lang, base(want_lang) in ("sr", "sh"))
        if want_lang in auto:
            return ("auto", want_lang, False)
    # 1) rucni srpski -> bez prevoda
    for k in manual:
        if base(k) in ("sr", "sh"):
            return ("manual", k, True)
    # 2) rucni u originalnom jeziku
    if ol:
        for k in manual:
            if base(k) == base(ol):
                return ("manual", k, False)
    # 3) bilo koji rucni (prvi, prednost engleskom)
    if manual:
        ks = sorted(manual, key=lambda k: (base(k) != "en", k))
        return ("manual", ks[0], False)
    # 4) auto-generated originala (ne auto-prevod!)
    if ol:
        for cand in (f"{ol}-orig", ol):
            if cand in auto:
                return ("auto", cand, False)
    for k in auto:
        if k.endswith("-orig"):
            return ("auto", k, False)
    return None


def fetch_json3(args, url: str, kind: str, lang: str, work: Path) -> Path | None:
    out_tmpl = str(work / "subsrc.%(ext)s")
    for f in work.glob("subsrc.*"):
        f.unlink()
    cmd = ytdlp_base(args) + ["--no-playlist", "--skip-download",
                              "--sub-langs", lang, "--sub-format", "json3",
                              "-o", out_tmpl]
    cmd += ["--write-subs"] if kind == "manual" else ["--write-auto-subs"]
    cmd.append(url)
    p = subprocess.run(cmd, capture_output=True, text=True)
    files = list(work.glob("subsrc*.json3"))
    if p.returncode != 0 or not files:
        log("Titl nije preuzet: " + (p.stderr[-500:] or "nema fajla"))
        return None
    return files[0]


def cues_from_json3(path: Path, kind: str) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    events = [e for e in data.get("events", []) if e.get("segs")]
    if kind == "manual":
        cues = []
        for e in events:
            txt = "".join(s.get("utf8", "") for s in e["segs"]).strip()
            if not txt or e.get("aAppend"):
                continue
            st = e.get("tStartMs", 0)
            cues.append({"start": st, "end": st + e.get("dDurationMs", 2000), "text": txt})
        return fix_overlaps(cues)
    # auto (ASR): izvuci reci sa vremenima pa sam segmentiraj
    words = []
    for e in events:
        if e.get("aAppend"):
            continue
        t0 = e.get("tStartMs", 0)
        for s in e["segs"]:
            w = s.get("utf8", "")
            if not w.strip():
                continue
            words.append({"t": t0 + s.get("tOffsetMs", 0), "w": w.strip()})
        if words:
            words[-1]["end_hint"] = t0 + e.get("dDurationMs", 0)
    return segment_words(words)


def segment_words(words: list[dict], max_chars: int = 84, max_ms: int = 6500,
                  gap_ms: int = 900) -> list[dict]:
    cues, cur, cstart = [], [], None
    for i, w in enumerate(words):
        if cstart is None:
            cstart = w["t"]
        cur.append(w["w"])
        nxt = words[i + 1]["t"] if i + 1 < len(words) else None
        text = " ".join(cur)
        end_punct = w["w"][-1:] in ".?!…"
        gap = (nxt - w["t"]) if nxt is not None else 99999
        if (nxt is None or len(text) >= max_chars or (w["t"] - cstart) >= max_ms
                or gap >= gap_ms or (end_punct and len(text) > 25)):
            end = min(nxt, w["t"] + 1500) if nxt is not None else w.get("end_hint", w["t"] + 1500)
            end = max(end, w["t"] + 400)
            cues.append({"start": cstart, "end": end, "text": text})
            cur, cstart = [], None
    return fix_overlaps(cues)


def fix_overlaps(cues: list[dict]) -> list[dict]:
    cues = sorted(cues, key=lambda c: c["start"])
    for a, b in zip(cues, cues[1:]):
        if a["end"] > b["start"]:
            a["end"] = max(a["start"] + 300, b["start"] - 20)
    for c in cues:  # minimalno 1.2s na ekranu ako ima mesta
        c["end"] = max(c["end"], c["start"] + 1200)
    for a, b in zip(cues, cues[1:]):
        if a["end"] > b["start"]:
            a["end"] = b["start"] - 20
    return [c for c in cues if c["text"].strip() and c["end"] > c["start"]]


def whisper_cues(args, video: Path, work: Path, ffmpeg: str, runner=None) -> list[dict]:
    """runner(cmd) -> returncode; podrazumevano subprocess (CLI), panel prosledjuje job.run."""
    wbin = whisper_bin()
    if not wbin:
        raise RuntimeError("nema titla, a lokalni python whisper nije dostupan "
                           "(`whisper` na PATH-u ili putanja u YTA_WHISPER_BIN)")

    def go(cmd):
        rc = runner(cmd) if runner else subprocess.run(cmd).returncode
        if rc != 0:
            raise RuntimeError(f"komanda nije uspela (rc={rc}): {Path(cmd[0]).name}")
    wav = work / "audio16k.wav"
    if not wav.exists():
        log("Vadim audio za whisper...")
        part = work / "audio16k.part.wav"
        go([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
            "-vn", "-ac", "1", "-ar", "16000", str(part)])
        part.rename(wav)
    out_json = work / "audio16k.json"
    if not out_json.exists():
        log(f"Nema titla na YT -> lokalna transkripcija whisper ({args.whisper_model}); ovo traje...")
        go([wbin, str(wav), "--model", args.whisper_model,
            "--output_format", "json", "--output_dir", str(work),
            "--word_timestamps", "True", "--fp16", "False"])
    data = json.loads(out_json.read_text(encoding="utf-8"))
    words = []
    for seg in data.get("segments", []):
        for w in seg.get("words") or []:
            words.append({"t": int(w["start"] * 1000), "w": w["word"].strip(),
                          "end_hint": int(w["end"] * 1000)})
    if words:
        return segment_words(words)
    return fix_overlaps([{"start": int(s["start"] * 1000), "end": int(s["end"] * 1000),
                          "text": s["text"].strip()} for s in data.get("segments", [])])


# ----------------------------------------------------------------- srt/ass
def ms_srt(ms: int) -> str:
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(cues: list[dict], path: Path, key: str = "text") -> None:
    out = []
    for i, c in enumerate(cues, 1):
        out.append(f"{i}\n{ms_srt(c['start'])} --> {ms_srt(c['end'])}\n{c[key].strip()}\n")
    path.write_text("\n".join(out), encoding="utf-8")


def ms_ass(ms: int) -> str:
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h}:{m:02d}:{s:02d}.{ms // 10:02d}"


def write_ass(cues: list[dict], path: Path, width: int, height: int, key: str = "text",
              primary: str = "&H00FFFFFF") -> None:
    fs = max(18, round(height * 0.052))
    margin_v = round(height * 0.055)
    outline = max(1.5, round(height / 540, 1) * 1.6)
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Helvetica Neue,{fs},{primary},&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,{outline},{outline * 0.6:.1f},2,{round(width * 0.08)},{round(width * 0.08)},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for c in cues:
        t = c[key].strip().replace("\n", "\\N").replace("{", "(").replace("}", ")")
        lines.append(f"Dialogue: 0,{ms_ass(c['start'])},{ms_ass(c['end'])},Default,,0,0,0,,{t}")
    path.write_text(head + "\n".join(lines) + "\n", encoding="utf-8")


# ----------------------------------------------------------------- prevod
SYS_PROMPT = (
    "Ti si profesionalni prevodilac titlova na srpski jezik. Prevodis prirodno, tecno i "
    "gramaticki ispravno, kako bi govorio obrazovan izvorni govornik iz Srbije (ekavica). "
    "Ne prevodis doslovno rec po rec; prenosis smisao, ton i registar. Ispravljas ocigledne "
    "greske automatskog prepoznavanja govora na osnovu konteksta. Imena ljudi, brendova i "
    "tehnicke termine koji se u srpskom ne prevode ostavljas u originalu. Titlovi moraju biti "
    "kratki i citljivi. Odgovaras ISKLJUCIVO validnim JSON-om, bez ikakvog drugog teksta."
)

CTX_SYS = (
    "Ti si urednik prevoda titlova na srpski jezik. Pre prevoda pripremas kratak pregled videa i "
    "glosar da bi prevod bio dosledan od pocetka do kraja. Odgovaras ISKLJUCIVO validnim JSON-om."
)

CTX_FILE = "context.json"   # u radnom folderu prevoda (kes konteksta)
CTX_LINES = 150             # koliko prvih titlova ide u kontekst
CTX_VER = 2                 # menja se sa promptom konteksta -> stari context.json se ne koristi
GLOSSARY_MAX = 40
SCENE_GAP_MS = 2000         # pauza koja zatvara scenu
SCENE_MIN = 10
QC_WIDTH = 42
QC_SHARE = 0.10             # najvise 10% linija ide na ponovni prevod
QC_PRIO = {"prazno": 0, "pismo": 1, "neprevedeno": 2, "duzina": 3}


def context_block(context: dict | None) -> str:
    """Sinopsis + CEO glosar + registar, isti tekst u svakom pozivu za video (nepromenljiv pocetak
    prompta: modeli bez SWA ga kesiraju; gemma4 ga ipak obradi ponovo, ~+2 s po bloku)."""
    if not context:
        return ""
    out = []
    if context.get("synopsis"):
        out.append(f"O cemu je video (sinopsis, za razumevanje): {context['synopsis']}\n"
                   "Rod (npr. bio sam / bila sam) odredi po tome ko izgovara titl; kad govori vise osoba, "
                   "zakljuci iz konteksta i ne pretpostavljaj rod bez osnova.")
    gl = [{"src": g["src"], "sr": g.get("sr") or g["src"]} for g in context.get("glossary") or [] if g.get("src")]
    if gl:
        out.append("Glosar — kad se ovo pojavi, prevodi TACNO ovako i dosledno u svakom titlu "
                   "(menjaj samo padez i broj po srpskoj gramatici): " + json.dumps(gl, ensure_ascii=False))
    reg = context.get("register")
    if reg == "ti":
        out.append('Registar: neformalan — kad se govori jednoj osobi koristi "ti", grupi ili publici "vi". '
                   "Drzi se toga dosledno.")
    elif reg == "vi":
        out.append('Registar: formalan — jednoj osobi se obraca sa "vi" (persiranje), publici takodje "vi". '
                   "Drzi se toga dosledno.")
    if context.get("hints"):  # memorija: naucena pravila iz editora titla
        out.append("Ranije ispravke korisnika — ne ponavljaj ove greške (pogrešno → ispravno): "
                   + json.dumps(context["hints"], ensure_ascii=False))
    return "\n".join(out) + "\n" if out else ""


# ----------------------------------------------------------------- prevod: memorija ispravki (docs/memory-contract.md §6)
# args.mem = MemView iz memory.py (duck typing, bez importa) ili None; bez nje promptovi su isti kao ranije.
def mem_of(args):
    """Primenljiva memorija iz args.mem ili None (prazna memorija = kao da je nema)."""
    m = getattr(args, "mem", None)
    return None if m is None or getattr(m, "empty", False) else m


def examples_block(examples: list[dict] | None, en: bool = False) -> str:
    """Primeri ranijih ispravki za promenljivi deo prompta (pre "Ulaz"); en = prompt na engleskom (grcki)."""
    if not examples:
        return ""
    if en:
        ex = [{"source": e.get("izvor", ""), "wrong": e.get("pogresno", ""), "right": e.get("ispravno", "")}
              for e in examples]
        return ("Examples of the user's earlier corrections (learn style, terms and grammar; apply only where "
                f"relevant to these subtitles): {json.dumps(ex, ensure_ascii=False)}\n")
    return ("Primeri ranijih ispravki korisnika (uči stil, termine i gramatiku; primeni samo ako se odnosi na ove "
            f"titlove): {json.dumps(examples, ensure_ascii=False)}\n")


def mem_context(context: dict | None, glossary: list[dict], hints: list[dict]) -> dict | None:
    """Korisnicki glosar (pobedjuje za isti src) + naucena pravila preko konteksta modela; bez konteksta
    a sa glosarom/pravilima -> minimalan kontekst. Bez memorije vraca isti context."""
    if not glossary and not hints:
        return context
    out = dict(context) if context else {"synopsis": "", "glossary": [], "register": ""}
    if glossary:
        mine = {g["src"].casefold() for g in glossary}
        out["glossary"] = list(glossary) + [g for g in out.get("glossary") or []
                                            if str(g.get("src") or "").casefold() not in mine]
    if hints:
        out["hints"] = list(hints)
    return out


_CYR_CH = re.compile(r"[\u0400-\u04FF]")
_LAT_WORD = re.compile(r"[A-Za-zÀ-ɏ]+")  # cisto latinicna rec (bez cirilice)


def mem_post(mem, src: str, text: str, script: str | None, use_exact: bool = True,
             keep: set[str] | None = None) -> tuple[str, int, int]:
    """Posle prevoda: TM (exact) pa naucena pravila. script lat|cyr = srpski (radi se u latinici, pa
    finalize_script), None = drugi jezik. -> (tekst, 1 ako je TM zamenio liniju, broj zamena pravila);
    bez promene vraca isti tekst (i ista cirilica).
    keep = imena iz CELOG izvora videa (script_keep svih izvornih titlova; None = samo src ove linije): kod
    povratka u cirilicu ostaju latinicom, kao i latinicne reci koje je linija vec imala; linija bez cirilice
    (npr. samo imena) ostaje latinicna."""
    cur = normalize_script(text, "lat") if script else text
    n_tm = 0
    hit = mem.exact(src) if use_exact and src else None
    if hit and hit != cur:
        cur, n_tm = hit, 1
    cur, n = mem.apply_rules(cur)
    if not n_tm and not n:
        return text, 0, 0
    if script != "cyr":
        return (finalize_script(cur, script, []) if script else cur), n_tm, n
    if not _CYR_CH.search(text or ""):
        return finalize_script(cur, "lat", []), n_tm, n  # linija je bila latinicna -> ostaje latinicna
    k = set(script_keep([src] if src else [])) if keep is None else set(keep)
    k |= {w for w in _LAT_WORD.findall(text) if not _CYR_CH.search(w)}  # latinica koju je finalize vec sacuvao
    old = set(re.findall(r"[^\W\d_]+", normalize_script(text, "lat")))
    k |= {w for w in re.findall(r"[^\W\d_]+", cur) if w not in old and any(c.isupper() for c in w[1:])}  # iPhone
    return to_cyr(normalize_script(cur, "lat"), k), n_tm, n


def mem_report(args, **counts) -> None:
    rep = getattr(args, "mem_report", None)
    if isinstance(rep, dict):
        rep.update(counts)


def build_prompt(batch: list[dict], ctx_before: list[str], title: str, script: str,
                 context: dict | None = None, ctx_after: list[str] | None = None,
                 neighbors: tuple[list[dict], list[dict]] | None = None, hint: str | None = None,
                 examples: list[dict] | None = None) -> str:
    """context = {synopsis, glossary, register[, hints]} iz pre-pass-a (+ memorija); neighbors = (pre, posle) kao
    [{"orig","sr"}] vec prevedenih suseda (ponovni prevod jedne linije); hint = zasto se ponavlja;
    examples = primeri ranijih ispravki za ovaj blok (memorija).
    Redosled: nepromenljivi deo (naslov, kontekst, uputstvo) pa promenljivi (okolina, primeri, ulaz)."""
    pismo = "cirilicom" if script == "cyr" else "latinicom (sa dijakritikom č ć š ž đ)"
    items = [{"id": c["id"], "t": c["text"]} for c in batch]
    parts = [f"Video (naslov i opis, za kontekst i imena): \"{title}\"\n", context_block(context),
             f"\nPrevedi titlove iz Ulaza na srpski, pisano {pismo}. Recenice se cesto protezu kroz vise "
             f"uzastopnih titlova — prevedi ih kao celinu i rasporedi prevod po istim id-jevima tako da "
             f"svaki titl nosi odgovarajuci deo (redosled reci moze da se prilagodi srpskom). Svaki id "
             f"mora postojati u odgovoru, tacno jednom. Ako je titl samo zvuk/muzika, npr. [Music], "
             f"prevedi kao [muzika] i sl. Ne dodaji objasnjenja.\n\n"]
    if neighbors:
        parts.append("Okolni titlovi sa vec gotovim prevodom (samo za uklapanje, NE prevoditi ih ponovo):\n"
                     f"pre: {json.dumps(neighbors[0], ensure_ascii=False)}\n"
                     f"posle: {json.dumps(neighbors[1], ensure_ascii=False)}\n")
    else:
        parts.append(f"Prethodni kontekst (samo za razumevanje, NE prevoditi): "
                     f"{json.dumps(ctx_before, ensure_ascii=False)}\n")
        if ctx_after:
            parts.append(f"Sledeci titlovi (samo za razumevanje kraja recenice, NE prevoditi): "
                         f"{json.dumps(ctx_after, ensure_ascii=False)}\n")
    if examples:
        parts.append(examples_block(examples))
    if hint:
        parts.append(hint.rstrip() + "\n")
    # kompaktan JSON: ~40% manje generisanih tokena nego "lep" JSON sa uvlacenjem (= brze)
    parts.append(f"\nUlaz:\n{json.dumps(items, ensure_ascii=False)}\n\n"
                 f"Izlaz: kompaktan JSON u jednom redu, bez uvlacenja i novih redova: "
                 f"{{\"items\":[{{\"id\":<broj>,\"sr\":\"<prevod>\"}},...]}}")
    return "".join(parts)


def build_context_prompt(title: str, lines: list[str], total: int) -> str:
    numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(lines, 1))
    return (
        f"Naslov i opis videa: \"{title}\"\n\n"
        f"Pocetak titlova (izvorni jezik, {len(lines)} od ukupno {total}):\n{numbered}\n\n"
        "Napravi JSON objekat:\n"
        "- \"synopsis\": 2-4 recenice na srpskom (latinica sa č ć š ž đ): o cemu je video, ko govori "
        "(ime, uloga i POL svakog govornika — bitno za rod glagola u srpskom; da li je monolog ili razgovor "
        "vise osoba), kome se obraca i kojim tonom.\n"
        f"- \"glossary\": do {GLOSSARY_MAX} stavki {{\"src\": tacan oblik iz originala, \"sr\": kako se pise "
        "u srpskom prevodu (osnovni oblik)}. Ukljuci imena ljudi i mesta, nadimke, titule i funkcije, firme, "
        "brendove, nazive brodova, proizvoda i projekata, kao i strucne termine koji se ponavljaju. Licna imena, "
        "firme, brendove i nazive projekata ostavi u originalu (imena iz nelatinicnih pisama transkribuj latinicom). "
        "Titule i funkcije (npr. \"the Chancellor\" -> \"kancelar\"), nadimke i opste termine prevedi postojecim, "
        "ustaljenim srpskim izrazom (npr. \"load balancer\" -> \"balanser opterećenja\") — ne prepisuj zvuk strane "
        "reci i ne izmisljaj reci; proveri pravopis. To je obavezni prevod za ceo video. Ne ubacuj obicne reci "
        "(npr. kabl, telefon, računar) niti stavke kojih nema u tekstu.\n"
        "- \"register\": \"ti\" ako se govornici obracaju jedni drugima neformalno, \"vi\" ako je obracanje "
        "formalno ili ucitivo.\n"
        "Izlaz: samo kompaktan JSON u jednom redu, bez uvlacenja i novih redova: "
        "{\"synopsis\":\"...\",\"glossary\":[{\"src\":\"...\",\"sr\":\"...\"}],\"register\":\"ti\"}")


OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
if not OLLAMA_URL.startswith("http"):
    OLLAMA_URL = "http://" + OLLAMA_URL


# strukturisani izlaz: Ollama garantuje oblik JSON-a (manje popravki/ponavljanja)
JSON_SCHEMA = {"type": "object", "required": ["items"], "properties": {"items": {
    "type": "array", "items": {"type": "object", "required": ["id", "sr"],
                               "properties": {"id": {"type": "integer"}, "sr": {"type": "string"}}}}}}

CTX_SCHEMA = {"type": "object", "required": ["synopsis", "glossary", "register"], "properties": {
    "synopsis": {"type": "string"},
    "glossary": {"type": "array", "maxItems": GLOSSARY_MAX, "items": {
        "type": "object", "required": ["src", "sr"],
        "properties": {"src": {"type": "string"}, "sr": {"type": "string"}}}},
    "register": {"type": "string", "enum": ["vi", "ti"]}}}


def call_ollama(prompt: str, model: str, schema: dict | None = None, system: str | None = None) -> str:
    import urllib.error
    import urllib.request
    # num_ctx isti za sve pozive (promena bi naterala Ollama da ponovo ucita model)
    body = {"model": model, "stream": False, "format": schema or JSON_SCHEMA, "think": False,
            "keep_alive": "15m",
            "options": {"temperature": 0.2, "num_ctx": 8192},
            "messages": [{"role": "system", "content": system or SYS_PROMPT},
                         {"role": "user", "content": prompt}]}
    for attempt in range(2):
        req = urllib.request.Request(OLLAMA_URL + "/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                return json.loads(r.read())["message"]["content"]
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")
            if attempt == 0 and "think" in msg:  # model bez thinking moda
                body.pop("think", None)
                continue
            raise RuntimeError(f"ollama {e.code}: {msg[:400]}")
    raise RuntimeError("ollama: neuspeh")


LANG_EN = {"en": "English", "sr": "Serbian", "el": "Greek", "es": "Spanish", "it": "Italian", "de": "German",
           "fr": "French", "pt": "Portuguese", "ru": "Russian", "hr": "Croatian", "bs": "Bosnian", "tr": "Turkish",
           "ar": "Arabic", "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "hi": "Hindi", "nl": "Dutch",
           "pl": "Polish", "uk": "Ukrainian", "ro": "Romanian", "bg": "Bulgarian", "hu": "Hungarian", "cs": "Czech",
           "sv": "Swedish", "id": "Indonesian", "vi": "Vietnamese", "th": "Thai", "bn": "Bengali", "fa": "Persian",
           "he": "Hebrew", "mk": "Macedonian", "sl": "Slovenian", "sq": "Albanian"}
TARGETS = {"el": ("Greek", "el", "Ελληνικά")}          # ne-srpski ciljni jezici (srpski ide kroz translate())
TG_MODEL = os.environ.get("YTA_TG_MODEL", "translategemma:12b")
GEMMA_MODEL = "gemma4:12b-it-qat"
CLOUD_DEFAULT = {"gemini": "gemini-3.5-flash-lite", "cloudflare": "@cf/google/gemma-4-26b-a4b-it", "ollama": GEMMA_MODEL}
CLOUD_CHAIN = {"gemini": ["gemini", "cloudflare", "ollama"], "cloudflare": ["cloudflare", "ollama"]}
CF_ACCOUNT = os.environ.get("YTA_CF_ACCOUNT", "")   # panel postavlja iz podesavanja
KEY_SERVICES = {"gemini": "yt-arhiva-gemini", "cloudflare": "yt-arhiva-cf-token"}
_KEYS: dict[str, str] = {}


def base_code(code: str | None) -> str:
    return (code or "").split("-")[0].lower()


def keychain(service: str, fresh: bool = False) -> str:
    """Tajna iz macOS Keychain-a (ili env); nikad se ne loguje."""
    if service in _KEYS and not fresh:
        return _KEYS[service]
    env = {"yt-arhiva-gemini": "GEMINI_API_KEY", "yt-arhiva-cf-token": "CLOUDFLARE_API_TOKEN"}.get(service, "")
    v = os.environ.get(env, "")
    if not v:
        try:
            p = subprocess.run(["security", "find-generic-password", "-a", os.environ.get("USER", ""), "-s", service,
                                "-w"], capture_output=True, text=True, timeout=10)
            v = p.stdout.strip() if p.returncode == 0 else ""
        except Exception:  # noqa: BLE001
            v = ""
    _KEYS[service] = v
    return v


class QuotaError(RuntimeError):
    """Servis trenutno nedostupan (kvota, limit, nema kljuca) -> sledeci u lancu."""
    def __init__(self, msg: str, wait: float = 0.0, daily: bool = True):
        super().__init__(msg)
        self.wait, self.daily = wait, daily


def _post_json(url: str, body: dict, headers: dict, timeout: int = 300) -> dict:
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        msg = e.read().decode(errors="replace")[:600]
        if e.code == 429 or "RESOURCE_EXHAUSTED" in msg:
            m = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', msg)
            daily = bool(re.search(r"PerDay|per day|daily", msg, re.I))
            raise QuotaError(f"limit ({e.code})", float(m.group(1)) if m else 20.0, daily)
        if e.code in (401, 403):
            raise QuotaError(f"odbijen pristup ({e.code}) — proveri ključ")
        raise RuntimeError(f"HTTP {e.code}: {msg[:300]}")


def call_gemini(prompt: str, model: str, schema: dict | None = None, system: str | None = None) -> str:
    key = keychain(KEY_SERVICES["gemini"])
    if not key:
        raise QuotaError("nema Gemini ključa (Keychain: yt-arhiva-gemini)")
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    d = _post_json(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", body,
                   {"x-goog-api-key": key})  # kljuc u header-u, ne u URL-u
    cands = d.get("candidates") or []
    if not cands:
        raise RuntimeError(f"gemini: prazan odgovor ({str(d.get('promptFeedback') or d)[:200]})")
    return "".join(pt.get("text", "") for pt in (cands[0].get("content") or {}).get("parts") or [])


def call_cloudflare(prompt: str, model: str, schema: dict | None = None, system: str | None = None) -> str:
    tok = keychain(KEY_SERVICES["cloudflare"])
    if not tok or not CF_ACCOUNT:
        raise QuotaError("nema Cloudflare tokena (Keychain: yt-arhiva-cf-token) ili Account ID-ja")
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    d = _post_json(f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/ai/run/{model}",
                   {"messages": msgs, "max_tokens": 8192, "temperature": 0.2}, {"Authorization": f"Bearer {tok}"})
    r = d.get("result") or {}
    out = r.get("response") if isinstance(r, dict) else r
    if not out and isinstance(r, dict):  # OpenAI-kompatibilan oblik
        out = (((r.get("choices") or [{}])[0]).get("message") or {}).get("content")
    if isinstance(out, (dict, list)):
        out = json.dumps(out, ensure_ascii=False)
    if not out:
        raise RuntimeError(f"cloudflare: prazan odgovor ({str(d)[:200]})")
    return out


def _chain_llm(engine: str, model: str):
    """Cloud lanac: Gemini -> Cloudflare -> lokalna Gemma. Minutni limit: sacekaj; dnevni: sledeci."""
    chain = [(e, model if e == engine else CLOUD_DEFAULT[e]) for e in CLOUD_CHAIN[engine]]
    fns = {"gemini": call_gemini, "cloudflare": call_cloudflare, "ollama": call_ollama}
    st = {"i": 0}

    def llm(p, schema=None, system=None):
        tries = 0
        while True:
            e, m = chain[st["i"]]
            try:
                return fns[e](p, m, schema, system)
            except QuotaError as ex:
                if not ex.daily and ex.wait and tries < 3:
                    tries += 1
                    time.sleep(min(ex.wait, 60))
                    continue
                if st["i"] + 1 >= len(chain):
                    raise RuntimeError(f"{e}: {ex}")
                st["i"] += 1
                tries = 0
                log(f"{e} nedostupan ({ex}) → prelazim na {chain[st['i']][0]}")
    llm.active = lambda: chain[st["i"]]  # koji servis stvarno radi (za izvestaj)
    return llm


def make_llm(engine: str, model: str):
    """llm(prompt, schema=None, system=None) -> tekst; bez kwargs = prevod titlova (stari oblik).
    engine: ollama | synergy (TranslateGemma + Gemma; llm je Gemma) | gemini | cloudflare | claude"""
    if engine == "claude":
        return lambda p, schema=None, system=None: call_claude(p, model, system)
    if engine in CLOUD_CHAIN:
        return _chain_llm(engine, model or CLOUD_DEFAULT[engine])
    m = model if engine != "synergy" or (model and "translategemma" not in model) else GEMMA_MODEL
    return lambda p, schema=None, system=None: call_ollama(p, m or GEMMA_MODEL, schema, system)


# ---------------------------------------------------------------- TranslateGemma (specijalista)
TG_TARGET = {"sr": ("Serbian", "sr-Latn"), "el": ("Greek", "el")}


def tg_prompt(src_code: str, tgt: str, text: str) -> str:
    """Zvanicni format TranslateGemma (jedna user poruka, dva prazna reda pre teksta)."""
    sc = base_code(src_code) or "en"
    sl = LANG_EN.get(sc, "English")
    tl, tc = TG_TARGET.get(tgt, (LANG_EN.get(tgt, tgt), tgt))
    return (f"You are a professional {sl} ({sc}) to {tl} ({tc}) translator. Your goal is to accurately convey the "
            f"meaning and nuances of the original {sl} text while adhering to {tl} grammar, vocabulary, and cultural "
            f"sensitivities.\nProduce only the {tl} translation, without any additional explanations or commentary. "
            f"Please translate the following {sl} text into {tl}:\n\n\n{text}")


def call_tg(prompt: str) -> str:
    import urllib.request
    body = {"model": TG_MODEL, "stream": False, "keep_alive": "15m",
            "options": {"temperature": 0.1, "num_ctx": 8192}, "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(OLLAMA_URL + "/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.loads(r.read())["message"]["content"]


def tg_glossary(context: dict | None, src_code: str, args, cache_p: Path) -> dict | None:
    """Termine koje je Gemma 'prevela' u glosaru prevodi TranslateGemma; imena (sr == src) ostaju."""
    if not context or not context.get("glossary"):
        return context
    gl = [dict(g) for g in context["glossary"] if g.get("src")]
    terms = [g for g in gl if (g.get("sr") or g["src"]).strip() != g["src"].strip()]
    if not terms:
        return context
    key = _sha([g["src"] for g in terms])
    cache = load_json(cache_p)
    if cache.get("key") != key:
        tr = tg_lines([g["src"] for g in terms], src_code, "sr", args)
        cache = {"key": key, "tr": {g["src"]: t.strip().rstrip(".") for g, t in zip(terms, tr)}}
        save_json(cache_p, cache)
    for g in gl:
        if g["src"] in cache["tr"] and cache["tr"][g["src"]]:
            g["sr"] = cache["tr"][g["src"]]
    return {**context, "glossary": gl}


def tg_lines(lines: list[str], src_code: str, tgt: str, args=None, block: int = 12, status=None,
             on_block=None) -> list[str]:
    """Numerisani blokovi (poravnanje 1:1 + kontekst unutar bloka); nedostajuci brojevi -> pojedinacno.
    on_block(start, texts) se zove posle svakog bloka (kes posle bloka: prekid ne gubi uradjeno)."""
    tg = lambda p, schema=None, system=None: call_tg(p)  # noqa: E731
    out: list[str] = [""] * len(lines)
    for b0 in range(0, len(lines), block):
        chunk = lines[b0:b0 + block]
        if status:
            status(f"TranslateGemma nacrt {min(b0 + block, len(lines))}/{len(lines)}")
        txt = ask(tg, tg_prompt(src_code, tgt, "\n".join(f"{i + 1}. {t}" for i, t in enumerate(chunk))), args)
        got = {}
        for ln in txt.strip().split("\n"):
            mm = re.match(r"^\s*(\d+)[.)]\s*(.+)$", ln)
            if mm and 1 <= int(mm.group(1)) <= len(chunk):
                got[int(mm.group(1)) - 1] = mm.group(2).strip()
        for i, t in enumerate(chunk):
            out[b0 + i] = got.get(i) or " ".join(ask(tg, tg_prompt(src_code, tgt, t), args).split())
        if on_block:
            on_block(b0, out[b0:b0 + len(chunk)])
    return out


def call_claude(prompt: str, model: str, system: str | None = None) -> str:
    cmd = ["claude", "-p", "--model", model, "--output-format", "text",
           "--tools", "", "--no-session-persistence", "--setting-sources", "",
           "--system-prompt", system or SYS_PROMPT]
    p = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                       cwd=str(Path(os.environ.get("TMPDIR", "/tmp"))), timeout=600)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-800:] or p.stdout[-800:])
    return p.stdout


def parse_json_array(s: str) -> list:
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    i, j = s.find("["), s.rfind("]")
    if i < 0 or j < 0:
        raise ValueError("nema JSON niza u odgovoru")
    return json.loads(s[i:j + 1])


def parse_json_object(s: str) -> dict:
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s.strip())
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j < 0:
        raise ValueError("nema JSON objekta u odgovoru")
    d = json.loads(s[i:j + 1])
    if not isinstance(d, dict):
        raise ValueError("odgovor nije JSON objekat")
    return d


def is_cancelled(args) -> bool:
    c = getattr(args, "cancelled", None)
    return bool(c and c())


def ask(llm, prompt: str, args=None, **kw) -> str:
    """Poziv modela koji se prekida cim args.cancelled() postane True (ne ceka kraj odgovora)."""
    if is_cancelled(args):
        raise RuntimeError("otkazano")
    if not getattr(args, "cancelled", None):
        return llm(prompt, **kw)
    box: dict = {}

    def go():
        try:
            box["r"] = llm(prompt, **kw)
        except BaseException as e:  # noqa: BLE001
            box["e"] = e
    t = threading.Thread(target=go, daemon=True)
    t.start()
    while t.is_alive():
        t.join(0.25)
        if is_cancelled(args):
            raise RuntimeError("otkazano")
    if "e" in box:
        raise box["e"]
    return box["r"]


def load_json(p: Path) -> dict:
    try:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def save_json(p: Path, obj: dict) -> None:
    tmp = Path(p).with_name(Path(p).name + ".tmp")  # atomski: prekid ne ostavlja polu-upisan kes
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def _sha(obj) -> str:
    return hashlib.sha1(json.dumps(obj, ensure_ascii=False).encode()).hexdigest()


def src_fingerprint(cues: list[dict]) -> str:
    """Otisak CELOG izvora (svi tekstovi po redu); stari kes (duzina + pocetak) se ne poklapa -> ponisti."""
    return f"v2:{len(cues)}:{_sha([c['text'] for c in cues])}"


def line_hashes(cues: list[dict]) -> list[str]:
    return [hashlib.sha1(c["text"].encode("utf-8", "replace")).hexdigest()[:12] for c in cues]


def renew_cache(cache: dict, fp: str, cues: list[dict], args) -> tuple[dict, int]:
    """Kes za drugi izvor -> (nov kes, broj zadrzanih linija). Bez args.keep_lines (CLI, testovi): prazan, kao
    ranije. Sa keep_lines (panel): zadrzi prevode linija ciji se tekst po indeksu nije promenio (npr. memorija
    ispravi jednu liniju auto titla) — blok sa promenjenom linijom se prevodi ponovo, ostatak ostaje."""
    new: dict = {"__src__": fp}
    if not getattr(args, "keep_lines", False):
        return new, 0
    hs, old = line_hashes(cues), cache.get("__lines__")
    if isinstance(old, list):
        same = {str(i) for i, (a, b) in enumerate(zip(old, hs)) if a == b}
        new.update({k: v for k, v in cache.items() if k in same})
        qcd = [x for x in cache.get("__qcd__") or [] if str(x) in same]
        if qcd:
            new["__qcd__"] = qcd
    kept = len(new) - 1 - ("__qcd__" in new)
    new["__lines__"] = hs
    return new, kept


def mark_lines(cache: dict, cues: list[dict], args, path: Path) -> None:
    """keep_lines: kes koji jos nema otiske linija (stari kes, isti izvor) ih dobija odmah."""
    if getattr(args, "keep_lines", False) and "__lines__" not in cache:
        cache["__lines__"] = line_hashes(cues)
        save_json(path, cache)


# ----------------------------------------------------------------- prevod: kontekst (pre-pass)
_DOUBLE = re.compile(r"([bcčćdđfghjklmnprsštvzž])\1", re.I)
_PREFIX = re.compile(r"(naj|od|pod|nad|pred|iz|raz|bez|uz)", re.I)
# ijekavica (mjerilo, vjetar, djelo, svjetlo): suglasnik + "je" + suglasnik; ne posle prefiksa (objekat, odjednom)
_IJE = re.compile(r"[bcdmpstvz]je[^\W\d_aeiou]", re.I)
_IJE_OK = re.compile(r"(ob|sub|pod|nad|od|iz|ad)je", re.I)


def plausible_sr(src: str, sr: str) -> bool:
    """Grubi filter losih stavki glosara (glosar se primenjuje na ceo video, pa losa stavka
    kvari svako pojavljivanje; bolje je izbaciti je i pustiti model da prevede sam):
    udvojen suglasnik van granice prefiksa (izmisljeno "bojja"; ok: najjaci, poddomen) i
    ijekavica ("mjerilo plime"). Imena koja ostaju ista se ne diraju."""
    if sr.lower() == src.lower():
        return True
    for w in re.findall(r"[^\W\d_]+", sr):
        if w.lower() in src.lower():
            continue
        if _DOUBLE.search(w) and not _PREFIX.match(w):
            return False
        if _IJE.search(w) and not _IJE_OK.search(w):
            return False
    return True


def clean_context(d: dict, corpus: str) -> dict:
    syn = " ".join(str(d.get("synopsis") or "").split())[:900]
    low, seen, gl = corpus.lower(), set(), []
    for g in d.get("glossary") or []:
        if not isinstance(g, dict):
            continue
        src = " ".join(str(g.get("src") or "").split())
        sr = normalize_script(" ".join(str(g.get("sr") or "").split()), "lat") or src
        if not src or len(src) > 60 or len(sr) > 80 or src.lower() in seen or not plausible_sr(src, sr):
            continue
        toks = [t for t in re.findall(r"[^\W\d_]+", src.lower()) if len(t) >= 3]
        # samo stavke koje se stvarno pojavljuju u naslovu/opisu/titlovima (bez izmisljenih)
        if src.lower() not in low and not any(t in low for t in toks):
            continue
        seen.add(src.lower())
        gl.append({"src": src, "sr": sr})
        if len(gl) >= GLOSSARY_MAX:
            break
    reg = str(d.get("register") or "").strip().lower()
    return {"synopsis": normalize_script(syn, "lat"), "glossary": gl,
            "register": reg if reg in ("vi", "ti") else ""}


def get_context(cues: list[dict], title: str, args, work: Path, llm, build: bool = True) -> dict | None:
    """Sinopsis + glosar + oslovljavanje za ceo video; jedan LLM poziv, kes u work/context.json
    (kljuc: izvor + naslov + engine/model). build=False samo cita kes."""
    sample = [c["text"] for c in cues[:CTX_LINES]]
    key = _sha([CTX_VER, args.engine, args.model, title, sample])
    p = Path(work) / CTX_FILE
    d = load_json(p)
    if d.get("key") == key:
        return {k: d.get(k) for k in ("synopsis", "glossary", "register")}
    if not build:
        return None
    t0, err = time.time(), None
    for _ in range(2):
        if is_cancelled(args):
            raise RuntimeError("otkazano")
        try:
            raw = ask(llm, build_context_prompt(title, sample, len(cues)), args,
                      schema=CTX_SCHEMA, system=CTX_SYS)
            ctx = clean_context(parse_json_object(raw), title + "\n" + "\n".join(sample))
            break
        except Exception as e:  # noqa: BLE001  kontekst je pomoc, ne sme da obori prevod
            err = str(e)
            if "otkazano" in err:
                raise
    else:
        log(f"Kontekst nije napravljen ({err[:200]}) -> prevodim bez glosara")
        return None
    save_json(p, {"key": key, "engine": args.engine, "model": args.model,
                  "created": time.strftime("%Y-%m-%d %H:%M:%S"), **ctx})
    log(f"Kontekst ({time.time() - t0:.0f}s): glosar {len(ctx['glossary'])} stavki, "
        f"oslovljavanje {ctx['register'] or '?'}"
        + (" — " + ", ".join(f"{g['src']}={g['sr']}" for g in ctx["glossary"][:8]) if ctx["glossary"] else ""))
    return ctx


# ----------------------------------------------------------------- prevod: scene
def _ends_sentence(text: str) -> bool:
    return bool(re.search(r"[.?!…][\"'”’)\]]*\s*$", text or ""))


def scene_batches(cues: list[dict], max_n: int = 30, min_n: int = SCENE_MIN,
                  gap_ms: int = SCENE_GAP_MS) -> list[list[dict]]:
    """Blokovi po scenama: blok se sece SAMO na pauzi > gap_ms (kraj scene), i to na poslednjoj
    takvoj pauzi koja staje u max_n (kratke scene se spajaju -> manje poziva modela). Bez pauze u
    prozoru: rez na najvecoj pauzi / kraju recenice. Blok ima >= min_n titlova (osim kad je ceo
    ulaz kraci), pa ne ostaje sam kratak ostatak."""
    n = len(cues)
    max_n = max(1, int(max_n))
    min_n = max(1, min(int(min_n), max_n))

    def gap(i):  # pauza posle cues[i]
        if i + 1 >= n:
            return 10 ** 9
        a, b = cues[i], cues[i + 1]
        return b.get("start", 0) - a.get("end", a.get("start", 0))

    out, s = [], 0
    while s < n:
        if n - s <= max_n:
            cut = n
        else:
            ends = [e for e in range(s + min_n, s + max_n + 1) if n - e >= min_n]
            scene = [e for e in ends if gap(e - 1) > gap_ms]
            if scene:
                cut = scene[-1]
            elif ends:
                cut = max(ends, key=lambda e: (gap(e - 1) + (1500 if _ends_sentence(cues[e - 1]["text"]) else 0), e))
            else:  # max_n < 2*min_n: podeli ostatak otprilike na pola
                cut = s + min(max_n, max(1, (n - s + 1) // 2))
        out.append(cues[s:cut])
        s = cut
    return out


# ----------------------------------------------------------------- prevod: QC
_WORD = re.compile(r"[^\W\d_]+")
_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯豈-﫿]")


def looks_serbian(text: str) -> bool:
    return bool(re.search(r"[čćšžđČĆŠŽĐђћџљњјЂЋЏЉЊЈ]", text or ""))


def foreign_letters(text: str) -> str:
    """Slova koja nisu latinica (posle vracanja srpske cirilice u latinicu) — mesano pismo."""
    lat = normalize_script(text or "", "lat")
    return "".join(ch for ch in lat if ch.isalpha() and "LATIN" not in unicodedata.name(ch, ""))


def _eff_len(text: str) -> float:
    return len(text) + 1.5 * len(_CJK.findall(text))  # CJK znak nosi vise smisla od latinicnog


def qc_line(src: str, sr: str, width: int = QC_WIDTH, keep: set[str] | frozenset = frozenset()) -> list[str]:
    """Problemi jednog prevedenog titla: prazno, pismo, neprevedeno, duzina, prelom (prelom = samo info).
    keep = reci (mala slova) koje smeju ostati iste (imena/termini iz glosara)."""
    s, t = (src or "").strip(), (sr or "").strip()
    if not s:
        return []
    if not t:
        return ["prazno"]
    issues = []
    lat = normalize_script(t, "lat")
    if foreign_letters(lat):
        issues.append("pismo")
    if not looks_serbian(s):
        sw = _WORD.findall(normalize_script(s, "lat").lower())
        tw = _WORD.findall(lat.lower())
        common = [w for w, orig in zip(sw, _WORD.findall(s)) if orig[:1].islower() and w not in keep]
        same = sw == tw and len(common) >= 2
        long_src = {w for w in common if len(w) >= 4}
        overlap = long_src & set(tw)
        if same or (len(long_src) >= 3 and len(overlap) / len(long_src) >= 0.6):
            issues.append("neprevedeno")
    if len(s) >= 12:
        r = len(lat) / max(1.0, _eff_len(s))
        if r < 0.3 or r > 2.5:
            issues.append("duzina")
    rows = wrap_lines(t, width).split("\n")
    if len(rows) > 2 or max(len(x) for x in rows) > width:
        issues.append("prelom")
    return issues


def _keep_words(context: dict | None) -> set[str]:
    keep = set()
    for g in (context or {}).get("glossary") or []:
        if normalize_script(g.get("sr", ""), "lat").lower() == g.get("src", "").lower():  # termin ostaje isti
            keep.update(w.lower() for w in _WORD.findall(g["src"]))
    return keep


def _bad(issues: list[str]) -> int:
    return len([x for x in issues if x != "prelom"])


QC_WEIGHT = {"prazno": 10, "neprevedeno": 5, "pismo": 4, "duzina": 1}


def _severity(issues: list[str]) -> int:
    """Tezina problema: ponovni prevod se prihvata samo ako je ukupno bolji (prazno -> preduga
    linija JESTE bolje, iako je broj problema isti)."""
    return sum(QC_WEIGHT.get(x, 0) for x in issues)


def qc_scan(cues: list[dict], key: str = "sr", context: dict | None = None) -> dict[int, list[str]]:
    """{indeks: [problemi]} za sve linije sa bar jednim problemom (bez ponovnog prevoda)."""
    keep = _keep_words(context)
    out = {}
    for i, c in enumerate(cues):
        iss = qc_line(c["text"], c.get(key, ""), keep=keep)
        if iss:
            out[i] = iss
    return out


def qc_summary(found: dict[int, list[str]]) -> dict[str, int]:
    cnt: dict[str, int] = {}
    for iss in found.values():
        for x in iss:
            cnt[x] = cnt.get(x, 0) + 1
    return cnt


def retranslate_line(cues: list[dict], i: int, cur: dict[int, str], title: str, args, llm,
                     context: dict | None, issues: list[str]) -> str:
    """Ponovni prevod jedne linije sa +-3 susedne (original + postojeci prevod)."""
    c = cues[i]
    def pair(x):  # model odgovara latinicom, pa i susede vidi latinicom
        return {"orig": x["text"], "sr": normalize_script(cur.get(x["id"], ""), "lat")}
    nb = ([pair(x) for x in cues[max(0, i - 3):i]], [pair(x) for x in cues[i + 1:i + 4]])
    why = {"prazno": "prazan", "neprevedeno": "ostao neprevedeno (na stranom jeziku)",
           "pismo": "sadrzi slova stranog pisma", "duzina": "duzina ne odgovara originalu"}
    probs = ", ".join(why[x] for x in issues if x in why)
    prev = normalize_script(cur.get(c["id"], ""), "lat")
    hint = (f"Prethodni prevod ovog titla je odbacen ({probs}): {json.dumps(prev, ensure_ascii=False)}. "
            "Prevedi ga ponovo, potpuno na srpski, samo sadrzaj ovog titla (ne ponavljaj okolne prevode).")
    mem = mem_of(args)
    exs = mem.examples([c["text"]]) if mem else None
    last = None
    for _ in range(2):
        try:
            raw = ask(llm, build_prompt([c], [], title, "lat", context=context, neighbors=nb, hint=hint,
                                        examples=exs), args)
            got = {int(o["id"]): str(o["sr"]).strip() for o in parse_json_array(raw) if "id" in o and "sr" in o}
            if got.get(c["id"]):
                return finalize_script(got[c["id"]], args.script, [x["text"] for x in cues[max(0, i - 3):i + 4]])
            last = "prazan odgovor"
        except Exception as e:  # noqa: BLE001
            last = str(e)
            if "otkazano" in last:
                raise
    raise RuntimeError(f"ponovni prevod linije {i} nije uspeo: {last}")


def qc_pass(cues: list[dict], cache: dict, save, title: str, args, llm, ctx_fn) -> dict:
    """QC posle prevoda: sumnjive linije (osim preloma) se ponovo prevode pojedinacno, najvise 10%
    linija ukupno (i kroz ponovljena pokretanja: cache['__qcd__'] pamti vec pokusane)."""
    n = len(cues)
    cur = {c["id"]: cache[str(c["id"])] for c in cues}
    context = ctx_fn(False)
    keep = _keep_words(context)
    found = {c["id"]: iss for c in cues if (iss := qc_line(c["text"], cur[c["id"]], keep=keep))}
    before = qc_summary(found)
    n_bad = len([1 for iss in found.values() if _bad(iss)])
    tried = {int(x) for x in cache.get("__qcd__", [])}
    cap = max(1, int(n * QC_SHARE))
    cand = sorted((i for i, iss in found.items() if _bad(iss) and i not in tried),
                  key=lambda i: (min(QC_PRIO.get(x, 9) for x in found[i]), i))
    todo = cand[:max(0, cap - len(tried))] if getattr(args, "qc_retry", True) else []
    fixed, improved, examples = 0, 0, []
    if todo:
        context = ctx_fn(True)
        log(f"QC: ponovo prevodim {len(todo)} sumnjivih linija (od {len(cand)}, limit {cap})...")
        with cf.ThreadPoolExecutor(max_workers=max(1, int(getattr(args, "jobs", 1) or 1))) as ex:
            futs = {ex.submit(retranslate_line, cues, i, cur, title, args, llm, context, found[i]): i for i in todo}
            for f in cf.as_completed(futs):
                i = futs[f]
                try:
                    new = f.result()
                except Exception as e:  # noqa: BLE001
                    if "otkazano" in str(e):
                        raise
                    log(f"  QC linija {i}: {e}")
                    continue
                new_iss = qc_line(cues[i]["text"], new, keep=keep)
                if _severity(new_iss) < _severity(found[i]):
                    if len(examples) < 5:
                        examples.append({"id": i, "src": cues[i]["text"], "before": cur[i], "after": new,
                                         "issues": found[i]})
                    cur[i] = cache[str(i)] = new
                    improved += 1
                    fixed += not _bad(new_iss)
                    if new_iss:
                        found[i] = new_iss
                    else:
                        found.pop(i, None)
        tried.update(todo)
        cache["__qcd__"] = sorted(tried)
        save()
    after = qc_summary(found)
    rep = {"lines": n, "flagged": before, "suspicious": n_bad,
           "retried": len(todo), "improved": improved, "fixed": fixed, "cap": cap,
           "remaining": after, "remaining_ids": sorted(i for i, iss in found.items() if _bad(iss))[:50],
           "wrap": after.get("prelom", 0), "examples": examples}
    bad_txt = ", ".join(f"{k} {v}" for k, v in before.items() if k != "prelom") or "nema"
    log(f"QC: {n} linija · sumnjivo: {bad_txt} · ponovo prevedeno {len(todo)} (limit {cap}) -> "
        f"ispravljeno {fixed}, poboljsano {improved} · ostalo {_bad_count(after)} · "
        f"prelom >2 reda/>{QC_WIDTH} zn.: {after.get('prelom', 0)} (samo info)")
    return rep


def _bad_count(summary: dict[str, int]) -> int:
    return sum(v for k, v in summary.items() if k != "prelom")


# ----------------------------------------------------------------- prevod: batch + ceo tok
MERGE_SYS = ("Ti si vrhunski lektor i prevodilac titlova na srpski (ekavica, latinica sa č ć š ž đ). Prevod mora biti "
             "gramaticki besprekoran (rod, broj, padez, glagolski vid i vreme) i prirodan. Odgovaras ISKLJUCIVO JSON-om.")


def merge_c(batch: list[dict], helper: dict[int, str], drafts: dict, title: str, context, args, llm,
            examples: list[dict] | None = None) -> dict[int, str]:
    """Varijanta C: TranslateGemma nacrt je OSNOVA (gramatika), Gemma prevod je POMOCNI (znacenje/kontekst).
    examples = primeri ranijih ispravki za ovaj blok (memorija), pre "Ulaz"."""
    items = [{"id": c["id"], "src": c["text"], "osnova": drafts.get(str(c["id"])) or helper.get(c["id"], ""),
              "pomocni": helper.get(c["id"], "")} for c in batch]
    prompt = (f"Video: \"{title}\"\n{context_block(context)}\nZa svaku liniju imas izvor, 'osnova' (gramaticki pouzdan "
              "prevod) i 'pomocni' (prevod koji bolje zna kontekst). Rezultat je OSNOVA, osim:\n"
              "1) ako se osnova i pomocni razlikuju po ZNACENJU — proveri izvor i uzmi tacno znacenje, pravilnim srpskim;\n"
              "2) imena i termini tacno po glosaru (sa pravilnim padezom);\n"
              "3) rod u 1. licu po tome ko govori; 4) oslovljavanje (ti/vi) dosledno; 5) ekavica, bez hrvatskih reci.\n"
              "Ako je osnova tacna, prepisi je NEPROMENJENU. Ne seli tekst izmedju id-jeva.\n"
              f"{examples_block(examples)}"
              f"Ulaz:\n{json.dumps(items, ensure_ascii=False)}\n\n"
              "Izlaz: kompaktan JSON u jednom redu: {\"items\":[{\"id\":<broj>,\"sr\":\"<prevod>\"}]}")
    try:
        arr = parse_json_array(ask(llm, prompt, args, system=MERGE_SYS))
        got = {int(o["id"]): str(o["sr"]).strip() for o in arr if "id" in o and "sr" in o}
    except Exception as e:  # noqa: BLE001  spajanje je nadogradnja; na gresku ostaje pomocni prevod
        if "otkazano" in str(e):
            raise
        log(f"spajanje (C) nije uspelo: {e} — ostaje Gemma prevod")
        return helper
    return {c["id"]: got.get(c["id"]) or helper.get(c["id"], "") for c in batch}


def translate_batch(batch, ctx, title, args, llm=None, context=None, ctx_after=None,
                    examples=None) -> dict[int, str]:
    """examples = primeri ranijih ispravki za ovaj blok (memorija; i za podeljene delove / dopunu)."""
    llm = llm or make_llm(args.engine, args.model)
    want = {c["id"] for c in batch}
    src = [c["text"] for c in batch]
    last_err = None
    for attempt in range(3):
        if is_cancelled(args):
            raise RuntimeError("otkazano")
        try:
            arr = parse_json_array(ask(llm, build_prompt(batch, ctx, title, "lat", context=context,
                                                         ctx_after=ctx_after, examples=examples), args))
            got = {int(o["id"]): str(o["sr"]).strip() for o in arr if "id" in o and "sr" in o}
            missing = want - got.keys()
            if not missing:
                res = {k: got[k] for k in want}
                if getattr(args, "_tg", None):  # Kvalitetno (C): spoji sa TranslateGemma osnovom
                    res = merge_c(batch, res, args._tg, title, context, args, llm, examples=examples)
                elif getattr(args, "review", False):
                    res = review_batch(batch, res, title, "lat", llm)
                return {k: finalize_script(v, args.script, src) for k, v in res.items()}
            last_err = f"nedostaju id-jevi {sorted(missing)[:10]}"
            if len(missing) <= max(2, len(want) // 20):  # sitna rupa: dopuni pojedinacno
                res = {k: finalize_script(got[k], args.script, src) for k in want - missing}
                for c in batch:
                    if c["id"] in missing:
                        res[c["id"]] = translate_batch([c], ctx, title, args, llm, context,
                                                       examples=examples)[c["id"]]
                return res
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            if "otkazano" in last_err:
                raise
        time.sleep(2 * (attempt + 1))
    if len(batch) > 1:  # podeli na pola
        mid = len(batch) // 2
        a = translate_batch(batch[:mid], ctx, title, args, llm, context, src[mid:mid + 3], examples)
        b = translate_batch(batch[mid:], ctx + src[:mid][-6:], title, args, llm, context, ctx_after, examples)
        return {**a, **b}
    raise RuntimeError(f"prevod nije uspeo: {last_err}")


def translate(cues: list[dict], title: str, args, work: Path, progress=None) -> list[dict]:
    """Prevod titlova na srpski (c['sr']). title = naslov + opis (kontekst).
    progress(done_batches, total_batches) opcioni callback (panel).
    Opcioni args: ctx_pass (False = bez konteksta), scenes (False = fiksni blokovi),
    qc_retry (False = QC samo izvestaj), qc (dict -> popunjava se izvestajem QC-a),
    mem (MemView: glosar/pravila/primeri + TM i pravila na kraju; kes ostaje cist prevod modela),
    mem_report (dict -> {"examples","glossary","hints","exact","rules"})."""
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    # kes je vezan za izvor + model + pismo, da promena opcija ne pokupi stari prevod
    tag = re.sub(r"[^a-zA-Z0-9]+", "_", f"{args.engine}_{args.model}_{args.script}"
                 f"{'_rev' if getattr(args, 'review', False) else ''}")
    cache_p = work / f"prevod_cache_{tag}.json"
    fp = src_fingerprint(cues)
    cache = load_json(cache_p)
    if cache.get("__src__") != fp:
        had = any(k != "__src__" for k in cache)
        cache, kept = renew_cache(cache, fp, cues, args)
        if kept:
            log(f"Izvor se promenio u {len(cues) - kept} linija -> ponovo prevodim samo njihove blokove")
        elif had:
            log("Kes prevoda je za drugi (ili stari) izvor -> prevodim ispocetka")
    else:
        mark_lines(cache, cues, args, cache_p)
    llm = make_llm(args.engine, args.model)
    for i, c in enumerate(cues):
        c["id"] = i
    bs = max(1, int(args.batch))
    if getattr(args, "scenes", True):
        batches = [b for b in scene_batches(cues, bs) if any(str(c["id"]) not in cache for c in b)]
    else:
        todo = [c for c in cues if str(c["id"]) not in cache]
        batches = [todo[i:i + bs] for i in range(0, len(todo), bs)]
    use_ctx = getattr(args, "ctx_pass", True)
    memo: dict = {}
    mem = mem_of(args)
    mem_gl: list[dict] = []
    mem_hints: list[dict] = []
    n_ex = 0
    if mem:
        mem_gl = mem.glossary_for(title + "\n" + "\n".join(c["text"] for c in cues))
        mem_hints = mem.hints()

    def base_ctx(build: bool):  # kontekst modela (kes context.json), bez memorije
        if not use_ctx:
            return None
        if "v" not in memo:
            v = get_context(cues, title, args, work, llm, build=build)
            if v is None and not build:
                return None
            memo["v"] = v
        return memo["v"]

    def ctx_fn(build: bool):  # + korisnicki glosar i naucena pravila (i kad konteksta nema)
        return mem_context(base_ctx(build), mem_gl, mem_hints)

    def save():
        save_json(cache_p, cache)

    if progress:
        progress(0, len(batches))
    if batches and args.engine == "synergy":
        tg_p = work / "tg_sr_cache.json"
        tgc = load_json(tg_p)
        if tgc.get("__src__") != fp:
            tgc = renew_cache(tgc, fp, cues, args)[0]
        need = [c for b in batches for c in b if str(c["id"]) not in tgc]
        if need:
            log(f"TranslateGemma nacrt: {len(need)} titlova")
            st = getattr(args, "status", None)
            def keep(b0, texts, need=need):
                for c, d in zip(need[b0:b0 + len(texts)], texts):
                    tgc[str(c["id"])] = d
                save_json(tg_p, tgc)
            tg_lines([c["text"] for c in need], getattr(args, "src_lang", "") or "en", "sr", args, status=st,
                     on_block=keep)
        args._tg = tgc
    if batches:
        context = base_ctx(True)
        if args.engine == "synergy" and context:
            context = memo["v"] = tg_glossary(context, getattr(args, "src_lang", "") or "en", args,
                                              work / "tg_glossary.json")
        context = mem_context(context, mem_gl, mem_hints)  # korisnik posle tg_glossary (ne ide kroz TranslateGemma)
        n_todo = sum(len(b) for b in batches)
        sizes = [len(b) for b in batches]
        log(f"Prevodim {n_todo} titlova na srpski ({len(batches)} blokova"
            f"{' po scenama ' + str(min(sizes)) + '–' + str(max(sizes)) if getattr(args, 'scenes', True) else ''}, "
            f"{args.jobs} paralelno, model {args.model}"
            f"{', kontekst+glosar' if context else ''})...")
        done = 0
        if progress:  # posle TranslateGemma nacrta i konteksta: odmah "blok 0/N" (inace faza izgleda zaglavljeno)
            progress(0, len(batches))
        with cf.ThreadPoolExecutor(max_workers=max(1, int(args.jobs or 1))) as ex:
            futs = {}
            for b in batches:
                first, last = b[0]["id"], b[-1]["id"]
                ctx = [c["text"] for c in cues[max(0, first - 6):first]]
                after = None
                if last + 1 < len(cues) and cues[last + 1].get("start", 0) - cues[last].get("end", 0) <= SCENE_GAP_MS:
                    after = [c["text"] for c in cues[last + 1:last + 3]]  # recenica se nastavlja posle bloka
                exs = mem.examples([c["text"] for c in b]) if mem else None
                n_ex += bool(exs)
                futs[ex.submit(translate_batch, b, ctx, title, args, llm, context, after, exs)] = b
            for f in cf.as_completed(futs):
                res = f.result()
                cache.update({str(k): v for k, v in res.items()})
                if cache.get("__qcd__"):  # nov prevod linije -> QC je sme ponovo pokusati
                    cache["__qcd__"] = sorted(set(cache["__qcd__"]) - set(res))
                save()
                done += 1
                log(f"  blok {done}/{len(batches)} gotov")
                if progress:
                    progress(done, len(batches))
    rep = qc_pass(cues, cache, save, title, args, llm, ctx_fn)
    out = getattr(args, "qc", None)
    if isinstance(out, dict):
        out.clear()
        out.update(rep)
    for c in cues:
        c["sr"] = cache[str(c["id"])]
    if mem:  # posle QC i za linije iz kesa; u kes ne ide (memorija se primenjuje svaki put)
        n_tm = n_rules = 0
        keep = script_keep([c["text"] for c in cues]) if args.script == "cyr" else None  # imena iz celog izvora
        for c in cues:
            c["sr"], tm, nr = mem_post(mem, c["text"], c["sr"], args.script, keep=keep)
            n_tm, n_rules = n_tm + tm, n_rules + nr
        # glosar i pravila su "u promptu" samo ako je model stvarno pozvan (blokovi ili QC), ne za sve iz kesa
        sent = bool(batches) or bool(rep.get("retried"))
        n_gl, n_hi = (len(mem_gl), len(mem_hints)) if sent else (0, 0)
        mem_report(args, examples=n_ex, glossary=n_gl, hints=n_hi, exact=n_tm, rules=n_rules)
        log(f"Memorija: primeri u {n_ex} blokova, glosar {n_gl}, pravila u promptu {n_hi} · "
            f"prevodna memorija {n_tm} linija, {n_rules} zamena po pravilima")
    return cues


# ---------------------------------------------------------------- drugi ciljni jezici (npr. grcki)
GEN_SCHEMA = {"type": "object", "required": ["items"], "properties": {"items": {
    "type": "array", "items": {"type": "object", "required": ["id", "tr"],
                               "properties": {"id": {"type": "integer"}, "tr": {"type": "string"}}}}}}
_GREEK = re.compile(r"[\u0370-\u03FF\u1F00-\u1FFF]")


def gen_system(lang: str) -> str:
    name = TARGETS.get(lang, (LANG_EN.get(lang, lang),))[0]
    return (f"You are a professional subtitle translator into {name}. Translate naturally and idiomatically as a "
            f"native {name} speaker would write subtitles: keep meaning, tone and register (formal/informal address "
            f"consistent), correct grammar and gender agreement, short readable lines. Keep names of people, brands, "
            f"ships and projects as in the original (use an established {name} form only if one exists). "
            f"Fix obvious speech-recognition errors from context. Output ONLY valid JSON.")


def mem_glossary_block(glossary: list[dict] | None) -> str:
    """Korisnicki glosar za engleski prompt (grcki): [{"src","tr"}]."""
    if not glossary:
        return ""
    gl = [{"src": g["src"], "tr": g.get("sr") or g.get("tr") or g["src"]} for g in glossary]
    return f"Glossary (always translate exactly like this): {json.dumps(gl, ensure_ascii=False)}\n"


def gen_prompt(batch: list[dict], prev: list[str], title: str, lang: str, drafts: dict | None = None,
               examples: list[dict] | None = None, glossary: list[dict] | None = None,
               hints: list[dict] | None = None) -> str:
    """Memorija: glosar i naucena pravila (isti za ceo video) posle naslova, primeri bloka pre "Input"."""
    name = TARGETS.get(lang, (LANG_EN.get(lang, lang),))[0]
    mem_txt = mem_glossary_block(glossary)
    if hints:
        mem_txt += ("Earlier corrections by the user — do not repeat these mistakes (wrong → right): "
                    + json.dumps([{"wrong": h.get("pogresno", ""), "right": h.get("ispravno", "")} for h in hints],
                                 ensure_ascii=False) + "\n")
    if drafts:
        items = [{"id": c["id"], "t": c["text"], "draft": drafts.get(str(c["id"]), "")} for c in batch]
        task = (f"Each item has the source text 't' and a grammatically reliable {name} 'draft' from a specialised "
                f"translator. Return the draft UNCHANGED unless it has a meaning error versus the source, an "
                f"inconsistent name/term, wrong gender/person or wrong formal/informal address — then fix it.")
    else:
        items = [{"id": c["id"], "t": c["text"]} for c in batch]
        task = f"Translate each item's 't' into {name}."
    return (f"Video (title and description, for context and names): \"{title}\"\n"
            f"{mem_txt}"
            f"Previous subtitles (context only, do NOT translate): {json.dumps(prev, ensure_ascii=False)}\n\n"
            f"{task} Sentences often span several consecutive items — translate them as a whole and distribute the "
            f"text over the same ids. Every id exactly once. Sound/music tags like [Music] get the {name} equivalent.\n"
            f"{examples_block(examples, en=True)}"
            f"Input:\n{json.dumps(items, ensure_ascii=False)}\n\n"
            'Output: compact one-line JSON: {"items":[{"id":<number>,"tr":"<translation>"}]}')


_CYR = re.compile(r"[\u0400-\u04FF]")
_LAT = re.compile(r"[A-Za-z]")


def _gen_ok(src: str, out: str, lang: str) -> bool:
    if not out.strip():
        return False
    if lang == "el":  # grcki mora imati grcka slova (osim ako je red samo ime/broj)
        letters = re.findall(r"[^\W\d_]", out)
        if letters and len(_GREEK.findall(out)) / len(letters) < 0.5 and out.strip() != src.strip():
            return False
        if _CYR.search(out):  # cirilica u grckom = pokvaren red
            return False
        for w in re.findall(r"[^\W\d_]+", out):  # rec sa grckim I latinicnim slovima ("joinάσατε")
            if _GREEK.search(w) and _LAT.search(w):
                return False
    return not (out.strip() == src.strip() and len(re.findall(r"[^\W\d_]", src)) > 12)


def translate_to(cues: list[dict], title: str, args, work: Path, lang: str, progress=None) -> list[dict]:
    """Prevod na ne-srpski jezik (c['tr']). args.engine: gemini | cloudflare | ollama | synergy | tg | claude.
    args.mem (MemView) -> glosar, pravila i primeri u promptu, TM i pravila na kraju (kes ostaje cist);
    args.mem_report (dict) -> {"examples","glossary","hints","exact","rules"}."""
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    engine, model = args.engine, args.model
    mem = mem_of(args)
    mem_gl: list[dict] = []
    mem_hints: list[dict] = []
    n_ex = 0
    if mem:
        mem_gl = mem.glossary_for(title + "\n" + "\n".join(c["text"] for c in cues))
        mem_hints = mem.hints()
    tag = re.sub(r"[^a-zA-Z0-9]+", "_", f"{lang}_{engine}_{model}")
    cache_p = work / f"prevod_{tag}.json"
    fp = src_fingerprint(cues)
    cache = load_json(cache_p)
    if cache.get("__src__") != fp:
        cache = renew_cache(cache, fp, cues, args)[0]
    else:
        mark_lines(cache, cues, args, cache_p)
    for i, c in enumerate(cues):
        c["id"] = i
    src_code = getattr(args, "src_lang", "") or "en"
    status = getattr(args, "status", None)
    todo = [c for c in cues if str(c["id"]) not in cache]
    drafts = None
    if todo and engine in ("synergy", "tg"):  # TranslateGemma prvo (jedna zamena modela)
        tg_p = work / f"tg_{lang}_cache.json"
        tgc = load_json(tg_p)
        if tgc.get("__src__") != fp:
            tgc = renew_cache(tgc, fp, cues, args)[0]
        need = [c for c in todo if str(c["id"]) not in tgc]
        if need:
            def keep(b0, texts, need=need):
                for c, d in zip(need[b0:b0 + len(texts)], texts):
                    tgc[str(c["id"])] = d
                save_json(tg_p, tgc)
            tg_lines([c["text"] for c in need], src_code, lang, args, status=status, on_block=keep)
        drafts = tgc
        if engine == "tg":
            for c in todo:
                cache[str(c["id"])] = tgc[str(c["id"])]
            save_json(cache_p, cache)
            todo = []
    batches = [b for b in scene_batches(cues, max(1, int(args.batch or 30)))
               if any(str(c["id"]) not in cache for c in b)] if todo else []
    if engine in CLOUD_CHAIN:  # za ne-srpske jezike lanac zavrsava na TranslateGemma, ne na Gemmi
        chain = [(e, model if e == engine else CLOUD_DEFAULT[e]) for e in CLOUD_CHAIN[engine] if e != "ollama"]
        fns = {"gemini": call_gemini, "cloudflare": call_cloudflare}
        st = {"i": 0}

        def llm(p, schema=None, system=None):
            tries = 0
            while True:
                if st["i"] >= len(chain):
                    raise QuotaError("cloud nedostupan")
                e, m = chain[st["i"]]
                try:
                    return fns[e](p, m, schema, system)
                except QuotaError as ex:
                    if not ex.daily and ex.wait and tries < 3:
                        tries += 1
                        time.sleep(min(ex.wait, 60))
                        continue
                    st["i"] += 1
                    tries = 0
                    log(f"{e} nedostupan ({ex})" + (f" → {chain[st['i']][0]}" if st["i"] < len(chain) else
                                                    " → lokalni TranslateGemma"))
    else:
        llm = make_llm("ollama" if engine == "synergy" else engine, GEMMA_MODEL if engine == "synergy" else model)
    cloud_down = False
    n_sent = 0  # broj poslatih gen_prompt-ova (glosar/pravila "u promptu" samo ako je bar jedan poslat)
    if progress:
        progress(0, len(batches))
    for n, b in enumerate(batches, 1):
        prev = [c["text"] for c in cues[max(0, b[0]["id"] - 6):b[0]["id"]]]
        got: dict[int, str] = {}
        exs = mem.examples([c["text"] for c in b]) if mem and not cloud_down else None
        n_ex += bool(exs) and not cloud_down
        for attempt in range(0 if cloud_down else 3):
            try:
                n_sent += 1
                txt = ask(llm, gen_prompt(b, prev, title, lang, drafts, examples=exs, glossary=mem_gl, hints=mem_hints),
                          args, schema=GEN_SCHEMA, system=gen_system(lang))
                d = parse_json_object(txt)
                got = {int(o["id"]): str(o.get("tr") or "").strip() for o in d.get("items") or [] if "id" in o}
                break
            except QuotaError:
                cloud_down = True  # ostatak ide preko lokalnog TranslateGemma
                break
            except Exception as e:  # noqa: BLE001
                if "otkazano" in str(e):
                    raise
                log(f"blok {n}: {e} (pokusaj {attempt + 1})")
                time.sleep(2 * (attempt + 1))
        if cloud_down and not drafts:  # TranslateGemma za ceo blok odjednom (numerisano)
            got = {c["id"]: t for c, t in zip(b, tg_lines([c["text"] for c in b], src_code, lang, args))}
        for c in b:
            out = got.get(c["id"], "")
            if not _gen_ok(c["text"], out, lang):  # rupa ili sumnjiv red -> nacrt ili TranslateGemma
                out = (drafts or {}).get(str(c["id"])) or " ".join(ask(
                    lambda p, schema=None, system=None: call_tg(p), tg_prompt(src_code, lang, c["text"]), args).split())
            cache[str(c["id"])] = out
        save_json(cache_p, cache)
        if progress:
            progress(n, len(batches))
    n_tm = n_rules = 0
    for c in cues:
        t = cache[str(c["id"])]
        if mem:  # pre prelamanja (pravila traze celu frazu); u kes ne ide
            t, tm, nr = mem_post(mem, c["text"], t, None)
            n_tm, n_rules = n_tm + tm, n_rules + nr
        c["tr"] = wrap_lines(t)
    if mem:
        n_gl, n_hi = (len(mem_gl), len(mem_hints)) if n_sent else (0, 0)  # tg / sve iz kesa: bez prompta
        mem_report(args, examples=n_ex, glossary=n_gl, hints=n_hi, exact=n_tm, rules=n_rules)
        log(f"Memorija ({lang}): primeri u {n_ex} blokova, glosar {n_gl}, pravila u promptu {n_hi} · "
            f"prevodna memorija {n_tm} linija, {n_rules} zamena po pravilima")
    return cues


CYR2LAT = dict(zip("абвгдђежзијклљмнњопрстћуфхцчџшАБВГДЂЕЖЗИЈКЛЉМНЊОПРСТЋУФХЦЧЏШ",
                   ["a", "b", "v", "g", "d", "đ", "e", "ž", "z", "i", "j", "k", "l", "lj", "m", "n", "nj",
                    "o", "p", "r", "s", "t", "ć", "u", "f", "h", "c", "č", "dž", "š",
                    "A", "B", "V", "G", "D", "Đ", "E", "Ž", "Z", "I", "J", "K", "L", "Lj", "M", "N", "Nj",
                    "O", "P", "R", "S", "T", "Ć", "U", "F", "H", "C", "Č", "Dž", "Š"]))
LAT2CYR_MULTI = [("dž", "џ"), ("Dž", "Џ"), ("DŽ", "Џ"), ("lj", "љ"), ("Lj", "Љ"), ("LJ", "Љ"),
                 ("nj", "њ"), ("Nj", "Њ"), ("NJ", "Њ")]
LAT2CYR = {v: k for k, v in CYR2LAT.items() if len(v) == 1}


# reci gde lj/nj/dž NISU jedno slovo (preslovljavanje bi bilo pogresno)
DIGRAPH_EXC = ("nadž", "odž", "podž", "injek", "konjug", "konjunk", "vanjezi", "tanjug")


def to_cyr(text: str, keep: set[str]) -> str:
    """Latinica -> cirilica; imena/brendovi iz originala i reci sa q/w/x/y ostaju latinicom."""
    def conv(m):
        w = m.group(0)
        if w in keep or re.search(r"[qwxyQWXY]", w) or w.isdigit():
            return w
        low = w.lower()
        out, i = [], 0
        while i < len(w):
            two = w[i:i + 2]
            if two.lower() in ("lj", "nj", "dž") and not any(low.startswith(e) or e in low[max(0, i - 3):i + 4]
                                                             for e in DIGRAPH_EXC):
                out.append({"lj": "љ", "nj": "њ", "dž": "џ"}[two.lower()] if two[0].islower()
                           else {"lj": "Љ", "nj": "Њ", "dž": "Џ"}[two.lower()])
                i += 2
                continue
            out.append(LAT2CYR.get(w[i], w[i]))
            i += 1
        return "".join(out)
    return re.sub(r"[^\W\d_]+", conv, text)


def script_keep(src_texts: list[str]) -> set[str]:
    """Reci iz izvora koje u cirilici ostaju latinicom: samo imena/brendovi (veliko pocetno slovo, 3+ slova);
    obicne reci ("to", "I") se preslovljavaju."""
    return {w for w in re.findall(r"[^\W\d_]+", " ".join(src_texts)) if len(w) >= 3 and w[0].isupper()}


def finalize_script(text: str, script: str, src_texts: list[str]) -> str:
    text = normalize_script(text, "lat")
    if script != "cyr":
        return text
    return to_cyr(text, script_keep(src_texts))


def normalize_script(text: str, script: str) -> str:
    """Model ponekad pomesa pisma usred reci (npr. 'izvuкли'); ispravi deterministicki."""
    if script == "cyr":
        # samo reci koje su vec delimicno cirilicne; cisto latinicna imena ostaju
        def fix(m):
            w = m.group(0)
            if not re.search(r"[\u0400-\u04FF]", w):
                return w
            for a, b in LAT2CYR_MULTI:
                w = w.replace(a, b)
            return "".join(LAT2CYR.get(ch, ch) for ch in w)
        return re.sub(r"\w+", fix, text)
    return "".join(CYR2LAT.get(ch, ch) for ch in text)


REVIEW_PROMPT = (
    "Ti si lektor srpskih titlova. Dobijas original i predlog prevoda. Ispravi SAMO greske: "
    "pogresne ili nepostojece reci, rod/broj/padez, doslovne prevode idioma, reci ostavljene na "
    "stranom jeziku koje imaju srpski ekvivalent, mesanje pisma, ijekavicu -> ekavica. Dobar prevod "
    "ostavi kakav jeste. Ne skracuj, ne dodaji. Odgovaras ISKLJUCIVO JSON-om."
)


def review_batch(batch: list[dict], drafts: dict[int, str], title: str, script: str, llm) -> dict[int, str]:
    pismo = "cirilicom" if script == "cyr" else "latinicom"
    items = [{"id": c["id"], "orig": c["text"], "sr": drafts[c["id"]]} for c in batch]
    prompt = (f"{REVIEW_PROMPT}\nKontekst videa: \"{title}\"\nPisano {pismo}.\n\n"
              f"Ulaz:\n{json.dumps(items, ensure_ascii=False)}\n\n"
              f"Izlaz: kompaktan JSON u jednom redu, bez uvlacenja, za SVE id-jeve: "
              f"{{\"items\":[{{\"id\":<broj>,\"sr\":\"<ispravljen prevod>\"}},...]}}")
    try:
        arr = parse_json_array(llm(prompt))
        got = {int(o["id"]): str(o["sr"]).strip() for o in arr if "id" in o and "sr" in o}
    except Exception:  # noqa: BLE001  lektura je opciona; na gresku ostaje nacrt
        return drafts
    return {k: (got.get(k) or v) for k, v in drafts.items()}


# ----------------------------------------------------------------- lektura postojeceg prevoda (v3)
LEKTURA_BLOCK = 40
LEKTURA_SCHEMA = {"type": "object", "required": ["items"], "properties": {"items": {
    "type": "array", "items": {"type": "object", "required": ["id", "t"],
                               "properties": {"id": {"type": "integer"}, "t": {"type": "string"}}}}}}
LEKTURA_SYS = {
    "sr": ("Ti si iskusan lektor srpskih titlova (ekavica). Dobijas izvorni titl i postojeci prevod. Menjas sto "
           "manje: ispravljas samo gramatiku, rod, broj i padez, pogresno znacenje u odnosu na izvor, doslovne "
           "prevode idioma, reci ostavljene na stranom jeziku koje imaju srpski ekvivalent i nedosledna imena i "
           "termine. Dobar red ostavljas TACNO kakav jeste. Ne skracujes, ne dodajes, ne spajas redove. "
           "Odgovaras ISKLJUCIVO validnim JSON-om."),
    "el": ("You are an experienced proofreader of Greek subtitles. You get the source subtitle and an existing Greek "
           "translation. Change as little as possible: fix only grammar, gender/number/case, meaning errors versus "
           "the source, literal idioms and inconsistent names/terms. Leave good lines EXACTLY as they are. Do not "
           "shorten, add or merge lines. Output ONLY valid JSON."),
}


def lektura_prompt(items: list[dict], prev: list[str], title: str, lang: str,
                   examples: list[dict] | None = None, glossary: list[dict] | None = None) -> str:
    """Memorija: korisnicki glosar posle naslova (isti za ceo video), primeri bloka pre "Ulaz"."""
    if lang == "el":
        return (f"Video (title and description): \"{title}\"\n"
                f"{mem_glossary_block(glossary)}"
                f"Previous (already proofread) subtitles, context only: {json.dumps(prev, ensure_ascii=False)}\n\n"
                "Each item has the source text 'src' and the Greek translation 't'. Return every id exactly once, "
                "with 't' corrected only where needed (Greek script).\n"
                f"{examples_block(examples, en=True)}"
                f"Ulaz:\n{json.dumps(items, ensure_ascii=False)}\n\n"
                'Output: compact one-line JSON: {"items":[{"id":<number>,"t":"<text>"}]}')
    gl = ("Glosar korisnika — imena i termine piši TAČNO ovako (menjaj samo padež i broj): "
          f"{json.dumps(glossary, ensure_ascii=False)}\n") if glossary else ""
    return (f"Video (naslov i opis): \"{title}\"\n"
            f"{gl}"
            f"Prethodni (vec lektorisani) titlovi, samo kontekst: {json.dumps(prev, ensure_ascii=False)}\n\n"
            "Svaka stavka ima izvorni tekst 'src' i srpski prevod 't'. Vrati svaki id tacno jednom, sa 't' "
            "ispravljenim samo gde treba. Pisi latinicom sa č ć š ž đ. Imena i termine drzi dosledno kroz ceo video.\n"
            f"{examples_block(examples)}"
            f"Ulaz:\n{json.dumps(items, ensure_ascii=False)}\n\n"
            'Izlaz: kompaktan JSON u jednom redu, bez uvlacenja: {"items":[{"id":<broj>,"t":"<tekst>"}]}')


def _lektura_items(txt: str) -> dict[int, str]:
    """{"items":[{"id","t"}]} (ili goli niz); los oblik -> ValueError (pozivalac ponavlja blok)."""
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", (txt or "").strip())
    if body.startswith("["):  # goli niz: parse_json_object bi uzeo samo prvi {..} (1 stavka -> nista)
        arr = parse_json_array(body)
    else:
        try:
            d = parse_json_object(body)
        except ValueError:
            d = {}
        if not isinstance(d.get("items"), list):
            d = {"items": parse_json_array(body)}  # "Evo: [{..}]" ili nista upotrebljivo -> ValueError
        arr = d["items"]
    out = {}
    for o in arr:
        if isinstance(o, dict) and "id" in o and isinstance(o.get("t", o.get("sr")), str):
            try:
                out[int(o["id"])] = o.get("t", o.get("sr"))
            except (TypeError, ValueError):
                continue
    return out


def review_translation(src_cues: list[dict] | None, draft_cues: list[dict], title: str, lang: str, llm, args,
                       progress=None) -> list[str]:
    """Lektura postojeceg prevoda (lat | cyr | el), u blokovima, poravnanje 1:1 po indeksu.
    Vraca listu tekstova iste duzine kao draft_cues; red koji model izostavi ili pokvari ostaje nacrt.
    Srpski: model radi u latinici, pa finalize_script (cirilica deterministicki); grcki: provera pisma.
    args.mem (MemView) -> glosar i primeri u promptu, naucena pravila na kraju; args.mem_report (dict)."""
    src = [(c.get("text") or "") for c in (src_cues or [])]
    drafts = [" ".join((c.get("text") or "").split()) for c in draft_cues]
    out = list(drafts)
    block = max(1, int(getattr(args, "batch", 0) or LEKTURA_BLOCK))
    total = (len(drafts) + block - 1) // block
    sr = lang in ("lat", "cyr")
    system = LEKTURA_SYS["sr" if sr else "el"]
    status = getattr(args, "status", None)
    mem = mem_of(args)
    mem_gl: list[dict] = []
    n_ex = 0
    if mem:
        mem_gl = mem.glossary_for("\n".join([title] + src + drafts))
    for bi, b0 in enumerate(range(0, len(drafts), block)):
        if is_cancelled(args):
            raise RuntimeError("otkazano")
        ids = list(range(b0, min(b0 + block, len(drafts))))
        items = [{"id": i, "src": " ".join(src[i].split()) if i < len(src) else "",
                  "t": normalize_script(drafts[i], "lat") if sr else drafts[i]} for i in ids]
        prev = out[max(0, b0 - 3):b0]
        exs = mem.examples([it["src"] for it in items] + [it["t"] for it in items]) if mem else None
        n_ex += bool(exs)
        prompt = lektura_prompt(items, prev, title, "sr" if sr else "el", examples=exs, glossary=mem_gl)
        got: dict[int, str] = {}
        for attempt in (1, 2):
            try:
                got = _lektura_items(ask(llm, prompt, args, schema=LEKTURA_SCHEMA, system=system))
                break
            except (ValueError, KeyError) as e:
                log(f"lektura blok {bi + 1}: los JSON ({e}){' — ponavljam' if attempt == 1 else ' — ostaje nacrt'}")
        for i in ids:
            t = " ".join((got.get(i) or "").split())
            if not t:
                continue
            s = src[i] if i < len(src) else ""
            if sr:
                t = finalize_script(t, lang, [s] if s else [])
            elif not _gen_ok(s, t, lang):
                continue  # grcki bez grckih slova / mesano pismo / neprevedeno -> ostaje nacrt
            out[i] = t
        if status:
            status(f"lektura {bi + 1}/{total}")
        if progress:
            progress(bi + 1, total)
    if mem:  # naucena pravila na ceo rezultat (i na redove koje model nije dirao)
        n_rules = 0
        keep = script_keep(src) if lang == "cyr" else None  # imena iz celog izvora
        for i, t in enumerate(out):
            out[i], _, r = mem_post(mem, src[i] if i < len(src) else "", t, lang if sr else None, use_exact=False,
                                    keep=keep)
            n_rules += r
        n_gl = len(mem_gl) if drafts else 0  # glosar je u promptu samo ako je bar jedan blok poslat
        mem_report(args, examples=n_ex, glossary=n_gl, hints=0, exact=0, rules=n_rules)
        log(f"Memorija (lektura): primeri u {n_ex} blokova, glosar {n_gl}, {n_rules} zamena po pravilima")
    return out


def wrap_lines(text: str, width: int = 42) -> str:
    text = " ".join(text.split())
    if len(text) <= width:
        return text
    words, best = text.split(), None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        score = abs(len(a) - len(b))
        if best is None or score < best[0]:
            best = (score, a, b)
    return f"{best[1]}\n{best[2]}"


# ----------------------------------------------------------------- burn
def video_dims(ffmpeg: str, video: Path) -> tuple[int, int, float]:
    ffprobe = str(Path(ffmpeg).with_name("ffprobe"))
    p = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height:format=duration", "-of", "json", str(video)],
                       capture_output=True, text=True)
    d = json.loads(p.stdout or "{}")
    st = (d.get("streams") or [{}])[0]
    return int(st.get("width", 1920)), int(st.get("height", 1080)), float(d.get("format", {}).get("duration", 0) or 0)


def burn(ffmpeg: str, video: Path, ass: Path, srt: Path, out: Path, args) -> None:
    w, h, dur = video_dims(ffmpeg, video)
    log(f"Burn titla -> {out.name} ({w}x{h}, encoder {args.encoder})...")
    if args.encoder == "hevc":
        venc = ["-c:v", "hevc_videotoolbox", "-q:v", str(args.quality), "-tag:v", "hvc1"]
    elif args.encoder == "h264":
        venc = ["-c:v", "h264_videotoolbox", "-q:v", str(args.quality)]
    else:  # x265 softverski, sporije ali manji fajl
        venc = ["-c:v", "libx265", "-crf", "20", "-preset", "medium", "-tag:v", "hvc1"]
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-stats", "-y",
           "-i", video.name, "-i", srt.name,
           "-map", "0:v:0", "-map", "0:a?", "-map", "1:0",
           "-vf", f"ass={ass.name}",
           *venc,
           "-c:a", "aac", "-b:a", "192k",
           "-c:s", "mov_text", "-metadata:s:s:0", "language=srp",
           "-metadata:s:s:0", "title=Srpski",
           "-movflags", "+faststart", str(out.resolve())]
    run(cmd, cwd=str(video.parent))


# ----------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description="YouTube -> srpski titl -> burn (licna arhiva)")
    ap.add_argument("url", nargs="?")
    ap.add_argument("--resume", type=Path, help="radni folder prekinutog posla")
    ap.add_argument("-o", "--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--browser", default="chrome",
                    help="browser za cookies (chrome, safari, firefox, brave, edge, none)")
    ap.add_argument("--cookies", type=Path, help="cookies.txt umesto browsera")
    ap.add_argument("--format", default="bv*+ba/b", help="yt-dlp format selektor")
    ap.add_argument("--sub-lang", help="forsiraj jezik izvornog titla (npr. en, en-orig, de)")
    ap.add_argument("--cyr", dest="script", action="store_const", const="cyr", default="lat",
                    help="prevod cirilicom (default latinica)")
    ap.add_argument("--engine", choices=["ollama", "claude"], default="ollama",
                    help="prevodilac: lokalni ollama (default) ili claude -p")
    ap.add_argument("--model", default=None,
                    help="model (default: gemma4:12b-it-qat za ollama, sonnet za claude)")
    ap.add_argument("--jobs", type=int, default=None, help="paralelnih prevodilackih poziva")
    ap.add_argument("--batch", type=int, default=30, help="najvise titlova po bloku (blokovi se seku po scenama)")
    ap.add_argument("--no-context", dest="ctx_pass", action="store_false",
                    help="bez pre-pass-a (sinopsis + glosar + oslovljavanje)")
    ap.add_argument("--no-qc", dest="qc_retry", action="store_false",
                    help="QC samo izvestaj, bez ponovnog prevoda sumnjivih linija")
    ap.add_argument("--whisper-model", default="turbo")
    ap.add_argument("--force-whisper", action="store_true", help="ignorisi YT titlove")
    ap.add_argument("--no-burn", action="store_true", help="bez burn-a, samo .srt")
    ap.add_argument("--encoder", choices=["hevc", "h264", "x265"], default="hevc")
    ap.add_argument("--quality", type=int, default=65, help="VideoToolbox -q:v (1-100)")
    args = ap.parse_args()
    args.model = args.model or ("gemma4:12b-it-qat" if args.engine == "ollama" else "sonnet")
    args.jobs = args.jobs or (1 if args.engine == "ollama" else 6)
    args.qc = {}

    try:
        ffmpeg = find_ffmpeg()
    except RuntimeError as e:
        die(str(e))
    for b in ("yt-dlp",):
        if not shutil.which(b):
            die(f"nedostaje {b}")

    if args.resume:
        work = args.resume.expanduser().resolve()
        meta = json.loads((work / "meta.json").read_text())
        url = meta["url"]
    else:
        if not args.url:
            ap.error("treba URL ili --resume")
        url = args.url
        meta = None

    info = probe(args, url)
    title, vid = info.get("title") or "video", info.get("id") or "x"
    if not args.resume:
        date = info.get("upload_date") or time.strftime("%Y%m%d")
        work = args.out.expanduser() / safe_name(f"{date} - {title} [{vid}]")
        work.mkdir(parents=True, exist_ok=True)
    (work / "meta.json").write_text(json.dumps(
        {"url": url, "id": vid, "title": title, "channel": info.get("channel"),
         "live_status": info.get("live_status"), "language": info.get("language")},
        ensure_ascii=False, indent=2))
    (work / "info.json").write_text(json.dumps(info, ensure_ascii=False))
    log(f"Naslov: {title}  |  kanal: {info.get('channel')}  |  status: {info.get('live_status')}")
    log(f"Folder: {work}")

    video = download_video(args, url, info, work)

    # posle live-a titlovi se pojave tek kad stream zavrsi -> osvezi info
    if info.get("live_status") in ("is_live", "is_upcoming"):
        info = probe(args, url)

    cues, is_sr, src = None, False, None
    if not args.force_whisper:
        tr = pick_track(info, args.sub_lang)
        if tr:
            kind, lang, is_sr = tr
            log(f"Titl: {'RUCNI CC' if kind == 'manual' else 'auto-generated'} [{lang}]"
                + (" -> vec srpski, bez prevoda" if is_sr else ""))
            j = fetch_json3(args, url, kind, lang, work)
            if j:
                cues = cues_from_json3(j, kind)
                src = f"{kind}:{lang}"
    if not cues:
        cues = whisper_cues(args, video, work, ffmpeg)
        src, is_sr = f"whisper:{args.whisper_model}", False
    if not cues:
        die("nema titlova ni posle whisper-a")
    log(f"Izvor titla: {src}, {len(cues)} titlova")

    stem = safe_name(title)
    write_srt(cues, work / f"{stem}.orig.srt")
    if is_sr:
        for c in cues:
            c["sr"] = c["text"]
    else:
        ctx_title = title + (" — " + (info.get("description") or "")[:600] if info.get("description") else "")
        translate(cues, ctx_title, args, work)
    for c in cues:
        c["sr"] = wrap_lines(c["sr"])
    sr_srt = work / f"{stem}.sr.srt"
    write_srt(cues, sr_srt, key="sr")
    log(f"Srpski titl: {sr_srt.name}")

    if args.no_burn:
        log("Gotovo (bez burn-a).")
        return
    w, h, _ = video_dims(ffmpeg, video)
    ass = work / "sr.ass"
    write_ass(cues, ass, w, h, key="sr")
    shutil.copy(sr_srt, work / "sr.srt")
    out = work / f"{stem} [{vid}].sr.mp4"
    burn(ffmpeg, video, ass, work / "sr.srt", out, args)
    log(f"GOTOVO: {out}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        die("prekinuto; nastavi sa --resume <folder>", 130)
