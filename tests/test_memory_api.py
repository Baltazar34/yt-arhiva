#!/usr/bin/env python3
"""Test panela za memoriju ispravki (docs/memory-contract.md §1, §5, §7): stt/tr parovi iz editora, /api/memory*,
zastita POST ruta, podesavanja mem_tr/mem_stt, whisper_prompt_for, primena memorije u whisper-u i prevodu.

  python3 tests/test_memory_api.py            # panel na 127.0.0.1:8823 (YTA_TEST_PORT), privremeni YTA_DATA/out_dir
  python3 tests/test_memory_api.py glossary   # samo testovi ciji naziv sadrzi 'glossary'
  YTA_TEST_TMP=/neki/folder -> privremeni folder "memapi-*" pravi se tu;  YTA_TEST_KEEP=1 -> ne brisi ga

Bez mreze, bez ffmpeg-a i bez pravih modela: lazna arhiva (manifest + .srt), panel se pokrece kroz launcher koji
zameni LLM pozive funkcijom koja pada, PATH ima lazne `yt-dlp`/`claude`/`whisper-cli`. Testovi u procesu
(whisper_prompt_for, do_whisper, translate_cues/translate_step) koriste lazni whisper-cli, ffmpeg i core.translate.
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PORT = int(os.environ.get("YTA_TEST_PORT", "8823"))
TMP = Path(tempfile.mkdtemp(prefix="memapi-", dir=os.environ.get("YTA_TEST_TMP") or None)).resolve()
DATA, OUT, FAKEBIN = TMP / "data", TMP / "out", TMP / "bin"
IT: dict[str, Path] = {}
S: dict = {}  # stanje izmedju testova (id-jevi)

SRT_W = """1
00:00:00,000 --> 00:00:01,000
Ovo je general kenobi.

2
00:00:01,000 --> 00:00:02,000
Zdravo kenobi, kako si?

3
00:00:02,000 --> 00:00:03,000
Hvala svima.
"""
SRT_AUTO = """1
00:00:00,000 --> 00:00:01,000
hello there obi one

2
00:00:01,000 --> 00:00:02,000
you were the chosen one
"""
SRT_EN = """1
00:00:00,000 --> 00:00:01,000
Hello there, my old friend

