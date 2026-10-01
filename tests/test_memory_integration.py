#!/usr/bin/env python3
"""Test primene memorije u prevodu (docs/memory-contract.md §6, §9): yt_arhiva sa laznim LLM-om.

  python3 tests/test_memory_integration.py            # svi testovi (par sekundi), izlaz != 0 ako nesto padne
  python3 tests/test_memory_integration.py cyr el     # samo testovi ciji naziv sadrzi 'cyr' ili 'el'
  YTA_TEST_TMP=/neki/folder -> privremeni folder "memint-*" pravi se tu;  YTA_TEST_KEEP=1 -> ne brisi ga

Pokriva: bez memorije (ili sa praznom) isti promptovi i rezultat kao pre (poredjenje sa yt_arhiva.py iz git-a,
verzija pre memorije), primeri/glosar/pravila u promptovima (ollama, synergy merge_c, QC, grcki, lektura),
TM (exact) i naucena pravila posle prevoda, cirilica, kes bez memorijskih zamena, mem_report.
Bez mreze i bez modela: lazan LLM belezi promptove i vraca validan JSON; lazan call_tg (TranslateGemma).
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import yt_arhiva as core  # noqa: E402
from memory import Memory  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="memint-", dir=os.environ.get("YTA_TEST_TMP") or None)).resolve()
_N = [0]
CHANNEL = "Harbour TV"
TITLE = "Harbour station — a week of storms"

# (izvor en, prevod sr, prevod el ili None); scena A (0–9) deli reci sa ispravkama, scena B (10–19) ne
LINES = [
    ("Welcome back to the harbour station.", "Dobro došli nazad u lučku stanicu.", None),
    ("The tide gauge needs checking before Maren arrives.", "Merač plime treba proveriti pre nego što Maren stigne.",
     "Ο παλιρροιογράφος θέλει έλεγχο πριν έρθει η Μαρέν."),
    ("Tobias fixed the relay last night.", "Tobias je sinoć popravio relej.", None),
    ("We lost power twice during the storm.", "Dvaput smo ostali bez struje tokom oluje.", None),
    ("Call the coast guard if the wind picks up.", "Pozovi obalsku stražu ako vetar pojača.",
     "Καλέστε το λιμενικό αν δυναμώσει ο αέρας."),
    ("Nobody touches the old tide gauge.", "Niko ne dira stari merač plime.", None),
    ("Maren wants a full report by Friday.", "Maren želi ceo izveštaj do petka.",
     "Η Μαρέν θέλει πλήρη αναφορά μέχρι την Παρασκευή."),
    ("The generator is running on diesel now.", "Generator sada radi na dizel.", None),
    ("Keep the logbook dry.", "Čuvaj dnevnik suvim.", None),
    ("See you at the pier.", "Vidimo se na molu.", None),
    ("The kitchen smells of fresh bread.", "Kuhinja miriše na svež hleb.", None),
    ("My sister bakes every Sunday.", "Moja sestra peče svake nedelje.", None),
    ("She uses flour from the valley.", "Koristi brašno iz doline.", None),
    ("The oven is older than me.", "Rerna je starija od mene.", None),
    ("We sell the loaves at the market.", "Vekne prodajemo na pijaci.", None),
    ("People queue before sunrise.", "Ljudi čekaju u redu pre izlaska sunca.", None),
    ("The recipe belongs to grandmother.", "Recept pripada baki.", None),
    ("Nobody knows the secret ingredient.", "Niko ne zna tajni sastojak.", None),
    ("Butter makes everything better.", "Puter sve čini boljim.", None),
    ("Thank you for watching.", "Hvala na gledanju.", None),
]
SR = {en: sr for en, sr, _ in LINES}
EL = {en: el or f"Υπότιτλος αριθμός {i}." for i, (en, _, el) in enumerate(LINES)}
TG_TERMS = {"tide gauge": {"sr": "merilo plime", "el": "παλιρροιογράφος"}}
CTX = {"synopsis": "Tim lučke stanice (Maren, žena; Tobias, muškarac) bori se sa olujom.",
       "glossary": [{"src": "Tobias", "sr": "Tobias"}, {"src": "tide gauge", "sr": "merač plime"}],
       "register": "ti"}
TM_SR = "Pozovi obalsku stražu ako vetar ojača."
TM_EL = "Καλέστε το Λιμενικό αν δυναμώσει ο άνεμος."


# ----------------------------------------------------------------- pomocne
def eq(got, want, what: str):
    if got != want:
        raise AssertionError(f"{what}: očekivano {want!r}, dobijeno {got!r}")


def ok(cond, what: str):
    if not cond:
        raise AssertionError(what)


def tdir(prefix: str = "w") -> Path:
    _N[0] += 1
    d = TMP / f"{prefix}{_N[0]}"
    d.mkdir(parents=True)
    return d


def make_cues() -> list[dict]:
    cues, t = [], 0
    for i, (en, _, _) in enumerate(LINES):
        if i:
            t += 3000 if i == 10 else 400  # scena B posle pauze > 2 s
        cues.append({"start": t, "end": t + 2000, "text": en})
        t += 2000
    return cues


def targs(**kw) -> SimpleNamespace:
    a = SimpleNamespace(engine="ollama", model="fake:1b", script="lat", jobs=1, batch=10, review=False,
                        cancelled=lambda: False, qc={}, src_lang="en")
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def between(p: str, what: str, a: str, b: str) -> bool:
    """what se nalazi u p posle a i pre b."""
    return a in p and b in p and what in p and p.index(a) < p.index(what) < p.index(b)


class Fake:
    """Lazan model: kontekst -> CTX; prevod -> SR/EL recnik; merge -> osnova; lektura -> isti tekst.
    bad = id-jevi koji u prvom prevodu dobiju prazan red (QC ih ponovo prevodi)."""

    def __init__(self, bad=()):
        self.calls: list[tuple] = []
        self.bad = set(bad)
        self.lock = threading.Lock()

    def rec(self, kind, prompt, system=None, schema=None):
        with self.lock:
            self.calls.append((kind, prompt, system, json.dumps(schema, sort_keys=True) if schema else None))

    def kinds(self) -> list[str]:
        return [c[0] for c in self.calls]

    def prompts(self, kind: str) -> list[str]:
        return [c[1] for c in self.calls if c[0] == kind]

    def __call__(self, prompt, schema=None, system=None):
        if schema is not None and "glossary" in (schema.get("properties") or {}):
            self.rec("ctx", prompt, system, schema)
            return json.dumps(CTX, ensure_ascii=False)
        m = re.search(r"(?:Ulaz|Input):\n(\[.*?\])\n\n", prompt, re.S)
        items = json.loads(m.group(1))
        if items and "osnova" in items[0]:
            kind, out = "merge", [{"id": it["id"], "sr": it["osnova"]} for it in items]
        elif items and "src" in items[0] and "t" in items[0]:
            kind, out = "lektura", [{"id": it["id"], "t": it["t"]} for it in items]
        elif "Output:" in prompt:  # gen_prompt (grcki)
            kind, out = "el", [{"id": it["id"], "tr": it.get("draft") or EL[it["t"]]} for it in items]
        else:
            retry = "odbacen" in prompt
            kind = "qc" if retry else "batch"
            out = [{"id": it["id"], "sr": "" if not retry and it["id"] in self.bad else SR[it["t"]]} for it in items]
        self.rec(kind, prompt, system, schema)
        return json.dumps({"items": out}, ensure_ascii=False)

    def tg(self, prompt: str) -> str:
        """Lazan TranslateGemma (call_tg): numerisani redovi -> isti brojevi sa prevodom."""
        self.rec("tg", prompt)
        tgt = "el" if "into Greek" in prompt else "sr"
        text = prompt.split("\n\n\n", 1)[1]

        def tr(t):
            t = t.strip()
            if t in TG_TERMS:
                return TG_TERMS[t][tgt]
            return (EL if tgt == "el" else SR).get(t, t)
        rows = [re.match(r"^\s*(\d+)[.)]\s*(.+)$", ln) for ln in text.split("\n")]
        if rows and all(rows):
            return "\n".join(f"{mm.group(1)}. {tr(mm.group(2))}" for mm in rows)
        return tr(text)


@contextlib.contextmanager
def fake_models(mod, fake: Fake):
    """Podmeni make_llm i call_tg u modulu (nista ne ide na mrezu)."""
    old = (mod.make_llm, mod.call_tg)
    mod.make_llm = lambda engine, model: fake
    mod.call_tg = fake.tg
    try:
        yield
    finally:
        mod.make_llm, mod.call_tg = old


# ----------------------------------------------------------------- memorija (privremeni DATA)
def row(src, before, after, lang="sr-lat") -> dict:
    _N[0] += 1
    return {"ts": f"2026-09-{1 + _N[0] % 28:02d}T10:{_N[0] % 60:02d}:00+0200", "type": "tr", "channel": CHANNEL,
            "src_lang": "en", "video_id": "vid0", "folder": "/x/y", "file": f"subs/{lang}.x.srt", "lang": lang,
            "engine": "ollama", "model": "m", "src": src, "before": before, "after": after}


def make_data() -> Path:
    d = tdir("data")
    rows = [
        # "merač plime" -> "mareograf": termin od vise reci (pojedinacna rec bi bila samo savet modelu)
        row("Check the tide gauge now.", "Proveri merač plime sada.", "Proveri mareograf sada."),
        row("A new tide gauge is broken again.", "Novi merač plime je opet pokvaren.",
            "Novi mareograf je opet pokvaren."),
        row("Call the coast guard if the wind picks up.", "Pozovi obalsku stražu ako vetar pojača.", TM_SR),
        row("Maren is coming.", "Η Μαρέν έρχεται.", "Η Μάρεν έρχεται.", lang="el"),
        row("We are waiting for Maren.", "Περιμένουμε τη Μαρέν.", "Περιμένουμε τη Μάρεν.", lang="el"),
        row("Call the coast guard if the wind picks up.", "Καλέστε το λιμενικό αν δυναμώσει ο αέρας.", TM_EL,
            lang="el"),
    ]
    (d / "corrections.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                         encoding="utf-8")
    mem = Memory.load(d)
    for e in [{"type": "tr", "lang": "sr", "src": "Tobias", "tgt": "Tobija"},
              {"type": "tr", "lang": "sr", "src": "coast guard", "tgt": "obalska straža"},
              {"type": "tr", "lang": "sr", "src": "lighthouse", "tgt": "svetionik"},           # nema ga u videu
              {"type": "tr", "lang": "sr", "src": "relay", "tgt": "prenosnik", "scope": "channel",
               "channel": "Other TV"},                                                      # drugi kanal
              {"type": "tr", "lang": "el", "src": "coast guard", "tgt": "Λιμενικό"}]:
        mem.glossary_op("add", e)
    return d


_DATA: list[Path] = []


def views():
    if not _DATA:
        _DATA.append(make_data())
    mem = Memory.load(_DATA[0])
    return mem.view("tr", "sr", CHANNEL), mem.view("tr", "el", CHANNEL)


def empty_view(lang: str = "sr"):
    v = Memory.load(TMP / "nema-podataka").view("tr", lang, CHANNEL)
    assert v.empty
    return v


# ----------------------------------------------------------------- stari yt_arhiva.py (pre memorije)
_OLD: list = []


def old_source() -> str | None:
    """yt_arhiva.py pre uvodjenja memorije: roditelj prvog commit-a sa 'mem_report', a dok nije komitovano HEAD."""
    try:
        lg = subprocess.run(["git", "-C", str(ROOT), "log", "--format=%H", "-S", "mem_report", "--", "yt_arhiva.py"],
                            capture_output=True, text=True, timeout=30)
        if lg.returncode != 0:
            return None
        hs = lg.stdout.split()
        rev = f"{hs[-1]}^" if hs else "HEAD"
        r = subprocess.run(["git", "-C", str(ROOT), "show", f"{rev}:yt_arhiva.py"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 and "mem_report" not in r.stdout else None


def old_core():
    if not _OLD:
        src = old_source()
        mod = None
        if src:
            p = TMP / "yt_arhiva_pre_memorije.py"
            p.write_text(src, encoding="utf-8")
            spec = importlib.util.spec_from_file_location("yt_arhiva_pre_memorije", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        _OLD.append(mod)
    return _OLD[0]


# ----------------------------------------------------------------- scenariji (isti za staru i novu verziju)
def drafts_for(target: str) -> tuple[list[dict], list[dict]]:
    src = make_cues()
    if target == "el":
        dr = [{"start": c["start"], "end": c["end"], "text": EL[c["text"]]} for c in src]
    else:
        dr = [{"start": c["start"], "end": c["end"], "text": core.finalize_script(SR[c["text"]], target, [c["text"]])}
              for c in src]
    return src, dr


def run_scenario(mod, name: str, extra: dict) -> tuple[list, list]:
    """-> (pozivi modela, rezultat). extra = dodatni args (mem, mem_report)."""
    fake = Fake(bad={5})
    work = tdir("w")
    cues = make_cues()
    with fake_models(mod, fake):
        if name.startswith("sr_"):
            kw = {"sr_ollama": {}, "sr_synergy": {"engine": "synergy"},
                  "sr_cyr_noctx": {"script": "cyr", "ctx_pass": False}}[name]
            a = targs(**kw, **extra)
            mod.translate(cues, TITLE, a, work)
            res = [c["sr"] for c in cues] + [a.qc]
        elif name.startswith("el_"):
            a = targs(engine=name[3:], **extra)
            mod.translate_to(cues, TITLE, a, work, "el")
            res = [c["tr"] for c in cues]
        else:
            target = name.split("_")[1]
            src, dr = drafts_for(target)
            a = SimpleNamespace(engine="claude", model="fake", batch=10, cancelled=lambda: False, **extra)
            res = mod.review_translation(src, dr, TITLE, target, fake, a)
    return fake.calls, res


SCENARIOS = ["sr_ollama", "sr_synergy", "sr_cyr_noctx", "el_ollama", "el_synergy", "lek_lat", "lek_cyr", "lek_el"]


# ----------------------------------------------------------------- testovi
def t_no_memory_same_prompts():
    old = old_core()
    if old is None:
        print("SKIP poređenje sa starim yt_arhiva.py (git nije dostupan) — poredi se samo bez/prazna memorija")
    for name in SCENARIOS:
        base_calls, base_res = run_scenario(core, name, {})
        ok(base_calls, f"{name}: lazan model nije pozvan")
        if old is not None:
            oc, orr = run_scenario(old, name, {})
            eq(len(base_calls), len(oc), f"{name}: broj poziva modela (novo vs pre memorije)")
            for i, (x, y) in enumerate(zip(base_calls, oc)):
                eq(x, y, f"{name}: poziv {i} ({x[0]}) nije bajt-identičan staroj verziji")
            eq(base_res, orr, f"{name}: rezultat (novo vs pre memorije)")
        lang = "el" if name in ("el_ollama", "el_synergy", "lek_el") else "sr"
        for label, extra in [("mem=None", {"mem": None, "mem_report": {}}),
                             ("prazna memorija", {"mem": empty_view(lang), "mem_report": {}})]:
            c, r = run_scenario(core, name, extra)
            eq(c, base_calls, f"{name} ({label}): promptovi")
            eq(r, base_res, f"{name} ({label}): rezultat")
            eq(extra["mem_report"], {}, f"{name} ({label}): mem_report mora ostati prazan")


def t_sr_prompts_post_and_cache():
    vsr, _ = views()
    fake = Fake(bad={5})
    work = tdir("w")
    cues = make_cues()
    rep: dict = {}
    with fake_models(core, fake):
        a = targs(mem=vsr, mem_report=rep)
        core.translate(cues, TITLE, a, work)
    eq(fake.kinds(), ["ctx", "batch", "batch", "qc"], "pozivi modela")
    pa, pb = fake.prompts("batch")
    ok("Welcome back" in pa and "kitchen" in pb, "redosled blokova")
    # glosar: korisnik pobedjuje za isti src, stavke van videa / drugog kanala ne ulaze
    for p in (pa, pb):
        ok('{"src": "Tobias", "sr": "Tobija"}' in p and '{"src": "coast guard", "sr": "obalska straža"}' in p,
           "korisnički glosar u promptu")
        ok('{"src": "tide gauge", "sr": "merač plime"}' in p, "glosar modela ostaje")
        ok('"sr": "Tobias"' not in p and "lighthouse" not in p and "prenosnik" not in p, "glosar: pogrešne stavke")
        ok('Ranije ispravke korisnika — ne ponavljaj ove greške (pogrešno → ispravno): '
           '[{"pogresno": "merač plime", "ispravno": "mareograf"}]' in p, "hints u kontekstu")
        ok(p.index("Ranije ispravke korisnika") < p.index("Prevedi titlove iz Ulaza"), "hints u nepromenljivom delu")
    eq(pa[:pa.index("Prethodni kontekst")], pb[:pb.index("Prethodni kontekst")], "nepromenljiv prefiks (Ollama keš)")
    ok(between(pa, "Primeri ranijih ispravki korisnika (uči stil", "Prethodni kontekst", "\nUlaz:"),
       "primeri u promenljivom delu, pre Ulaza")
    ok("Proveri mareograf sada." in pa and TM_SR in pa, "primeri bloka A")
    ok("Primeri ranijih" not in pb, "blok B nema slične ispravke -> bez primera")
    qp = fake.prompts("qc")[0]
    ok("Nobody touches the old tide gauge." in qp and between(qp, "Primeri ranijih ispravki", "Okolni titlovi", "\nUlaz:"),
       "QC ponovni prevod dobija primere")
    ok("Ranije ispravke korisnika" in qp and "Tobija" in qp, "QC prompt ima glosar i hints")
    # post: TM + pravila (i veliko slovo), ostalo netaknuto
    got = [c["sr"] for c in cues]
    want = [sr for _, sr, _ in LINES]
    want[1] = "Mareograf treba proveriti pre nego što Maren stigne."
    want[4] = TM_SR
    want[5] = "Niko ne dira stari mareograf."
    eq(got, want, "prevod posle memorije")
    eq(rep, {"examples": 1, "glossary": 2, "hints": 1, "exact": 1, "rules": 2}, "mem_report")
    # kes ostaje cist prevod modela; context.json bez memorije
    cache = core.load_json(work / "prevod_cache_ollama_fake_1b_lat.json")
    eq(cache["1"], LINES[1][1], "keš: linija 1")
    eq(cache["4"], LINES[4][1], "keš: TM linija")
    ok(not any("areograf" in str(v) for v in cache.values()), "keš ne sme imati memorijske zamene")
    ctxf = (work / core.CTX_FILE).read_text(encoding="utf-8")
    ok("Tobija" not in ctxf and "hints" not in ctxf and "plimomer" not in ctxf, "context.json bez memorije")
    # drugi put: sve iz kesa (bez modela), memorija se primenjuje ponovo
    fake.calls.clear()
    c2 = make_cues()
    rep2: dict = {}
    with fake_models(core, fake):
        core.translate(c2, TITLE, targs(mem=vsr, mem_report=rep2), work)
    eq(fake.calls, [], "iz keša: bez poziva modela")
    eq([c["sr"] for c in c2], want, "iz keša: memorija primenjena")
    # sve iz kesa: prompt nije poslat -> glosar/pravila "u promptu" = 0; TM i pravila posle prevoda rade
    eq(rep2, {"examples": 0, "glossary": 0, "hints": 0, "exact": 1, "rules": 2}, "mem_report iz keša")
    # isti kes bez memorije -> cist prevod modela
    c3 = make_cues()
    with fake_models(core, fake):
        core.translate(c3, TITLE, targs(), work)
    eq([c["sr"] for c in c3], [sr for _, sr, _ in LINES], "bez memorije: čist prevod iz keša")


def t_synergy_merge():
    vsr, _ = views()
    fake = Fake()
    work = tdir("w")
    cues = make_cues()
    rep: dict = {}
    with fake_models(core, fake):
        core.translate(cues, TITLE, targs(engine="synergy", mem=vsr, mem_report=rep), work)
    eq(fake.kinds().count("merge"), 2, "merge_c po bloku")
    ma, mb = fake.prompts("merge")
    ok(between(ma, "Primeri ranijih ispravki korisnika", "Ne seli tekst izmedju id-jeva.", "Ulaz:"),
       "merge_c: primeri pre Ulaza")
    ok("Primeri ranijih" not in mb, "merge_c blok B bez primera")
    ok("Ranije ispravke korisnika" in ma and '{"src": "Tobias", "sr": "Tobija"}' in ma, "merge_c: hints + glosar")
    ok('{"src": "tide gauge", "sr": "merilo plime"}' in ma, "glosar modela ide kroz TranslateGemma")
    ok("Primeri ranijih ispravki" in fake.prompts("batch")[0], "Gemma prevod bloka A dobija primere")
    # korisnicki glosar NE ide kroz TranslateGemma
    tgg = core.load_json(work / "tg_glossary.json")
    eq(sorted(tgg.get("tr", {})), ["tide gauge"], "tg_glossary: samo glosar modela")
    ok(not any("Tobija" in p or "obalska" in p for p in fake.prompts("tg")), "TranslateGemma ne vidi korisnički glosar")
    eq(cues[1]["sr"], "Mareograf treba proveriti pre nego što Maren stigne.", "synergy: pravilo")
    eq(cues[4]["sr"], TM_SR, "synergy: TM")
    eq(rep, {"examples": 1, "glossary": 2, "hints": 1, "exact": 1, "rules": 2}, "mem_report synergy")


def t_cyr_without_context():
    vsr, _ = views()
    fake = Fake()
    work = tdir("w")
    cues = make_cues()
    rep: dict = {}
    with fake_models(core, fake):
        core.translate(cues, TITLE, targs(script="cyr", ctx_pass=False, mem=vsr, mem_report=rep), work)
    eq(fake.kinds(), ["batch", "batch"], "bez konteksta: nema ctx poziva")
    pa = fake.prompts("batch")[0]
    ok("Glosar — kad se ovo pojavi" in pa and "Tobija" in pa and "Ranije ispravke korisnika" in pa,
       "bez konteksta modela: minimalan kontekst sa glosarom i hints")
    ok("sinopsis" not in pa and "Registar" not in pa, "minimalan kontekst nema sinopsis/registar")
    cache = core.load_json(work / "prevod_cache_ollama_fake_1b_cyr.json")
    src = [c["text"] for c in cues]
    eq(cues[1]["sr"], core.finalize_script("Mareograf treba proveriti pre nego što Maren stigne.", "cyr", [src[1]]),
       "ćirilica: pravilo u latinici pa nazad")
    ok("Мареограф" in cues[1]["sr"] and "Maren" in cues[1]["sr"], "ćirilica + ime iz izvora ostaje latinicom")
    eq(cues[4]["sr"], core.finalize_script(TM_SR, "cyr", [src[4]]), "ćirilica: TM")
    eq(cues[5]["sr"], core.finalize_script("Niko ne dira stari mareograf.", "cyr", [src[5]]), "ćirilica: pravilo 2")
    for i, c in enumerate(cues):
        if i not in (1, 4, 5):
            eq(c["sr"], cache[str(i)], f"ćirilica: linija {i} bez promene ostaje kao u kešu")
    ok("Мерач плиме" in cache["1"] and not any("ареограф" in str(v) for v in cache.values()), "ćirilica: keš čist")
    eq(rep, {"examples": 1, "glossary": 2, "hints": 1, "exact": 1, "rules": 2}, "mem_report ćirilica")


def t_el_translate_to():
    _, vel = views()
    fake = Fake()
    work = tdir("w")
    cues = make_cues()
    rep: dict = {}
    with fake_models(core, fake):
        core.translate_to(cues, TITLE, targs(mem=vel, mem_report=rep), work, "el")
    ga, gb = fake.prompts("el")
    for p in (ga, gb):
        ok('Glossary (always translate exactly like this): [{"src": "coast guard", "tr": "Λιμενικό"}]' in p,
           "el: glosar")
        ok('[{"wrong": "Μαρέν", "right": "Μάρεν"}]' in p, "el: naučena pravila")
        ok(p.index("Glossary") < p.index("Previous subtitles"), "el: glosar u nepromenljivom delu")
    ok(between(ga, "Examples of the user's earlier corrections", "Every id exactly once", "Input:"),
       "el: primeri pre Input")
    ok("Η Μάρεν έρχεται." in ga and '"source": "Maren is coming."' in ga, "el: sadržaj primera")
    ok("Examples of the user's" not in gb, "el: blok B bez primera")
    ok("obalska" not in ga, "el: srpski glosar ne ulazi u grčki")
    eq(cues[1]["tr"], core.wrap_lines("Ο παλιρροιογράφος θέλει έλεγχο πριν έρθει η Μάρεν."), "el: pravilo")
    eq(cues[6]["tr"], core.wrap_lines("Η Μάρεν θέλει πλήρη αναφορά μέχρι την Παρασκευή."), "el: pravilo 2")
    eq(cues[4]["tr"], core.wrap_lines(TM_EL), "el: TM")
    cache = core.load_json(work / "prevod_el_ollama_fake_1b.json")
    ok("Μαρέν" in cache["1"] and not any("Μάρεν" in str(v) for v in cache.values()), "el: keš čist")
    eq(rep, {"examples": 1, "glossary": 1, "hints": 1, "exact": 1, "rules": 2}, "mem_report el")
    # samo TranslateGemma (bez gen_prompt): memorija se ipak primenjuje na kraju
    fake2 = Fake()
    c2 = make_cues()
    rep2: dict = {}
    with fake_models(core, fake2):
        core.translate_to(c2, TITLE, targs(engine="tg", mem=vel, mem_report=rep2), tdir("w"), "el")
    ok(set(fake2.kinds()) == {"tg"}, f"tg: samo TranslateGemma ({fake2.kinds()})")
    eq(c2[1]["tr"], cues[1]["tr"], "tg: pravilo")
    eq(c2[4]["tr"], cues[4]["tr"], "tg: TM")
    eq(rep2, {"examples": 0, "glossary": 0, "hints": 0, "exact": 1, "rules": 2}, "mem_report tg (bez prompta)")


def t_lektura():
    vsr, vel = views()
    for target in ("lat", "cyr"):
        fake = Fake()
        src, dr = drafts_for(target)
        rep: dict = {}
        a = SimpleNamespace(engine="claude", model="fake", batch=10, cancelled=lambda: False, mem=vsr, mem_report=rep)
        out = core.review_translation(src, dr, TITLE, target, fake, a)
        la, lb = fake.prompts("lektura")
        for p in (la, lb):
            ok("Glosar korisnika — imena i termine piši TAČNO ovako" in p and '{"src": "Tobias", "sr": "Tobija"}' in p,
               f"lektura {target}: glosar")
            ok(p.index("Glosar korisnika") < p.index("Prethodni (vec lektorisani)"), "lektura: glosar pre konteksta")
            ok("Ranije ispravke korisnika" not in p, "lektura: bez hints (ugovor: primeri + glosar)")
        ok(between(la, "Primeri ranijih ispravki korisnika", "Imena i termine drzi dosledno", "\nUlaz:"),
           f"lektura {target}: primeri pre Ulaza")
        ok("Primeri ranijih" not in lb, f"lektura {target}: blok B bez primera")
        eq(out[1], core.finalize_script("Mareograf treba proveriti pre nego što Maren stigne.", target,
                                        [src[1]["text"]]), f"lektura {target}: pravilo")
        eq(out[5], core.finalize_script("Niko ne dira stari mareograf.", target, [src[5]["text"]]),
           f"lektura {target}: pravilo 2")
        eq(out[4], dr[4]["text"], f"lektura {target}: bez TM (samo pravila)")
        eq(rep, {"examples": 1, "glossary": 2, "hints": 0, "exact": 0, "rules": 2}, f"mem_report lektura {target}")
    fake = Fake()
    src, dr = drafts_for("el")
    rep = {}
    a = SimpleNamespace(engine="claude", model="fake", batch=10, cancelled=lambda: False, mem=vel, mem_report=rep)
    out = core.review_translation(src, dr, TITLE, "el", fake, a)
    la = fake.prompts("lektura")[0]
    ok('Glossary (always translate exactly like this): [{"src": "coast guard", "tr": "Λιμενικό"}]' in la,
       "lektura el: glosar")
    ok(between(la, "Examples of the user's earlier corrections", "Greek script", "Ulaz:"), "lektura el: primeri")
    eq(out[1], "Ο παλιρροιογράφος θέλει έλεγχο πριν έρθει η Μάρεν.", "lektura el: pravilo")
    eq(rep, {"examples": 1, "glossary": 1, "hints": 0, "exact": 0, "rules": 2}, "mem_report lektura el")


def t_cyr_names_from_whole_source():
    """Ime koje je model stavio u susedni titl (recenica preko dva titla) ostaje latinicom i posle memorije."""
    vsr, _ = views()
    cues = [{"start": 0, "end": 1000, "text": "The new tide gauge from"},
            {"start": 1100, "end": 2000, "text": "Siemens arrived today."}]
    out = {"The new tide gauge from": "Novi merač plime firme Siemens", "Siemens arrived today.": "stigao je danas."}

    class F(Fake):
        def __call__(self, prompt, schema=None, system=None):
            items = json.loads(re.search(r"Ulaz:\n(\[.*?\])\n\n", prompt, re.S).group(1))
            self.rec("batch", prompt)
            return json.dumps({"items": [{"id": it["id"], "sr": out[it["t"]]} for it in items]}, ensure_ascii=False)
    res = {}
    for label, extra in [("bez", {}), ("sa", {"mem": vsr, "mem_report": {}})]:
        cc = [dict(c) for c in cues]
        with fake_models(core, F()):
            core.translate(cc, "Plima", targs(script="cyr", ctx_pass=False, qc_retry=False, **extra), tdir("w"))
        res[label] = [c["sr"] for c in cc]
    ok("Siemens" in res["bez"][0], f"bez memorije ime ostaje latinicom: {res['bez']}")
    eq(res["sa"][0], res["bez"][0].replace("мерач плиме", "мареограф"), "sa memorijom: samo pravilo, ime ostaje")
    eq(res["sa"][0], "Нови мареограф фирме Siemens", "ćirilica + ime iz susednog titla")
    # mem_post: linija bez cirilice ostaje latinicna; reci koje je linija vec imala latinicom ostaju
    eq(core.mem_post(vsr, "", "Siemens merač plime", "cyr")[0], "Siemens mareograf", "latinična linija ostaje")
    eq(core.mem_post(vsr, "", "Нови merač plime и online", "cyr")[0], "Нови мареограф и online", "online ostaje")


def t_keep_lines_cache():
    """keep_lines (panel): promena jedne linije izvora prevodi ponovo samo njen blok, ne ceo video."""
    fake = Fake()
    work = tdir("w")
    with fake_models(core, fake):
        core.translate(make_cues(), TITLE, targs(keep_lines=True), work)
        n1 = fake.kinds().count("batch")
        fake.calls.clear()
        cues = make_cues()
        cues[12]["text"] = "She uses flour from the valley!"  # scena B
        SR[cues[12]["text"]] = "Koristi brašno iz doline!"
        try:
            core.translate(cues, TITLE, targs(keep_lines=True), work)
        finally:
            SR.pop(cues[12]["text"], None)
    eq(n1, 2, "prvi prevod: 2 bloka")
    # kontekst je vezan za prve titlove (CTX_LINES) pa se gradi ponovo; prevodi se samo blok sa promenjenom linijom
    eq(fake.kinds(), ["ctx", "batch"], "posle promene jedne linije: samo njen blok")
    ok("kitchen" in fake.prompts("batch")[0] and "Welcome back" not in fake.prompts("batch")[0], "blok B")
    eq(cues[12]["sr"], "Koristi brašno iz doline!", "nova linija prevedena")
    eq(cues[1]["sr"], LINES[1][1], "ostale linije iz keša")
    # bez keep_lines (CLI): kao ranije, ceo prevod ispocetka
    fake2 = Fake()
    with fake_models(core, fake2):
        c2 = make_cues()
        c2[12]["text"] = "She uses flour from the valley?"
        SR[c2[12]["text"]] = "Koristi brašno iz doline?"
        try:
            core.translate(c2, TITLE, targs(), work)
        finally:
            SR.pop(c2[12]["text"], None)
    eq(fake2.kinds().count("batch"), 2, "bez keep_lines: ceo prevod")


def t_helpers():
    # mem_context: bez memorije isti objekat; korisnik pobedjuje (bez obzira na velika slova); minimalan kontekst
    base = {"synopsis": "S", "glossary": [{"src": "Tide Gauge", "sr": "x"}, {"src": "Maren", "sr": "Maren"}],
            "register": "vi"}
    ok(core.mem_context(base, [], []) is base and core.mem_context(None, [], []) is None, "mem_context bez memorije")
    m = core.mem_context(base, [{"src": "tide gauge", "sr": "mareograf"}], [{"pogresno": "a", "ispravno": "b"}])
    eq(m["glossary"], [{"src": "tide gauge", "sr": "mareograf"}, {"src": "Maren", "sr": "Maren"}], "korisnik pobeđuje")
    eq((m["synopsis"], m["register"], m["hints"]), ("S", "vi", [{"pogresno": "a", "ispravno": "b"}]), "ostatak konteksta")
    ok("hints" not in base and len(base["glossary"]) == 2, "mem_context ne menja ulaz")
    eq(core.mem_context(None, [], [{"pogresno": "a", "ispravno": "b"}]),
       {"synopsis": "", "glossary": [], "register": "", "hints": [{"pogresno": "a", "ispravno": "b"}]},
       "minimalan kontekst")
    cb = core.context_block({"synopsis": "", "glossary": [], "register": "", "hints": [{"pogresno": "a", "ispravno": "b"}]})
    eq(cb, 'Ranije ispravke korisnika — ne ponavljaj ove greške (pogrešno → ispravno): '
           '[{"pogresno": "a", "ispravno": "b"}]\n', "context_block sa hints")
    # build_prompt / gen_prompt / lektura_prompt: prazni primeri = isti prompt
    b = [{"id": 0, "text": "Hello"}]
    eq(core.build_prompt(b, ["x"], "t", "lat", examples=[]), core.build_prompt(b, ["x"], "t", "lat"), "build_prompt")
    eq(core.gen_prompt(b, [], "t", "el", examples=[], glossary=[], hints=[]), core.gen_prompt(b, [], "t", "el"),
       "gen_prompt")
    it = [{"id": 0, "src": "Hello", "t": "Zdravo"}]
    eq(core.lektura_prompt(it, [], "t", "sr", examples=None, glossary=[]), core.lektura_prompt(it, [], "t", "sr"),
       "lektura_prompt")
    eq(core.examples_block([]), "", "prazni primeri")
    ok(core.mem_of(SimpleNamespace()) is None and core.mem_of(SimpleNamespace(mem=None)) is None, "mem_of bez mem")
    ok(core.mem_of(SimpleNamespace(mem=empty_view())) is None, "mem_of: prazna memorija = bez memorije")
    # mem_post: cirilica se dira samo kad ima promene; TM isti kao prevod se ne broji
    vsr, _ = views()
    cyr = core.finalize_script("Dobro došli nazad u lučku stanicu.", "cyr", [])
    t, a_, r_ = core.mem_post(vsr, LINES[0][0], cyr, "cyr")
    ok(t is cyr and (a_, r_) == (0, 0), "mem_post: bez promene isti tekst")
    eq(core.mem_post(vsr, LINES[4][0], TM_SR, "lat"), (TM_SR, 0, 0), "mem_post: TM jednak prevodu")
    eq(core.mem_post(vsr, LINES[4][0], "x", "lat"), (TM_SR, 1, 0), "mem_post: TM")
    eq(core.mem_post(vsr, LINES[4][0], "x", "lat", use_exact=False), ("x", 0, 0), "mem_post: bez TM (lektura)")
    eq(core.mem_post(vsr, "", "stari merač plime i Merač plime", "lat"), ("stari mareograf i Mareograf", 0, 2),
       "mem_post: pravila")
    # mem_report samo ako je dict
    core.mem_report(SimpleNamespace(), rules=1)
    core.mem_report(SimpleNamespace(mem_report=None), rules=1)
    d: dict = {"x": 1}
    core.mem_report(SimpleNamespace(mem_report=d), rules=2)
    eq(d, {"x": 1, "rules": 2}, "mem_report dopunjuje dict")


# ----------------------------------------------------------------- main
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
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):  # log() iz yt_arhiva ide na stdout
                    f()
                skip = [ln for ln in buf.getvalue().splitlines() if ln.startswith("SKIP")]
                print(f"PASS  {n[2:]:<32} {time.time() - t:5.2f}s" + (f"  ({skip[0]})" if skip else ""))
            except Exception as e:  # noqa: BLE001
                fails += 1
                msg = str(e) if isinstance(e, AssertionError) else traceback.format_exc()
                print(f"FAIL  {n[2:]:<32} {time.time() - t:5.2f}s  {msg}\n{buf.getvalue()[-1500:]}")
    finally:
        if not os.environ.get("YTA_TEST_KEEP"):
            shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{len(tests) - fails}/{len(tests)} prošlo za {time.time() - t_all:.1f}s")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
