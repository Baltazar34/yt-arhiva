#!/usr/bin/env python3
"""Test panel.py HTTP API (UX v2): recepti, batch akcije Biblioteke, graceful skip, per-item opts, zastita.

  python3 tests/test_panel_api.py          # panel na 127.0.0.1:8797, privremeni YTA_DATA i out_dir (~20 s)
  python3 tests/test_panel_api.py audio    # samo testovi ciji naziv sadrzi 'audio'
  YTA_TEST_KEEP=1 python3 tests/test_panel_api.py   # ne brisi privremeni folder

Lazna arhiva bez mreze: stavke sa .yta/manifest.json i sitnim izvorima iz ffmpeg lavfi.
Privremeni folder pravi tempfile (postuje TMPDIR). Prevod se nikad ne pokrece: model u test
podesavanjima ne postoji, pa bi slucajan prevod pao (a test bi to uhvatio).
"""
from __future__ import annotations

import http.client
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
PORT = int(os.environ.get("YTA_TEST_PORT", "8797"))
BIN = Path(os.environ.get("TB_FFMPEG_DIR", "/opt/homebrew/opt/ffmpeg-full/bin"))
FF, FP = str(BIN / "ffmpeg"), str(BIN / "ffprobe")
TMP = Path(tempfile.mkdtemp(prefix="yta-api-", dir=os.environ.get("YTA_TEST_TMP") or None))
DATA, OUT = TMP / "data", TMP / "out"
IT: dict[str, Path] = {}  # oznaka -> folder stavke

SRT_EN = """1
00:00:00,200 --> 00:00:00,900
Hello there

2
00:00:01,000 --> 00:00:01,800
General Kenobi
"""
SRT_SR = SRT_EN.replace("Hello there", "Zdravo").replace("General Kenobi", "Generale Kenobi")
SR_KEY = "sr:lat:gemma4:12b-it-qat:manual:en"
SR_FILE = "subs/sr-lat.manual_en.gemma4_12b-it-qat.srt"


# ================================================================ pomocne
def sh(cmd: list[str], cwd=None) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(f"komanda nije uspela: {' '.join(cmd)}\n{p.stderr[-800:]}")
    return p


def streams(path: Path) -> list[dict]:
    p = sh([FP, "-v", "error", "-show_entries", "stream=codec_type,codec_name:stream_tags=language,title",
            "-of", "json", str(path)])
    return json.loads(p.stdout).get("streams") or []


def req(method: str, path: str, body=None, headers: dict | None = None, raw: bytes | None = None,
        ctype: str = "application/json"):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
    h = {"Host": f"127.0.0.1:{PORT}"}
    data = None
    if body is not None or raw is not None:
        data = raw if raw is not None else json.dumps(body).encode()
        h["Content-Type"] = ctype
    h.update(headers or {})
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    txt = r.read()
    c.close()
    try:
        j = json.loads(txt)
    except ValueError:
        j = None
    return r.status, j


def get(path: str):
    st, j = req("GET", path)
    assert st == 200, f"GET {path} -> {st} {j}"
    return j


def post(path: str, body: dict, code: int = 200):
    st, j = req("POST", path, body)
    assert st == code, f"POST {path} -> {st} {j} (ocekivano {code})"
    return j


def batch(folders: list, action: str, params: dict | None = None, code: int = 200) -> dict:
    return post("/api/library/batch", {"folders": [str(f) for f in folders], "action": action,
                                       "params": params or {}}, code)


def job_log(jid: str) -> str:
    return "\n".join(get(f"/api/jobs/{jid}/log")["log"][-25:])


def wait_jobs(ids: list[str], timeout: float = 120) -> dict[str, dict]:
    t0 = time.time()
    while time.time() - t0 < timeout:
        by = {j["id"]: j for j in get("/api/jobs")}
        if all(by.get(i, {}).get("state") in ("done", "error", "cancelled") for i in ids):
            return {i: by[i] for i in ids}
        time.sleep(0.3)
    raise AssertionError(f"poslovi nisu zavrseni za {timeout}s: {ids}")


def assert_done(j: dict):
    assert j["state"] == "done" and not j["error"], \
        f"posao {j['id']} ({j['title']}): {j['state']} {j['error']}\n{job_log(j['id'])}"