2
00:00:01,000 --> 00:00:02,000
General Kenobi
"""
SRT_SR = SRT_EN.replace("Hello there, my old friend", "Zdravo tamo, moj stari prijatelju") \
    .replace("General Kenobi", "Generale Kenobi")
SR_KEY = "sr:lat:ollama:gemma4:12b-it-qat:manual:en"
SR_FILE = "subs/sr-lat.manual_en.srt"

# launcher: pravi LLM se nikad ne poziva (svaki poziv pada i ostavlja trag u logu)
LAUNCH = r'''
import os, sys
sys.path.insert(0, os.environ["YTA_ROOT"])
import yt_arhiva as core

def no_llm(*a, **k):
    print("PRAVI LLM POZVAN U TESTU", flush=True)
    raise RuntimeError("LLM nije dozvoljen u testu")

core.call_claude = core.call_ollama = core.call_gemini = core.call_cloudflare = no_llm
core.make_llm = lambda engine, model: no_llm
import runpy
runpy.run_path(os.path.join(os.environ["YTA_ROOT"], "panel.py"), run_name="__main__")
'''


# ================================================================ pomocne
def raw(method: str, path: str, body=None, headers: dict | None = None, ctype="application/json"):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
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


def corr_rows() -> list[dict]:
    p = DATA / "corrections.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def edit(tag: str, file: str, changes: dict[int, str]) -> dict:
    """Izmeni linije (indeks -> nov tekst) kroz /api/subs pa /api/subs/save."""
    f = IT[tag]
    cues = get("/api/subs?" + q(f=f, file=file))["cues"]
    for i, t in changes.items():
        cues[i]["text"] = t
    return post("/api/subs/save", {"folder": str(f), "file": file, "cues": cues})


def mem_dump(**kw) -> dict:
    return get("/api/memory" + (("?" + q(**kw)) if kw else ""))


# ================================================================ lazna arhiva
def mk_item(tag: str, vid: str, title: str, channel: str | None, subs: list[dict], files: dict[str, str]) -> Path:
    d = OUT / f"20260101 - {title} [{vid}]"
    (d / ".yta").mkdir(parents=True)
    (d / "subs").mkdir()
    man = {"version": 1, "url": f"https://www.youtube.com/watch?v={vid}", "id": vid, "title": title,
           "channel": channel, "duration": 3, "thumbnail": None, "upload_date": "20260101", "language": "en",
           "live_status": None, "source": None, "kind": None, "video": None, "audio": [], "subs": subs,
           "outputs": [], "playlists": [], "updated": time.time()}
    (d / ".yta" / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=1))
    for rel, txt in files.items():
        (d / rel).write_text(txt, encoding="utf-8")
    IT[tag] = d
    return d


def make_archive():
    for d in (OUT, DATA, FAKEBIN):
        d.mkdir(parents=True)
    # W: whisper transkript (srpski), kanal "Kanal W"
    mk_item("W", "WWWWWWWWWWW", "Whisper stavka", "Kanal W",
            [{"key": "whisper", "kind": "whisper", "lang": "sr", "file": "subs/whisper.srt", "count": 3,
              "name": "Whisper (Srpski)"}], {"subs/whisper.srt": SRT_W})
    # A: automatski YouTube titl (engleski), kanal "Kanal A"
    mk_item("A", "AAAAAAAAAAA", "Auto stavka", "Kanal A",
            [{"key": "auto:en", "kind": "auto", "lang": "en", "file": "subs/en.auto.srt", "count": 2,
              "name": "English (auto)"}], {"subs/en.auto.srt": SRT_AUTO})
    # T: rucni en CC + srpski prevod iz njega, kanal "Kanal T"
    mk_item("T", "TTTTTTTTTTT", "Prevod stavka", "Kanal T",
            [{"key": "manual:en", "kind": "manual", "lang": "en", "file": "subs/en.srt", "count": 2,
              "name": "English"},
             {"key": SR_KEY, "kind": "sr", "lang": "sr", "file": SR_FILE, "count": 2, "src": "manual:en",
              "name": "Srpski latinica — Brzo (iz: English)"}],
            {"subs/en.srt": SRT_EN, SR_FILE: SRT_SR})
    (DATA / "settings.json").write_text(json.dumps({
        "out_dir": str(OUT), "browser": "none", "cookies_file": "", "engine": "ollama",
        "model": "yta-test-nema-modela", "script": "lat"}, indent=1))
    for name in ("yt-dlp", "claude", "whisper-cli"):
        f = FAKEBIN / name
        f.write_text(f"#!/bin/sh\necho 'lazni {name} (test bez mreze)' >&2\nexit 1\n")
        f.chmod(0o755)
    (TMP / "launch.py").write_text(LAUNCH)


# ================================================================ testovi: HTTP
def t_pairs_stt_whisper():
    r = edit("W", "subs/whisper.srt", {0: "Ovo je general Kenobi.", 1: "Zdravo Kenobi, kako si?"})
    assert r["ok"] and r["changed"] == 2 and r["pairs_saved"] == 2, r
    assert isinstance(r["memory"]["rules_active"], int) and r["memory"]["rules_active"] >= 1, r
    rows = corr_rows()
    assert len(rows) == 2, rows
    for row, (b, a) in zip(rows, [("Ovo je general kenobi.", "Ovo je general Kenobi."),
                                  ("Zdravo kenobi, kako si?", "Zdravo Kenobi, kako si?")]):
        want = {"type": "stt", "lang": "sr", "src": None, "src_lang": None, "channel": "Kanal W",
                "engine": "whisper", "model": None, "video_id": "WWWWWWWWWWW", "file": "subs/whisper.srt",
                "before": b, "after": a}
        assert {k: row.get(k, "NEMA") for k in want} == want, row
        assert row["ts"] and Path(row["folder"]) == IT["W"].resolve(), row
    # isti sadrzaj -> odgovor kao i do sada (bez "memory")
    cues = get("/api/subs?" + q(f=IT["W"], file="subs/whisper.srt"))["cues"]
    r = post("/api/subs/save", {"folder": str(IT["W"]), "file": "subs/whisper.srt", "cues": cues})
    assert r == {"ok": True, "changed": 0, "pairs_saved": 0, "backup": None}, r


def t_pairs_stt_auto():
    r = edit("A", "subs/en.auto.srt", {0: "hello there Obi-Wan"})
    assert r["pairs_saved"] == 1 and "rules_active" in r["memory"], r
    row = corr_rows()[-1]
    want = {"type": "stt", "lang": "en", "src": None, "src_lang": None, "channel": "Kanal A", "engine": None,
            "model": None, "before": "hello there obi one", "after": "hello there Obi-Wan"}
    assert {k: row.get(k, "NEMA") for k in want} == want, row


def t_pairs_tr_channel_src_lang():
    r = edit("T", SR_FILE, {1: "Generale Kenobi!"})
    assert r["pairs_saved"] == 1, r
    row = corr_rows()[-1]
    want = {"type": "tr", "lang": "sr-lat", "src": "General Kenobi", "src_lang": "en", "channel": "Kanal T",
            "engine": "ollama", "model": "gemma4:12b-it-qat", "before": "Generale Kenobi", "after": "Generale Kenobi!"}
    assert {k: row.get(k, "NEMA") for k in want} == want, row
    # rucni titl (manual): izmena se cuva, ali bez para
    r = edit("T", "subs/en.srt", {0: "Hello there, my dear friend"})
    assert r["changed"] == 1 and r["pairs_saved"] == 0 and "memory" in r, r
    assert len(corr_rows()) == 4, corr_rows()
    st = get("/api/corrections/stats")
    assert st["total"] == 4 and st["by_lang"] == {"sr": 2, "en": 1, "sr-lat": 1}, st
    assert isinstance(st["rules_active"], int) and st["rules_active"] >= 1 and st["path"].endswith("corrections.jsonl"), st


def t_memory_dump():
    d = mem_dump()
    assert set(d) >= {"stats", "glossary", "rules", "recent", "settings"}, d.keys()
    st = d["stats"]
    assert st["total"] == 4 and st["by_type"] == {"stt": 3, "tr": 1} and st["disabled"] == 0, st
    assert st["glossary"] == 0 and st["rules_active"] >= 1, st
    ken = [r for r in d["rules"] if r["wrong"] == "kenobi" and r["right"] == "Kenobi"]
    assert len(ken) == 1, d["rules"]
    k = ken[0]
    assert (k["type"], k["lang"], k["count"], k["class"], k["state"], k["active"], k["conflict"]) == \
        ("stt", "sr", 2, "ime", "auto", True, False), k
    assert k["channels"] == ["Kanal W"] and len(k["id"]) == 12 and k["last_ts"], k
    assert d["rules"][0]["active"], d["rules"][:2]  # aktivna prvo
    S["rule_kenobi"] = k["id"]
    rec = d["recent"]
    assert len(rec) == 4 and rec[0]["type"] == "tr" and rec[0]["channel"] == "Kanal T", rec  # najnoviji prvi
    assert set(rec[0]) >= {"id", "ts", "type", "lang", "channel", "video_id", "file", "src", "before", "after",
                           "disabled"}, rec[0]
    assert rec[-1]["before"] == "Ovo je general kenobi." and not any(x["disabled"] for x in rec), rec
    S["corr_first"] = rec[-1]["id"]
    assert d["settings"] == {"mem_tr": True, "mem_stt": True}, d["settings"]
    assert len(mem_dump(recent=1)["recent"]) == 1
    assert len(mem_dump(recent=0)["recent"]) == 0
    st, j = req("GET", "/api/memory?recent=abc")
    assert st == 400 and "recent" in j["error"], (st, j)


def t_glossary_crud():
    ok = post("/api/memory/glossary", {"op": "add", "entry": {"type": "tr", "lang": "sr", "src": "Death Star",
                                                              "tgt": "Zvezda smrti", "note": "Star Wars"}})
    assert ok["ok"] and len(ok["glossary"]) == 1, ok
    e = ok["glossary"][0]
    assert len(e["id"]) == 10 and e["created"] and (e["type"], e["lang"], e["src"], e["tgt"], e["scope"]) == \
        ("tr", "sr", "Death Star", "Zvezda smrti", "global"), e
    S["gl_tr"] = e["id"]
    r = post("/api/memory/glossary", {"op": "add", "entry": {"type": "stt", "lang": "sr", "tgt": "Anakin",
                                                             "wrong": "ana kin", "scope": "channel",
                                                             "channel": "Kanal W"}})
    assert len(r["glossary"]) == 2, r
    gl_stt = next(x for x in r["glossary"] if x["type"] == "stt")
    assert gl_stt["src"] == "" and gl_stt["channel"] == "Kanal W", gl_stt
    # glosar stt sa "wrong" pravi aktivno pravilo klase glosar
    g = [x for x in mem_dump()["rules"] if x["wrong"] == "ana kin"]
    assert len(g) == 1 and g[0]["class"] == "glosar" and g[0]["active"], g
    # validacija -> 400 (poruka na srpskom), glosar se ne menja
    bad = [{"op": "add", "entry": {"type": "tr", "lang": "sr", "src": "Death Star", "tgt": "Zvezda smrti",
                                   "note": "duplikat"}},
           {"op": "add", "entry": {"type": "tr", "lang": "sr", "src": "X"}},
           {"op": "add", "entry": {"type": "tr", "lang": "srpski", "src": "X", "tgt": "Y"}},
           {"op": "add", "entry": {"type": "stt", "lang": "sr", "tgt": "Y", "scope": "channel"}},
           {"op": "add", "entry": {"type": "xx", "lang": "sr", "src": "X", "tgt": "Y"}},
           {"op": "add", "entry": {"type": "tr", "lang": "sr", "src": "X" * 121, "tgt": "Y"}},
           {"op": "add", "entry": "nije objekat"},
           {"op": "obrisi", "entry": {"id": S["gl_tr"]}},
           {"entry": {"type": "tr", "lang": "sr", "src": "X", "tgt": "Y"}}]
    for b in bad:
        st, j = req("POST", "/api/memory/glossary", b)
        assert st == 400 and isinstance(j.get("error"), str) and j["error"], (b, st, j)
    st, j = req("POST", "/api/memory/glossary", bad[0])
    assert "već postoji" in j["error"], j
    assert len(mem_dump()["glossary"]) == 2
    # update
    r = post("/api/memory/glossary", {"op": "update", "entry": {"id": S["gl_tr"], "tgt": "Zvezda Smrti"}})
    e = next(x for x in r["glossary"] if x["id"] == S["gl_tr"])
    assert e["tgt"] == "Zvezda Smrti" and e["src"] == "Death Star" and e["note"] == "Star Wars", e
    st, j = req("POST", "/api/memory/glossary", {"op": "update", "entry": {"id": S["gl_tr"], "tgt": ""}})
    assert st == 400, (st, j)
    # nepostojeci -> 404 (poruka bez navodnika KeyError-a)
    for op in ("update", "delete"):
        st, j = req("POST", "/api/memory/glossary", {"op": op, "entry": {"id": "0000000000", "tgt": "x"}})
        assert st == 404 and j == {"error": "stavka glosara ne postoji"}, (op, st, j)
    # delete
    r = post("/api/memory/glossary", {"op": "delete", "entry": {"id": gl_stt["id"]}})
    assert [x["id"] for x in r["glossary"]] == [S["gl_tr"]], r
    assert not [x for x in mem_dump()["rules"] if x["wrong"] == "ana kin"]
    post("/api/memory/glossary", {"op": "delete", "entry": {"id": gl_stt["id"]}}, 404)
    assert mem_dump()["stats"]["glossary"] == 1
    assert json.loads((DATA / "memory.json").read_text())["glossary"][0]["id"] == S["gl_tr"]


def t_rule_state():
    rid = S["rule_kenobi"]
    before = get("/api/corrections/stats")["rules_active"]
    r = post("/api/memory/rule", {"id": rid, "state": "off"})
    assert r["ok"] and r["rule"]["id"] == rid and r["rule"]["state"] == "off" and not r["rule"]["active"], r
    assert get("/api/corrections/stats")["rules_active"] == before - 1
    k = next(x for x in mem_dump()["rules"] if x["id"] == rid)
    assert k["state"] == "off" and not k["active"], k
    assert json.loads((DATA / "memory.json").read_text())["rules"] == {rid: "off"}
    r = post("/api/memory/rule", {"id": rid, "state": "on"})
    assert r["rule"]["state"] == "on" and r["rule"]["active"], r
    r = post("/api/memory/rule", {"id": rid, "state": "auto"})
    assert r["rule"]["state"] == "auto" and r["rule"]["active"], r
    assert json.loads((DATA / "memory.json").read_text())["rules"] == {}
    st, j = req("POST", "/api/memory/rule", {"id": rid, "state": "uvek"})
    assert st == 400 and j["error"], (st, j)
    st, j = req("POST", "/api/memory/rule", {"id": "000000000000", "state": "on"})
    assert st == 404 and j == {"error": "pravilo ne postoji"}, (st, j)
    st, j = req("POST", "/api/memory/rule", {"state": "on"})
    assert st == 400 and "id" in j["error"], (st, j)


def t_correction_disabled():
    cid = S["corr_first"]
    before = get("/api/corrections/stats")["rules_active"]
    r = post("/api/memory/correction", {"id": cid, "disabled": True})
    assert r["ok"] is True and r["correction"]["id"] == cid and r["correction"]["disabled"] is True, r
    d = mem_dump()
    assert d["stats"]["disabled"] == 1 and next(x for x in d["recent"] if x["id"] == cid)["disabled"], d["stats"]
    # jedna od dve ispravke "kenobi -> Kenobi" iskljucena -> pravilo ostaje sa count 1 i vise nije aktivno
    k = next(x for x in d["rules"] if x["wrong"] == "kenobi" and x["right"] == "Kenobi")
    assert k["count"] == 1 and not k["active"], k
    assert get("/api/corrections/stats")["rules_active"] == before - 1
    for bad in ({"id": cid, "disabled": "da"}, {"id": cid}, {"disabled": True}):
        st, j = req("POST", "/api/memory/correction", bad)
        assert st == 400 and j["error"], (bad, st, j)
    st, j = req("POST", "/api/memory/correction", {"id": "000000000000", "disabled": True})
    assert st == 404 and j == {"error": "ispravka ne postoji"}, (st, j)
    post("/api/memory/correction", {"id": cid, "disabled": False})
    d = mem_dump()
    assert d["stats"]["disabled"] == 0 and get("/api/corrections/stats")["rules_active"] == before, d["stats"]


def t_protection_memory():
    snap = (DATA / "memory.json").read_text()
    body = {"op": "add", "entry": {"type": "tr", "lang": "sr", "src": "Evil", "tgt": "Zlo"}}
    for path in ("/api/memory/glossary", "/api/memory/rule", "/api/memory/correction"):
        assert req("POST", path, body, {"Origin": "http://evil.test"})[0] == 403, path
        assert req("POST", path, body, {"Host": f"evil.test:{PORT}"})[0] == 403, path
        assert raw("POST", path, body, ctype="text/plain")[0] == 415, path
        assert raw("POST", path, body, ctype="application/x-www-form-urlencoded")[0] == 415, path
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    c.request("POST", "/api/memory/glossary", body=b"{nije json", headers={
        "Host": f"127.0.0.1:{PORT}", "Content-Type": "application/json"})
    r = c.getresponse()
    assert r.status == 400, r.status
    c.close()
    assert req("POST", "/api/memory/glossary", ["niz"])[0] == 400
    assert req("GET", "/api/memory", headers={"Host": f"evil.test:{PORT}"})[0] == 403
    assert req("GET", "/api/memory", headers={"Origin": "http://evil.test"})[0] == 403
    assert req("POST", "/api/memory/nepostoji", {})[0] == 404
    # dozvoljen Origin panela prolazi
    st, j = req("POST", "/api/memory/rule", {"id": S["rule_kenobi"], "state": "auto"},
                {"Origin": f"http://127.0.0.1:{PORT}"})
    assert st == 200 and j["ok"], (st, j)
    assert (DATA / "memory.json").read_text() == snap


def t_settings_mem():
    s = get("/api/settings")
    assert s["mem_tr"] is True and s["mem_stt"] is True, s
    s = post("/api/settings", {"mem_tr": "false", "mem_stt": False})
    assert s["mem_tr"] is False and s["mem_stt"] is False, s
    assert mem_dump()["settings"] == {"mem_tr": False, "mem_stt": False}
    assert json.loads((DATA / "settings.json").read_text())["mem_tr"] is False
    s = post("/api/settings", {"mem_tr": True, "mem_stt": "true"})
    assert s["mem_tr"] is True and s["mem_stt"] is True, s
    assert mem_dump()["settings"] == {"mem_tr": True, "mem_stt": True}


def t_static_memory_js():
    st, h, b = raw("GET", "/static/memory.js")
    if not (ROOT / "static" / "memory.js").exists():
        assert st == 404, st
        print("      (static/memory.js jos ne postoji — preskoceno)")
        return
    assert st == 200 and h["content-type"].startswith("text/javascript") and b, (st, h)
    assert raw("GET", "/static/../panel.py")[0] == 404
    html = raw("GET", "/")[2].decode()
    if "memory.js" not in html:
        print("      (index.html jos ne ucitava memory.js)")


def t_no_llm_called():
    assert "PRAVI LLM" not in (TMP / "panel.log").read_text()


# ================================================================ testovi: u procesu (bez servera)
def _panel():
    """panel uvezen u proces sa sopstvenim YTA_DATA (ne dira HTTP instancu ni pravi data/)."""
    if "panel" in S:
        return S["panel"]
    d = TMP / "inproc"
    d.mkdir(exist_ok=True)
    (d / "settings.json").write_text(json.dumps({"out_dir": str(TMP / "inproc-out"), "browser": "none"}))
    os.environ["YTA_DATA"] = str(d)
    panel = importlib.import_module("panel")
    assert str(panel.DATA) == str(d), panel.DATA
    S["panel"], S["inproc"] = panel, d
    return panel


def _row(before, after, typ, lang, channel=None, src=None, n=[0]):  # noqa: B006
    n[0] += 1
    return {"ts": f"2026-09-{n[0] % 28 + 1:02d}T10:00:{n[0] % 60:02d}+0200", "video_id": "vid", "folder": "/x",
            "file": "subs/x.srt", "lang": lang, "engine": None, "model": None, "src": src, "before": before,
            "after": after, "type": typ, "channel": channel, "src_lang": "en" if typ == "tr" else None}


def _setup_inproc_memory():
    panel = _panel()
    d = S["inproc"]
    if (d / "corrections.jsonl").exists():
        return panel, d
    rows = [_row("Ovo je general kenobi.", "Ovo je general Kenobi.", "stt", "sr", "K"),
            _row("Zdravo kenobi, kako si?", "Zdravo Kenobi, kako si?", "stt", "sr", "K"),
            _row("hello there obi one", "hello there Obi-Wan", "stt", "en", "K"),
            _row("so obi one said", "so Obi-Wan said", "stt", "en", "K"),
            _row("Zdravo tamo moj stari prijatelju", "Zdravo tamo, moj stari druže", "tr", "sr-lat", "K",
                 src="Hello there my old friend")]
    (d / "corrections.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    ents = [("stt", "sr", "", "Mandalorijanac", None), ("stt", "sr", "", "Grogu", "K"),
            ("stt", "fr", "", "Jean-Luc Picard", None), ("tr", "sr", "Death Star", "Zvezda smrti", None)]
    for i in range(40):  # mnogo termina: provera limita 400 (latinica) / 250 (grcki) / 600 ukupno
        ents += [("stt", "sr", "", f"Terminbroj{i:02d}", None), ("stt", "el", "", f"Όροςαριθμός{i:02d}", None)]
    gl = [{"id": f"{n:010x}", "type": t, "lang": lg, "src": src, "tgt": tgt, "wrong": "",
           "scope": "channel" if ch else "global", "channel": ch, "note": "", "created": "2026-09-01T10:00:00+0200"}
          for n, (t, lg, src, tgt, ch) in enumerate(ents)]
    (d / "memory.json").write_text(json.dumps({"glossary": gl, "rules": {}, "disabled": []}, ensure_ascii=False))
    assert panel.memlib.Memory.load(d).stats()["glossary"] == len(ents)
    return panel, d


class _Settings:
    """Privremene vrednosti panel.SETTINGS (vraca se posle bloka)."""

    def __init__(self, panel, **kw):
        self.p, self.kw, self.old = panel, kw, {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.old[k] = self.p.SETTINGS.get(k, KeyError)
            self.p.SETTINGS[k] = v

    def __exit__(self, *a):
        for k, v in self.old.items():
            if v is KeyError:
                self.p.SETTINGS.pop(k, None)
            else:
                self.p.SETTINGS[k] = v


def t_whisper_prompt_for_unit():
    panel, _d = _setup_inproc_memory()
    wp = panel.whisper_prompt_for
    base = panel.WHISPER_PROMPTS["sr"].format(names="Star Wars Kenobi").strip()
    with _Settings(panel, whisper_prompt=True, mem_stt=True):
        p = wp("sr", "Star Wars: Kenobi — the end", "K")
        assert p.startswith(base + " "), p
        terms = p[len(base) + 1:].split(", ")
        assert terms[:3] == ["Grogu", "Terminbroj39", "Terminbroj38"], terms[:4]  # kanal ispred globalnog, novije
        assert len(p) <= 600 and len(p) - len(base) - 1 <= 400 < len(p) - len(base) - 1 + 16, (len(p), p)
        p2 = wp("sr", "Star Wars: Kenobi — the end", "Drugi kanal")
        assert "Grogu" not in p2 and p2[len(base) + 1:].startswith("Terminbroj39, "), p2  # stavka za kanal K samo za K
        # dug naslov: ukupno <= 600
        long_title = " ".join(f"Imenko{i:03d}" for i in range(60))
        p3 = wp("sr", long_title, "K")
        assert len(p3) <= 600 and "Grogu" in p3, (len(p3), p3)
        # grcki: termini <= 250
        pe = wp("el", "", None)
        be = panel.WHISPER_PROMPTS["el"].format(names="").strip()
        assert pe.startswith(be + " ") and 200 < len(pe) - len(be) - 1 <= 250 and "Όροςαριθμός39" in pe, pe
        # jezik bez ugradjenog prompta, ali sa terminima -> samo termini
        assert wp("fr", "", None) == "Jean-Luc Picard", wp("fr", "", None)
        assert wp("de", "Ein Titel", None) == panel.WHISPER_PROMPTS["de"].format(names="Ein Titel").strip()
        assert wp(None, "x", None) is None and wp("und", "", None) is None
    with _Settings(panel, whisper_prompt=False, mem_stt=True):
        p = wp("sr", "Star Wars Kenobi", "K")
        assert p.startswith("Grogu, Terminbroj39, ") and len(p) <= 400, p
        assert wp("de", "Titel", None) is None  # nema prompta ni termina
    with _Settings(panel, whisper_prompt=True, mem_stt=False):
        assert wp("sr", "Star Wars Kenobi", "K") == panel.WHISPER_PROMPTS["sr"].format(names="Star Wars Kenobi").strip()
        assert wp("fr", "", None) is None
    with _Settings(panel, whisper_prompt=False, mem_stt=False):
        assert wp("sr", "Star Wars Kenobi", "K") is None
    # greska u memoriji -> prompt bez termina (ne pada)
    orig = panel.memlib.Memory.load
    panel.memlib.Memory.load = classmethod(lambda cls, d: (_ for _ in ()).throw(OSError("disk nije dostupan")))
    try:
        with _Settings(panel, whisper_prompt=True, mem_stt=True):
            assert wp("sr", "", "K") == panel.WHISPER_PROMPTS["sr"].format(names="").strip()
    finally:
        panel.memlib.Memory.load = orig


def _job(panel):
    return panel.Job("", {"_title": "test"}, kind="transcode")


def _log(job) -> str:
    return "\n".join(job.log)


def t_mem_apply_unit():
    panel, _d = _setup_inproc_memory()
    job = _job(panel)
    with _Settings(panel, mem_stt=True):
        v = panel.mem_view(job, "stt", "sr", "K")
        assert v is not None and v.lang == "sr" and not v.empty, v
        cyr = panel.core.to_cyr
        cues = [{"text": "Ovo je general kenobi."}, {"text": cyr("Ovo je general kenobi.", set())},
                {"text": "Bez promene."}]
        n = panel.mem_apply_cues(job, v, cues)
        assert n == 2 and [c["text"] for c in cues] == ["Ovo je general Kenobi.", cyr("Ovo je general Kenobi.", set()),
                                                         "Bez promene."], cues
        assert "Кеноби" in cues[1]["text"], cues[1]
        assert panel.mem_view(job, "stt", "und") is None and panel.mem_view(job, "stt", None) is None
        assert panel.mem_view(job, "stt", "ja") is None  # prazan pogled
    with _Settings(panel, mem_stt=False):
        assert panel.mem_view(job, "stt", "sr", "K") is None
    # SafeMem: greska u metodi -> neutralan rezultat + jedno upozorenje
    class Boom:
        lang, empty = "sr", False

        def apply_rules(self, t):
            raise RuntimeError("pukla pravila")

        def examples(self, texts, k=6):
            raise RuntimeError("pukli primeri")
    sm = panel.SafeMem(Boom(), job)
    assert sm.apply_rules("tekst") == ("tekst", 0) and sm.examples(["a"]) == [] and sm.lang == "sr"
    assert _log(job).count("⚠ memorija ispravki nije primenjena") == 1, _log(job)
    cues = [{"text": "x kenobi"}]
    assert panel.mem_apply_cues(job, sm, cues) == 0 and cues[0]["text"] == "x kenobi"


def t_mem_apply_mixed_script_unit():
    """Srpski: red je cirilicni tek kad vecina slova jeste cirilica; latinicne reci reda i imena iz celog
    transkripta ostaju latinicom; ime/brend iz pravila sa mesanim slovima (iPhone) ostaje latinicom."""
    panel, _d = _setup_inproc_memory()
    job = _job(panel)
    with _Settings(panel, mem_stt=True):
        v = panel.mem_view(job, "stt", "sr", "K")
        cues = [{"text": "Ovo je rekao kenobi, а ne ja."},          # latinica + zalutalo cirilicno "а"
                {"text": "Гледао сам online, рекао кеноби"},       # cirilica + latinicna rec
                {"text": "Видео сам Kenobi филм јуче"},            # ime latinicom u cirilicnom redu
                {"text": "па је рекао кеноби"}]
        n = panel.mem_apply_cues(job, v, cues)
        assert n == 3, (n, cues)
        assert [c["text"] for c in cues] == ["Ovo je rekao Kenobi, а ne ja.", "Гледао сам online, рекао Kenobi",
                                             "Видео сам Kenobi филм јуче", "па је рекао Kenobi"], cues

    class V:
        lang = "sr"

        def apply_rules(self, t):
            return (t.replace("ajfon", "iPhone"), 1) if "ajfon" in t else (t, 0)
    assert panel.mem_apply(V(), "Купио сам ајфон јуче") == ("Купио сам iPhone јуче", 1)
    assert panel.mem_apply(V(), "Купио сам телефон") == ("Купио сам телефон", 0)
    # corrections_stats: red sa losim UTF-8 bajtom ili ne-objekat se preskace (ne obara rutu)
    p = TMP / "corr-bad.jsonl"
    p.write_bytes(b'{"lang": "sr", "before": "a", "after": "b"}\n{"lang": "sr", "before": "k\xffla"}\n[1, 2]\n')
    old = panel.CORR_P
    panel.CORR_P = p
    try:
        st = panel.corrections_stats()
    finally:
        panel.CORR_P = old
    assert st["total"] == 1 and st["by_lang"] == {"sr": 1}, st


def t_do_whisper_unit():
    """do_whisper -> whisper_media sa laznim ffmpeg-om i whisper-cli: kanal iz manifesta, termini u promptu,
    naucene zamene u transkriptu, izvestaj u logu. Bez mreze (model postoji u privremenom WHISPER_DIR)."""
    panel, _d = _setup_inproc_memory()
    fb = TMP / "wbin"
    fb.mkdir(exist_ok=True)
    args_p = TMP / "whisper_args.json"
    (fb / "whisper-cli").write_text(f"""#!{sys.executable}
