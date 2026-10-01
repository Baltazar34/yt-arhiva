#!/usr/bin/env python3
"""Test 3.2 "Šta da uradim" backend (docs/plan-contract.md §A, §B migracija/recept samo, §D):
opts.after (validacija + koraci posle preuzimanja), titlovi u MKV izvoru (embed_subs), subs "all+tr",
audio_groups sa ulogom, tr_state/translated/transcripts (biblioteka + katalog), akcija transcribe, recept "samo"
i jednokratna migracija recipe arhiva -> samo.

  python3 tests/test_plan_api.py            # panel na 127.0.0.1:8824 (YTA_TEST_PORT), privremeni YTA_DATA/out_dir
  python3 tests/test_plan_api.py embed      # samo testovi ciji naziv sadrzi 'embed'
  YTA_TEST_TMP=/neki/folder  -> privremeni folder "plan-*" pravi se tu;  YTA_TEST_KEEP=1 -> ne brisi ga

Bez mreze i bez modela: PATH ima lazni `yt-dlp` (Python skripta) koji za poznate ID-jeve vraca info JSON,
"preuzima" mali pravi MKV (ffmpeg lavfi: video + 3 audio + poglavlja + omot) i pise json3 titlove;
za sve ostalo odmah pada. Whisper se nikad ne pokrece (transcribe se proverava kroz razloge preskakanja
i oblik posla u procesu, bez start_job).
"""
from __future__ import annotations

import hashlib
import http.client
import importlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PORT = int(os.environ.get("YTA_TEST_PORT", "8824"))
BIN = Path(os.environ.get("TB_FFMPEG_DIR", "/opt/homebrew/opt/ffmpeg-full/bin"))
FF, FP = str(BIN / "ffmpeg"), str(BIN / "ffprobe")
TMP = Path(tempfile.mkdtemp(prefix="plan-", dir=os.environ.get("YTA_TEST_TMP") or None)).resolve()
DATA, OUT, FAKEBIN, FAKE = TMP / "data", TMP / "out", TMP / "bin", TMP / "fake"
VID = "PPPPPPPPPPP"
URL = f"https://www.youtube.com/watch?v={VID}"
IT: dict[str, Path] = {}
STATE: dict = {}

SRT_EN = "1\n00:00:00,200 --> 00:00:00,900\nHello there\n\n2\n00:00:01,000 --> 00:00:01,800\nGeneral Kenobi\n"

# lazni yt-dlp: -J (info), --flat-playlist (lista), --skip-download (json3 titlovi), inace "preuzimanje"
FAKE_YTDLP = r'''#!{py}
import json, os, re, shutil, sys
from pathlib import Path
FAKE = Path({fake!r})
args = sys.argv[1:]
url = args[-1] if args else ""
with open(FAKE / "calls.jsonl", "a") as fh:
    fh.write(json.dumps(args) + "\n")

def opt(name):
    return args[args.index(name) + 1] if name in args else ""

if "--flat-playlist" in args:
    m = re.search(r"list=([A-Za-z0-9_-]+)", url)
    p = FAKE / "lists" / f"{{m.group(1)}}.json" if m else None
    if not p or not p.exists():
        sys.exit("ERROR: lazni yt-dlp: nepoznata lista")
    print(p.read_text())
    sys.exit(0)
m = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{{11}})", url)
p = FAKE / "info" / f"{{m.group(1)}}.json" if m else None
if not p or not p.exists():
    sys.exit("ERROR: lazni yt-dlp (test bez mreze)")
info = json.loads(p.read_text())
if "-J" in args:
    print(json.dumps(info))
    sys.exit(0)
outs = [args[i + 1] for i, a in enumerate(args) if a == "-o"]
if "--skip-download" in args:
    manual, auto = info.get("subtitles") or {{}}, info.get("automatic_captions") or {{}}
    for lg in opt("--sub-langs").split(","):
        if lg in manual and "--write-subs" in args:
            ev = [{{"tStartMs": 200, "dDurationMs": 700, "segs": [{{"utf8": "Hello " + lg}}]}},
                  {{"tStartMs": 1000, "dDurationMs": 800, "segs": [{{"utf8": "Second line"}}]}}]
        elif lg in auto and "--write-auto-subs" in args:
            ev = [{{"tStartMs": 0, "dDurationMs": 1900, "segs": [{{"utf8": "hello"}}, {{"utf8": " world", "tOffsetMs": 400}}]}}]
        else:
            continue
        Path(outs[-1].replace("%(ext)s", lg + ".json3")).write_text(json.dumps({{"events": ev}}))
    sys.exit(0)
tmpl = next(o for o in outs if not o.startswith("thumbnail:"))
ids = ["137" if x.startswith("bv") else x for x in opt("-f").split("/")[0].split("+")]
shutil.copyfile(FAKE / "fixture.mkv", tmpl.replace("%(ext)s", "mkv"))
th = next((o[len("thumbnail:"):] for o in outs if o.startswith("thumbnail:")), None)
if th:
    shutil.copyfile(FAKE / "cover.jpg", th.replace("%(ext)s", "jpg"))
i = args.index("--print-to-file")
Path(args[i + 2]).write_text("+".join(ids) + "\n")
print("[download] 100% lazno")
'''


def fmt(fid, lang, lp, note, acodec="opus", abr=128):
    return {"format_id": fid, "vcodec": "none", "acodec": acodec, "language": lang, "language_preference": lp,
            "format_note": note, "abr": abr, "ext": "webm" if acodec == "opus" else "m4a"}


AUDIO_FORMATS = [
    fmt("251", "en", 10, "English original, medium", abr=130),
    fmt("140", "en", 10, "English original, medium", "mp4a.40.2", 128),       # isti zvuk, drugi kodek
    fmt("251-drc", "en", 10, "English original, medium, DRC", abr=131),       # DRC duplikat
    fmt("250", "en", -10, "English descriptive, low", abr=70),                 # opisna traka (ne sme nestati)
    fmt("139", "en", -10, "English descriptive, low", "mp4a.40.5", 48),
    fmt("251-1", "de", -1, "German (Germany) dubbed-auto, medium", abr=120),
]
INFO = {"id": VID, "title": "Plan test", "fulltitle": "Plan test", "channel": "Kanal", "uploader": "Kanal",
        "duration": 2, "upload_date": "20260102", "language": "en", "live_status": "not_live", "thumbnail": None,
        "description": "opis",
        "formats": [{"format_id": "137", "vcodec": "avc1.4d401e", "acodec": "none", "height": 120, "width": 160,
                     "fps": 25, "tbr": 100, "ext": "mp4", "protocol": "https"}] + AUDIO_FORMATS,
        "subtitles": {"en": [{"ext": "json3", "name": "English"}], "de": [{"ext": "json3", "name": "Deutsch"}]},
        "automatic_captions": {"en-orig": [{"ext": "json3"}], "en": [{"ext": "json3"}], "fr": [{"ext": "json3"}]}}