def reason(res: dict, folder: Path) -> str:
    hit = [s["reason"] for s in res["skipped"] if Path(s["folder"]).resolve() == folder.resolve()]
    assert hit, f"{folder.name} nije u skipped: {res}"
    return hit[0]


def lib_by_id() -> dict[str, dict]:
    return {i["id"]: i for i in get("/api/library")}


# ================================================================ lazna arhiva
def mk_item(tag: str, vid: str, title: str, *, source: str | None = None, kind: str | None = None,
            video: dict | None = None, audio: list | None = None, subs: list | None = None) -> Path:
    d = OUT / f"20260101 - {title} [{vid}]"
    (d / ".yta").mkdir(parents=True)
    (d / "subs").mkdir()
    m = {"version": 1, "url": f"https://www.youtube.com/watch?v={vid}", "id": vid, "title": title,
         "channel": "Test kanal", "duration": 2, "thumbnail": None, "upload_date": "20260101",
         "language": "en", "live_status": None, "source": source, "kind": kind, "video": video,
         "audio": audio or [], "subs": subs or [], "outputs": [], "playlists": [], "updated": time.time()}
    (d / ".yta" / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=1))
    IT[tag] = d
    return d


def en_sub() -> dict:
    return {"key": "manual:en", "kind": "manual", "lang": "en", "file": "subs/en.srt", "count": 2, "name": "English"}


