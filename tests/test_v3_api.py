#!/usr/bin/env python3
"""Test v3 API (docs/v3-contract.md A–F): editor titla + ispravke, lektura (MOCK LLM), plejliste (snimak,
album, missing), zdravlje kataloga (+rebuild), audio iz arhive, uloga audio trake.

  python3 tests/test_v3_api.py            # panel na 127.0.0.1:8811 (YTA_TEST_PORT), privremeni YTA_DATA/out_dir
  python3 tests/test_v3_api.py lektura    # samo testovi ciji naziv sadrzi 'lektura'
  YTA_TEST_TMP=/neki/folder  -> privremeni folder "v3-*" pravi se tu;  YTA_TEST_KEEP=1 -> ne brisi ga

Bez mreze i bez pravih modela: panel se pokrece kroz launcher koji zameni core.call_claude / core.make_llm
lazom lekturom, a PATH ima lazne `yt-dlp` i `claude` koji odmah padaju (pravi claude se nikad ne poziva).
"""
from __future__ import annotations

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
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PORT = int(os.environ.get("YTA_TEST_PORT", "8811"))
BIN = Path(os.environ.get("TB_FFMPEG_DIR", "/opt/homebrew/opt/ffmpeg-full/bin"))
FF, FP = str(BIN / "ffmpeg"), str(BIN / "ffprobe")
TMP = Path(tempfile.mkdtemp(prefix="v3-", dir=os.environ.get("YTA_TEST_TMP") or None)).resolve()
DATA, OUT, FAKEBIN = TMP / "data", TMP / "out", TMP / "bin"
IT: dict[str, Path] = {}

SRT_EN = """1
00:00:00,200 --> 00:00:00,900
Hello there

2
00:00:01,000 --> 00:00:01,800
General Kenobi
"""
SRT_SR = SRT_EN.replace("Hello there", "Zdravo tamo").replace("General Kenobi", "Generale Kenobi")
SR_FILE = "subs/sr-lat.x.srt"
SR_KEY = "sr:lat:ollama:gemma4:12b-it-qat:manual:en"
LEK_KEY = "sr:lat:claude:sonnet:lektura:manual:en"
LEK_FILE = "subs/sr-lat.manual_en.claude-lektura_sonnet.srt"

# lazna lektura: svaku liniju sa "Kenobi" ispravi, ostale vrati nepromenjene; poziv se belezi u DATA/mock_calls.jsonl
MOCK = r'''
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.environ["YTA_ROOT"])
import yt_arhiva as core

def fake_llm_call(prompt, model, system=None, engine="claude"):
    items = json.loads(prompt.split("Ulaz:\n", 1)[1].split("\n\n", 1)[0])
    with open(Path(os.environ["YTA_DATA"]) / "mock_calls.jsonl", "a") as fh:
        fh.write(json.dumps({"engine": engine, "model": model, "system": system, "n": len(items)}) + "\n")
    return json.dumps({"items": [{"id": it["id"], "t": it["t"].replace("Generale Kenobi", "Generale Kenobi, dobro došli")}
                                 for it in items]}, ensure_ascii=False)

core.call_claude = lambda prompt, model, system=None: fake_llm_call(prompt, model, system)
core.make_llm = lambda engine, model: (lambda p, schema=None, system=None: fake_llm_call(p, model, system, engine))
import runpy
runpy.run_path(os.path.join(os.environ["YTA_ROOT"], "panel.py"), run_name="__main__")
'''


# ================================================================ pomocne
def sh(cmd: list[str]) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(f"komanda nije uspela: {' '.join(cmd)}\n{p.stderr[-800:]}")
    return p


def probe(path: Path) -> dict:
    return json.loads(sh([FP, "-v", "error", "-show_entries",
                          "stream=codec_type,codec_name:stream_tags:stream_disposition=attached_pic"
                          ":format=duration:format_tags", "-of", "json", str(path)]).stdout)


def raw(method: str, path: str, body=None, headers: dict | None = None, ctype="application/json"):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=60)
    h = {"Host": f"127.0.0.1:{PORT}"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = ctype
    h.update(headers or {})
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    b = r.read()
    hd = {k.lower(): v for k, v in r.getheaders()}
    c.close()
    return r.status, hd, b


def req(method: str, path: str, body=None, headers=None):
    st, _, b = raw(method, path, body, headers)
    try:
        return st, json.loads(b)
    except ValueError:
        return st, None


def get(path: str, code: int = 200):
    st, j = req("GET", path)
    assert st == code, f"GET {path} -> {st} {j} (ocekivano {code})"
    return j


def post(path: str, body: dict, code: int = 200):
    st, j = req("POST", path, body)
    assert st == code, f"POST {path} -> {st} {j} (ocekivano {code})"
    return j


def q(**kw) -> str:
    from urllib.parse import urlencode
    return urlencode({k: str(v) for k, v in kw.items()})


def batch(folders, action, params=None, code=200):
    return post("/api/library/batch", {"folders": [str(f) for f in folders], "action": action,
                                       "params": params or {}}, code)


def wait_jobs(ids: list[str], timeout: float = 60) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        by = {j["id"]: j for j in get("/api/jobs")}
        if all(by.get(i, {}).get("state") in ("done", "error", "cancelled") for i in ids):
            return {i: by[i] for i in ids}
        time.sleep(0.3)
    raise AssertionError(f"poslovi nisu zavrseni: {ids}")


def assert_done(j: dict):
    log = "\n".join(get(f"/api/jobs/{j['id']}/log")["log"][-20:])
    assert j["state"] == "done" and not j["error"], f"{j['title']}: {j['state']} {j['error']}\n{log}"


def reason(res: dict, folder: Path) -> str:
    hit = [s["reason"] for s in res["skipped"] if Path(s["folder"]).resolve() == folder.resolve()]
    assert hit, f"{folder.name} nije u skipped: {res}"
    return hit[0]


def manifest(tag: str) -> dict:
    return json.loads((IT[tag] / ".yta" / "manifest.json").read_text())


# ================================================================ lazna arhiva
def mk_item(tag: str, vid: str, title: str, *, root: Path | None = None, **m) -> Path:
    d = (root or OUT) / f"20260101 - {title} [{vid}]"
    (d / ".yta").mkdir(parents=True)
    (d / "subs").mkdir()
    man = {"version": 1, "url": f"https://www.youtube.com/watch?v={vid}", "id": vid, "title": title,
           "channel": "Test kanal", "duration": 2, "thumbnail": None, "upload_date": "20260101",
           "language": "en", "live_status": None, "source": None, "kind": None, "video": None,
           "audio": [], "subs": [], "outputs": [], "playlists": [], "updated": time.time()}
    man.update(m)
    (d / ".yta" / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=1))
    IT[tag] = d
    return d