# ================================================================ pomocne
def sh(cmd: list[str]) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(f"komanda nije uspela: {' '.join(cmd)}\n{p.stderr[-800:]}")
    return p


def probe(path: Path) -> dict:
    return json.loads(sh([FP, "-v", "error", "-show_chapters", "-show_entries",
                          "stream=index,codec_type,codec_name:stream_tags:stream_disposition=default,attached_pic"
                          ":format_tags", "-of", "json", str(path)]).stdout)


def subs_of(pr: dict) -> list[tuple]:
    return [((s.get("tags") or {}).get("language"), (s.get("tags") or {}).get("title"), s["disposition"]["default"])
            for s in pr["streams"] if s["codec_type"] == "subtitle"]


def has_cover(pr: dict) -> bool:
    """Omot je MKV prilog (attached_pic 1) i jedina video traka pored glavne (ne druga obicna video traka)."""
    vids = [s for s in pr["streams"] if s["codec_type"] == "video"]
    cover = [s for s in vids if {k.lower(): v for k, v in (s.get("tags") or {}).items()}.get("filename") == "cover.jpg"]
    return len(cover) == 1 and cover[0]["disposition"].get("attached_pic") == 1 and \
        len([s for s in vids if not s["disposition"].get("attached_pic")]) == 1


def req(method: str, path: str, body=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=60)
    h = {"Host": f"127.0.0.1:{PORT}"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    b = r.read()
    c.close()
    try:
        return r.status, json.loads(b)
    except ValueError:
        return r.status, None


def get(path: str, code: int = 200):
    st, j = req("GET", path)
    assert st == code, f"GET {path} -> {st} {j} (ocekivano {code})"
    return j


def post(path: str, body: dict, code: int = 200):
    st, j = req("POST", path, body)
    assert st == code, f"POST {path} -> {st} {j} (ocekivano {code})"
    return j


def batch(folders, action, params=None, code=200):
    return post("/api/library/batch", {"folders": [str(f) for f in folders], "action": action,
                                       "params": params or {}}, code)


def wait_jobs(ids: list[str], timeout: float = 90) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        by = {j["id"]: j for j in get("/api/jobs")}
        if all(by.get(i, {}).get("state") in ("done", "error", "cancelled") for i in ids):
            return {i: by[i] for i in ids}
        time.sleep(0.3)
    raise AssertionError(f"poslovi nisu zavrseni: {ids}")


def job_log(jid: str) -> str:
    return "\n".join(get(f"/api/jobs/{jid}/log")["log"])


def assert_done(j: dict):
    assert j["state"] == "done" and not j["error"], f"{j['title']}: {j['state']} {j['error']}\n{job_log(j['id'])[-2500:]}"


def reason(res: dict, folder: Path) -> str:
    hit = [s["reason"] for s in res["skipped"] if Path(s["folder"]).resolve() == folder.resolve()]
    assert hit, f"{folder.name} nije u skipped: {res}"
    return hit[0]


def mk_item(tag: str, vid: str, title: str, *, root: Path | None = None, subs_files: dict | None = None, **m) -> Path:
    d = (root or OUT) / f"20260101 - {title} [{vid}]"
    (d / ".yta").mkdir(parents=True)
    (d / "subs").mkdir()
    man = {"version": 1, "url": f"https://www.youtube.com/watch?v={vid}", "id": vid, "title": title,
           "channel": "Test kanal", "duration": 2, "thumbnail": None, "upload_date": "20260101",
           "language": "en", "live_status": None, "source": None, "kind": None, "video": None,
           "audio": [], "subs": [], "outputs": [], "playlists": [], "updated": time.time()}
    man.update(m)
    (d / ".yta" / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=1))
    for rel, txt in (subs_files or {}).items():
        (d / rel).write_text(txt)
    IT[tag] = d
    return d


def manifest(d: Path) -> dict:
    return json.loads((d / ".yta" / "manifest.json").read_text())


def sub(key, kind, lang, file, **kw) -> dict:
    return {"key": key, "kind": kind, "lang": lang, "file": file, "count": 2, "name": kw.pop("name", key), **kw}


