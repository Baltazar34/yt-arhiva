#!/usr/bin/env python3
"""Test memory.py (docs/memory-contract.md §2–§4, §9): pravila, konflikt, klase, primena sa velikim slovom, TM,
primeri, whisper termini, iskljucene ispravke, glosar CRUD + validacija, cirilica -> latinica, kes i invalidacija,
pokvaren jsonl / memory.json, dump, performanse (20.000 redova).

  python3 tests/test_memory.py            # svi testovi (nekoliko sekundi), izlaz != 0 ako nesto padne
  python3 tests/test_memory.py glos tm    # samo testovi ciji naziv sadrzi 'glos' ili 'tm'
  YTA_TEST_TMP=/neki/folder -> privremeni folder "mem-*" pravi se tu;  YTA_TEST_KEEP=1 -> ne brisi ga

Bez mreze i bez modela: samo privremeni DATA folderi sa corrections.jsonl / memory.json.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import memory as M  # noqa: E402
from memory import Memory  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="mem-", dir=os.environ.get("YTA_TEST_TMP") or None)).resolve()
_N = [0]


def eq(got, want, what: str):
    if got != want:
        raise AssertionError(f"{what}: očekivano {want!r}, dobijeno {got!r}")


def ok(cond, what: str):
    if not cond:
        raise AssertionError(what)


def raises(exc, fn, what: str, contains: str | None = None):
    try:
        fn()
    except exc as e:
        if contains and contains not in str(e):
            raise AssertionError(f"{what}: poruka {str(e)!r} ne sadrži {contains!r}") from None
        return e
    except Exception as e:  # noqa: BLE001
        raise AssertionError(f"{what}: očekivano {exc.__name__}, dobijeno {type(e).__name__}: {e}") from None
    raise AssertionError(f"{what}: očekivano {exc.__name__}, nije bačeno")


def ddir() -> Path:
    _N[0] += 1
    d = TMP / f"data{_N[0]}"
    d.mkdir()
    return d


def row(before, after, lang="sr-lat", src=None, typ=None, channel=None, ts=None, file="subs/sr-lat.x.srt",
        **extra) -> dict:
    _N[0] += 1
    r = {"ts": ts or f"2026-09-{1 + _N[0] % 28:02d}T10:{_N[0] % 60:02d}:00+0200", "video_id": "video1",
         "folder": "/x/y", "file": file, "lang": lang, "engine": "ollama", "model": "m", "src": src,
         "before": before, "after": after}
    if typ:
        r["type"] = typ
    if typ or channel:
        r["channel"] = channel
    r.update(extra)
    return r


def write_rows(d: Path, rows: list[dict], mode="w"):
    with (d / "corrections.jsonl").open(mode, encoding="utf-8") as fh:
        for r in rows:
            fh.write((r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)) + "\n")


def rule_of(mem: Memory, wrong: str, right: str | None = None, typ: str | None = None) -> dict | None:
    for r in mem.dump(0)["rules"]:
        if r["wrong"] == wrong and (right is None or r["right"] == right) and (typ is None or r["type"] == typ):
            return r
    return None


# ================================================================ pravila
def t_rules_basic():
    d = ddir()
    write_rows(d, [row("Moj kućni kompjuter je spor.", "Moj stoni računar je spor.", src="My home computer is slow."),
                   row("Novi kućni kompjuter stiže sutra", "Novi stoni računar stiže sutra",
                       src="The new home computer arrives"),
                   row("Zdravo tamo", "Zdravo svete", src="Hello there")])
    m = Memory.load(d)
    r = rule_of(m, "kućni kompjuter", "stoni računar")
    ok(r, "pravilo kućni kompjuter -> stoni računar postoji")
    eq((r["count"], r["class"], r["active"], r["conflict"], r["state"], r["type"], r["lang"], r["scope"]),
       (2, "termin", True, False, "auto", "tr", "sr", "all"), "pravilo kućni kompjuter")
    eq(r["id"], hashlib.sha1("tr|sr|kućni kompjuter|stoni računar".encode()).hexdigest()[:12], "rule_id")
    t = rule_of(m, "tamo", "svete")
    eq((t["count"], t["active"]), (1, False), "jedna pojava nije aktivna")
    eq(m.stats()["rules_active"], 1, "rules_active")
    v = m.view("tr", "sr-lat")
    eq(v.apply_rules("Taj kućni kompjuter radi."), ("Taj stoni računar radi.", 1), "apply_rules")
    eq(v.apply_rules("Zdravo tamo"), ("Zdravo tamo", 0), "neaktivno pravilo se ne primenjuje")
    ok(not v.empty, "pogled nije prazan")
    ok(m.view("tr", "el").empty, "grčki pogled je prazan")
    ok(m.view("stt", "sr").empty, "stt pogled je prazan")
    # "on" aktivira i posle jedne pojave; "auto" vraca
    rr = m.set_rule_state(t["id"], "on")
    eq((rr["state"], rr["active"]), ("on", True), "stanje on")
    eq(m.view("tr", "sr").apply_rules("Zdravo tamo"), ("Zdravo svete", 1), "on pravilo se primenjuje")
    eq(m.stats()["rules_active"], 2, "rules_active posle on")
    m.set_rule_state(r["id"], "off")
    eq(m.view("tr", "sr").apply_rules("kućni kompjuter"), ("kućni kompjuter", 0), "off pravilo se ne primenjuje")
    rr = m.set_rule_state(t["id"], "auto")
    eq((rr["state"], rr["active"]), ("auto", False), "auto posle on")
    mj = json.loads((d / "memory.json").read_text())
    eq(mj["rules"], {r["id"]: "off"}, "memory.json rules (auto = obrisan ključ)")
    raises(KeyError, lambda: m.set_rule_state("nema", "on"), "nepostojeće pravilo")
    raises(ValueError, lambda: m.set_rule_state(r["id"], "uvek"), "nevalidno stanje", "auto, on ili off")


def t_name_in_phrase():
    """Ime ispravljeno uz susednu rec ("kapitane Rejes" -> "kapetane Reyes") i na pocetku recenice
    ("Kapitane Rejes") uci se kao "Rejes" -> "Reyes" (2 pojave -> aktivno); susedna mala rec ostaje gramatika."""
    d = ddir()
    write_rows(d, [row("Hvala, kapitane Rejes, i srećno.", "Hvala vam, kapetane Reyes, i srećno.",
                       src="Thank you, Captain Reyes, and good luck."),
                   row("Kapitane Rejes, šta biste rekli nekome?", "Kapetane Reyes, šta biste rekli nekome?",
                       src="Captain Reyes, what would you tell someone?")])
    m = Memory.load(d)
    r = rule_of(m, "Rejes", "Reyes")
    ok(r, "pravilo Rejes -> Reyes postoji")
    eq((r["count"], r["class"], r["active"]), (2, "ime", True), "ime iz fraze je aktivno")
    k = rule_of(m, "kapitane", "kapetane")
    ok(not (k and k["active"]), "susedna mala rec se ne primenjuje sama")
    ok(rule_of(m, "Kapitane Rejes") is None, "veliko slovo na pocetku recenice spojeno sa oblikom malim slovom")
    ph = rule_of(m, "kapitane Rejes", "kapetane Reyes")
    eq(ph["count"], 2, "fraza: obe pojave u istom pravilu")
    v = m.view("tr", "sr")
    eq(v.apply_rules("Kapetan Rejes je stigao."), ("Kapetan Reyes je stigao.", 1), "ime se ispravlja i u drugom kontekstu")
    once = v.apply_rules("Kapitane Rejes, dobro jutro.")[0]
    eq(v.apply_rules(once), (once, 0), "idempotentno")


def t_conflict():
    d = ddir()
    write_rows(d, [row("kompjuter radi", "računar radi"), row("taj kompjuter", "taj računar"),
                   row("kompjuter ne radi", "kompjutor ne radi"), row("moj kompjuter", "moj kompjutor"),
                   row("miš radi", "miš radi dobro")])
    m = Memory.load(d)
    a, b = rule_of(m, "kompjuter", "računar"), rule_of(m, "kompjuter", "kompjutor")
    eq((a["conflict"], b["conflict"], a["active"], b["active"]), (True, True, False, False), "konflikt")
    eq(m.view("tr", "sr").apply_rules("kompjuter"), ("kompjuter", 0), "konflikt se ne primenjuje")
    ok(not m.view("tr", "sr").hints(), "konflikt nije u hints")
    m.set_rule_state(a["id"], "on")
    eq(m.view("tr", "sr").apply_rules("kompjuter"), ("računar", 1), "korisnik bira pobednika")
    eq(len(rule_of(m, "miš radi") or {}), 0, "insert nije pravilo")


def t_classes():
    d = ddir()
    write_rows(d, [row("idemo u beograd sutra", "idemo u Beograd sutra"),
                   row("sta radiš", "šta radiš"),
                   row("On je rekao da dolazi", "On je rekla da dolazi"),
                   row("Ona je rekao sve", "Ona je rekla sve"),
                   row("On je otišao", "Ona je otišla"),
                   row("šaljem mejl sada", "šaljem imejl sada"),
                   row("šaljem e-mail sada", "šaljem imejl sada"),
                   row("ima 5 jabuka", "ima pet jabuka"),
                   row("ima 3 kruške", "ima 4 kruške"),
                   row("ima dva psa", "ima 2 psa"),
                   row("hvala svima", "Hvala svima"), row("hvala svima opet", "Hvala svima opet"),
                   row("- da, idemo", "- Da, idemo"), row("stigao je obi-wan", "stigao je obi-Wan"),
                   row("Juče sam bio u gradu.", "Juče sam bila u gradu."),
                   row("Bio sam tamo.", "Bila sam tamo."),
                   row("Marko je išao kući.", "Marko je išla kući."),
                   row("Jesi li ti dobro?", "Jeste li vi dobro?"),
                   row("Bili smo u koncertu.", "Bili smo na koncertu."),
                   row("On je došao.", "On su došao."),
                   row("Τι κάνει το σπίτι", "Τι κάνει τη σπίτι", lang="el"),
                   row("Το σπίτι που αγόρασα", "Το σπίτι πού αγόρασα", lang="el"),
                   row("to je sto puta", "to je što puta"),
                   row("Rekao je Petrovic danas", "Rekao je Petrović danas"),
                   row("bio sam siguran", "bila sam sigurna"),
                   row("bio siguran tad", "bila sigurna tad"),
                   row("Znaš, Mislim da je gotovo.", "Znaš, mislim da je gotovo."),
                   row("Pa, Mislim da ne.", "Pa, mislim da ne."),
                   row("Poslao ga je Petar juče", "Poslao ga je Marko juče"),
                   row("Petar je došao", "Marko je došao"),
                   row("Možeš li sada?", "Možete li sada?")])
    m = Memory.load(d)
    cls = {(r["wrong"], r["right"]): r["class"] for r in m.dump(0)["rules"]}
    eq(cls.get(("beograd", "Beograd")), "ime", "ime")
    eq(cls.get(("sta", "šta")), "pravopis", "pravopis")
    eq(cls.get(("Petrovic", "Petrović")), "pravopis", "pravopis (kvačice)")
    eq(cls.get(("rekao", "rekla")), "gramatika", "gramatika")
    eq(cls.get(("On", "Ona")), "gramatika", "veliko početno na obe strane nije ime (On -> Ona)")
    eq(cls.get(("mejl", "imejl")), "gramatika", "pojedinačna mala reč -> gramatika (samo savet modelu)")
    eq(cls.get(("e-mail", "imejl")), "termin", "termin od više reči")
    eq(cls.get(("5", "pet")), "gramatika", "broj -> reč (pojedinačna reč)")
    eq(cls.get(("dva", "2")), "ime", "cifra -> ime")
    ok(("3", "4") not in cls, "samo cifre nije pravilo")
    # rod, registar, funkcijske reci: uvek gramatika (nikad automatska zamena)
    for w, r_ in [("bio", "bila"), ("Bio", "Bila"), ("išao", "išla"), ("ti", "vi"), ("Jesi", "Jeste"), ("u", "na"),
                  ("je", "su"), ("το", "τη"), ("που", "πού"), ("sto", "što"), ("bio siguran", "bila sigurna")]:
        # "Bio" -> "Bila" na pocetku recenice je spojeno sa "bio" -> "bila" (isto pravilo)
        eq(cls.get((w, r_)) or cls.get((w[:1].lower() + w[1:], r_[:1].lower() + r_[1:])), "gramatika",
           f"gramatika: {w} -> {r_}")
    ok(("Mislim", "mislim") not in cls, "samo spušteno veliko slovo nije pravilo")
    eq(cls.get(("Petar", "Marko")), "ime", "ime i na početku rečenice (nije obična reč)")
    eq(cls.get(("Možeš", "Možete")), "gramatika", "drugi oblik iste reči na početku rečenice nije ime")
    for w, r_ in [("bio", "bila"), ("ti", "vi"), ("u", "na"), ("το", "τη")]:
        ok(not rule_of(m, w, r_)["active"], f"{w} -> {r_} nije automatski aktivno")
    v = m.view("tr", "sr", "DrugiKanal")
    eq(v.apply_rules("Bio je veliki car. Reći ću ti sutra u Beogradu. Mislim da je on bio tu."),
       ("Bio je veliki car. Reći ću ti sutra u Beogradu. Mislim da je on bio tu.", 0),
       "rod/registar/predlozi/veliko slovo se ne menjaju")
    ok({"pogresno": "bio", "ispravno": "bila"} in v.hints(), "gramatika count>=2 ide modelu kao savet")
    ok(("hvala", "Hvala") not in cls, "veliko slovo na početku rečenice nije pravilo")
    ok(("da", "Da") not in cls, "posle crte dijaloga je početak rečenice")
    eq(cls.get(("wan", "Wan")), "ime", "crta u reči nije početak rečenice")
    g = rule_of(m, "rekao", "rekla")
    eq((g["count"], g["active"]), (2, False), "gramatika nije automatski aktivna")
    ok({"pogresno": "rekao", "ispravno": "rekla"} in m.view("tr", "sr").hints(), "gramatika count>=2 u hints")
    m.set_rule_state(g["id"], "on")
    eq(m.view("tr", "sr").apply_rules("rekao je"), ("rekla je", 1), "gramatika na on")
    m.set_rule_state(g["id"], "off")
    ok({"pogresno": "rekao", "ispravno": "rekla"} not in m.view("tr", "sr").hints(), "off nije u hints")


def t_apply_capital_and_boundaries():
    d = ddir()
    write_rows(d, [row("star kućni kompjuter je tu", "stari stoni računar je tu"),
                   row("i star kućni kompjuter", "i stari stoni računar"),
                   row("kućni kompjuter radi", "stoni računar radi"), row("to je kućni kompjuter", "to je stoni računar"),
                   row("plava ptica je tu", "crvena lisica je tu"), row("i plava ptica sad", "i crvena lisica sad"),
                   row("x crvena lisica y", "x zelena žaba y"), row("crvena lisica z", "zelena žaba z"),
                   row("šaljem e-mail", "šaljem imejl"), row("novi e-mail stiže", "novi imejl stiže"),
                   row("Poslao ga je Petar juče", "Poslao ga je Marko juče"),
                   row("Video sam Petar sinoć", "Video sam Marko sinoć")])
    v = Memory.load(d).view("tr", "sr")
    eq(v.apply_rules("Kućni kompjuter je nov."), ("Stoni računar je nov.", 1), "veliko početno slovo")
    eq(v.apply_rules("KUĆNI KOMPJUTER"), ("KUĆNI KOMPJUTER", 0), "sva velika slova se ne diraju")
    eq(v.apply_rules("kućni kompjuteri i kućni kompjuterčić"), ("kućni kompjuteri i kućni kompjuterčić", 0),
       "cela reč (unicode \\w)")
    eq(v.apply_rules("star kućni kompjuter i kućni kompjuter"), ("stari stoni računar i stoni računar", 2),
       "duža fraza prvo")
    eq(v.apply_rules("Star kućni kompjuter"), ("Stari stoni računar", 1), "fraza sa velikim slovom")
    eq(v.apply_rules("plava ptica crvena lisica"), ("crvena lisica zelena žaba", 2),
       "bez kaskade (a->b pa b->c ne daje c)")
    eq(v.apply_rules("pošalji e-mail, e-mail!"), ("pošalji imejl, imejl!", 2), "fraza sa crticom")
    eq(v.apply_rules("petar i Petar"), ("petar i Marko", 1), "wrong sa velikim slovom je osetljiv na mala slova")
    eq(v.apply_rules(""), ("", 0), "prazno")
    eq(v.apply_rules("\U000F0001 kompjuter"), ("\U000F0001 kompjuter", 0), "privatni opseg -> bez zamene")


def t_apply_idempotent():
    d = ddir()
    stt = dict(lang="sr", typ="stt")
    write_rows(d, [row("Pozdrav od fanova nikole.", "Pozdrav od fanova Nikole Tesle."),
                   row("Muzej nikole je otvoren.", "Muzej Nikole Tesle je otvoren."),
                   row("Glasala je vlada danas.", "Glasala je Vlada Srbije danas."),
                   row("Odlučila je vlada juče.", "Odlučila je Vlada Srbije juče."),
                   row("kaže Mister Smit", "kaže gospodin Smit"), row("reče Mister Smit", "reče gospodin Smit")])
    m = Memory.load(d)
    for w, r_ in [("nikole", "Nikole Tesle"), ("vlada", "Vlada Srbije")]:
        g = rule_of(m, w, r_)
        eq((g["class"], g["count"], g["active"]), ("ime", 2, True), f"pravilo {w} -> {r_}")
    m.glossary_op("add", {"type": "stt", "lang": "sr", "tgt": "Tom Kessler", "wrong": "Kessler"})
    m.glossary_op("add", {"type": "stt", "lang": "sr", "tgt": "Nikola Tesla", "wrong": "tesla"})
    v, s = m.view("tr", "sr"), m.view("stt", "sr")
    eq(v.apply_rules("Posetili smo muzej Nikole Tesle."), ("Posetili smo muzej Nikole Tesle.", 0),
       "tekst koji već ima right se ne dira (bez 'Nikole Tesle Tesle')")
    eq(v.apply_rules("muzej nikole i Nikole Tesle"), ("muzej Nikole Tesle i Nikole Tesle", 1), "zamena samo gde treba")
    eq(v.apply_rules("Vlada Srbije je odlučila, a vlada Srbije zaseda sutra."),
       ("Vlada Srbije je odlučila, a vlada Srbije zaseda sutra.", 0), "ostatak right posle wrong -> ne dira")
    eq(s.apply_rules("Tom Kessler i Kessler"), ("Tom Kessler i Tom Kessler", 1), "glosar: right ispred wrong")
    eq(s.apply_rules("Nikola Tesla je rođen, tesla takođe"), ("Nikola Tesla je rođen, Nikola Tesla takođe", 1),
       "glosar: veliko slovo, right već tu")
    for view, txt in [(v, "Pozdrav od fanova nikole. Muzej nikole i vlada."), (v, "Nikole Tesle i nikole"),
                      (s, "Kessler, Tom Kessler i kessler"), (s, "tesla, Tesla, Nikola Tesla")]:
        once = view.apply_rules(txt)[0]
        eq(view.apply_rules(once), (once, 0), f"apply(apply(x)) == apply(x): {txt!r}")
    # pravilo Velikim -> malim: pocetak recenice ostaje velikim slovom
    g = rule_of(m, "Mister", "gospodin")
    m.set_rule_state(g["id"], "on")
    eq(m.view("tr", "sr").apply_rules("Mister Smit je došao. Kaže Mister Smit: - Mister?"),
       ("Gospodin Smit je došao. Kaže gospodin Smit: - Gospodin?", 3), "veliko slovo na početku rečenice ostaje")


def t_tm_exact():
    d = ddir()
    write_rows(d, [row("Ovo je moj stari kompjuter.", "Ovo je moj stari računar.", src="This is my old computer."),
                   row("Ovo je moj stari kompjutor.", "Ovo je moj stari računarčić.", src="“This is my  old computer!”"),
                   row("Zdravo tamo", "Zdravo svete", src="Hello there"),
                   row("Он је мој веома стари друг", "Он је мој веома стари пријатељ", lang="sr-cyr",
                       src="He is my very old friend"),
                   row("Bez izvora ovde", "Bez izvora sada"),
                   row("Umoran sam.", "Umorna sam.", src="I'm tired."),
                   row("Tu je bio juče.", "Tu je bila juče.", src="He was here yesterday."),
                   row("Da li si bio tamo juče?", "Da li si bila tamo juče?", src="Were you there yesterday?"),
                   row("Gde si bio sinoć?", "Gde si bila sinoć?", src="Where were you last night?", channel="KanalA",
                       typ="tr")])
    m = Memory.load(d)
    v = m.view("tr", "sr")
    eq(v.exact("this is my old computer"), "Ovo je moj stari računarčić.", "TM: najnoviji pobeđuje, norm_src")
    eq(v.exact("  THIS is my old computer...  "), "Ovo je moj stari računarčić.", "TM: velika slova i interpunkcija")
    eq(v.exact("This is my old computer?"), None, "TM: pitanje ne dobija prevod izjave")
    eq(v.exact("Hello there"), None, "TM: izvor < 4 reči")
    eq(v.exact("I'm tired."), None, "TM: \"I'm tired.\" su 2 reči (po razmacima)")
    eq(v.exact("He is my very old friend."), "On je moj veoma stari prijatelj", "TM: ćirilica -> latinica")
    eq(v.exact("Something else entirely here"), None, "TM: nema")
    eq(v.exact("Were you there yesterday?"), "Da li si bila tamo juče?", "TM: pitanje -> pitanje")
    eq(v.exact("Were you there yesterday."), None, "TM: izjava ne dobija prevod pitanja")
    eq(v.exact("He was here yesterday?"), None, "TM: pitanje ne dobija prevod izjave (2)")
    eq(m.view("stt", "sr").exact("this is my old computer"), None, "TM samo tr")
    # kanal: ispravka iz kanala vazi samo u tom kanalu; ispravka bez kanala samo za video bez kanala
    eq(m.view("tr", "sr", "KanalA").exact("Where were you last night?"), "Gde si bila sinoć?", "TM: isti kanal")
    eq(m.view("tr", "sr", "DrugiKanal").exact("Where were you last night?"), None, "TM: drugi kanal")
    eq(v.exact("Where were you last night?"), None, "TM: video bez kanala ne dobija ispravku iz kanala")
    eq(m.view("tr", "sr", "KanalA").exact("This is my old computer."), None, "TM: kanal ne dobija ispravku bez kanala")
    eq((M.tm_key("Short line here"), M.tm_key("I am so sad now")), (None, None), "TM: < 4 reči / < 20 znakova")
    newest = m.dump(10)["recent"][7]
    eq(newest["after"], "Ovo je moj stari računarčić.", "recent redosled")
    m.set_correction_disabled(newest["id"], True)
    eq(m.view("tr", "sr").exact("This is my old computer."), "Ovo je moj stari računar.", "TM: isključena preskočena")
    eq(M.norm_src("  “Hello   there’s  friend!” "), "hello there's friend", "norm_src")


def t_examples():
    d = ddir()
    rows = [row("Ovo je vulkan Etna", "Ovo je vulkan Etna na Siciliji", src="This is the volcano Etna",
                channel="Geo", typ="tr"),
            row("Lava teče niz planinu", "Lava se sliva niz planinu", src="Lava flows down the mountain"),
            row("Vulkan je eruptirao", "Vulkan je izbacio lavu", src="The volcano erupted", channel="Druga",
                typ="tr"),
            row("Mačka spava", "Mačka drema", src="The cat is sleeping"),
            row("Mačka spava opet", "Mačka drema opet", src="The cat is sleeping"),  # drugo ispravno
            row("Dugačko " + "a" * 300, "Dugačko " + "b" * 300, src="volcano " + "x" * 400)]
    rows.append(dict(rows[3], ts="2026-09-30T00:00:00+0200"))  # duplikat (izvor, ispravno)
    write_rows(d, rows)
    m = Memory.load(d)
    v = m.view("tr", "sr")
    ex = v.examples(["The volcano erupted yesterday", "and nothing else"], k=6)
    ok(ex and ex[0]["izvor"] == "The volcano erupted", f"najsličniji prvi: {ex}")
    eq(v.examples(["The volcano erupted", "lava flows"], k=1)[0]["izvor"], "Lava flows down the mountain",
       "retke reči (idf) nose više")
    ok(all(set(e) == {"izvor", "pogresno", "ispravno"} for e in ex), "polja primera")
    ok(all(len(e[f]) <= 200 for e in ex for f in e), "polja <= 200 znakova")
    ok(not any("cat" in e["izvor"] for e in ex), "skor 0 ne ulazi")
    eq(len(v.examples(["volcano lava"], k=2)), 2, "top k")
    eq(v.examples(["the and is"], k=6), [], "samo stop-reči -> ništa")
    eq(v.examples([], k=6), [], "prazan blok")
    cat = v.examples(["the cat is sleeping"], k=6)
    eq([e["ispravno"] for e in cat], ["Mačka drema", "Mačka drema opet"],
       "dedup (izvor, ispravno): 3 reda -> 2 primera, najnoviji prvi")
    # isti kanal dobija bonus: "Geo" red (Etna) pretice "Druga" za upit sa "volcano"
    g = m.view("tr", "sr", channel="Geo").examples(["volcano"], k=6)
    eq(g[0]["izvor"], "This is the volcano Etna", "bonus za isti kanal")
    o = m.view("tr", "sr", channel="Druga").examples(["volcano"], k=6)
    eq(o[0]["izvor"], "The volcano erupted", "bonus za drugi kanal")
    eq(v.examples("volcano", k=0), [], "k=0")
    # stt: poredi se before, izvor prazan
    d2 = ddir()
    write_rows(d2, [row("putovao sam u lajpcig", "putovao sam u Leipzig", lang="sr", typ="stt", src=None)])
    s = Memory.load(d2).view("stt", "sr").examples(["lajpcig leti"])
    eq(s, [{"izvor": "", "pogresno": "putovao sam u lajpcig", "ispravno": "putovao sam u Leipzig"}],
       "stt primer")


def t_whisper_terms():
    d = ddir()
    stt = dict(lang="sr", typ="stt")
    write_rows(d, [row("gledali smo kopakabana snimak", "gledali smo Copacabana snimak", channel="Putopis", **stt),
                   row("opet kopakabana leti", "opet Copacabana leti", channel="Putopis", **stt),
                   row("pričamo o glazgov vozu", "pričamo o Glasgow vozu", channel="Putopis", **stt),
                   row("zvao je jovan", "zvao je Jovan", channel="Drugi", **stt),
                   row("zvao je jovan", "zvao je Jovan", channel="Drugi", ts="2026-09-30T11:00:00+0200", **stt),
                   row("Ovo je snimak", "Snimak je ovo", **stt),
                   row("Kompjuter radi", "Računar radi", src="Computer works")])
    m = Memory.load(d)
    m.glossary_op("add", {"type": "stt", "lang": "sr", "tgt": "Leipzig"})
    m.glossary_op("add", {"type": "stt", "lang": "sr", "tgt": "Salzburg", "scope": "channel", "channel": "Putopis"})
    m.glossary_op("add", {"type": "stt", "lang": "el", "tgt": "Αθήνα"})
    m.glossary_op("add", {"type": "stt", "lang": "sr", "tgt": "copacabana"})  # duplikat bez obzira na slova
    v = m.view("stt", "sr", channel="Putopis")
    terms = v.whisper_terms(400).split(", ")
    eq(terms[:3], ["Salzburg", "copacabana", "Leipzig"], "glosar prvi (isti kanal ispred, pa novije)")
    ok("Copacabana" in terms or "copacabana" in terms, f"Copacabana jednom: {terms}")
    eq(sum(1 for t in terms if t.lower() == "copacabana"), 1, "jedinstveno bez obzira na velika/mala slova")
    ok("Jovan" in terms and "Glasgow" in terms, f"aktivna pravila i imena iz ispravki: {terms}")
    ok(terms.index("Jovan") > terms.index("Leipzig"), "glosar pre pravila")
    ok("Snimak" not in terms, "početak rečenice nije ime")
    ok("Računar" not in terms, "tr ispravke ne ulaze u stt")
    ok("Αθήνα" not in terms, "drugi jezik ne ulazi")
    eq(v.whisper_terms(20), "Salzburg, copacabana", "max_chars tačno")
    eq(v.whisper_terms(19), "Salzburg", "max_chars ne prelazi")
    ok(len(v.whisper_terms(5)) <= 5, "max_chars mali")
    eq(v.whisper_terms(0), "", "max_chars 0")
    eq(m.view("stt", "sr").whisper_terms(400).split(", ")[:2], ["copacabana", "Leipzig"],
       "bez kanala: samo global glosar")
    ok("Salzburg" not in m.view("stt", "sr").whisper_terms(400), "kanal glosar samo za isti kanal")
    eq(m.view("tr", "sr").whisper_terms(400), "", "tr pogled nema whisper termine")
    eq(m.view("stt", "el").whisper_terms(100), "Αθήνα", "grčki termin")


def t_stt_function_words():
    d = ddir()
    stt = dict(lang="sr", typ="stt")
    write_rows(d, [row("rekao je i otišao", "rekao je da otišao", **stt), row("mislim i neće", "mislim da neće", **stt),
                   row("Onda smo krenuli, Onda je pao mrak", "Onda smo krenuli, onda je pao mrak", **stt),
                   row("Bio sam tamo, Onda kući", "Bio sam tamo, onda kući", **stt)])
    m = Memory.load(d)
    g = rule_of(m, "i", "da")
    eq((g["class"], g["count"], g["active"]), ("gramatika", 2, False), "stt i -> da je gramatika")
    ok(rule_of(m, "Onda", "onda") is None, "spušteno veliko slovo nije pravilo")
    v = m.view("stt", "sr")
    eq(v.apply_rules("Marko i Ana su došli i otišli. Onda smo krenuli."),
       ("Marko i Ana su došli i otišli. Onda smo krenuli.", 0), "transkript se ne kvari")
    ok("da" not in v.whisper_terms(400).split(", "), "gramatika ne ide u whisper prompt")


def t_stt_glossary_rule():
    d = ddir()
    m = Memory.load(d)
    ok(m.view("stt", "sr").empty, "prazna memorija")
    m.glossary_op("add", {"type": "stt", "lang": "sr", "tgt": "Leipzig", "wrong": "lajpcig"})
    m.glossary_op("add", {"type": "stt", "lang": "sr", "tgt": "Glasgow", "wrong": "glazgov", "scope": "channel",
                          "channel": "Putopis"})
    v = m.view("stt", "sr")
    ok(not v.empty, "glosar puni pogled")
    eq(v.apply_rules("putovao sam u lajpcig, Lajpcig je lep"), ("putovao sam u Leipzig, Leipzig je lep", 2),
       "stt glosar pravilo")
    eq(v.apply_rules("glazgov"), ("glazgov", 0), "kanal glosar ne važi van kanala")
    eq(m.view("stt", "sr", "Putopis").apply_rules("glazgov"), ("Glasgow", 1), "kanal glosar u svom kanalu")
    r = rule_of(m, "lajpcig", "Leipzig")
    eq((r["class"], r["active"], r["count"], r["type"]), ("glosar", True, 0, "stt"), "glosar pravilo u dump-u")
    eq(m.stats()["rules_active"], 2, "rules_active sa glosarom")
    m.set_rule_state(r["id"], "off")
    eq(m.view("stt", "sr").apply_rules("lajpcig"), ("lajpcig", 0), "glosar pravilo off")
    gid = next(g["id"] for g in m.dump(0)["glossary"] if g["tgt"] == "Leipzig")
    m.glossary_op("delete", {"id": gid})
    ok(rule_of(m, "lajpcig") is None, "brisanje stavke briše pravilo")
    raises(KeyError, lambda: m.set_rule_state(r["id"], "on"), "pravilo više ne postoji")
    eq(m.view("tr", "sr").apply_rules("lajpcig"), ("lajpcig", 0), "stt pravilo ne važi za tr")


def t_disabled():
    d = ddir()
    write_rows(d, [row("kućni kompjuter radi", "stoni računar radi", src="computer works fine"),
                   row("taj kućni kompjuter", "taj stoni računar", src="that computer there")])
    m = Memory.load(d)
    ok(rule_of(m, "kućni kompjuter")["active"], "aktivno pre isključenja")
    cid = m.dump(5)["recent"][0]["id"]
    out = m.set_correction_disabled(cid, True)
    eq((out["id"], out["disabled"]), (cid, True), "odgovor set_correction_disabled")
    r = rule_of(m, "kućni kompjuter")
    eq((r["count"], r["active"]), (1, False), "isključena ispravka ne broji")
    eq(m.stats()["disabled"], 1, "stats disabled")
    eq(m.stats()["total"], 2, "total i dalje broji sve")
    ex = m.view("tr", "sr").examples(["that computer"])
    ok(all(e["izvor"] != "that computer there" for e in ex), "isključena nije primer")
    eq(m.dump(5)["recent"][0]["disabled"], True, "recent disabled")
    eq(json.loads((d / "memory.json").read_text())["disabled"], [cid], "memory.json disabled")
    m.set_correction_disabled(cid, False)
    ok(rule_of(m, "kućni kompjuter")["active"], "ponovo aktivno")
    raises(KeyError, lambda: m.set_correction_disabled("nema", True), "nepostojeća ispravka")
    raises(ValueError, lambda: m.set_correction_disabled(cid, "da"), "disabled nije bool")
    # corr_id stabilan: sha1(ts|file|before|after)
    rr = json.loads((d / "corrections.jsonl").read_text().splitlines()[0])
    want = hashlib.sha1(f"{rr['ts']}|{rr['file']}|{rr['before']}|{rr['after']}".encode()).hexdigest()[:12]
    ok(want in {x["id"] for x in m.dump(5)["recent"]}, "corr_id = sha1(ts|file|before|after)[:12]")


def t_glossary_crud():
    d = ddir()
    m = Memory.load(d)
    gl = m.glossary_op("add", {"type": "tr", "lang": "sr", "src": "Obi-Wan Kenobi", "tgt": "Оби-Ван Кеноби",
                               "note": "  ime  "})
    eq(len(gl), 1, "dodato")
    e = gl[0]
    eq((e["tgt"], e["scope"], e["channel"], e["wrong"], e["note"]), ("Obi-Van Kenobi", "global", None, "", "ime"),
       "tr sr tgt u latinici")
    ok(len(e["id"]) == 10 and all(c in "0123456789abcdef" for c in e["id"]), "id 10 hex")
    ok(e["created"], "created")
    raises(ValueError, lambda: m.glossary_op("add", {"type": "tr", "lang": "sr", "src": "obi-wan kenobi",
                                                     "tgt": "Obi-Van Kenobi"}), "duplikat", "već postoji")
    m.glossary_op("add", {"type": "tr", "lang": "sr", "src": "Death Star", "tgt": "Zvezda smrti",
                          "scope": "channel", "channel": "StarWars"})
    m.glossary_op("add", {"type": "tr", "lang": "el", "src": "computer", "tgt": "υπολογιστής"})
    for bad, msg in [({"type": "xx", "lang": "sr", "src": "a", "tgt": "b"}, "tip"),
                     ({"type": "tr", "lang": "srpski", "src": "a", "tgt": "b"}, "jezik"),
                     ({"type": "tr", "lang": "sr", "src": "", "tgt": "b"}, "src"),
                     ({"type": "tr", "lang": "sr", "src": "a", "tgt": ""}, "tgt"),
                     ({"type": "stt", "lang": "sr", "tgt": ""}, "tgt"),
                     ({"type": "tr", "lang": "sr", "src": "a" * 121, "tgt": "b"}, "120"),
                     ({"type": "tr", "lang": "sr", "src": "a", "tgt": "b", "note": "n" * 301}, "300"),
                     ({"type": "tr", "lang": "sr", "src": "a", "tgt": "b", "scope": "channel"}, "kanal"),
                     ({"type": "tr", "lang": "sr", "src": "a", "tgt": "b", "scope": "svuda"}, "opseg"),
                     ({"type": "tr", "lang": "sr", "src": ["a"], "tgt": "b"}, "tekst")]:
        raises(ValueError, lambda b=bad: m.glossary_op("add", b), f"validacija {bad}", msg)
    raises(ValueError, lambda: m.glossary_op("put", {}), "nepoznata operacija", "operacija")
    raises(ValueError, lambda: m.glossary_op("add", "x"), "stavka nije objekat")
    # update / delete
    gl = m.glossary_op("update", {"id": e["id"], "tgt": "Obi-Van Kenobi Stariji", "id2": "ignorisano"})
    u = next(g for g in gl if g["id"] == e["id"])
    eq((u["tgt"], u["src"], u["created"]), ("Obi-Van Kenobi Stariji", "Obi-Wan Kenobi", e["created"]), "update")
    raises(KeyError, lambda: m.glossary_op("update", {"id": "nema", "tgt": "x"}), "update nepostojeće")
    raises(KeyError, lambda: m.glossary_op("delete", {"id": "nema"}), "delete nepostojeće")
    raises(ValueError, lambda: m.glossary_op("update", {"id": e["id"], "src": "Death Star", "tgt": "Zvezda smrti",
                                                        "scope": "channel", "channel": "StarWars"}),
           "update u duplikat", "već postoji")
    # glossary_for
    v = m.view("tr", "sr-lat")
    eq(v.glossary_for("Hello there. General KENOBI is here, obi-wan kenobi."),
       [{"src": "Obi-Wan Kenobi", "sr": "Obi-Van Kenobi Stariji"}], "case-insensitive pogodak")
    eq(v.glossary_for("And then Kenobi said"), [{"src": "Obi-Wan Kenobi", "sr": "Obi-Van Kenobi Stariji"}],
       "ime iz src-a u korpusu")
    eq(v.glossary_for("kenobi is lowercase"), [], "ime mora biti velikim slovom u korpusu")
    eq(v.glossary_for("the death star is big"), [], "kanal stavka van kanala")
    eq(m.view("tr", "sr", "StarWars").glossary_for("the death star is big"),
       [{"src": "Death Star", "sr": "Zvezda smrti"}], "kanal stavka u kanalu")
    eq(m.view("tr", "el").glossary_for("my computer"), [{"src": "computer", "sr": "υπολογιστής"}], "el ključ sr")
    eq(m.view("tr", "el").glossary_for("my computers"), [], "cela reč")
    eq(m.view("stt", "sr").glossary_for("Kenobi"), [], "stt nema tr glosar")
    gl = m.glossary_op("delete", {"id": e["id"]})
    eq(len(gl), 2, "obrisano")
    mj = json.loads((d / "memory.json").read_text())
    eq(len(mj["glossary"]), 2, "memory.json glosar")
    eq([p.name for p in d.iterdir() if p.name.endswith(".tmp")], [], "bez tmp ostataka")
    eq(m.stats()["glossary"], 2, "stats glossary")
    # stt: isti termin sa razlicitim pogresnim oblikom je dozvoljen
    m.glossary_op("add", {"type": "stt", "lang": "sr", "tgt": "Leipzig", "wrong": "lajpcig"})
    m.glossary_op("add", {"type": "stt", "lang": "sr", "tgt": "Leipzig", "wrong": "lajp cig"})
    raises(ValueError, lambda: m.glossary_op("add", {"type": "stt", "lang": "sr", "tgt": "Leipzig",
                                                     "wrong": "Lajpcig"}), "stt duplikat", "već postoji")
    raises(ValueError, lambda: m.glossary_op("add", {"type": "stt", "lang": "sr", "tgt": "X", "wrong": "X"}),
           "wrong == tgt")
    # limit 2000
    mj = json.loads((d / "memory.json").read_text())
    mj["glossary"] = [{"id": f"{i:010x}", "type": "tr", "lang": "sr", "src": f"w{i}", "tgt": f"r{i}",
                       "scope": "global"} for i in range(M.GLOSSARY_MAX)]
    (d / "memory.json").write_text(json.dumps(mj))
    m2 = Memory.load(d)
    eq(m2.stats()["glossary"], M.GLOSSARY_MAX, "2000 stavki učitano")
    raises(ValueError, lambda: m2.glossary_op("add", {"type": "tr", "lang": "sr", "src": "a", "tgt": "b"}),
           "limit", "pun")


def t_cyr_lat():
    d = ddir()
    write_rows(d, [row("Мој кућни компјутер ради", "Мој стони рачунар ради", lang="sr-cyr", src="My computer works"),
                   row("Taj kućni kompjuter je nov", "Taj stoni računar je nov", lang="sr-lat",
                       src="That computer is new"),
                   row("Он је љут", "Он је љутит", lang="sr-cyr"),
                   row("Здраво", "Zdravo", lang="sr-cyr")])  # samo promena pisma -> nista
    m = Memory.load(d)
    r = rule_of(m, "kućni kompjuter", "stoni računar")
    eq((r["count"], r["active"], r["lang"]), (2, True, "sr"), "sr-cyr + sr-lat = ista porodica, latinica")
    v = m.view("tr", "sr-cyr")
    eq(v.apply_rules("Moj kućni kompjuter"), ("Moj stoni računar", 1), "primena u latinici")
    ok(rule_of(m, "ljut", "ljutit"), "ćirilica -> latinica (lj)")
    ok(all("Здраво" not in (e["pogresno"] + e["ispravno"]) for e in v.examples(["computer"])), "primeri latinica")
    rec = m.dump(10)["recent"]
    eq(rec[-1]["before"], "Мој кућни компјутер ради", "recent čuva original")
    eq(m.stats()["by_lang"], {"sr-cyr": 3, "sr-lat": 1}, "by_lang")
    eq(m.stats()["by_type"], {"tr": 4}, "stari redovi bez type = tr")
    ex = v.examples(["my computer works"], k=1)
    eq(ex[0]["ispravno"], "Moj stoni računar radi", "primer iz ćirilice u latinici")
    m.glossary_op("add", {"type": "stt", "lang": "sr", "tgt": "Џон", "wrong": "џони"})
    eq(m.view("stt", "sr").apply_rules("zvao je džoni"), ("zvao je Džon", 1), "stt glosar u latinici")


def t_greek():
    d = ddir()
    el = dict(lang="el", file="subs/el.x.srt")
    write_rows(d, [row("Ο κομπιούτερ δουλεύει τώρα", "Ο υπολογιστής λειτουργεί τώρα", src="The computer works", **el),
                   row("Νέος κομπιούτερ δουλεύει εδώ", "Νέος υπολογιστής λειτουργεί εδώ", src="New computer here",
                       **el),
                   row("Γεια σου κόσμε", "Γεια σου κόσμε μου", src="Hello world", **el),
                   row("Τι κάνει το σπίτι", "Τι κάνει τη σπίτι", src="What the house does", **el),
                   row("Πού είναι το αυτοκίνητο", "Πού είναι τη αυτοκίνητο", src="Where the car is", **el)])
    m = Memory.load(d)
    v = m.view("tr", "el")
    eq(v.apply_rules("Κομπιούτερ δουλεύει και κομπιούτερ δουλεύει."),
       ("Υπολογιστής λειτουργεί και υπολογιστής λειτουργεί.", 2), "grčki sa velikim slovom")
    eq(v.apply_rules("κομπιούτερς δουλεύει"), ("κομπιούτερς δουλεύει", 0), "grčki cela reč")
    g = rule_of(m, "το", "τη")
    eq((g["class"], g["count"], g["active"]), ("gramatika", 2, False), "grčki član -> gramatika")
    eq(v.apply_rules("Το σπίτι και το αυτοκίνητο."), ("Το σπίτι και το αυτοκίνητο.", 0), "član se ne menja")
    ok(m.view("tr", "sr").empty, "el ne ulazi u sr")
    ex = v.examples(["the computer is here"], k=3)
    eq(len(ex), 2, "grčki primeri")
    d2 = ddir()
    write_rows(d2, [row("Είναι στην αθήνα", "Είναι στην Αθήνα", lang="el", typ="stt"),
                    row("Πάμε στην αθήνα", "Πάμε στην Αθήνα", lang="el", typ="stt")])
    s = Memory.load(d2).view("stt", "el")
    eq(s.apply_rules("η αθήνα"), ("η Αθήνα", 1), "stt grčko ime")
    eq(s.whisper_terms(250), "Αθήνα", "grčki whisper termin")
    ok(Memory.load(d2).view("stt", "el").examples(["ΑΘΗΝΑ τώρα"]), "grčki casefold u primerima")


def t_cache():
    d = ddir()
    write_rows(d, [row("kućni kompjuter radi", "stoni računar radi")])
    a, b = Memory.load(d), Memory.load(d)
    ok(a._st is b._st, "isti snimak iz keša")
    ok(a.view("tr", "sr") is b.view("tr", "sr"), "isti pogled iz keša")
    write_rows(d, [row("taj kućni kompjuter", "taj stoni računar")], mode="a")
    c = Memory.load(d)
    ok(c._st is not a._st, "dopisan red -> novo čitanje")
    eq((c.stats()["total"], a.stats()["total"]), (2, 1), "stari snimak ostaje nepromenjen")
    ok(rule_of(c, "kućni kompjuter")["active"], "novo pravilo aktivno")
    # izmena memorije osvezava isti objekat i kes (bez ponovnog citanja)
    c.glossary_op("add", {"type": "tr", "lang": "sr", "src": "hello", "tgt": "zdravo"})
    eq(c.stats()["glossary"], 1, "objekat osvežen")
    ok(Memory.load(d)._st is c._st, "keš osvežen posle upisa")
    eq(a.stats()["glossary"], 0, "stari objekat je snimak")
    # spoljna izmena memory.json (drugi proces / ruka)
    time.sleep(0.01)
    mj = json.loads((d / "memory.json").read_text())
    mj["glossary"] = []
    (d / "memory.json").write_text(json.dumps(mj))
    eq(Memory.load(d).stats()["glossary"], 0, "spoljna izmena memory.json")
    # brisanje fajlova -> prazna memorija
    (d / "corrections.jsonl").unlink()
    (d / "memory.json").unlink()
    e = Memory.load(d)
    eq((e.stats()["total"], e.stats()["glossary"]), (0, 0), "nepostojeći fajlovi = prazno")
    ok(e.view("tr", "sr").empty, "prazan pogled")
    eq(e.view("tr", "sr").examples(["x"]), [], "prazni primeri")
    eq(e.view("tr", "sr").apply_rules("kompjuter"), ("kompjuter", 0), "prazna pravila")
    nd = TMP / "nema" / "data"
    eq(Memory.load(nd).stats()["total"], 0, "nepostojeći folder")
    Memory.load(nd).glossary_op("add", {"type": "tr", "lang": "sr", "src": "a", "tgt": "b"})
    ok((nd / "memory.json").exists(), "upis pravi folder")
    # niti: paralelno citanje + dopisivanje + izmene bez gresaka
    write_rows(d, [row(f"reč{i} kompjuter", f"reč{i} računar") for i in range(50)])
    errs: list[str] = []

    def reader():
        try:
            for _ in range(40):
                mm = Memory.load(d)
                mm.view("tr", "sr").apply_rules("kompjuter")
                mm.stats()
        except Exception:  # noqa: BLE001
            errs.append(traceback.format_exc())

    def writer():
        try:
            for i in range(20):
                write_rows(d, [row(f"x{i} kompjuter", f"x{i} računar")], mode="a")
                Memory.load(d).glossary_op("add", {"type": "tr", "lang": "sr", "src": f"s{i}", "tgt": f"t{i}"})
        except Exception:  # noqa: BLE001
            errs.append(traceback.format_exc())

    th = [threading.Thread(target=reader) for _ in range(6)] + [threading.Thread(target=writer)]
    for t in th:
        t.start()
    for t in th:
        t.join()
    eq(errs, [], "niti bez grešaka")
    f = Memory.load(d)
    eq((f.stats()["total"], f.stats()["glossary"]), (70, 20), "posle niti")
    ok(json.loads((d / "memory.json").read_text()), "memory.json validan posle niti")


def t_broken_files():
    d = ddir()
    good = [row("kućni kompjuter radi", "stoni računar radi", src="computer works"),
            row("taj kućni kompjuter", "taj stoni računar", src="that computer")]
    old = {"ts": "2026-01-01T10:00:00+0100", "video_id": "v", "folder": "/f", "file": "a.srt", "lang": "sr-lat",
           "engine": None, "model": None, "src": None, "before": "stari red", "after": "stari redak"}
    with (d / "corrections.jsonl").open("wb") as fh:
        fh.write(b"\n   \n{pokvaren json\n[1, 2]\n\"string\"\n")
        fh.write(json.dumps({"before": "samo before"}).encode() + b"\n")
        fh.write(json.dumps({"before": 5, "after": "x"}).encode() + b"\n")
        fh.write(json.dumps({"before": "  ", "after": "x"}).encode() + b"\n")
        for r in good + [old]:
            fh.write(json.dumps(r, ensure_ascii=False).encode() + b"\n")
        fh.write(b'{"before": "lo\xff\xfe\xfdse", "after": "bajtovi"}\n')   # nevalidan utf-8
        # kopija validnog reda sa losim bajtom u "after": ne sme da napravi konflikt sa ispravnim pravilom
        fh.write(json.dumps(dict(good[0], ts="2026-09-30T10:00:00+0200"), ensure_ascii=False).encode()
                 .replace("stoni".encode(), b"st\xffni") + b"\n")
        fh.write(b'{"ts": "x", "before": "prekinut", "aft')                  # prekinut poslednji red
    m = Memory.load(d)
    st = m.stats()
    eq(st["total"], 3, "validni redovi (pokvareni, i nevalidan UTF-8, preskočeni)")
    eq(st["by_type"], {"tr": 3}, "stari red bez type = tr")
    r = rule_of(m, "kućni kompjuter")
    eq((r["right"], r["active"], r["conflict"]), ("stoni računar", True, False), "red sa lošim bajtom ne pravi konflikt")
    ok(not any("\ufffd" in x["right"] for x in m.dump(0)["rules"]), "nema U+FFFD u pravilima")
    ok(rule_of(m, "red", "redak"), "stari red bez type daje pravilo")
    # pokvaren memory.json: citanje radi, upis pravi rezervnu kopiju
    (d / "memory.json").write_text("{ovo nije json")
    m = Memory.load(d)
    eq(m.stats()["glossary"], 0, "pokvaren memory.json = prazno")
    ok(rule_of(m, "kućni kompjuter")["active"], "pravila i uz pokvaren memory.json")
    m.glossary_op("add", {"type": "tr", "lang": "sr", "src": "hello", "tgt": "zdravo"})
    bad = [p for p in d.iterdir() if p.name.startswith("memory.json.bad-")]
    eq(len(bad), 1, "rezervna kopija pokvarenog memory.json")
    eq(bad[0].read_text(), "{ovo nije json", "kopija netaknuta")
    eq(json.loads((d / "memory.json").read_text())["glossary"][0]["src"], "hello", "nov memory.json")
    # memory.json sa losim tipovima
    (d / "memory.json").write_text(json.dumps({"glossary": {"x": 1}, "rules": ["a"], "disabled": "x",
                                               "extra": 1}))
    m = Memory.load(d)
    eq(m.stats()["glossary"], 0, "loš glosar ignorisan")
    m.glossary_op("add", {"type": "tr", "lang": "sr", "src": "a", "tgt": "b"})
    eq(json.loads((d / "memory.json").read_text())["extra"], 1, "nepoznati ključevi ostaju")
    (d / "memory.json").write_text(json.dumps({"glossary": [{"id": "abc", "type": "tr", "lang": "sr", "src": "x",
                                                              "tgt": "y"}, {"id": "abc", "type": "tr"}, 5]}))
    eq(Memory.load(d).stats()["glossary"], 1, "nevalidne stavke glosara preskočene")


def t_dump_and_validation():
    d = ddir()
    rows = [row("kućni kompjuter radi", "stoni računar radi", channel="K1", typ="tr", src_lang="en"),
            row("taj kućni kompjuter", "taj stoni računar", channel="K2", typ="tr", src_lang="en"),
            row("opet e-mail", "opet imejl"), row("kucam e-mail", "kucam imejl"), row("novi e-mail", "novi imejl"),
            row("jedan tamo", "jedan svete"),
            row("snimak lajpcig", "snimak Leipzig", lang="sr", typ="stt")]
    write_rows(d, rows)
    (d / "settings.json").write_text(json.dumps({"mem_stt": False, "mem_tr": "true"}))
    m = Memory.load(d)
    dm = m.dump(3)
    eq(set(dm), {"stats", "glossary", "rules", "recent", "settings"}, "dump ključevi")
    eq(dm["settings"], {"mem_tr": True, "mem_stt": False}, "settings iz settings.json")
    eq(set(dm["stats"]), {"total", "by_lang", "by_type", "disabled", "rules_active", "glossary"}, "stats ključevi")
    eq(dm["stats"]["by_type"], {"tr": 6, "stt": 1}, "by_type")
    eq(len(dm["recent"]), 3, "recent limit")
    eq(dm["recent"][0]["after"], "snimak Leipzig", "recent najnoviji prvi")
    eq(set(dm["recent"][0]), {"id", "ts", "type", "lang", "channel", "video_id", "file", "src", "before", "after",
                              "disabled"}, "recent polja")
    rk = {"id", "type", "lang", "wrong", "right", "count", "channels", "class", "conflict", "state", "active",
          "last_ts", "scope"}
    ok(all(set(r) == rk for r in dm["rules"]), "rules polja")
    eq([(r["wrong"], r["active"]) for r in dm["rules"]][:2], [("e-mail", True), ("kućni kompjuter", True)],
       "aktivna prvo, pa count")
    ok(not dm["rules"][-1]["active"], "neaktivna na kraju")
    eq(rule_of(m, "kućni kompjuter")["channels"], ["K1", "K2"], "kanali pravila")
    # glosar samo za kanal: scope "channel" (u drugim kanalima se ne primenjuje)
    m.glossary_op("add", {"type": "stt", "lang": "sr", "tgt": "Kenobi", "wrong": "kenobija", "scope": "channel",
                          "channel": "Kanal A"})
    m.glossary_op("add", {"type": "stt", "lang": "sr", "tgt": "Glasgow", "wrong": "glazgov"})
    eq((rule_of(m, "kenobija")["scope"], rule_of(m, "kenobija")["active"]), ("channel", True), "scope channel")
    eq(rule_of(m, "glazgov")["scope"], "all", "globalni glosar: scope all")
    eq(m.view("stt", "sr", "Kanal B").apply_rules("kenobija"), ("kenobija", 0), "scope channel van kanala")
    eq(m.dump(0)["recent"], [], "recent 0")
    ok(M.RULES_DUMP_MAX == 500, "najviše 500 pravila")
    raises(ValueError, lambda: m.dump("x"), "recent nije broj")
    raises(ValueError, lambda: m.view("xx", "sr"), "loša vrsta pogleda")
    raises(ValueError, lambda: m.view("tr", ""), "prazan jezik")
    eq(m.view("stt", "sr").apply_rules("lajpcig"), ("lajpcig", 0), "stt 1 pojava nije aktivna")
    json.dumps(dm, ensure_ascii=False)  # dump je JSON-serijalizabilan


def t_hints():
    d = ddir()
    rows = []
    for i in range(3):
        rows += [row(f"a{i} kućni kompjuter", f"a{i} stoni računar"), row(f"b{i} rekao", f"b{i} rekla")]
    rows += [row("c e-mail", "c imejl"), row("d e-mail", "d imejl"),
             row("e tamo", "e svete")]
    write_rows(d, rows)
    v = Memory.load(d).view("tr", "sr")
    eq(v.hints(), [{"pogresno": "rekao", "ispravno": "rekla"},
                   {"pogresno": "kućni kompjuter", "ispravno": "stoni računar"},
                   {"pogresno": "e-mail", "ispravno": "imejl"}], "hints: count opadajuće, pa novije")
    eq(len(v.hints(limit=1)), 1, "hints limit")
    eq(v.rules_active, 2, "MemView.rules_active")


def t_perf_20k():
    d = ddir()
    rnd = random.Random(7)
    syl = ["ka", "ra", "to", "mi", "ne", "lo", "sa", "vi", "de", "po", "ru", "za", "če", "šo", "đu", "ži"]
    vocab = sorted({"".join(rnd.choice(syl) for _ in range(rnd.randint(2, 4))) for _ in range(6000)})
    en = sorted({"".join(rnd.choice("bcdfghklmnprstvw") + rnd.choice("aeiou") for _ in range(rnd.randint(2, 4)))
                 for _ in range(6000)})
    wts = [1 / (i + 1) for i in range(len(vocab))]
    ewts = [1 / (i + 1) for i in range(len(en))]
    fixes = [(f"{vocab[i]} {vocab[i + 1]}", f"{vocab[-1 - i]} {vocab[-2 - i]}") for i in range(100, 400, 3)]
    lines = []
    for i in range(20000):
        words = rnd.choices(vocab, wts, k=rnd.randint(4, 10))
        a, b = fixes[i % len(fixes)]
        pos = rnd.randrange(len(words))
        before = words[:pos] + a.split() + words[pos:]
        after = words[:pos] + b.split() + words[pos:]
        r = {"ts": f"2026-09-{1 + i % 28:02d}T{i % 24:02d}:00:00+0200", "video_id": f"v{i % 300}", "folder": "/f",
             "file": f"subs/sr-lat.{i % 50}.srt", "lang": "sr-lat" if i % 3 else "sr-cyr", "engine": "ollama",
             "model": "m", "src": " ".join(rnd.choices(en, ewts, k=rnd.randint(4, 10))),
             "before": " ".join(before), "after": " ".join(after), "channel": f"K{i % 40}", "type": "tr"}
        if i % 3 == 0:
            r["before"], r["after"] = r["before"].translate(CYR), r["after"].translate(CYR)
        lines.append(json.dumps(r, ensure_ascii=False))
    (d / "corrections.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    M.clear_cache()
    t0 = time.time()
    m = Memory.load(d)
    t_load = time.time() - t0
    eq(m.stats()["total"], 20000, "20k redova")
    t0 = time.time()
    v = m.view("tr", "sr", channel="K3")
    t_view = time.time() - t0
    block = [" ".join(rnd.choices(en, ewts, k=8)) for _ in range(30)]
    t0 = time.time()
    for _ in range(5):
        ex = v.examples(block)
    t_ex = (time.time() - t0) / 5
    eq(len(ex), 6, "6 primera")
    t0 = time.time()
    n = sum(v.apply_rules(" ".join(rnd.choices(vocab, wts, k=8)) + " " + fixes[k % len(fixes)][0])[1]
            for k in range(300))
    t_apply = time.time() - t0
    t0 = time.time()
    Memory.load(d)
    t_cached = time.time() - t0
    with (d / "corrections.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(lines[5] + "\n")
    t0 = time.time()
    Memory.load(d)
    t_reload = time.time() - t0
    print(f"      load {t_load * 1000:.0f} ms, view {t_view * 1000:.0f} ms, examples(30) {t_ex * 1000:.1f} ms, "
          f"apply_rules x300 {t_apply * 1000:.0f} ms ({n} zamena, {m.stats()['rules_active']} aktivnih pravila), "
          f"keš {t_cached * 1000:.2f} ms, dopisan red {t_reload * 1000:.0f} ms")
    ok(t_load + t_view + t_ex < 3, f"performanse (load {t_load:.2f}s, view {t_view:.2f}s, primeri {t_ex:.2f}s)")


CYR = str.maketrans({"a": "а", "b": "б", "v": "в", "g": "г", "d": "д", "e": "е", "ž": "ж", "z": "з", "i": "и",
                     "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "r": "р", "s": "с", "t": "т",
                     "u": "у", "f": "ф", "h": "х", "c": "ц", "č": "ч", "š": "ш", "ć": "ћ", "đ": "ђ", "j": "ј"})


def main():
    only = [x.lower() for x in sys.argv[1:]]
    tests = [(n, f) for n, f in globals().items() if n.startswith("t_") and callable(f)]
    if only:
        tests = [(n, f) for n, f in tests if any(o in n.lower() for o in only)]
    t_all = time.time()
    fails = 0
    try:
        for n, f in tests:
            t = time.time()
            try:
                f()
                print(f"PASS  {n[2:]:<32} {time.time() - t:5.2f}s")
            except Exception as e:  # noqa: BLE001
                fails += 1
                msg = str(e) if isinstance(e, AssertionError) else traceback.format_exc()
                print(f"FAIL  {n[2:]:<32} {time.time() - t:5.2f}s  {msg}")
    finally:
        if not os.environ.get("YTA_TEST_KEEP"):
            shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{len(tests) - fails}/{len(tests)} prošlo za {time.time() - t_all:.1f}s")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