def make_video(path: Path, dur: float = 2, audio: int = 1):
    cmd = [FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=160x120:r=25:d={dur}"]
    for n in range(audio):
        cmd += ["-f", "lavfi", "-i", f"sine=f={440 * (n + 1)}:d={dur}"]
    cmd += ["-map", "0:v"] + sum((["-map", f"{n + 1}:a"] for n in range(audio)), [])
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-b:a", "64k"]
        for n, lg in enumerate(["deu", "eng"][:audio] if audio == 2 else ["eng"]):
            cmd += [f"-metadata:s:a:{n}", f"language={lg}"]
    sh(cmd + [str(path)])


def make_archive():
    for d in (OUT, DATA, FAKEBIN):
        d.mkdir(parents=True)
    en = {"key": "manual:en", "kind": "manual", "lang": "en", "file": "subs/en.srt", "count": 2, "name": "English"}
    vinfo = {"format_id": "137", "width": 160, "height": 120, "codec": "h264", "fps": 25, "hdr": False,
             "premium": False, "duration": 2.0}
    # V: video, 2 audio trake (dub pa original, STARI manifest bez role), en CC + srpski prevod sa src kljucem
    src_v = "Video [VVVVVVVVVVV].source.mkv"
    v = mk_item("V", "VVVVVVVVVVV", "Video", source=src_v, kind="video", video=vinfo, audio=[
        {"index": 0, "lang": "de", "original": False, "variant": "dub", "codec": "aac"},
        {"index": 1, "lang": "en", "original": True, "variant": "original", "codec": "aac"}],
        subs=[en, {"key": SR_KEY, "kind": "sr", "lang": "sr", "file": SR_FILE, "count": 2, "src": "manual:en",
                   "name": "Srpski latinica — Brzo (iz: English)"}])
    (v / "subs" / "en.srt").write_text(SRT_EN)
    (v / SR_FILE).write_text(SRT_SR)
    make_video(v / src_v, audio=2)
    sh([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x120:d=1",
        "-frames:v", "1", str(v / ".yta" / "thumb.jpg")])
    # A, A2: samo audio (m4a, opus) za album
    a = mk_item("A", "AAAAAAAAAAA", "Pesma A", source="Pesma A [AAAAAAAAAAA].audio.m4a", kind="audio",
                channel="Kanal A", audio=[{"index": 0, "lang": "en", "original": True, "codec": "aac"}])
    sh([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=2",
        "-i", str(v / ".yta" / "thumb.jpg"), "-map", "0", "-map", "1", "-c:a", "aac", "-b:a", "64k", "-c:v", "copy",
        "-disposition:v:0", "attached_pic", "-f", "mp4", str(a / "Pesma A [AAAAAAAAAAA].audio.m4a")])
    a2 = mk_item("A2", "AAAAAAAAAA2", "Pesma B", source="Pesma B [AAAAAAAAAA2].audio.opus", kind="audio",
                 channel="Kanal B", audio=[{"index": 0, "lang": "en", "original": True, "codec": "opus"}])
    sh([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=660:d=2", "-c:a", "libopus",
        "-b:a", "48k", "-metadata", "artist=Postojeci izvodjac", str(a2 / "Pesma B [AAAAAAAAAA2].audio.opus")])
    # T: skracen izvor (2 s, manifest kaze 10 s) + en CC (za brisanje titla u editoru)
    t = mk_item("T", "TTTTTTTTTTT", "Skracen", source="Skracen [TTTTTTTTTTT].source.mkv", kind="video",
                video=vinfo, duration=10, subs=[en])
    make_video(t / "Skracen [TTTTTTTTTTT].source.mkv")
    (t / "subs" / "en.srt").write_text(SRT_EN)
    # M: sve nedostaje (izvor, titl, izlaz)
    mk_item("M", "MMMMMMMMMMM", "Nedostaje", source="Nedostaje [MMMMMMMMMMM].source.mkv", kind="video",
            video=vinfo, subs=[{"key": "manual:de", "kind": "manual", "lang": "de", "file": "subs/de.srt",
                                "count": 1, "name": "Deutsch"}], outputs=["Nedostaje.sr-lat.mp4"])
    # E: bez izvora; N: video bez zvuka
    mk_item("E", "EEEEEEEEEEE", "Prazna")
    n = mk_item("N", "NNNNNNNNNNN", "Bez zvuka", source="Bez zvuka [NNNNNNNNNNN].source.mkv", kind="video",
                video=vinfo)
    make_video(n / "Bez zvuka [NNNNNNNNNNN].source.mkv", audio=0)
    # P: izvor sa opus zvukom (-> .opus + omot kao METADATA_BLOCK_PICTURE); Q: mp3 zvuk (-> .mka + prilog)
    for tag, vid, codec in (("P", "PPPPPPPPPPP", "libopus"), ("Q", "QQQQQQQQQQQ", "libmp3lame")):
        d = mk_item(tag, vid, f"Kodek {tag}", source=f"Kodek {tag} [{vid}].source.mkv", kind="video", video=vinfo,
                    audio=[{"index": 0, "lang": "en", "original": True, "codec": codec}])
        sh([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x120:r=25:d=2",
            "-f", "lavfi", "-i", "sine=f=440:d=2", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", codec, str(d / f"Kodek {tag} [{vid}].source.mkv")])
        shutil.copyfile(v / ".yta" / "thumb.jpg", d / ".yta" / "thumb.jpg")
    # siroce: folder sa [id] bez manifesta
    o = OUT / "20200101 - x [abcdefghijk]"
    (o / "subs").mkdir(parents=True)
    make_video(o / "x [abcdefghijk].source.mkv")
    (o / "subs" / "en.srt").write_text(SRT_EN)
    (o / "subs" / "en.auto.srt").write_text(SRT_EN)
    (o / "subs" / "en.forced.srt").write_text(SRT_EN)  # isti jezik -> kljuc ne sme da se sudari sa manual:en
    (o / "x [abcdefghijk].sr-lat.mp4").write_bytes(b"\0" * 10)
    IT["O"] = o
    # R: prevod od 4 titla sa izvorom (regresije editora: brisanje reda, podela, prazan titl)
    r4 = "".join(f"{n + 1}\n00:00:0{n},000 --> 00:00:0{n},900\n{{}} {n}\n\n" for n in range(4))
    rr = mk_item("R", "RRRRRRRRRRR", "Regresija", subs=[
        {"key": "manual:en", "kind": "manual", "lang": "en", "file": "subs/en.srt", "count": 4, "name": "English"},
        {"key": "sr:lat:ollama:m:manual:en", "kind": "sr", "lang": "sr", "file": "subs/sr.srt", "count": 4,
         "src": "manual:en", "name": "Srpski"}])
    (rr / "subs" / "en.srt").write_text(r4.replace("{}", "src"))
    (rr / "subs" / "sr.srt").write_text(r4.replace("{}", "tr"))
    # snimak plejliste: A, A2 (audio), V (video), X (nije u biblioteci)
    (DATA / "playlists").mkdir()
    (DATA / "playlists" / "PLtest.json").write_text(json.dumps({
        "id": "PLtest", "title": "Test plejlista", "url": "https://www.youtube.com/playlist?list=PLtest",
        "uploader": "Urednik liste", "fetched_at": 1790000000,
        "entries": [{"id": "AAAAAAAAAAA", "title": "Pesma A", "duration": 2, "pos": 1},
                    {"id": "AAAAAAAAAA2", "title": "Pesma B", "duration": 2, "pos": 2},
                    {"id": "VVVVVVVVVVV", "title": "Video", "duration": 2, "pos": 3},
                    {"id": "XXXXXXXXXXX", "title": "Nema je", "duration": 5, "pos": 4}],
        "history": [{"fetched_at": 1790000000, "count": 4}], "removed": []}))
    (DATA / "settings.json").write_text(json.dumps({
        "out_dir": str(OUT), "browser": "none", "cookies_file": "", "engine": "ollama",
        "model": "yta-test-nema-modela", "script": "lat", "recipe": "muzika"}, indent=1))
    for name, msg in (("yt-dlp", "lazni yt-dlp (test bez mreze)"), ("claude", "PRAVI CLAUDE POZVAN U TESTU")):
        f = FAKEBIN / name
        f.write_text(f"#!/bin/sh\necho '{msg}' >&2\nexit 1\n")
        f.chmod(0o755)
    (TMP / "launch.py").write_text(MOCK)


# ================================================================ testovi: HTTP
def t_subs_get():
    v = IT["V"]
    r = get("/api/subs?" + q(f=v, file=SR_FILE))
    assert r["entry"]["key"] == SR_KEY, r
    assert [c["text"] for c in r["cues"]] == ["Zdravo tamo", "Generale Kenobi"], r["cues"]
    assert r["cues"][0] == {"i": 0, "start": 200, "end": 900, "text": "Zdravo tamo"}, r["cues"][0]
    assert [c["text"] for c in r["src"]] == ["Hello there", "General Kenobi"] and r["src"][1]["i"] == 1, r["src"]
    assert r["src_entry"]["key"] == "manual:en" and r["media"] == "Video [VVVVVVVVVVV].source.mkv", r
    r = get("/api/subs?" + q(f=v, file="subs/en.srt"))
    assert r["entry"]["key"] == "manual:en" and r["src"] is None and r["src_entry"] is None, r
    get("/api/subs?" + q(f=v, file="../../data/settings.json"), 404)
    get("/api/subs?" + q(f=v, file="subs/nema.srt"), 404)
    get("/api/subs?" + q(f=TMP, file="x.srt"), 400)  # izvan root-ova
    get("/api/subs?" + q(f=OUT, file=f"{v.name}/{SR_FILE}"), 400)  # root nije stavka
    outside = TMP / "outside"
    outside.mkdir(exist_ok=True)
    (outside / "tajno.srt").write_text(SRT_EN)
    links = [v / "subs" / "link.srt", OUT / "link-van-arhive"]
    links[0].symlink_to(outside / "tajno.srt")
    links[1].symlink_to(outside, target_is_directory=True)
    try:
        get("/api/subs?" + q(f=v, file="subs/link.srt"), 404)
        get("/api/subs?" + q(f=links[1], file="tajno.srt"), 400)
        post("/api/subs/save", {"folder": str(v), "file": "subs/link.srt", "cues": [{"start": 0, "end": 1, "text": "x"}]}, 400)
        assert (outside / "tajno.srt").read_text() == SRT_EN
    finally:
        for ln in links:
            ln.unlink()


def t_subs_save():
    v = IT["V"]
    cues = get("/api/subs?" + q(f=v, file=SR_FILE))["cues"]
    cues[1]["text"] = "Generale\nKenobi!"
    r = post("/api/subs/save", {"folder": str(v), "file": SR_FILE, "cues": cues})
    assert r["ok"] and r["changed"] == 1 and r["pairs_saved"] == 1, r
    assert r["backup"].startswith(".yta/history/sr-lat.x.") and (v / r["backup"]).read_text() == SRT_SR, r
    assert "Generale\nKenobi!" in (v / SR_FILE).read_text()
    assert not list((v / "subs").glob("*.tmp"))
    e = next(s for s in manifest("V")["subs"] if s["key"] == SR_KEY)
    assert e["count"] == 2 and e.get("edited"), e
    rows = [json.loads(x) for x in (DATA / "corrections.jsonl").read_text().splitlines()]
    assert len(rows) == 1, rows
    row = rows[0]
    assert {k: row[k] for k in ("video_id", "file", "lang", "engine", "model", "src", "before", "after")} == {
        "video_id": "VVVVVVVVVVV", "file": SR_FILE, "lang": "sr-lat", "engine": "ollama",
        "model": "gemma4:12b-it-qat", "src": "General Kenobi", "before": "Generale Kenobi",
        "after": "Generale Kenobi!"}, row
    assert row["ts"] and Path(row["folder"]) == v.resolve(), row
    # isti sadrzaj -> nista se ne pise
    r = post("/api/subs/save", {"folder": str(v), "file": SR_FILE, "cues": get("/api/subs?" + q(f=v, file=SR_FILE))["cues"]})
    assert r == {"ok": True, "changed": 0, "pairs_saved": 0, "backup": None}, r
    # vracanje na original (za lekturu): jos jedan par
    cues[1]["text"] = "Generale Kenobi"
    r = post("/api/subs/save", {"folder": str(v), "file": SR_FILE, "cues": cues})
    assert r["pairs_saved"] == 1 and len(list((v / ".yta" / "history").iterdir())) == 2, r
    st = get("/api/corrections/stats")
    assert st["total"] == 2 and st["by_lang"] == {"sr-lat": 2} and st["path"].endswith("corrections.jsonl"), st
    # original (nije prevod): brisanje i pomeranje titla -> changed, bez parova
    t = IT["T"]
    c = get("/api/subs?" + q(f=t, file="subs/en.srt"))["cues"]
    c[0]["start"], c[1]["text"] = 100, ""
    r = post("/api/subs/save", {"folder": str(t), "file": "subs/en.srt", "cues": c})
    assert r["changed"] == 2 and r["pairs_saved"] == 0, r
    assert [x["text"] for x in get("/api/subs?" + q(f=t, file="subs/en.srt"))["cues"]] == ["Hello there"]
    assert next(s for s in manifest("T")["subs"] if s["key"] == "manual:en")["count"] == 1
    # neispravni zahtevi
    post("/api/subs/save", {"folder": str(v), "file": SR_FILE, "cues": "x"}, 400)
    post("/api/subs/save", {"folder": str(v), "file": SR_FILE, "cues": [{"i": 0, "start": 5, "end": 1, "text": "a"}]}, 400)
    post("/api/subs/save", {"folder": str(v), "file": "subs/nema.srt", "cues": []}, 400)
    post("/api/subs/save", {"folder": str(v), "file": "../x.srt", "cues": []}, 400)
    post("/api/subs/save", {"folder": str(TMP), "file": "x.srt", "cues": []}, 400)
    assert get("/api/corrections/stats")["total"] == 2
    # regresije (review): prazan titl se ne cuva; posle brisanja reda izvor para ide po vremenu, ne po indeksu
    rr = IT["R"]
    c = get("/api/subs?" + q(f=rr, file="subs/sr.srt"))["cues"]
    post("/api/subs/save", {"folder": str(rr), "file": "subs/sr.srt", "cues": [dict(x, text=" ") for x in c]}, 400)
    assert len(get("/api/subs?" + q(f=rr, file="subs/sr.srt"))["cues"]) == 4
    r = post("/api/subs/save", {"folder": str(rr), "file": "subs/sr.srt", "cues": c[1:]})  # obrisan prvi red
    assert r["changed"] == 1 and r["pairs_saved"] == 0, r
    c = get("/api/subs?" + q(f=rr, file="subs/sr.srt"))["cues"]
    c[2]["text"] = "tr 3 ispravljeno"
    assert post("/api/subs/save", {"folder": str(rr), "file": "subs/sr.srt", "cues": c})["pairs_saved"] == 1
    row = json.loads((DATA / "corrections.jsonl").read_text().splitlines()[-1])
    assert (row["before"], row["after"], row["src"]) == ("tr 3", "tr 3 ispravljeno", "src 3"), row
    # kopija reda (isti i) mora da se sacuva; podela reda (isti i dvaput) nije par za obuku
    c = get("/api/subs?" + q(f=rr, file="subs/sr.srt"))["cues"]
    r = post("/api/subs/save", {"folder": str(rr), "file": "subs/sr.srt", "cues": c[:1] + [dict(c[0])] + c[1:]})
    assert r["changed"] == 1 and r["pairs_saved"] == 0 and len(get("/api/subs?" + q(f=rr, file="subs/sr.srt"))["cues"]) == 4, r
    c = get("/api/subs?" + q(f=rr, file="subs/sr.srt"))["cues"]
    r = post("/api/subs/save", {"folder": str(rr), "file": "subs/sr.srt",
                                "cues": [dict(c[0], text="tr"), dict(c[0], text="1", start=c[0]["start"] + 400)] + c[2:]})
    assert r["pairs_saved"] == 0, r
    assert get("/api/corrections/stats")["total"] == 3


def t_media_range():
    v = IT["V"]
    src = v / "Video [VVVVVVVVVVV].source.mkv"
    data, size = src.read_bytes(), src.stat().st_size
    path = "/api/media?" + q(f=v, file=src.name)
    st, h, b = raw("GET", path)
    assert st == 200 and b == data and h["accept-ranges"] == "bytes" and h["content-type"] == "video/x-matroska", (st, h)
    assert int(h["content-length"]) == size
    st, h, b = raw("GET", path, headers={"Range": "bytes=0-99"})
    assert st == 206 and b == data[:100] and h["content-range"] == f"bytes 0-99/{size}", (st, h)
    st, h, b = raw("GET", path, headers={"Range": "bytes=100-"})
    assert st == 206 and b == data[100:] and h["content-range"] == f"bytes 100-{size - 1}/{size}", (st, h)
    st, h, b = raw("GET", path, headers={"Range": "bytes=-10"})
    assert st == 206 and b == data[-10:], (st, h)
    st, h, b = raw("GET", path, headers={"Range": f"bytes={size}-"})
    assert st == 416 and h["content-range"] == f"bytes */{size}", (st, h)
    st, h, b = raw("GET", path, headers={"Range": f"bytes=0-{size + 500}"})
    assert st == 206 and b == data, st
    st, h, b = raw("HEAD", path)
    assert st == 200 and b == b"" and int(h["content-length"]) == size, (st, h)
    st, h, _ = raw("GET", "/api/media?" + q(f=IT["A"], file="Pesma A [AAAAAAAAAAA].audio.m4a"), headers={"Range": "bytes=0-1"})
    assert st == 206 and h["content-type"] == "audio/mp4", (st, h)
    st, h, _ = raw("GET", "/api/media?" + q(f=IT["A2"], file="Pesma B [AAAAAAAAAA2].audio.opus"))
    assert st == 200 and h["content-type"] == "audio/ogg", (st, h)
    assert raw("GET", "/api/media?" + q(f=v, file="../../data/settings.json"))[0] == 404
    assert raw("GET", "/api/media?" + q(f=v, file=".yta/manifest.json"))[0] == 404  # nije medij
    assert raw("GET", "/api/media?" + q(f=TMP / "data", file="settings.json"))[0] == 400
    assert raw("GET", path, headers={"Host": f"evil.test:{PORT}"})[0] == 403
    assert raw("GET", path, headers={"Range": "bytes=-0"})[0] == 416
    assert raw("GET", path, headers={"Range": "bytes=50-10"})[0] == 416
    st, h, b = raw("GET", path, headers={"Range": "bytes=0-1,5-6"})  # vise opsega -> ceo fajl
    assert st == 200 and b == data, st
    assert h.get("x-content-type-options") == "nosniff", h
    # <video src> sa tudjeg sajta (bez Origin) -> Sec-Fetch-Site
    assert raw("GET", path, headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert raw("GET", path, headers={"Sec-Fetch-Site": "same-origin", "Range": "bytes=0-0"})[0] == 206
    assert raw("GET", "/api/media?" + q(f=OUT, file=f"{v.name}/{src.name}"))[0] == 400  # root nije stavka
    ext = TMP / "outside.mkv"
    shutil.copyfile(src, ext)
    (v / "link.mkv").symlink_to(ext)
    try:
        assert raw("GET", "/api/media?" + q(f=v, file="link.mkv"))[0] == 404
    finally:
        (v / "link.mkv").unlink()


def t_lektura():
    v, e = IT["V"], IT["E"]
    batch([v], "lektura", {"engine": "claude", "model": "gpt-9"}, 400)
    batch([v], "lektura", {"engine": "openai"}, 400)
    batch([v], "lektura", {"engine": "gemini", "model": "sonnet"}, 400)
    r = batch([v, e], "lektura", {"target": "lat", "engine": "claude", "model": "sonnet"})
    assert len(r["jobs"]) == 1 and reason(r, e) == "nema prevoda za lekturu", r
    j = wait_jobs(r["jobs"])[r["jobs"][0]]
    assert_done(j)
    st = j["stages"]
    assert st["download"]["status"] == "skipped" and st["subs"]["status"] == "done", st
    assert st["translate"]["status"] == "done" and "izmenjeno 1/2" in st["translate"]["info"], st
    assert st["output"]["status"] == "skipped", st
    le = next(s for s in manifest("V")["subs"] if s["key"] == LEK_KEY)
    assert le["file"] == LEK_FILE and le["kind"] == "sr" and le["lang"] == "sr" and le["src"] == "manual:en", le
    assert le["name"] == "Srpski latinica — Claude lektura" and le["count"] == 2 and le["draft"] == SR_KEY, le
    txt = (v / LEK_FILE).read_text()
    assert "Zdravo tamo" in txt and "Generale Kenobi, dobro došli" in txt, txt
    calls = [json.loads(x) for x in (DATA / "mock_calls.jsonl").read_text().splitlines()]
    assert calls and all(c["engine"] == "claude" and c["model"] == "sonnet" for c in calls), calls
    assert "lektor" in (calls[0]["system"] or ""), calls[0]
    # editor: lektura je prevod sa izvorom (en)
    s = get("/api/subs?" + q(f=v, file=LEK_FILE))
    assert s["entry"]["key"] == LEK_KEY and [c["text"] for c in s["src"]] == ["Hello there", "General Kenobi"], s
    lib = {i["id"]: i for i in get("/api/library")}
    assert lib["VVVVVVVVVVV"]["sr"] == ["lat"], lib["VVVVVVVVVVV"]["sr"]
    # vec ima lekturu; force -> ponovo (prethodna u history); grcki nema prevoda
    r = batch([v], "lektura", {"target": "lat"})
    assert reason(r, v) == "već ima lekturu" and not r["jobs"], r
    r = batch([v], "lektura", {"target": "el", "engine": "gemini"})
    assert reason(r, v) == "nema prevoda za lekturu", r
    r = batch([v], "lektura", {"target": "lat", "force": True})
    assert len(r["jobs"]) == 1, r
    assert_done(wait_jobs(r["jobs"])[r["jobs"][0]])
    assert any(p.name.startswith("sr-lat.manual_en.claude-lektura_sonnet.") for p in (v / ".yta" / "history").iterdir())
    assert "PRAVI CLAUDE" not in (TMP / "panel.log").read_text()


def t_audio_copy():
    v, e, n = IT["V"], IT["E"], IT["N"]
    r = batch([v, e, n], "audio_copy")
    assert len(r["jobs"]) == 1 and reason(r, e) == "nema izvora" and reason(r, n) == "izvor nema zvuk", r
    j = wait_jobs(r["jobs"])[r["jobs"][0]]
    assert_done(j)
    name = "Video [VVVVVVVVVVV].audio-orig.m4a"
    assert j["outputs"] == [name] and name in manifest("V")["outputs"], (j["outputs"], manifest("V")["outputs"])
    p = probe(v / name)
    aud = [s for s in p["streams"] if s["codec_type"] == "audio"]
    assert len(aud) == 1 and aud[0]["codec_name"] == "aac" and aud[0]["tags"]["language"] == "eng", p
    tags = {k.lower(): x for k, x in p["format"].get("tags", {}).items()}
    assert tags.get("title") == "Video" and tags.get("artist") == "Test kanal" and tags.get("date", "").startswith("2026"), tags
    cover = [s for s in p["streams"] if s["codec_type"] == "video"]
    print(f"      (omot u m4a: {'da' if cover else 'ne — ffmpeg ga nije primio, fajl bez omota'})")
    assert not list(v.glob("*.part.*"))
    r = batch([IT["P"], IT["Q"]], "audio_copy")
    js = wait_jobs(r["jobs"])
    for j in js.values():
        assert_done(j)
    po = probe(IT["P"] / "Kodek P [PPPPPPPPPPP].audio-orig.opus")
    assert [x["codec_name"] for x in po["streams"] if x["codec_type"] == "audio"] == ["opus"], po
    assert any(x["codec_type"] == "video" for x in po["streams"]), f"opus bez omota: {po}"
    qo = probe(IT["Q"] / "Kodek Q [QQQQQQQQQQQ].audio-orig.mka")
    assert [x["codec_name"] for x in qo["streams"] if x["codec_type"] == "audio"] == ["mp3"], qo
    assert any(x["codec_type"] == "attachment" for x in qo["streams"]) or \
        "cover.jpg" in sh([FP, "-v", "error", "-show_streams", str(IT["Q"] / "Kodek Q [QQQQQQQQQQQ].audio-orig.mka")]).stdout, qo
    assert get("/api/library") and name in {i["id"]: i for i in get("/api/library")}["VVVVVVVVVVV"]["outputs"]


def t_playlists():
    pl = {x["id"]: x for x in get("/api/playlists")}["PLtest"]
    assert (pl["count"], pl["have"], pl["missing"], pl["removed"]) == (4, 3, 1, 0), pl
    assert pl["title"] == "Test plejlista" and pl["fetched_at"] == 1790000000 and pl["url"].endswith("PLtest"), pl
    r = post("/api/playlists/PLtest/album", {"m3u": True, "tags": True})
    assert r["tagged"] == 2, r
    why = {s["id"]: s["reason"] for s in r["skipped"]}
    assert why == {"VVVVVVVVVVV": "nije samo audio", "XXXXXXXXXXX": "nije u biblioteci"}, why
    m3u = Path(r["m3u"])
    assert m3u == OUT / "Test plejlista.m3u8", r
    lines = m3u.read_text().splitlines()
    assert lines == ["#EXTM3U", "#EXTINF:2,Pesma A", "20260101 - Pesma A [AAAAAAAAAAA]/Pesma A [AAAAAAAAAAA].audio.m4a",
                     "#EXTINF:2,Pesma B", "20260101 - Pesma B [AAAAAAAAAA2]/Pesma B [AAAAAAAAAA2].audio.opus"], lines
    pa = probe(IT["A"] / "Pesma A [AAAAAAAAAAA].audio.m4a")
    assert any(x["codec_type"] == "video" for x in pa["streams"]), f"omot izgubljen pri tagovanju: {pa}"
    ta = {k.lower(): x for k, x in pa["format"]["tags"].items()}
    assert ta.get("album") == "Test plejlista" and ta.get("track") == "1/4" and ta.get("artist") == "Kanal A" \
        and ta.get("album_artist") == "Urednik liste", ta
    pb = probe(IT["A2"] / "Pesma B [AAAAAAAAAA2].audio.opus")
    tb = {k.lower(): x for k, x in pb["format"].get("tags", {}).items()}
    tb.update({k.lower(): x for s in pb["streams"] for k, x in (s.get("tags") or {}).items()})
    assert tb.get("album") == "Test plejlista" and tb.get("artist") == "Postojeci izvodjac", tb  # artist ostaje
    assert not list(IT["A"].glob("*.tagtmp*")) and not list(IT["A2"].glob("*.tagtmp*"))
    # samo m3u, bez tagova
    r = post("/api/playlists/PLtest/album", {"m3u": True, "tags": False})
    assert r["tagged"] == 0 and r["m3u"], r
    post("/api/playlists/nema/album", {}, 404)
    post("/api/playlists/PLtest/missing", {"recipe": "nepostojeci"}, 400)
    # missing: jedan posao (X); lazni yt-dlp odmah padne (bez mreze)
    r = post("/api/playlists/PLtest/missing", {"recipe": "muzika"})
    assert len(r["jobs"]) == 1, r
    job = {j["id"]: j for j in get("/api/jobs")}[r["jobs"][0]]
    assert job["url"] == "https://www.youtube.com/watch?v=XXXXXXXXXXX" and job["vid"] == "XXXXXXXXXXX", job
    assert job["opts"]["dl"]["video_fmt"] == "audio" and job["opts"]["_playlist"]["id"] == "PLtest", job["opts"]
    r2 = post("/api/playlists/PLtest/missing", {"opts": {"dl": {"video_fmt": "none", "subs": "all"}, "proc": None}})
    j2 = {j["id"]: j for j in get("/api/jobs")}[r2["jobs"][0]]
    assert j2["opts"]["dl"]["video_fmt"] == "none", j2["opts"]
    r3 = post("/api/playlists/PLtest/missing", {})  # podrazumevani recept iz podesavanja (muzika)
    js = wait_jobs(r["jobs"] + r2["jobs"] + r3["jobs"])
    assert {j["opts"]["dl"].get("video_fmt") for j in js.values()} == {"audio", "none"}
    for jid in js:
        post(f"/api/jobs/{jid}/remove", {})


def t_health_and_rebuild():
    h = get("/api/health")
    by = {Path(i["folder"]).name: i["issues"] for i in h["items"]}
    t, m = IT["T"].name, IT["M"].name
    assert len(by[t]) == 1 and by[t][0].startswith("izvor skraćen (") and by[t][0].endswith("%)"), by
    assert 18 <= int(by[t][0].split("(")[1].rstrip("%)")) <= 22, by[t]
    assert by[m] == ["izvor nedostaje", "titl nedostaje: subs/de.srt", "izlaz nedostaje: Nedostaje.sr-lat.mp4"], by[m]
    assert IT["V"].name not in by and IT["A"].name not in by, by
    assert h["orphans"] == [{"folder": str(IT["O"]), "id": "abcdefghijk"}], h["orphans"]
    assert isinstance(h["checked_at"], int)
    cache = json.loads((DATA / "probe_cache.json").read_text())
    assert any("Skracen [TTTTTTTTTTT].source.mkv" in k for k in cache), cache
    t0 = time.time()
    assert get("/api/health")["items"] == h["items"]  # iz kesa
    print(f"      (drugi /api/health: {time.time() - t0:.2f}s)")
    r = post("/api/health/rebuild", {})
    assert r["rebuilt"] == 1 and not r.get("errors"), r
    assert get("/api/health")["orphans"] == []
    it = {i["id"]: i for i in get("/api/library")}["abcdefghijk"]
    assert it["source"] == "x [abcdefghijk].source.mkv" and it["kind"] == "video" and it["title"] == "x", it
    assert it["upload_date"] == "20200101" and it["has_video"] and len(it["audio"]) == 1, it
    subs = {s["key"]: s for s in it["subs"]}
    assert subs["manual:en"]["kind"] == "manual" and subs["auto:en"]["kind"] == "auto" and subs["auto:en"]["count"] == 2, subs
    assert len(subs) == 3 and len(it["subs"]) == 3, subs  # en.forced.srt ne gazi manual:en
    assert it["outputs"] == ["x [abcdefghijk].sr-lat.mp4"], it["outputs"]
    assert post("/api/health/rebuild", {})["rebuilt"] == 0


def t_audio_role_library():
    v = {i["id"]: i for i in get("/api/library")}["VVVVVVVVVVV"]
    assert [a["role"] for a in v["audio"]] == ["dub", "original"], v["audio"]
    assert "role" not in manifest("V")["audio"][0]  # stari manifest se ne prepisuje


def t_protection_v3():
    assert req("POST", "/api/subs/save", {"folder": str(IT["V"])}, {"Host": f"evil.test:{PORT}"})[0] == 403
    assert req("POST", "/api/health/rebuild", {}, {"Origin": "http://evil.test"})[0] == 403
    st, _, _ = raw("POST", "/api/subs/save", {"folder": "x"}, ctype="text/plain")
    assert st == 415, st
    assert req("GET", "/api/health", headers={"Host": f"evil.test:{PORT}"})[0] == 403


def t_existing_routes():
    assert isinstance(get("/api/tb/presets"), list) and "limits" in get("/api/status")
    assert [x["id"] for x in get("/api/recipes")] == ["samo", "arhiva", "arhiva_video", "muzika", "titlovi"]
    assert raw("GET", "/")[0] == 200
    r = batch([IT["V"]], "open")
    assert r["opened"] == [str(IT["V"].resolve())], r


# ================================================================ testovi: u procesu (bez servera)
def t_review_translation_unit():
    core = importlib.import_module("yt_arhiva")
    src = [{"text": "Hello"}, {"text": "He was tired"}, {"text": "Bye"}]
    calls = []

    def llm(prompt, schema=None, system=None):
        items = json.loads(prompt.split("Ulaz:\n", 1)[1].split("\n\n", 1)[0])
        calls.append(items)
        if len(calls) == 1:
            return "nije json"
        fix = {"Bila je umorna": "Bio je umoran", "Zdravo": "Zdravo!", "Здраво": "Zdravo!"}
        return json.dumps({"items": [{"id": it["id"], "t": fix.get(it["t"], it["t"])} for it in items
                                     if it["id"] != 2]})  # id 2 izostavljen -> ostaje nacrt
    prog = []
    out = core.review_translation(src, [{"text": "Zdravo"}, {"text": "Bila je\numorna"}, {"text": "Ćao"}], "Naslov",
                                  "lat", llm, SimpleNamespace(), progress=lambda d, t: prog.append((d, t)))
    assert out == ["Zdravo!", "Bio je umoran", "Ćao"], out
    assert len(calls) == 2 and calls[1][1] == {"id": 1, "src": "He was tired", "t": "Bila je umorna"}, calls
    assert prog == [(1, 1)], prog
    # cirilica: model radi u latinici, izlaz se preslovljava
    calls.clear()
    calls.append(None)  # preskoci "nije json"
    out = core.review_translation(src[:1], [{"text": "Здраво"}], "", "cyr", llm, SimpleNamespace())
    assert out == ["Здраво!"], out
    # grcki: latinicni odgovor se odbacuje
    greek = lambda p, schema=None, system=None: json.dumps({"items": [{"id": 0, "t": "Hello there my friend"},  # noqa: E731
                                                                       {"id": 1, "t": "Γεια σου φίλε"}]})
    out = core.review_translation([{"text": "Hello there my friend"}, {"text": "Hi friend"}],
                                  [{"text": "Γεια σας φίλε μου"}, {"text": "Γεια φίλε"}], "", "el", greek, SimpleNamespace())
    assert out == ["Γεια σας φίλε μου", "Γεια σου φίλε"], out
    # blokovi + bez izvora
    many = [{"text": f"red {i}"} for i in range(85)]
    seen = []
    out = core.review_translation(None, many, "", "lat",
                                  lambda p, schema=None, system=None: (seen.append(1), json.dumps({"items": []}))[1],
                                  SimpleNamespace(), progress=lambda d, t: None)
    assert out == [c["text"] for c in many] and len(seen) == 3, (len(seen), out[:2])
    # goli niz sa jednom stavkom (poslednji blok) se prihvata; objekat bez "items" -> ponovi blok
    li = core._lektura_items
    assert li('[{"id": 0, "t": "x"}]') == {0: "x"} and li('Evo: [{"id": 0, "t": "x"}]') == {0: "x"}
    for bad in ('{"id": 0, "t": "x"}', '{"foo": 1}', "nije json"):
        try:
            li(bad)
            raise AssertionError(f"ocekivan ValueError za {bad!r}")
        except ValueError:
            pass
    replies = iter(['{"id": 0, "t": "Zdravo!"}', '[{"id": 0, "t": "Zdravo!"}]'])
    out = core.review_translation(None, [{"text": "Zdravo"}], "", "lat",
                                  lambda p, schema=None, system=None: next(replies), SimpleNamespace())
    assert out == ["Zdravo!"], out


def t_probe_list_snapshot_unit():
    os.environ["YTA_DATA"] = str(TMP / "inproc")
    (TMP / "inproc").mkdir(exist_ok=True)
    (TMP / "inproc" / "settings.json").write_text(json.dumps({"out_dir": str(TMP / "inproc-out"), "browser": "none"}))
    panel = importlib.import_module("panel")
    assert str(panel.DATA) == str(TMP / "inproc")
    cat: dict = {}
    panel.catalog_index = lambda: cat
    flat = {"entries": []}
    panel._flat = lambda url: {"id": "PLx", "title": "Lista", "uploader": "U", "entries": flat["entries"]}

    def ent(*ids):
        return [{"id": i, "title": f"T {i}", "duration": 10} for i in ids]
    flat["entries"] = ent("a", "b", "c")
    r = panel.probe_list("https://www.youtube.com/playlist?list=PLx")
    assert [e["state"] for e in r["entries"]] == ["missing"] * 3 and r["new_count"] == 0 and r["removed"] == [], r
    snap = json.loads((TMP / "inproc" / "playlists" / "PLx.json").read_text())
    assert [e["pos"] for e in snap["entries"]] == [1, 2, 3] and len(snap["history"]) == 1, snap
    assert set(snap) >= {"id", "title", "url", "uploader", "fetched_at", "entries", "history", "removed"}
    cat["c"] = [{"folder": "/x", "source": "c.mkv", "updated": 1, "subs": []}]
    flat["entries"] = ent("b", "c", "d")
    r = panel.probe_list("https://www.youtube.com/playlist?list=PLx")
    assert {e["id"]: e["state"] for e in r["entries"]} == {"b": "missing", "c": "have", "d": "new"}, r["entries"]
    assert r["new_count"] == 1 and [x["id"] for x in r["removed"]] == ["a"] and r["removed"][0]["last_seen"], r
    snap = json.loads((TMP / "inproc" / "playlists" / "PLx.json").read_text())
    assert len(snap["history"]) == 2 and [x["id"] for x in snap["removed"]] == ["a"], snap
    # a se vratio -> vise nije "sklonjen"
    flat["entries"] = ent("a", "b", "c", "d")
    r = panel.probe_list("https://www.youtube.com/playlist?list=PLx")
    assert r["removed"] == [] and {e["id"]: e["state"] for e in r["entries"]}["a"] == "new", r
    pl = panel.playlists_list()
    assert pl[0]["id"] == "PLx" and (pl[0]["count"], pl[0]["have"], pl[0]["missing"]) == (4, 1, 3), pl
    # kanal pa samo njegov tab /videos (isti yt-dlp id): tab ima svoj snimak, nista nije "sklonjeno"
    panel._flat = lambda url: {"id": "UCkanal", "title": "Kanal", "uploader": "K", "entries": flat["entries"]}
    flat["entries"] = ent("v1", "v2", "s1", "s2")
    panel.probe_list("https://www.youtube.com/@kanal")
    flat["entries"] = ent("v1", "v2")
    r = panel.probe_list("https://www.youtube.com/@kanal/videos")
    assert r["removed"] == [] and r["new_count"] == 0, r
    flat["entries"] = ent("v1", "v2", "s1", "s2")  # ponovo ceo kanal: s1/s2 i dalje tu
    r = panel.probe_list("https://www.youtube.com/@kanal")
    assert r["removed"] == [], r
    snaps = {x["id"]: x for x in panel.playlists_list()}
    assert snaps["UCkanal"]["count"] == 4 and snaps["UCkanal_videos"]["count"] == 2, snaps
    assert panel.load_playlist("UCkanal_videos")["list_id"] == "UCkanal"
    # uloga audio trake
    ar = panel.audio_role
    assert ar({"language_preference": 10}) == "original" and ar({"language_preference": 5}) == "default"
    assert ar({"language_preference": -10}) == "descriptive"
    assert ar({"language_preference": -1, "format_note": "German (Germany) dubbed-auto"}) == "dub"
    assert ar({"format_note": "English original, high"}) == "original" and ar({}, True) == "original"
    assert ar({"language_preference": -1}) == "default"
    # kljuc prevoda -> engine/model
    kem = panel.key_engine_model
    assert kem({"key": "sr:lat:synergy:gemma4:12b-it-qat:rev:manual:en", "src": "manual:en"}) == ("synergy", "gemma4:12b-it-qat")
    assert kem({"key": "sr:lat:gemma4:12b-it-qat:manual:en", "src": "manual:en"}) == (None, "gemma4:12b-it-qat")
    assert kem({"key": LEK_KEY, "src": "manual:en", "engine": "claude", "model": "sonnet"}) == ("claude", "sonnet")
    assert kem({"key": "tr:el:gemini:gemini-3.5-flash-lite:auto:en", "src": "auto:en"}) == ("gemini", "gemini-3.5-flash-lite")
    assert kem({"key": "sr:lat:rebuilt:sr-lat.x", "kind": "sr"}) == (None, None)


HTTP_TESTS = [t_subs_get, t_subs_save, t_media_range, t_lektura, t_audio_copy, t_playlists,
              t_health_and_rebuild, t_audio_role_library, t_protection_v3, t_existing_routes]
UNIT_TESTS = [t_review_translation_unit, t_probe_list_snapshot_unit]


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
    env = dict(os.environ, YTA_DATA=str(DATA), YTA_PORT=str(PORT), YTA_NO_OPEN="1", YTA_ROOT=str(ROOT),
               PATH=f"{FAKEBIN}:{os.environ.get('PATH', '')}")
    log = open(TMP / "panel.log", "w")
    p = subprocess.Popen([sys.executable, str(TMP / "launch.py")], env=env, stdout=log, stderr=subprocess.STDOUT)
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