# ================================================================ lazna arhiva + lazni yt-dlp
def make_fixture():
    sh([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x120:d=1",
        "-frames:v", "1", str(FAKE / "cover.jpg")])
    (FAKE / "meta.txt").write_text(";FFMETADATA1\ntitle=Plan test\nartist=Kanal\n"
                                   "[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1000\ntitle=Uvod\n"
                                   "[CHAPTER]\nTIMEBASE=1/1000\nSTART=1000\nEND=2000\ntitle=Kraj\n")
    cmd = [FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x120:r=25:d=2"]
    for f in (440, 660, 880):
        cmd += ["-f", "lavfi", "-i", f"sine=f={f}:d=2"]
    cmd += ["-i", str(FAKE / "meta.txt"), "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:a",
            "-map_metadata", "4", "-map_chapters", "4", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "48k", "-attach", str(FAKE / "cover.jpg"),
            "-metadata:s:t:0", "mimetype=image/jpeg", "-metadata:s:t:0", "filename=cover.jpg", str(FAKE / "fixture.mkv")]
    sh(cmd)
    pr = probe(FAKE / "fixture.mkv")
    assert has_cover(pr) and len(pr["chapters"]) == 2, pr


def make_archive():
    for d in (OUT, DATA, FAKEBIN, FAKE / "info", FAKE / "lists", DATA / "playlists"):
        d.mkdir(parents=True)
    make_fixture()
    (FAKE / "info" / f"{VID}.json").write_text(json.dumps(INFO))
    f = FAKEBIN / "yt-dlp"
    f.write_text(FAKE_YTDLP.format(py=sys.executable, fake=str(FAKE)))
    f.chmod(0o755)
    # stanje prevoda / transkripta
    tr1 = mk_item("TR1", "TTTTTTTTTT1", "Prevedeno", subs=[
        sub("manual:en", "manual", "en", "subs/en.srt", name="English"),
        sub("sr:lat:synergy:gemma4:12b-it-qat:manual:en", "sr", "sr", "subs/sr-lat.a.srt", src="manual:en",
            engine="synergy", model="gemma4:12b-it-qat", date="2026-09-30"),
        sub("sr:lat:claude:sonnet:lektura:manual:en", "sr", "sr", "subs/sr-lat.b.srt", src="manual:en",
            engine="claude", model="sonnet", date="2026-10-01"),
        sub("tr:el:gemini:gemini-3.5-flash-lite:manual:en", "tr", "el", "subs/el.c.srt", src="manual:en")],
        subs_files={"subs/en.srt": SRT_EN, "subs/sr-lat.a.srt": SRT_EN, "subs/sr-lat.b.srt": SRT_EN,
                    "subs/el.c.srt": SRT_EN})
    mk_item("TR2", "TTTTTTTTTT2", "Samo whisper", subs=[sub("whisper", "whisper", "sr", "subs/whisper.srt")],
            subs_files={"subs/whisper.srt": SRT_EN})
    mk_item("TR3", "TTTTTTTTTT3", "Samo auto", subs=[sub("auto:en-orig", "auto", "en", "subs/en.auto.srt")],
            subs_files={"subs/en.auto.srt": SRT_EN})
    mk_item("TR4", "TTTTTTTTTT4", "Srpski CC", subs=[sub("manual:sr", "manual", "sr", "subs/sr.srt")],
            subs_files={"subs/sr.srt": SRT_EN})
    mk_item("TR5", "TTTTTTTTTT5", "Nema titla", subs=[sub("manual:en", "manual", "en", "subs/nema.srt")])
    # NA: video bez zvuka (audio [] u manifestu) -> transcribe "nema izvora zvuka"
    na = mk_item("NA", "NNNNNNNNNNA", "Bez zvuka", source="Bez zvuka [NNNNNNNNNNA].source.mkv", kind="video",
                 video={"width": 160, "height": 120}, audio=[])
    (na / "Bez zvuka [NNNNNNNNNNA].source.mkv").write_bytes(b"x")
    # katalog: probe jednog videa (TR1) i plejliste (TR2, TR5, nepoznat)
    (FAKE / "info" / "TTTTTTTTTT1.json").write_text(json.dumps({
        "id": "TTTTTTTTTT1", "title": "Prevedeno", "formats": [], "subtitles": {}, "automatic_captions": {}}))
    (FAKE / "lists" / "PLcat.json").write_text(json.dumps({"id": "PLcat", "title": "Katalog", "uploader": "U",
        "entries": [{"id": "TTTTTTTTTT2", "title": "Samo whisper"}, {"id": "TTTTTTTTTT5", "title": "Nema titla"},
                    {"id": "ZZZZZZZZZZZ", "title": "Nema je"}]}))
    (DATA / "playlists" / "PLplan.json").write_text(json.dumps({
        "id": "PLplan", "title": "Plan lista", "url": "https://www.youtube.com/playlist?list=PLplan",
        "uploader": "U", "fetched_at": 1790000000, "entries": [{"id": "XXXXXXXXXXX", "title": "Nema je", "pos": 1}],
        "history": [], "removed": []}))
    # stari podrazumevani recept "arhiva" bez plan_migrated -> migracija pri startu panela
    (DATA / "settings.json").write_text(json.dumps({
        "out_dir": str(OUT), "browser": "none", "cookies_file": "", "engine": "ollama",
        "model": "yta-test-nema-modela", "script": "lat", "recipe": "arhiva"}, indent=1))


# ================================================================ testovi: HTTP
def t_migration_and_samo():
    s = get("/api/settings")
    assert s["recipe"] == "samo" and s["plan_migrated"] is True and s["embed_subs"] is True, s
    disk = json.loads((DATA / "settings.json").read_text())
    assert disk["recipe"] == "samo" and disk["plan_migrated"] is True and disk["model"] == "yta-test-nema-modela", disk
    r = get("/api/recipes")
    assert r[0]["id"] == "samo" and r[0]["proc"] is None and r[0]["default"], r[0]
    assert r[0]["dl"] == {"video_fmt": "auto:best", "audio_fmt": "all", "subs": "all"}, r[0]
    assert "MKV" in r[0]["desc"] and [x["id"] for x in r if x["default"]] == ["samo"], r
    # izricit izbor "arhiva" posle migracije ostaje (migracija je jednokratna)
    assert post("/api/settings", {"recipe": "arhiva"})["recipe"] == "arhiva"
    assert json.loads((DATA / "settings.json").read_text())["plan_migrated"] is True
    assert post("/api/settings", {"recipe": "samo", "embed_subs": "false"})["embed_subs"] is False
    assert "MKV" not in get("/api/recipes")[0]["desc"]
    s = post("/api/settings", {"embed_subs": True})
    assert s["embed_subs"] is True and s["recipe"] == "samo", s


def t_after_validation():
    n_jobs = len(get("/api/jobs"))
    dl = {"video_fmt": "auto:best", "audio_fmt": "all", "subs": "all"}
    bad = [
        ({"foo": {}}, "nepoznat ključ"),
        ("toolbox", "objekat"),
        ({"toolbox": "remux"}, "objekat"),
        ({"toolbox": {"preset": "nema"}}, "preset"),
        ({"toolbox": {"preset": "remux", "sub": "x"}}, "sub"),
        ({"toolbox": {"preset": "remux", "sub": "soft", "target": "xx"}}, "target"),
        ({"toolbox": {"preset": "remux", "nepoznato": 1}}, "nepoznat ključ"),
        ({"audio": {"preset": "arhiva"}}, "preset"),
        ({"audio": {"preset": "audio_mp3", "sub": "soft"}}, "nepoznat ključ"),
    ]
    for after, word in bad:
        e = post("/api/jobs", {"url": URL, "opts": {"dl": dl, "proc": None, "after": after}}, 400)
        assert word in e["error"], (after, e)
    # upecen titl + preset koji kopira video -> ista poruka kao library_batch
    e = post("/api/jobs", {"url": URL, "opts": {"dl": dl, "after": {"toolbox": {"preset": "remux", "sub": "burn"}}}}, 400)
    e2 = batch([IT["TR1"]], "toolbox", {"preset": "remux", "sub": "burn"}, code=400)
    assert e["error"] == e2["error"] and "samo kopira" in e["error"], (e, e2)
    # batch: zajednicke i opcije stavke; nijedan posao se ne pravi
    e = post("/api/jobs/batch", {"items": [{"url": "http://127.0.0.1:9/a"}],
                                 "opts": {"dl": dl, "after": {"bad": 1}}}, 400)
    assert "nepoznat ključ" in e["error"], e
    e = post("/api/jobs/batch", {"items": [{"url": "http://127.0.0.1:9/a"},
                                           {"url": "http://127.0.0.1:9/b", "opts": {"after": {"audio": {"preset": "x"}}}}],
                                 "opts": {"dl": dl, "proc": None}}, 400)
    assert e["error"].startswith("stavka 2:"), e
    e = post("/api/playlists/PLplan/missing", {"opts": {"dl": dl, "proc": None, "after": {"toolbox": {"preset": "x"}}}}, 400)
    assert "preset" in e["error"], e
    assert len(get("/api/jobs")) == n_jobs
    # ispravno: normalizovano u opts, posao (nepoznat URL -> greska u probe-u) nema after poslova
    good = {"toolbox": {"preset": "remux"}, "audio": {"preset": "audio_mp3"}}
    r = post("/api/jobs/batch", {"items": [{"url": "http://127.0.0.1:9/a"},
                                           {"url": "http://127.0.0.1:9/b", "opts": {"after": {"audio": None}}}],
                                 "opts": {"dl": dl, "proc": None, "after": good}})
    r2 = post("/api/playlists/PLplan/missing", {"opts": {"dl": dl, "proc": None,
                                                         "after": {"toolbox": {"preset": "kompat", "sub": "soft"}}}})
    j1 = post("/api/jobs", {"url": "http://127.0.0.1:9/c", "opts": {"dl": dl, "after": {}}})
    js = wait_jobs(r["jobs"] + r2["jobs"] + [j1["id"]])
    a, b = (js[i] for i in r["jobs"])
    assert a["opts"]["after"] == {"toolbox": {"preset": "remux", "sub": "none", "target": None},
                                  "audio": {"preset": "audio_mp3"}}, a["opts"]
    assert b["opts"]["after"] == {"toolbox": {"preset": "remux", "sub": "none", "target": None}}, b["opts"]
    c = js[r2["jobs"][0]]
    assert c["opts"]["after"] == {"toolbox": {"preset": "kompat", "sub": "soft", "target": "lat"}}, c["opts"]
    assert "after" not in js[j1["id"]]["opts"], js[j1["id"]]["opts"]
    for j in js.values():
        assert j["state"] == "error" and j["after_jobs"] == [], (j["state"], j["after_jobs"])
        post(f"/api/jobs/{j['id']}/remove", {})


def t_download_embed_after():
    r = post("/api/jobs", {"url": URL, "opts": {"dl": {"video_fmt": "auto:best", "audio_fmt": "all", "subs": "all"},
                                               "proc": None,
                                               "after": {"toolbox": {"preset": "remux", "sub": "none"},
                                                         "audio": {"preset": "audio_m4a"}}}})
    j = wait_jobs([r["id"]])[r["id"]]
    assert_done(j)
    folder = Path(j["folder"])
    STATE.update(job=r["id"], folder=folder)
    assert folder.name == f"20260102 - Plan test [{VID}]", folder
    m = manifest(folder)
    src = folder / m["source"]
    # sve audio trake: original + dub + opisna (descriptive ne nestaje), bez kodek/DRC duplikata
    assert [a["role"] for a in m["audio"]] == ["original", "dub", "descriptive"], m["audio"]
    assert [a["format_id"] for a in m["audio"]] == ["251", "251-1", "250"], m["audio"]
    assert m["audio"][2]["title"] == "English — audio opis", m["audio"][2]
    # titlovi u subs/ i u MKV izvoru (bez YT prevoda), jezici ISO-639-2, nijedan podrazumevan
    keys = sorted(s["key"] for s in m["subs"])
    assert keys == ["auto:en-orig", "manual:de", "manual:en"], keys
    assert sorted(x["key"] for x in m["source_subs"]) == keys, m["source_subs"]
    assert {x["kind"] for x in m["source_subs"]} == {"manual", "auto"} and all(x["lang"] for x in m["source_subs"])
    pr = probe(src)
    assert subs_of(pr) == [("eng", "English (CC)", 0), ("deu", "Deutsch (CC)", 0), ("eng", "English (auto)", 0)], subs_of(pr)
    assert [s["codec_type"] for s in pr["streams"]].count("audio") == 3, pr["streams"]
    assert has_cover(pr), f"omot (attachment) izgubljen: {pr['streams']}"
    assert [c["tags"]["title"] for c in pr["chapters"]] == ["Uvod", "Kraj"], pr["chapters"]
    assert {k.lower(): v for k, v in pr["format"]["tags"].items()}.get("title") == "Plan test", pr["format"]
    assert not list((folder / ".yta").glob("work/*")), list((folder / ".yta").glob("work/*"))
    # koraci posle preuzimanja: Toolbox pa audio, kao posebni poslovi; preuzimanje ostaje "done"
    assert len(j["after_jobs"]) == 2, j
    log = job_log(r["id"])
    for jid, label in zip(j["after_jobs"], ("Samo remux", "Samo audio — M4A")):
        assert f"posle preuzimanja: {label} → posao {jid}" in log, log[-1500:]
    aj = wait_jobs(j["after_jobs"])
    for x in aj.values():
        assert_done(x)
        assert x["kind"] == "transcode" and Path(x["folder"]) == folder, x
    outs = manifest(folder)["outputs"]
    assert any(o.endswith(".remux.mkv") for o in outs) and any(o.endswith(".audio_m4a.m4a") for o in outs), outs
    assert all((folder / o).is_file() for o in outs), outs
    persisted = json.loads((DATA / "jobs.json").read_text()) if (DATA / "jobs.json").exists() else []
    t0 = time.time()
    while time.time() - t0 < 5 and not any(d["id"] == r["id"] and d.get("after_jobs") for d in persisted):
        time.sleep(0.3)
        persisted = json.loads((DATA / "jobs.json").read_text())
    assert next(d for d in persisted if d["id"] == r["id"])["after_jobs"] == j["after_jobs"]


def t_embed_again_and_retry():
    folder = STATE["folder"]
    src = folder / manifest(folder)["source"]
    # novi rucni CC na YouTube-u + subs all+tr (YT automatski prevodi): ubacuje se samo novi CC, prevod ne
    info = dict(INFO, subtitles={**INFO["subtitles"], "es": [{"ext": "json3", "name": "Español"}]})
    (FAKE / "info" / f"{VID}.json").write_text(json.dumps(info))
    calls0 = len((FAKE / "calls.jsonl").read_text().splitlines())
    r = post("/api/jobs", {"url": f"https://youtu.be/{VID}", "opts": {"dl": {"video_fmt": "auto:best",
                                                                            "audio_fmt": "all", "subs": "all+tr"}}})
    j = wait_jobs([r["id"]])[r["id"]]
    assert_done(j)
    assert Path(j["folder"]) == folder and j["after_jobs"] == [], j
    calls = [json.loads(x) for x in (FAKE / "calls.jsonl").read_text().splitlines()[calls0:]]
    sub_calls = [c for c in calls if "--skip-download" in c]
    assert len(sub_calls) == 1 and sub_calls[0][sub_calls[0].index("--sub-langs") + 1] == "es,fr", sub_calls
    assert not any("--merge-output-format" in c for c in calls), "izvor je vec u arhivi — ne preuzima se ponovo"
    m = manifest(folder)
    assert {"manual:es", "auto:fr"} <= {s["key"] for s in m["subs"]}, m["subs"]
    assert [x["key"] for x in m["source_subs"]][-1] == "manual:es" and len(m["source_subs"]) == 4, m["source_subs"]
    pr = probe(src)
    assert subs_of(pr)[3] == ("spa", "Español (CC)", 0) and len(subs_of(pr)) == 4, subs_of(pr)
    assert has_cover(pr) and len(pr["chapters"]) == 2
    # Ponovi: nista novo -> izvor se ne dira, trake se ne dupliraju
    mt = src.stat().st_mtime_ns
    post(f"/api/jobs/{r['id']}/retry", {})
    time.sleep(0.5)
    assert_done(wait_jobs([r["id"]])[r["id"]])
    assert len(subs_of(probe(src))) == 4 and src.stat().st_mtime_ns == mt
    assert len(manifest(folder)["source_subs"]) == 4
    (FAKE / "info" / f"{VID}.json").write_text(json.dumps(INFO))


def t_library_tr_state():
    by = {i["id"]: i for i in get("/api/library")}
    t1 = by["TTTTTTTTTT1"]
    assert t1["tr_state"] == "translated", t1
    tr = [{k: x[k] for k in ("target", "engine", "model", "date", "lektura", "file")} for x in t1["translated"]]
    assert [x["date"] for x in tr] == sorted((x["date"] for x in tr), reverse=True), tr  # najnoviji prvi
    by_f = {x["file"]: x for x in tr}
    assert by_f["subs/sr-lat.b.srt"] == {"target": "lat", "engine": "claude", "model": "sonnet", "date": "2026-10-01",
                                         "lektura": True, "file": "subs/sr-lat.b.srt"}, tr
    assert by_f["subs/sr-lat.a.srt"] == {"target": "lat", "engine": "synergy", "model": "gemma4:12b-it-qat",
                                         "date": "2026-09-30", "lektura": False, "file": "subs/sr-lat.a.srt"}, tr
    el = by_f["subs/el.c.srt"]  # bez engine polja: iz kljuca, datum iz fajla
    assert el["engine"] == "gemini" and el["model"] == "gemini-3.5-flash-lite" and len(el["date"]) == 10, el
    assert t1["transcripts"] == [{"kind": "manual", "lang": "en", "file": "subs/en.srt"}], t1["transcripts"]
    t2, t3, t4, t5 = (by[f"TTTTTTTTTT{n}"] for n in (2, 3, 4, 5))
    assert t2["tr_state"] == "transcript" and t2["translated"] == [] and \
        t2["transcripts"] == [{"kind": "whisper", "lang": "sr", "file": "subs/whisper.srt"}], t2
    assert t3["tr_state"] == "transcript" and t3["transcripts"][0]["kind"] == "auto", t3
    assert t4["tr_state"] == "translated" and t4["translated"] == [] and t4["sr_cc"] is True, t4  # srpski CC
    assert t5["tr_state"] == "none" and t5["transcripts"] == [] and t5["translated"] == [], t5  # fajl ne postoji
    # preuzeta stavka (titlovi bez prevoda)
    assert by[VID]["tr_state"] == "transcript", by[VID]["tr_state"]
    # izvor bez zvuka (audio []): UI ne nudi Transkribuj; stari manifest bez kljuca "audio" -> ima zvuk
    assert by[VID]["has_audio"] is True and by["NNNNNNNNNNA"]["has_audio"] is False, by["NNNNNNNNNNA"]
    # katalog: probe kartica ("U arhivi već imaš") i plejlista
    pr = post("/api/probe", {"url": "https://www.youtube.com/watch?v=TTTTTTTTTT1"})
    a = pr["archive"]
    assert a["tr_state"] == "translated" and a["translated"] == t1["translated"], a
    assert a["transcripts"] == t1["transcripts"] and a["sr_cc"] is False, a
    pl = post("/api/probe", {"url": "https://www.youtube.com/playlist?list=PLcat"})
    have = {e["id"]: e["have"] for e in pl["entries"]}
    assert have["TTTTTTTTTT2"]["tr_state"] == "transcript" and have["TTTTTTTTTT2"]["transcripts"][0]["kind"] == "whisper"
    assert have["TTTTTTTTTT5"]["tr_state"] == "none" and have["ZZZZZZZZZZZ"] is None, have


def t_transcribe_skips():
    n_jobs = len(get("/api/jobs"))
    r = batch([IT["TR2"], IT["TR3"], IT["NA"]], "transcribe")
    assert r["jobs"] == [], r
    assert reason(r, IT["TR2"]) == "već ima whisper transkript"
    assert reason(r, IT["TR3"]) == "nema izvora zvuka" and reason(r, IT["NA"]) == "nema izvora zvuka", r
    r = batch([IT["TR2"]], "transcribe", {"force": True, "lang": "sr"})
    assert r["jobs"] == [] and reason(r, IT["TR2"]) == "nema izvora zvuka", r
    r = batch([IT["TR2"]], "transcribe", {"force": "false"})  # "false" iz forme nije force
    assert reason(r, IT["TR2"]) == "već ima whisper transkript", r
    e = batch([IT["TR2"]], "transcribe", {"lang": "srpski!"}, code=400)
    assert "lang" in e["error"], e
    e = batch([IT["TR2"]], "nepostojeca", code=400)
    assert "transcribe" in e["error"], e
    assert len(get("/api/jobs")) == n_jobs


def t_existing_routes():
    assert isinstance(get("/api/tb/presets"), list) and "limits" in get("/api/status")
    assert req("GET", "/")[0] == 200
    js = get("/api/jobs")
    assert all("after_jobs" in j for j in js), js[:1]


# ================================================================ testovi: u procesu (bez servera)
def inproc():
    if "panel" not in STATE:
        d = TMP / "inproc"
        d.mkdir(exist_ok=True)
        (d / "settings.json").write_text(json.dumps({"out_dir": str(TMP / "inproc-out"), "browser": "none"}))
        os.environ["YTA_DATA"] = str(d)
        STATE["panel"] = importlib.import_module("panel")
        assert str(STATE["panel"].DATA) == str(d)
    return STATE["panel"]


def t_audio_groups_unit():
    panel = inproc()
    g = panel.audio_groups({"formats": INFO["formats"]})
    assert [(x["id"], x["lang"], x["role"], x["original"]) for x in g] == [
        ("251", "en", "original", True), ("251-1", "de", "dub", False), ("250", "en", "descriptive", False)], g
    # engleski default + engleski descriptive (original je nemacki): obe trake ostaju
    f2 = [fmt("251", "de", 10, "German original"), fmt("250", "en", 5, "English (default)"),
          fmt("249", "en", -10, "English descriptive", abr=130)]
    assert sorted((x["lang"], x["role"]) for x in panel.audio_groups({"formats": f2})) == [
        ("de", "original"), ("en", "default"), ("en", "descriptive")]
    sel, multi = panel.build_selector({"video_fmt": "auto:best", "audio_fmt": "all"}, INFO)
    assert multi and sel.startswith("bv*+251+251-1+250/"), sel


def t_sub_keys_unit():
    panel = inproc()
    assert panel.sub_keys_for("all", INFO, None) == ["manual:en", "manual:de", "auto:en-orig"]
    k = panel.sub_keys_for("all+tr", INFO, None)
    assert k == ["manual:en", "manual:de", "auto:en-orig", "auto:fr"], k  # "en" pored "en-orig" = isti fajl
    assert panel.sub_keys_for("none", INFO, None) == []


def t_embed_unit():
    panel = inproc()
    out = TMP / "inproc-out"
    d = mk_item("U", "UUUUUUUUUUU", "Embed", root=out, source="Embed [UUUUUUUUUUU].source.mkv", kind="video",
                subs=[sub("manual:en", "manual", "en", "subs/en.srt", name="English"),
                      sub("manual:en-GB", "manual", "en-GB", "subs/en-GB.srt"),
                      sub("auto:fr", "auto", "fr", "subs/fr.auto.srt")],  # YT prevod: ne ide u izvor
                subs_files={"subs/en.srt": SRT_EN, "subs/en-GB.srt": SRT_EN, "subs/fr.auto.srt": SRT_EN})
    src = d / "Embed [UUUUUUUUUUU].source.mkv"
    shutil.copyfile(FAKE / "fixture.mkv", src)
    ff = panel.core.find_ffmpeg()
    job = panel.Job("", {}, kind="process", folder=str(d))
    assert panel.embed_source_subs(job, d, ff) == 2, list(job.log)
    assert subs_of(probe(src)) == [("eng", "English (CC)", 0), ("eng", "English en-GB (CC)", 0)], subs_of(probe(src))
    pr = probe(src)  # omot ostaje prilog (attached_pic), ne postaje druga video traka; poglavlja ostaju
    assert has_cover(pr) and len(pr["chapters"]) == 2, pr["streams"]
    assert panel.embed_source_subs(job, d, ff) == 0  # drugi poziv ne duplira
    assert len(subs_of(probe(src))) == 2 and len(manifest(d)["source_subs"]) == 2
    man = manifest(d)  # manifest bez source_subs (npr. obnovljen): trake vec u fajlu se prepoznaju po jeziku+nazivu
    man.pop("source_subs")
    (d / ".yta" / "manifest.json").write_text(json.dumps(man))
    mt = src.stat().st_mtime_ns
    assert panel.embed_source_subs(job, d, ff) == 0 and src.stat().st_mtime_ns == mt
    assert len(subs_of(probe(src))) == 2 and len(manifest(d)["source_subs"]) == 2
    # neuspeh (neispravan titl): upozorenje, izvor netaknut (sadrzaj i manifest), bez privremenih fajlova
    (d / "subs" / "de.srt").write_bytes(b"\x00\xff\x00 nije srt \xfe" * 40)
    man = manifest(d)
    man["subs"].append(sub("manual:de", "manual", "de", "subs/de.srt"))
    (d / ".yta" / "manifest.json").write_text(json.dumps(man))
    h0 = hashlib.sha256(src.read_bytes()).hexdigest()
    job2 = panel.Job("", {}, kind="process", folder=str(d))
    assert panel.embed_source_subs(job2, d, ff) == 0
    assert hashlib.sha256(src.read_bytes()).hexdigest() == h0
    assert [x["key"] for x in manifest(d)["source_subs"]] == ["manual:en", "manual:en-GB"]
    assert any("⚠" in x and "MKV izvor" in x for x in job2.log), list(job2.log)
    assert job2.stages["subs"]["status"] == "pending", job2.stages["subs"]
    assert not list((d / ".yta").glob("work/*")) and not list(d.glob("*.tmp*"))
    # iskljuceno u podesavanjima / izvor nije MKV -> nista
    (d / "subs" / "de.srt").write_text(SRT_EN)
    panel.SETTINGS["embed_subs"] = False
    try:
        assert panel.embed_source_subs(job2, d, ff) == 0
    finally:
        panel.SETTINGS["embed_subs"] = True
    a = mk_item("UA", "UUUUUUUUUUA", "Audio", root=out, source="Audio [UUUUUUUUUUA].audio.m4a", kind="audio",
                subs=[sub("manual:en", "manual", "en", "subs/en.srt")], subs_files={"subs/en.srt": SRT_EN})
    (a / "Audio [UUUUUUUUUUA].audio.m4a").write_bytes(b"m4a")
    assert panel.embed_source_subs(job2, a, ff) == 0 and "source_subs" not in manifest(a)
    assert panel.embed_source_subs(job2, d, ff) == 1  # sada ispravan de -> samo on se dodaje
    assert [t for t, _, _ in subs_of(probe(src))] == ["eng", "eng", "deu"]
    assert has_cover(probe(src)), probe(src)["streams"]


def t_after_and_transcribe_unit():
    panel = inproc()
    out = TMP / "inproc-out"
    started = []
    real = panel.start_job
    panel.start_job = started.append
    try:
        a = mk_item("UB", "UUUUUUUUUUB", "Samo audio", root=out, source="Samo audio [UUUUUUUUUUB].audio.m4a",
                    kind="audio", audio=[{"index": 0, "lang": "en", "original": True, "codec": "aac"}])
        (a / "Samo audio [UUUUUUUUUUB].audio.m4a").write_bytes(b"m4a")
        job = panel.Job("https://x", {"dl": {}, "proc": None,
                                      "after": {"toolbox": {"preset": "remux", "sub": "none", "target": None},
                                                "audio": {"preset": "audio_flac"}}})
        panel.run_after_steps(job, a)
        assert len(started) == 1 and job.after_jobs == [started[0].id], (started, list(job.log))
        assert started[0].opts["spec"]["container"] == "flac" and started[0].kind == "transcode", started[0].opts
        assert any("Transkoduj (Toolbox) preskočen: nema videa" in x for x in job.log), list(job.log)
        assert [(x["step"], x["label"]) for x in job.after_skipped] == [("toolbox", "Transkoduj (Toolbox)")], \
            job.after_skipped  # preskocen korak se vidi i u Redu (after_skipped u JSON-u posla)
        assert job.to_dict()["after_skipped"][0]["reason"].startswith("nema videa"), job.to_dict()
        # bez izvora: korak preskocen, bez izuzetka
        e = mk_item("UE", "UUUUUUUUUUE", "Prazna", root=out)
        job3 = panel.Job("https://x", {"dl": {}, "after": {"audio": {"preset": "audio_m4a"}}})
        panel.run_after_steps(job3, e)
        assert job3.after_jobs == [] and any("Izvuci audio preskočen: nema izvora u arhivi" in x for x in job3.log)
        assert job3.after_skipped == [{"step": "audio", "label": "Izvuci audio", "reason": "nema izvora u arhivi"}]
        # transcribe: oblik posla (bez pokretanja whisper-a)
        r = panel.lib_transcribe(a, manifest(a), {"lang": "sr"})
        assert isinstance(r, panel.Job) and r.kind == "process", r
        assert r.opts["proc"] == {"sub_source": "whisper:run", "whisper_lang": "sr", "target": "none",
                                  "output": "none", "keep_source": True}, r.opts["proc"]
        assert panel.lib_transcribe(a, manifest(a), {}).opts["proc"]["whisper_lang"] is None
    finally:
        panel.start_job = real
    # validacija after (isti oblik kao HTTP)
    v = panel.validate_after
    assert v(None) is None and v({}) is None and v({"toolbox": None, "audio": {}}) is None
    assert v({"toolbox": {"preset": "apple", "sub": "burn", "target": None}}) == {
        "toolbox": {"preset": "apple", "sub": "burn", "target": panel.SETTINGS["script"]}}
    for bad in ({"x": 1}, [], {"toolbox": {"preset": "remux", "sub": "burn"}}):
        try:
            v(bad)
            raise AssertionError(f"ocekivan BadRequest za {bad!r}")
        except panel.BadRequest:
            pass


def t_item_dir_keeps_id():
    """Dug naslov: [id] ostaje u imenu foldera (bez sudara); stari folder sa odsecenim id-jem se i dalje koristi."""
    panel = inproc()
    root = TMP / "itemdir-out"
    t = "Vrlo dugačak naslov videa koji se proteže preko devedeset znakova i ide još dalje do kraja"
    a = {"id": "AAAAAAAAAAA", "title": t + " A", "fulltitle": t + " A", "upload_date": "20260101"}
    b = {"id": "BBBBBBBBBBB", "title": t + " B", "fulltitle": t + " B", "upload_date": "20260101"}
    fa, fb = panel.item_dir(a, str(root)), panel.item_dir(b, str(root))
    assert fa != fb and fa.name.endswith("[AAAAAAAAAAA]") and fb.name.endswith("[BBBBBBBBBBB]"), (fa, fb)
    assert len(fa.name) <= 90, len(fa.name)
    old = root / panel.core.safe_name(f"20260101 - {t} A [AAAAAAAAAAA]")  # staro ime (id odsecen)
    assert "[AAAAAAAAAAA]" not in old.name
    (old / ".yta").mkdir(parents=True)
    (old / ".yta" / "manifest.json").write_text(json.dumps({"version": 1, "id": "AAAAAAAAAAA", "title": t}))
    assert panel.item_dir(a, str(root)) == old, "postojeci folder istog videa se koristi"
    assert panel.item_dir(b, str(root)) == fb, "drugi video ne ulazi u tudji stari folder"
    short = {"id": "CCCCCCCCCCC", "title": "Kratko", "upload_date": "20260101"}
    assert panel.item_dir(short, str(root)).name == "20260101 - Kratko [CCCCCCCCCCC]"


def t_after_keeps_source_unit():
    """keep_source=false + video izlaz + after koraci: izvor se ne brise pre Transkoduj / Izvuci audio."""
    panel = inproc()
    ff = panel.core.find_ffmpeg()
    vid, url = "KKKKKKKKKKK", "https://www.youtube.com/watch?v=KKKKKKKKKKK"
    info = {"id": vid, "title": "Keep", "fulltitle": "Keep", "upload_date": "20260101", "language": "en",
            "formats": [], "subtitles": {}, "automatic_captions": {}, "duration": 2}
    panel.PROBES[url] = info
    folder = panel.item_dir(info, str(TMP / "inproc-out"))
    src = folder / f"Keep [{vid}].source.mkv"
    mk_item("K", vid, "Keep", root=TMP / "inproc-out", source=src.name, kind="video", video={"height": 120},
            audio=[{"index": 0, "lang": "en", "original": True}],
            subs=[sub("manual:en", "manual", "en", "subs/en.srt", name="English")], subs_files={"subs/en.srt": SRT_EN})
    assert IT["K"] == folder, (IT["K"], folder)
    shutil.copyfile(FAKE / "fixture.mkv", src)
    started = []
    real = panel.start_job
    panel.start_job = started.append
    try:
        opts = {"dl": {"video_fmt": "auto:best", "audio_fmt": "all", "subs": "none"},
                "proc": {"sub_source": "auto-best", "target": "none", "output": "soft", "keep_source": False},
                "after": {"toolbox": {"preset": "remux", "sub": "none"}, "audio": {"preset": "audio_mp3"}}}
        job = panel.Job(url, panel.check_after(opts))
        job.folder = str(folder)
        panel.run_download_job(job, ff)
        log = "\n".join(job.log)
        assert src.is_file() and manifest(folder)["source"] == src.name, log[-1500:]
        assert len(started) == 2 and job.after_jobs == [x.id for x in started] and job.after_skipped == [], log[-1500:]
        assert "izvor se zadržava" in log and "izvor obrisan" not in log, log[-1500:]
        # bez after koraka izbor "ne zadrzi izvor" i dalje vazi
        started.clear()
        job2 = panel.Job(url, {k: v for k, v in opts.items() if k != "after"})
        job2.folder = str(folder)
        panel.run_download_job(job2, ff)
        assert not src.exists() and manifest(folder)["source"] is None and not started, "\n".join(job2.log)[-1500:]
    finally:
        panel.start_job = real


def t_migration_unit():
    """Ucitavanje podesavanja u posebnom procesu (SETTINGS se racuna pri importu)."""
    code = ("import json, panel; print(json.dumps({k: panel.SETTINGS.get(k) for k in "
            "('recipe', 'plan_migrated', 'embed_subs')}))")

    def load(name: str, settings: dict | None) -> tuple[dict, dict | None]:
        d = TMP / "mig" / name
        d.mkdir(parents=True)
        if settings is not None:
            (d / "settings.json").write_text(json.dumps(settings))
        p = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True, timeout=60,
                           env=dict(os.environ, YTA_DATA=str(d), PYTHONPATH=str(ROOT)))
        assert p.returncode == 0, p.stderr[-800:]
        f = d / "settings.json"
        return json.loads(p.stdout.strip().splitlines()[-1]), (json.loads(f.read_text()) if f.exists() else None)
    s, disk = load("stari", {"recipe": "arhiva", "script": "cyr"})
    assert s == {"recipe": "samo", "plan_migrated": True, "embed_subs": True}, s
    assert disk == {"recipe": "samo", "script": "cyr", "plan_migrated": True}, disk
    s, disk = load("izricit", {"recipe": "arhiva", "plan_migrated": True})
    assert s["recipe"] == "arhiva" and disk == {"recipe": "arhiva", "plan_migrated": True}, (s, disk)
    s, disk = load("muzika", {"recipe": "muzika"})
    assert s["recipe"] == "muzika" and disk == {"recipe": "muzika", "plan_migrated": True}, (s, disk)
    s, disk = load("bez_recepta", {"script": "lat"})
    assert s["recipe"] == "samo" and disk == {"script": "lat", "plan_migrated": True}, (s, disk)
    s, disk = load("novo", None)
    assert s == {"recipe": "samo", "plan_migrated": True, "embed_subs": True} and disk is None, (s, disk)
    (TMP / "mig" / "los").mkdir()  # neispravan settings.json se ne prepisuje
    (TMP / "mig" / "los" / "settings.json").write_text("{nije json")
    p = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, YTA_DATA=str(TMP / "mig" / "los"), PYTHONPATH=str(ROOT)))
    assert p.returncode == 0 and (TMP / "mig" / "los" / "settings.json").read_text() == "{nije json", p.stderr