import json, sys
a = sys.argv[1:]
json.dump(a, open({str(args_p)!r}, "w"))
of = a[a.index("-of") + 1]
open(of + ".srt", "w", encoding="utf-8").write("1\\n00:00:00,000 --> 00:00:01,000\\nOvo je general kenobi.\\n\\n"
                                                "2\\n00:00:01,000 --> 00:00:02,000\\nA ovo je grogu.\\n\\n")
print("whisper_print_progress_callback: progress =  50%")
""")
    (fb / "ffmpeg").write_text(f"#!{sys.executable}\nimport sys\nopen(sys.argv[-1], 'wb').write(b'RIFF')\n")
    for f in fb.iterdir():
        f.chmod(0o755)
    wdir = TMP / "whisper-models"
    wdir.mkdir(exist_ok=True)
    folder = TMP / "inproc-out" / "20260101 - W [WWWWWWWWWWW]"
    (folder / ".yta").mkdir(parents=True, exist_ok=True)
    (folder / "subs").mkdir(exist_ok=True)
    (folder / ".yta" / "manifest.json").write_text(json.dumps({"id": "WWWWWWWWWWW", "title": "Kenobi Show",
                                                               "channel": "K", "language": "sr", "subs": []}))
    src = folder / "W [WWWWWWWWWWW].source.mkv"
    src.write_bytes(b"x")
    old_path, old_wdir = os.environ.get("PATH", ""), panel.WHISPER_DIR
    os.environ["PATH"] = f"{fb}:{old_path}"
    panel.WHISPER_DIR = wdir
    try:
        with _Settings(panel, whisper_vad=False, whisper_quality_langs="", whisper_prompt=True, mem_stt=True):
            (wdir / f"ggml-{panel.SETTINGS['whisper_model']}.bin").write_bytes(b"model")
            assert shutil.which("whisper-cli") == str(fb / "whisper-cli")  # nikad brew/curl u testu
            job = _job(panel)
            e = panel.do_whisper(job, folder, src, str(fb / "ffmpeg"))
        a = json.loads(args_p.read_text())
        prompt = a[a.index("--prompt") + 1]
        assert prompt.startswith("Transkript na srpskom") and "Grogu" in prompt and "Kenobi" in prompt, prompt
        assert e["kind"] == "whisper" and e["lang"] == "sr" and e["count"] == 2, e
        txt = (folder / "subs" / "whisper.srt").read_text()
        assert "Ovo je general Kenobi." in txt and "A ovo je grogu." in txt, txt  # samo naucena pravila
        log = _log(job)
        assert "memorija: 1 zamena u transkriptu," in log and "termina u promptu" in log, log
        # memorija iskljucena: bez termina i bez zamena
        with _Settings(panel, whisper_vad=False, whisper_quality_langs="", whisper_prompt=True, mem_stt=False):
            job = _job(panel)
            panel.do_whisper(job, folder, src, str(fb / "ffmpeg"))
        a = json.loads(args_p.read_text())
        assert "Grogu" not in a[a.index("--prompt") + 1] and "memorija" not in _log(job), a
        assert "general kenobi" in (folder / "subs" / "whisper.srt").read_text()
    finally:
        os.environ["PATH"] = old_path
        panel.WHISPER_DIR = old_wdir


def _fake_translate(panel, seen: list):
    def fake(cues, ctx, args, cache, *_target, progress=None):
        seen.append({"mem": getattr(args, "mem", None), "has_mem": hasattr(args, "mem"),
                     "texts": [c["text"] for c in cues], "keep_lines": getattr(args, "keep_lines", None)})
        if getattr(args, "mem", None) is not None:
            args.mem.examples([c["text"] for c in cues])  # SafeMem prosledjuje pravom pogledu
            args.mem_report.update(examples=2, glossary=1, hints=0, exact=1, rules=3)
        for c in cues:
            c["sr"] = c["tr"] = c["text"]
        return cues
    return fake


def t_translate_cues_unit():
    panel, _d = _setup_inproc_memory()
    seen: list = []
    orig = panel.core.translate
    panel.core.translate = _fake_translate(panel, seen)
    try:
        cues = [{"start": 0, "end": 1000, "text": "Hello there my old friend"}]
        job = _job(panel)
        with _Settings(panel, mem_tr=True):
            panel.translate_cues(job, cues, "lat", "claude", "sonnet", False, TMP / "tc-cache", "ctx", "en",
                                 channel="K")
        m = seen[-1]["mem"]
        assert m is not None and (m.kind, m.lang, m.channel) == ("tr", "sr", "K") and not m.empty, m
        assert seen[-1]["keep_lines"] is True, seen[-1]  # promena par linija izvora ne baca ceo kes
        assert "memorija (prevod): primeri u 2 bloka · glosar 1 · prevodna memorija 1 · zamena 3" in _log(job), \
            _log(job)
        # mem_tr iskljucen -> args bez mem (ponasanje kao pre memorije)
        job = _job(panel)
        with _Settings(panel, mem_tr=False):
            panel.translate_cues(job, cues, "lat", "claude", "sonnet", False, TMP / "tc-cache", "ctx", "en",
                                 channel="K")
        assert not seen[-1]["has_mem"] and "memorija" not in _log(job), (seen[-1], _log(job))
        # prazan pogled (grcki bez ispravki/glosara) -> bez mem
        job = _job(panel)
        orig_to = panel.core.translate_to
        panel.core.translate_to = _fake_translate(panel, seen)
        try:
            with _Settings(panel, mem_tr=True):
                panel.translate_cues(job, [{"start": 0, "end": 1, "text": "Hi"}], "el", "claude", "sonnet", False,
                                     TMP / "tc-cache", "ctx", "en", channel="K")
        finally:
            panel.core.translate_to = orig_to
        assert not seen[-1]["has_mem"], seen[-1]
        # greska u memoriji -> upozorenje, prevod ide dalje bez memorije
        job = _job(panel)
        orig_load = panel.memlib.Memory.load
        panel.memlib.Memory.load = classmethod(lambda cls, d: (_ for _ in ()).throw(ValueError("pokvareno")))
        try:
            with _Settings(panel, mem_tr=True):
                panel.translate_cues(job, cues, "lat", "claude", "sonnet", False, TMP / "tc-cache", "ctx", "en",
                                     channel="K")
        finally:
            panel.memlib.Memory.load = orig_load
        assert not seen[-1]["has_mem"] and "⚠ memorija ispravki nije primenjena" in _log(job), _log(job)
        assert cues[0]["sr"] == "Hello there my old friend"
    finally:
        panel.core.translate = orig


def t_translate_step_auto_unit():
    panel, _d = _setup_inproc_memory()
    folder = TMP / "inproc-out" / "20260101 - A [AAAAAAAAAAA]"
    (folder / ".yta").mkdir(parents=True, exist_ok=True)
    (folder / "subs").mkdir(exist_ok=True)
    entry = {"key": "auto:en", "kind": "auto", "lang": "en", "file": "subs/en.auto.srt", "count": 1,
             "name": "English (auto)"}
    (folder / ".yta" / "manifest.json").write_text(json.dumps({"id": "AAAAAAAAAAA", "title": "A", "channel": "K",
                                                               "subs": [entry]}))
    (folder / "subs" / "en.auto.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nand obi one said hi\n")
    proc = {"target": "lat", "engine": "claude", "model": "sonnet"}
    seen: list = []
    orig = panel.core.translate
    panel.core.translate = _fake_translate(panel, seen)
    try:
        job = _job(panel)
        cues = [{"start": 0, "end": 1000, "text": "and obi one said hi"}]
        with _Settings(panel, mem_stt=True, mem_tr=True):
            assert panel.translate_step(job, folder, proc, cues, entry)
        assert seen[-1]["texts"] == ["and Obi-Wan said hi"], seen[-1]
        assert seen[-1]["mem"] is not None and seen[-1]["mem"].channel == "K", seen[-1]
        assert "memorija: 1 zamena u automatskom titlu pre prevoda" in _log(job), _log(job)
        assert "obi one" in (folder / "subs" / "en.auto.srt").read_text()  # fajl na disku se ne menja
        # mem_stt iskljucen -> auto titl ide u prevod kakav jeste
        cues = [{"start": 0, "end": 1000, "text": "and obi one said hi"}]
        with _Settings(panel, mem_stt=False, mem_tr=True):
            panel.translate_step(_job(panel), folder, proc, cues, entry)
        assert seen[-1]["texts"] == ["and obi one said hi"], seen[-1]
        # rucni titl: stt pravila se ne primenjuju
        man = dict(entry, key="manual:en", kind="manual")
        cues = [{"start": 0, "end": 1000, "text": "and obi one said hi"}]
        with _Settings(panel, mem_stt=True, mem_tr=True):
            panel.translate_step(_job(panel), folder, proc, cues, man)
        assert seen[-1]["texts"] == ["and obi one said hi"], seen[-1]
    finally:
        panel.core.translate = orig


HTTP_TESTS = [t_pairs_stt_whisper, t_pairs_stt_auto, t_pairs_tr_channel_src_lang, t_memory_dump, t_glossary_crud,
              t_rule_state, t_correction_disabled, t_protection_memory, t_settings_mem, t_static_memory_js,
              t_no_llm_called]
UNIT_TESTS = [t_whisper_prompt_for_unit, t_mem_apply_unit, t_mem_apply_mixed_script_unit, t_do_whisper_unit,
              t_translate_cues_unit, t_translate_step_auto_unit]


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
    if PORT == 8790:
        print(f"port {PORT} je podrazumevani port panela — izaberi drugi YTA_TEST_PORT")
        return 2
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