def make_archive():
    OUT.mkdir(parents=True)
    DATA.mkdir(parents=True)
    vinfo = {"format_id": "137", "width": 320, "height": 240, "codec": "h264", "fps": 25, "hdr": False,
             "premium": False, "duration": 2.0}
    a2 = [{"index": 0, "lang": "de", "original": False, "variant": "dub", "codec": "aac", "title": "Deutsch — dub"},
          {"index": 1, "lang": "en", "original": True, "variant": "original", "codec": "aac",
           "title": "English — original"}]
    # A: video (2 audio trake: dub pa original) + engleski CC + postojeci srpski prevod (latinica)
    src_a = "Test video [AAAAAAAAAAA].source.mkv"
    a = mk_item("A", "AAAAAAAAAAA", "Test video", source=src_a, kind="video", video=vinfo, audio=a2,
                subs=[en_sub(), {"key": SR_KEY, "kind": "sr", "lang": "sr", "file": SR_FILE, "count": 2,
                                 "src": "manual:en", "name": "Srpski latinica — gemma4:12b-it-qat (iz: English)"}])
    (a / "subs" / "en.srt").write_text(SRT_EN)
    (a / SR_FILE).write_text(SRT_SR)
    sh([FF, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=s=320x240:r=25:d=2",
        "-f", "lavfi", "-i", "sine=f=440:d=2", "-f", "lavfi", "-i", "sine=f=880:d=2",
        "-i", str(a / "subs" / "en.srt"),
        "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:s",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
        "-c:s", "srt", "-metadata:s:a:0", "language=deu", "-metadata:s:a:1", "language=eng",
        "-metadata:s:s:0", "language=eng", str(a / src_a)])
    # B: nema titla ni izvora (npr. recept "Samo titlovi" bez YT titla)
    mk_item("B", "BBBBBBBBBBB", "Bez titla")
    # C: samo audio
    src_c = "Samo audio [CCCCCCCCCCC].audio.m4a"
    c = mk_item("C", "CCCCCCCCCCC", "Samo audio", source=src_c, kind="audio",
                audio=[{"index": 0, "lang": "en", "original": True, "variant": "original", "codec": "aac"}])
    sh([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=2",
        "-c:a", "aac", "-b:a", "96k", str(c / src_c)])
    # D: video + engleski CC, bez srpskog prevoda
    src_d = "Video bez prevoda [DDDDDDDDDDD].source.mkv"
    d = mk_item("D", "DDDDDDDDDDD", "Video bez prevoda", source=src_d, kind="video", video=vinfo,
                audio=[a2[1] | {"index": 0}], subs=[en_sub()])
    (d / "subs" / "en.srt").write_text(SRT_EN)
    sh([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=25:d=2",
        "-f", "lavfi", "-i", "sine=f=440:d=2", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", str(d / src_d)])
    # E, F: samo manifest (za limit "Otvori foldere")
    mk_item("E", "EEEEEEEEEEE", "Prazna E")
    mk_item("F", "FFFFFFFFFFF", "Prazna F")
    # G: video + samo srpski rucni CC sa YouTube-a (latinica), bez prevoda
    src_g = "Srpski CC [GGGGGGGGGGG].source.mkv"
    g = mk_item("G", "GGGGGGGGGGG", "Srpski CC", source=src_g, kind="video", video=vinfo,
                audio=[a2[1] | {"index": 0}],
                subs=[{"key": "manual:sr", "kind": "manual", "lang": "sr", "file": "subs/sr.srt", "count": 2,
                       "name": "Srpski"}])
    (g / "subs" / "sr.srt").write_text(SRT_SR)
    shutil.copyfile(d / src_d, g / src_g)
    # H: YouTube ima titl (info.json), ali titl nije u arhivi i nema izvora (npr. 429 pri preuzimanju titla)
    h = mk_item("H", "HHHHHHHHHHH", "Titl nije preuzet")
    (h / ".yta" / "info.json").write_text(json.dumps({"id": "HHHHHHHHHHH", "language": "en",
                                                      "subtitles": {"en": [{"ext": "json3"}]}}))
    (DATA / "settings.json").write_text(json.dumps({
        "out_dir": str(OUT), "browser": "none", "cookies_file": "", "engine": "ollama",
        "model": "yta-test-nema-modela", "script": "lat", "parallel_encodes": 2}, indent=1))


# ================================================================ testovi
def t_recipes():
    r = get("/api/recipes")
    assert [x["id"] for x in r] == ["samo", "arhiva", "arhiva_video", "muzika", "titlovi"], r
    by = {x["id"]: x for x in r}
    for x in r:
        assert x["icon"] and x["label"] and x["desc"] and isinstance(x["knobs"], list), x
    assert by["arhiva"]["dl"] == {"video_fmt": "auto:best", "audio_fmt": "all", "subs": "all"}
    assert by["arhiva"]["proc"] == {"sub_source": "auto-best", "target": "lat", "output": "none",
                                    "keep_source": True}, by["arhiva"]["proc"]
    assert by["arhiva_video"]["dl"] == by["arhiva"]["dl"]
    assert by["arhiva_video"]["proc"] == {
        "sub_source": "auto-best", "target": "lat", "output": "burn", "encoder": "hevc", "vq": 65, "scale": "src",
        "audio_out": "orig", "audio_enc": "aac192", "subs_out": "sr+orig", "keep_source": True}
    assert by["muzika"]["dl"] == {"video_fmt": "audio", "audio_fmt": "auto", "audio_conv": "mp3", "subs": "none"}
    assert by["muzika"]["proc"] is None and by["muzika"]["knobs"] == ["audio_conv"]
    assert by["titlovi"]["dl"] == {"video_fmt": "none", "subs": "all"}
    assert by["titlovi"]["proc"] == {"sub_source": "auto-best", "target": "lat", "output": "srt"}
    assert "script" in by["arhiva"]["knobs"] and "quality" in by["arhiva"]["knobs"]
    # 3.2: podrazumevano samo preuzimanje (ceo sadrzaj, bez obrade)
    assert by["samo"]["dl"] == by["arhiva"]["dl"] and by["samo"]["proc"] is None, by["samo"]
    assert by["samo"]["default"] and not by["arhiva"]["default"] and not by["muzika"]["default"]
    assert "latinica" in by["arhiva"]["desc"]
    # pismo iz podesavanja
    post("/api/settings", {"script": "cyr"})
    by = {x["id"]: x for x in get("/api/recipes")}
    assert [by[k]["proc"]["target"] for k in ("arhiva", "arhiva_video", "titlovi")] == ["cyr"] * 3
    assert "ćirilica" in by["arhiva"]["desc"]
    # auto_translate iskljucen -> bez prevoda; Arhiva bez obrade
    post("/api/settings", {"auto_translate": False})
    by = {x["id"]: x for x in get("/api/recipes")}
    assert by["arhiva"]["proc"] is None, by["arhiva"]
    assert by["arhiva_video"]["proc"]["target"] == "none" and by["arhiva_video"]["proc"]["output"] == "burn"
    assert by["titlovi"]["proc"]["target"] == "none"
    assert "script" not in by["arhiva"]["knobs"] and "bez prevoda" in by["arhiva"]["desc"]
    # podrazumevani recept; nepoznat se ignorise
    post("/api/settings", {"recipe": "muzika"})
    post("/api/settings", {"recipe": "nepostojeci"})
    assert get("/api/settings")["recipe"] == "muzika"
    assert {x["id"]: x for x in get("/api/recipes")}["muzika"]["default"]
    s = post("/api/settings", {"script": "lat", "auto_translate": True, "recipe": "arhiva"})
    assert s["auto_translate"] is True and s["recipe"] == "arhiva" and s["script"] == "lat"


def t_library_fields():
    by = lib_by_id()
    assert len(by) == 8, list(by)
    g = by["GGGGGGGGGGG"]
    assert g["sr"] == [] and g["sr_cc"] is True and g["has_video"] is True, g
    a, b, c, d = by["AAAAAAAAAAA"], by["BBBBBBBBBBB"], by["CCCCCCCCCCC"], by["DDDDDDDDDDD"]
    assert a["sr"] == ["lat"] and a["has_video"] is True and a["sr_cc"] is False, a
    assert b["sr"] == [] and b["has_video"] is False and b["kind"] == "none", b
    assert c["sr"] == [] and c["has_video"] is False and c["kind"] == "audio", c
    assert d["sr"] == [] and d["has_video"] is True, d


def t_toolbox_none_and_soft():
    a = IT["A"]
    r1 = batch([a], "toolbox", {"preset": "remux", "sub": "none"})
    r2 = batch([a], "toolbox", {"preset": "remux", "sub": "soft", "target": "lat"})  # isti folder -> lock
    assert len(r1["jobs"]) == 1 and not r1["skipped"], r1
    assert len(r2["jobs"]) == 1 and not r2["skipped"], r2
    js = wait_jobs(r1["jobs"] + r2["jobs"])
    for j in js.values():
        assert_done(j)
        assert j["kind"] == "transcode" and j["folder"] and Path(j["folder"]).resolve() == a.resolve(), j
    j1, j2 = js[r1["jobs"][0]], js[r2["jobs"][0]]
    o1, o2 = a / j1["outputs"][0], a / j2["outputs"][0]
    assert o1 != o2 and o1.is_file() and o2.is_file(), (o1, o2)
    assert {o1.name, o2.name} == {"Test video [AAAAAAAAAAA].remux.mkv", "Test video [AAAAAAAAAAA].remux (2).mkv"}
    s1, s2 = streams(o1), streams(o2)
    assert [x["codec_type"] for x in s1].count("audio") == 2, s1
    sub1 = [(x.get("tags") or {}).get("language") for x in s1 if x["codec_type"] == "subtitle"]
    sub2 = [(x.get("tags") or {}).get("language") for x in s2 if x["codec_type"] == "subtitle"]
    assert sub1 == ["eng"], sub1
    assert "srp" in sub2 and "eng" in sub2, sub2
    title = next((x.get("tags") or {}).get("title") for x in s2
                 if x["codec_type"] == "subtitle" and (x.get("tags") or {}).get("language") == "srp")
    assert title and title.startswith("Srpski"), title
    outs = lib_by_id()["AAAAAAAAAAA"]["outputs"]
    assert o1.name in outs and o2.name in outs, outs
    assert j2["opts"]["spec"]["sub_src"] == "file" and j2["opts"]["spec"]["translate"] == "off"
    assert j2["opts"]["spec"]["sub_file"].endswith(SR_FILE.split("/")[1])


def t_toolbox_burn():
    r = batch([IT["A"]], "toolbox", {"preset": "kompat", "sub": "burn", "target": "lat"})
    assert len(r["jobs"]) == 1, r
    j = wait_jobs(r["jobs"])[r["jobs"][0]]
    assert_done(j)
    o = IT["A"] / j["outputs"][0]
    assert o.name == "Test video [AAAAAAAAAAA].kompat.mp4" and o.is_file(), o
    assert j["stages"]["subs"]["status"] == "done" and j["opts"]["spec"]["sub_mode"] == "burn", j["stages"]
    assert [x["codec_type"] for x in streams(o)][:1] == ["video"]


def t_toolbox_skips():
    r = batch([IT["D"], IT["C"], IT["B"]], "toolbox", {"preset": "apple", "sub": "burn", "target": "lat"})
    assert r["jobs"] == [], r
    assert "prvo Prevedi" in reason(r, IT["D"])
    assert "nema videa" in reason(r, IT["C"])
    assert "nema izvora" in reason(r, IT["B"])
    e = batch([IT["A"]], "toolbox", {"preset": "remux", "sub": "burn"}, code=400)
    assert "rekodiranje" in e["error"], e
    e = batch([IT["A"]], "toolbox", {"preset": "nema-ovog"}, code=400)
    assert "preset" in e["error"], e
    # bez srpskog titla (i za audio) target se ne gleda: "none" nije 400
    r = batch([IT["B"]], "toolbox", {"preset": "remux", "sub": "none", "target": "none"})
    assert r["jobs"] == [] and "nema izvora" in reason(r, IT["B"]), r
    r = batch([IT["B"]], "audio", {"preset": "audio_mp3", "target": "none"})
    assert r["jobs"] == [] and "nema izvora" in reason(r, IT["B"]), r
    e = batch([IT["A"]], "toolbox", {"preset": "arhiva", "sub": "soft", "target": "none"}, code=400)
    assert "target" in e["error"], e


def t_toolbox_sr_cc():
    # srpski CC sa YouTube-a: Prevedi ga preskace, pa ga Transkoduj sa titlom koristi (u izabranom pismu)
    g = IT["G"]
    r = batch([g], "translate", {"target": "cyr"})
    assert r["jobs"] == [] and "srpski titl sa YouTube-a" in reason(r, g), r
    r = batch([g], "toolbox", {"preset": "remux", "sub": "soft", "target": "cyr"})
    assert len(r["jobs"]) == 1 and not r["skipped"], r
    j = wait_jobs(r["jobs"])[r["jobs"][0]]
    assert_done(j)
    o = g / j["outputs"][0]
    st = streams(o)
    assert [(x.get("tags") or {}).get("language") for x in st if x["codec_type"] == "subtitle"] == ["srp"], st
    txt = sh([FF, "-hide_banner", "-loglevel", "error", "-i", str(o), "-map", "0:s:0", "-f", "srt", "-"]).stdout
    assert "Здраво" in txt, txt


def t_lib_roots_clean():
    # transkod u folder stavke ne sme taj folder upisati kao novi koren biblioteke (library_roots.json)
    p = DATA / "library_roots.json"
    roots = [Path(x).resolve() for x in (json.loads(p.read_text()) if p.exists() else [])]
    assert not [r for r in roots if OUT.resolve() in r.parents], roots


def t_settings_coercion():
    # bool iz forme kao string: "false" mora iskljuciti prevod (bool("false") je True)
    s = post("/api/settings", {"auto_translate": "false"})
    assert s["auto_translate"] is False, s
    assert {x["id"]: x for x in get("/api/recipes")}["arhiva"]["proc"] is None
    for v, want in (("0", False), ("true", True), (0, False), (1, True), (False, False), (True, True)):
        assert post("/api/settings", {"auto_translate": v})["auto_translate"] is want, v
    # nepoznato pismo se ignorise (inace Biblioteka -> Prevedi bez target-a vraca 400)
    assert post("/api/settings", {"script": "klingonski"})["script"] == "lat"
    # "⚙ Prilagođeno" kao podrazumevani recept (UI ga nudi) se pamti; tada nijedan gotov recept nije default
    assert post("/api/settings", {"recipe": "custom"})["recipe"] == "custom"
    assert not any(x["default"] for x in get("/api/recipes"))
    post("/api/jobs/batch", {"items": [{"url": "http://127.0.0.1:9/x"}], "recipe": "custom"}, 400)
    s = post("/api/settings", {"auto_translate": True, "script": "lat", "recipe": "arhiva"})
    assert s["auto_translate"] is True and s["script"] == "lat", s


def t_audio():
    ra = batch([IT["A"]], "audio", {"preset": "audio_mp3"})
    rc = batch([IT["C"]], "audio")  # podrazumevano M4A
    assert len(ra["jobs"]) == 1 and len(rc["jobs"]) == 1, (ra, rc)
    js = wait_jobs(ra["jobs"] + rc["jobs"])
    ja, jc = js[ra["jobs"][0]], js[rc["jobs"][0]]
    assert_done(ja)
    assert_done(jc)
    assert ja["opts"]["spec"]["audio_tracks"] == [1], ja["opts"]["spec"]["audio_tracks"]  # original, ne dub
    oa, oc = IT["A"] / ja["outputs"][0], IT["C"] / jc["outputs"][0]
    assert oa.name == "Test video [AAAAAAAAAAA].audio_mp3.mp3" and oa.is_file(), oa
    assert oc.suffix == ".m4a" and ".audio_m4a" in oc.name and oc.is_file(), oc
    sa = streams(oa)
    assert [x["codec_type"] for x in sa] == ["audio"] and sa[0]["codec_name"] == "mp3", sa
    assert oc.name in lib_by_id()["CCCCCCCCCCC"]["outputs"]
    e = batch([IT["A"]], "audio", {"preset": "arhiva"}, code=400)
    assert "preset" in e["error"], e


def t_translate_no_subs_no_source():
    r = batch([IT["B"]], "translate", {"target": "lat"})
    assert len(r["jobs"]) == 1 and not r["skipped"], r
    j = wait_jobs(r["jobs"])[r["jobs"][0]]
    assert_done(j)
    assert j["kind"] == "process" and j["opts"]["proc"]["sub_source"] == "auto-best", j["opts"]
    st = {k: v["status"] for k, v in j["stages"].items()}
    assert st == {"download": "skipped", "subs": "skipped", "translate": "skipped", "output": "skipped"}, st
    assert "YouTube nema titl" in j["stages"]["subs"]["info"], j["stages"]
    # izlaz SRT (recept Samo titlovi) bez titla i izvora -> takodje preskok, ne greska
    j = post("/api/process", {"folder": str(IT["B"]), "proc": {"sub_source": "auto-best", "target": "lat",
                                                              "output": "srt"}})
    j = wait_jobs([j["id"]])[j["id"]]
    assert_done(j)
    assert j["stages"]["output"]["status"] == "skipped" and j["outputs"] == [], j


def t_translate_sub_missing_is_error():
    # YouTube ima titl, a nije u arhivi -> greska (tihi "done" bi sakrio neuspelo preuzimanje titla)
    r = batch([IT["H"]], "translate", {"target": "lat"})
    assert len(r["jobs"]) == 1, r
    j = wait_jobs(r["jobs"])[r["jobs"][0]]
    assert j["state"] == "error" and "nije preuzet" in (j["error"] or ""), (j["state"], j["error"])


def t_translate_skip_existing():
    r = batch([IT["A"], IT["A"]], "translate", {"target": "lat"})  # duplikat se ignorise
    assert r["jobs"] == [] and len(r["skipped"]) == 1, r
    assert "već ima srpski prevod" in reason(r, IT["A"])
    e = batch([IT["A"]], "translate", {"target": "klingonski"}, code=400)
    assert "target" in e["error"], e


def t_video_soft_existing_sr():
    # postojeci prevod se koristi direktno (bez LLM-a): MKV sa srpskim + originalnim titlom, originalni zvuk
    post("/api/settings", {"quality": 55})  # bez vq u params -> kvalitet iz Podesavanja (kao do_output)
    try:
        r = batch([IT["A"]], "video", {"output": "soft", "target": "lat"})
    finally:
        post("/api/settings", {"quality": 65})
    assert len(r["jobs"]) == 1 and not r["skipped"], r
    j = wait_jobs(r["jobs"])[r["jobs"][0]]
    assert_done(j)
    assert j["opts"]["proc"]["sub_source"] == SR_KEY, j["opts"]["proc"]
    assert j["opts"]["proc"]["vq"] == 55, j["opts"]["proc"]
    assert j["stages"]["translate"]["status"] == "skipped", j["stages"]
    o = IT["A"] / j["outputs"][0]
    assert o.name == "Test video [AAAAAAAAAAA].sr-lat.mkv" and o.is_file(), o
    st = streams(o)
    assert [(x.get("tags") or {}).get("language") for x in st if x["codec_type"] == "subtitle"] == ["srp", "eng"], st
    assert [(x.get("tags") or {}).get("language") for x in st if x["codec_type"] == "audio"] == ["eng"], st


def t_video_skips():
    r = batch([IT["C"], IT["B"]], "video", {"output": "burn", "target": "lat"})
    assert r["jobs"] == [], r
    assert "nema videa" in reason(r, IT["C"]) and "samo audio" in reason(r, IT["C"])
    assert "nema videa" in reason(r, IT["B"])
    for bad in ({"output": "gif"}, {"output": "burn", "scale": "1080; rm"}, {"output": "burn", "audio_enc": "x"},
                {"output": "burn", "vq": "visok"}, {"output": "burn", "encoder": "gif"}):
        e = batch([IT["A"]], "video", bad, code=400)
        assert e.get("error"), (bad, e)


def t_open():
    outside = TMP / "van-arhive"
    outside.mkdir(exist_ok=True)
    fs = [IT[k] for k in "ABCDEF"] + [IT["A"], outside]
    r = batch(fs, "open")
    assert len(r["opened"]) == 5 and r["jobs"] == [], r
    assert "najviše 5" in reason(r, IT["F"])
    assert "nije u arhivi" in reason(r, outside)
    assert len(r["skipped"]) == 2, r


def t_protection():
    n_jobs = len(get("/api/jobs"))
    st, _ = req("GET", "/api/recipes", headers={"Host": "evil.example:8797"})
    assert st == 403, st
    st, _ = req("GET", "/api/library", headers={"Origin": "http://evil.example"})
    assert st == 403, st
    body = {"folders": [str(IT["D"])], "action": "audio", "params": {}}
    st, _ = req("POST", "/api/library/batch", body, headers={"Origin": "http://evil.example"})
    assert st == 403, st
    st, _ = req("POST", "/api/library/batch", body, headers={"Host": f"attacker.test:{PORT}"})
    assert st == 403, st
    st, _ = req("POST", "/api/library/batch", body, ctype="text/plain")
    assert st == 415, st
    st, _ = req("POST", "/api/library/batch", raw=b"{nope")
    assert st == 400, st
    st, _ = req("POST", "/api/library/batch", raw=b"[1, 2]")
    assert st == 400, st
    st, _ = req("POST", "/api/jobs/batch", {"items": [{"url": "http://x"}]}, ctype="text/plain")
    assert st == 415, st
    assert len(get("/api/jobs")) == n_jobs, "odbijen zahtev je napravio posao"
    r = batch(["/etc", "/", ""], "translate", {"target": "lat"})
    assert r["jobs"] == [] and len(r["skipped"]) == 3, r
    assert all(s["reason"] in ("nije u arhivi", "prazna putanja") for s in r["skipped"]), r
    e = batch([IT["A"]], "rm -rf", code=400)
    assert "akcija" in e["error"], e
    post("/api/process", {"folder": "/etc", "proc": {}}, code=400)
    for bad in ({"url": "--exec=id"}, {}, {"url": "http://127.0.0.1:9/x", "opts": [1]}):  # /api/jobs: samo URL
        st, _ = req("POST", "/api/jobs", bad)
        assert st == 400, (bad, st)
    # legitiman Origin prolazi
    st, _ = req("POST", "/api/library/batch", {"folders": [], "action": "open"},
                headers={"Origin": f"http://127.0.0.1:{PORT}"})
    assert st == 200, st
    assert len(get("/api/jobs")) == n_jobs


def t_jobs_batch_merge():
    n_jobs = len(get("/api/jobs"))
    e = post("/api/jobs/batch", {"items": [{"url": "http://127.0.0.1:9/ok"}, {"url": "--exec=id"}], "opts": {}}, 400)
    assert "stavka 2" in e["error"], e
    post("/api/jobs/batch", {"items": [{"url": "http://127.0.0.1:9/x"}], "recipe": "nema"}, 400)
    assert len(get("/api/jobs")) == n_jobs
    # port 9 ne slusa -> yt-dlp brzo padne; proveravaju se samo spojene opcije
    r = post("/api/jobs/batch", {"recipe": "arhiva", "items": [
        {"url": "http://127.0.0.1:9/a", "title": "a", "opts": {"dl": {"video_fmt": "auto:720"},
                                                              "proc": {"target": "cyr"}}},
        {"url": "http://127.0.0.1:9/b", "title": "b"},
        {"url": "http://127.0.0.1:9/c", "title": "c", "opts": {"proc": None, "_playlist": {"id": "x"}}}]})
    assert len(r["jobs"]) == 3, r
    js = wait_jobs(r["jobs"], timeout=90)
    for j in js.values():  # nedostupan URL -> greska u probe-u (nista nije preuzeto ni obradjeno)
        assert j["state"] == "error" and j["stages"]["translate"]["status"] == "pending", (j["state"], j["error"])
    a, b, c = (js[i]["opts"] for i in r["jobs"])
    assert a["dl"] == {"video_fmt": "auto:720", "audio_fmt": "all", "subs": "all"}, a["dl"]
    assert a["proc"] == {"sub_source": "auto-best", "target": "cyr", "output": "none", "keep_source": True}, a
    assert b["dl"]["video_fmt"] == "auto:best" and b["proc"]["target"] == "lat", b
    assert c["proc"] is None and c["dl"]["video_fmt"] == "auto:best" and c.get("_playlist") is None, c
    # eksplicitne zajednicke opcije (recept Samo titlovi) + override samo dl
    r = post("/api/jobs/batch", {"opts": {"dl": {"video_fmt": "none", "subs": "all"},
                                          "proc": {"sub_source": "auto-best", "target": "lat", "output": "srt"}},
                                 "items": [{"url": "http://127.0.0.1:9/d", "opts": {"dl": {"subs": "auto"}}}]})
    d = wait_jobs(r["jobs"], timeout=90)[r["jobs"][0]]["opts"]
    assert d["dl"] == {"video_fmt": "none", "subs": "auto"} and d["proc"]["output"] == "srt", d
    # bez zajednickih opcija: opcije stavke vaze kao da su zajednicke (bez nezatrazene obrade / prevoda)
    r2 = post("/api/jobs/batch", {"items": [{"url": "http://127.0.0.1:9/e",
                                             "opts": {"dl": {"video_fmt": "none", "subs": "all"}}}]})
    e_ = wait_jobs(r2["jobs"], timeout=90)[r2["jobs"][0]]["opts"]
    assert e_["dl"] == {"video_fmt": "none", "subs": "all"} and e_.get("proc") is None, e_
    for jid in list(js) + r["jobs"] + r2["jobs"]:  # pocisti red
        post(f"/api/jobs/{jid}/remove", {})


def t_existing_routes():
    assert isinstance(get("/api/tb/presets"), list)
    assert "limits" in get("/api/status")
    assert isinstance(get("/api/jobs"), list)
    st, _ = req("GET", "/")
    assert st == 200, st


TESTS = [t_recipes, t_settings_coercion, t_library_fields, t_toolbox_none_and_soft, t_toolbox_burn,
         t_toolbox_skips, t_toolbox_sr_cc, t_audio, t_lib_roots_clean,
         t_translate_no_subs_no_source, t_translate_sub_missing_is_error, t_translate_skip_existing, t_video_soft_existing_sr, t_video_skips,
         t_open, t_protection, t_jobs_batch_merge, t_existing_routes]


# ================================================================ main
def port_free() -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", PORT)) != 0


def main() -> int:
    filt = sys.argv[1:]
    if not port_free():
        print(f"port {PORT} je zauzet — ugasi drugu instancu")
        return 2
    make_archive()
    env = dict(os.environ, YTA_DATA=str(DATA), YTA_PORT=str(PORT), YTA_NO_OPEN="1")
    log = open(TMP / "panel.log", "w")
    p = subprocess.Popen([sys.executable, str(ROOT / "panel.py")], env=env, stdout=log, stderr=subprocess.STDOUT)
    fails = 0
    try:
        for _ in range(100):
            if p.poll() is not None:
                raise RuntimeError("panel se ugasio:\n" + (TMP / "panel.log").read_text()[-1500:])
            if not port_free():
                break
            time.sleep(0.1)
        run = [t for t in TESTS if not filt or any(f in t.__name__ for f in filt)]
        for t in run:
            t0 = time.time()
            try:
                t()
                print(f"OK    {t.__name__} ({time.time() - t0:.1f}s)")
            except Exception:  # noqa: BLE001
                fails += 1
                print(f"FAIL  {t.__name__}\n{traceback.format_exc()}")
        print(f"\n{len(run) - fails}/{len(run)} testova prošlo" + (f" · privremeni folder: {TMP}" if fails else ""))
    finally:
        p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
        log.close()
        if fails:
            print("panel log (kraj):\n" + (TMP / "panel.log").read_text()[-1500:])
        if not os.environ.get("YTA_TEST_KEEP") and not fails:
            shutil.rmtree(TMP, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