HTTP_TESTS = [t_migration_and_samo, t_after_validation, t_download_embed_after, t_embed_again_and_retry,
              t_library_tr_state, t_transcribe_skips, t_existing_routes]
UNIT_TESTS = [t_audio_groups_unit, t_sub_keys_unit, t_embed_unit, t_after_and_transcribe_unit, t_after_keeps_source_unit, t_item_dir_keeps_id,
              t_migration_unit]


# ================================================================ main
def port_free() -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", PORT)) != 0


def run(tests, filt) -> tuple[int, int]:
    n = fails = 0
    for t in tests:
        if filt and not any(f in t.__name__ for f in filt):
            continue
        n += 1
        t0 = time.time()
        try:
            t()
            print(f"OK    {t.__name__} ({time.time() - t0:.1f}s)")
        except Exception:  # noqa: BLE001
            fails += 1
            print(f"FAIL  {t.__name__}\n{traceback.format_exc()}")
    return n, fails


def main() -> int:
    filt = sys.argv[1:]
    if not port_free():
        print(f"port {PORT} je zauzet — ugasi drugu instancu")
        return 2
    make_archive()
    env = dict(os.environ, YTA_DATA=str(DATA), YTA_PORT=str(PORT), YTA_NO_OPEN="1",
               PATH=f"{FAKEBIN}:{os.environ.get('PATH', '')}")
    log = open(TMP / "panel.log", "w")
    p = subprocess.Popen([sys.executable, str(ROOT / "panel.py")], env=env, stdout=log, stderr=subprocess.STDOUT)
    n = fails = 0
    try:
        for _ in range(100):
            if p.poll() is not None:
                raise RuntimeError("panel se ugasio:\n" + (TMP / "panel.log").read_text()[-1500:])
            if not port_free():
                break
            time.sleep(0.1)
        n, fails = run(HTTP_TESTS, filt)
    finally:
        p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
        log.close()
    n2, f2 = run(UNIT_TESTS, filt)
    n, fails = n + n2, fails + f2
    print(f"\n{n - fails}/{n} testova prošlo" + (f" · privremeni folder: {TMP}" if fails else ""))
    if fails:
        print("panel log (kraj):\n" + (TMP / "panel.log").read_text()[-2000:])
    if not os.environ.get("YTA_TEST_KEEP") and not fails:
        shutil.rmtree(TMP, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
