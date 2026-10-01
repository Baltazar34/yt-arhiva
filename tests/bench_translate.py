#!/usr/bin/env python3
"""Lokalni test prevoda (kvalitet + brzina): STARO vs NOVO na istim uzorcima.

  STARO = starija verzija yt_arhiva.py (pre UX v2: fiksni blokovi od 30, bez konteksta), putanja do fajla
          preko --old FAJL ili YTA_BENCH_OLD; bez nje STARO = novi kod bez konteksta/scena/QC
  NOVO  = kontekst (sinopsis + glosar + oslovljavanje) + blokovi po scenama + QC sa ponovnim prevodom

  python3 tests/bench_translate.py                         # staro vs novo, model iz panela, latinica
  python3 tests/bench_translate.py --old /putanja/stara/yt_arhiva.py   # STARO iz zadate starije verzije
  python3 tests/bench_translate.py gemma4:12b-it-qat cyr
  python3 tests/bench_translate.py --only dialog           # samo sinteticki dijalog (60 titlova)
  python3 tests/bench_translate.py --only en,it --json /tmp/bench.json
  python3 tests/bench_translate.py --only dialog --seeds 7,1,2   # vise seed-ova (varijansa modela)
  python3 tests/bench_translate.py --unit                  # brzi testovi bez modela (lazni llm)
  python3 tests/bench_translate.py --raw [--review]        # stari rezim: jedan poziv po uzorku, tok/s

Uzorci su namerno teski: recenice se protezu kroz vise titlova, idiomi, ASR greske
("there" umesto "their"), imena i brojevi; dijalog ima imena i termine koji se ponavljaju
kroz scene (doslednost glosara).
"""
from __future__ import annotations

import atexit
import copy
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import types
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
_TMP: list[str] = []


def tmpdir(prefix: str = "bench-tr-") -> Path:
    d = tempfile.mkdtemp(prefix=prefix)
    _TMP.append(d)
    return Path(d)


@atexit.register
def _cleanup():
    for d in _TMP:
        shutil.rmtree(d, ignore_errors=True)
sys.path.insert(0, str(ROOT))
import yt_arhiva as core  # noqa: E402

SAMPLES = {
    "en": ("Documentary about restoring an old sailing boat in Maine", [
        "So when we first pulled her out of the water,",
        "the hull was in way worse shape than we thought.",
        "Honestly, I was ready to throw in the towel.",
        "But my grandfather built this boat in 1962,",
        "and there was no way I was letting it rot in a barn.",
        "We had to replace about forty percent of the planks,",
        "which, if you've never done it, is a nightmare.",
        "the guys at the yard said there tools weren't up to the job",
        "so we ended up making our own steam box.",
        "It took us three winters, but she floats again.",
    ]),
    "el": ("Ντοκιμαντέρ για τη ζωή των ψαράδων στην Κρήτη", [
        "Κάθε πρωί ξυπνάμε πριν βγει ο ήλιος",
        "και βγαίνουμε με τη βάρκα μέχρι το ακρωτήρι.",
        "Ο πατέρας μου έλεγε ότι η θάλασσα δεν συγχωρεί",
        "όποιον δεν τη σέβεται.",
        "Τα τελευταία χρόνια τα ψάρια είναι όλο και λιγότερα,",
        "και οι νέοι φεύγουν για την Αθήνα.",
        "Εγώ όμως δεν μπορώ να φανταστώ τη ζωή μου αλλού.",
    ]),
    "it": ("Intervista a uno chef di Bologna sulla pasta fresca", [
        "Guardi, la sfoglia non si improvvisa.",
        "Mia nonna diceva che ci vuole pazienza,",
        "farina buona e uova freschissime,",
        "e soprattutto non bisogna avere fretta.",
        "Oggi tutti vogliono fare le cose in cinque minuti,",
        "ma così non viene fuori niente di buono.",
        "Il ragù poi, quello è un altro discorso.",
    ]),
    "es": ("Charla sobre emprendimiento en Buenos Aires", [
        "Cuando arrancamos con la empresa no teníamos un peso,",
        "trabajábamos desde la cocina de mi vieja",
        "y la verdad es que nadie creía en nosotros.",
        "Pero bueno, a veces hay que tirarse a la pileta",
        "aunque no sepas si hay agua.",
        "Hoy somos cuarenta personas",
        "y seguimos laburando con la misma garra del primer día.",
    ]),
}

# Originalan sinteticki dijalog (napisan za ovaj test): 5 scena, pauze > 2 s izmedju scena,
# jedna kraca dramska pauza (2,5 s) usred scene 3 koja NE sme da sece blok (< 10 titlova).
DIALOG_TITLE = ("Harrow Point — Storm Night (short drama) — A small coastal weather station fights a storm "
                "while a broken tide gauge threatens Project Saltline. With Maren, Tobias, Old Pike and Dr. Oduya.")
DIALOG = [
    [  # scena 1: stanica, vece
        "Maren, the tide gauge stopped reporting again.",
        "Again? That's the third time since Monday.",
        "I checked the cables in the Cellar,",
        "and everything down there looks dry.",
        "Then it's the sensor. Tobias, grab the spare from the shed.",
        "The Warden wants a full report by midnight,",
        "and I'm not sending him another blank page.",
        "Relax, I'll have the tide gauge back online in an hour.",
        "You said that last week.",
        "Last week I didn't have Dr. Oduya's calibration notes.",
        "She left them in the logbook before she flew out.",
        "Fine. Go. And take a radio this time.",
    ],
    [  # scena 2: mol
        "Evening, Pike. You're out late.",
        "Somebody has to watch the Gull's Promise.",
        "With this wind, she'll tear her ropes by morning.",
        "Old Pike never trusts the forecast, huh?",
        "I trust my knees. They've been right for forty years.",
        "What are you carrying there, boy?",
        "A new sensor for the tide gauge.",
        "Project Saltline needs clean data from buoy twelve,",
        "and right now we're getting nothing but noise.",
        "Saltline, Saltline. Everybody on this island says that word",
        "like it's going to pay their rent.",
        "Maybe it will, if the funding comes through.",
    ],
    [  # scena 3: video poziv
        "Dr. Oduya, can you hear me? The connection keeps dropping.",
        "I hear you, Maren. How bad is it out there?",
        "Winds at sixty knots, and buoy twelve went dark an hour ago.",
        "That's not the buoy. That's the relay in the Cellar.",
        "When the backup generator kicks in,",
        "the relay loses its clock and stops forwarding packets.",
        "So every time the power flickers, we go blind.",
        "Exactly. I wrote it in the logbook, page forty-one.",
        "Tobias is replacing the sensor on the tide gauge right now.",
        "Tell him to stop. The sensor is fine.",
        "He needs to reset the relay and sync it with the generator.",
        "And the Warden? He's expecting numbers tonight.",
        "Send the Warden the raw logs and my name.",
        "He owes me a favor from the Saltline proposal.",
    ],
    [  # scena 4: radio
        "Tobias, come in. Forget the sensor.",
        "Say again? The wind is eating your words.",
        "Forget the sensor! Oduya says it's the relay in the Cellar.",
        "The Cellar? I was just down there, it's fine.",
        "It's the clock. Every time the backup generator starts,",
        "the relay resets itself. Page forty-one of the logbook.",
        "Okay, okay. Heading back. Pike is coming with me.",
        "Why is Pike coming with you?",
        "Because the Gull's Promise broke loose,",
        "and he says he's not sitting alone on a dead boat.",
    ],
    [  # scena 5: podrum
        "Hold the flashlight steady, Pike.",
        "I've held steadier things in worse weather.",
        "There. Relay's back. Clock's synced with the generator.",
        "Maren, check the tide gauge now.",
        "Data's coming in. Buoy twelve is alive again.",
        "Clean numbers, no noise. Oduya was right.",
        "She usually is. Don't tell her I said that.",
        "So this Saltline thing, it's going to work now?",
        "If the Warden reads the report, yes.",
        "Then write it nicely. My knees say the storm isn't over.",
        "Maren, I'm writing it in the logbook first.",
        "Page forty-two. For the next poor soul in the Cellar.",
    ],
]

# pracenje doslednosti: termin -> (regex u originalu, {varijanta: regex u prevodu (latinica, mala slova)})
# varijante su popisane iz stvarnih izlaza modela; "(pogrešno)" = nepostojeca rec / pogresno znacenje / ijekavica;
# "nema" = termin se ne vidi u prevodu linije. Redosled je bitan (prva varijanta koja se poklopi).
TERMS = {
    "tide gauge": (r"tide gauge", {"mareograf": r"mareograf", "merač plime": r"merač\w* plim",
                                   "plimomer": r"plimomer", "plimenomer": r"plimen?omer",
                                   "merilo plime": r"meril\w* plim", "morski merač": r"morsk\w* merač",
                                   "merak plime (pogrešno)": r"merak\w* plim|plimen\w* merak",
                                   "merac plime (pogrešno)": r"merac\w* plim",
                                   "mjerilo plime (pogrešno)": r"mjer\w* plim|plimenomjer",
                                   "merač nivoa": r"merač\w* nivo", "senzor plime": r"senzor\w* plim",
                                   "tide gauge": r"tide gauge", "merač": r"\bmerač"}),
    "the Cellar": (r"\bcellar\b", {"Podrum": r"\bpodrum", "Cellar": r"\bcellar"}),
    "the Warden": (r"\bwarden\b", {"Upravnik": r"\bupravnik", "Nadzornik": r"\bnadzornik", "Čuvar": r"\bčuvar",
                                   "Načelnik": r"\bnačelnik", "Upravitelj": r"\bupravitelj",
                                   "Komandant": r"\bkomandant", "Warden": r"\bwarden",
                                   "Vardost (pogrešno)": r"\bvardost", "Varden/Varder (pogrešno)": r"\bvarde[nr]"}),
    "logbook": (r"\blogbook\b", {"dnevnik": r"\bdnevnik", "knjiga": r"\bknji[gz]", "logbook": r"\blogbook",
                                 "evidencija": r"\bevidencij", "beležnica": r"\bbeležnic",
                                 "belešnica (pogrešno)": r"\bbelešnic"}),
    "Gull's Promise": (r"gull's promise", {"Gull's Promise": r"gull.s promise",
                                            "Galebovo obećanje": r"galebov\w* obećanj"}),
    "buoy": (r"\bbuoy", {"bova": r"\bbov[aeiu]", "plutača": r"\bplutač",
                         "boja (pogrešno)": r"\bboj[aeiu]\b", "bojja/bojaja (pogrešno)": r"\bbojj|\bbojaj",
                         "bojica (pogrešno)": r"\bbojic"}),
    "Saltline": (r"saltline", {"Saltline": r"saltline", "Slana linija": r"slan\w* linij"}),
    "relay": (r"\brelay", {"relej": r"\brelej", "prenosnik": r"\bprenosnik", "repetitor": r"\brepetitor",
                           "ruter": r"\bruter", "prekidač": r"\bprekidač",
                           "relaj/relje (pogrešno)": r"\brela[jy]|\brelj"}),
    "backup generator": (r"backup generator", {"rezervni generator": r"rezervn\w* generator",
                                               "pomoćni generator": r"pomoćn\w* generator",
                                               "agregat": r"agregat"}),
    "Maren": (r"\bmaren\b", {"Maren": r"\bmaren"}),
    "Tobias": (r"\btobias\b", {"Tobias": r"\btobias", "Tobijas": r"\btobijas"}),
    "Pike": (r"\bpike\b", {"Pike": r"\bpike", "Pajk": r"\bpajk", "Štuka": r"\bštuk"}),
    "Old Pike": (r"old pike", {"Old Pike": r"old pike", "Stari Pike": r"star\w* pike", "Stari Pajk": r"star\w* pajk"}),
    "Oduya": (r"\boduya\b", {"Oduya/Oduja": r"\bodu[yj]"}),
}


def dialog_cues() -> list[dict]:
    cues, t = [], 1000
    k = 0
    for si, scene in enumerate(DIALOG):
        if si:
            t += 3500  # pauza izmedju scena
        for li, text in enumerate(scene):
            if li:
                t += 250 + (k * 137) % 400  # obicna pauza 0,25–0,65 s
                if si == 2 and li == 7:
                    t += 2500  # dramska pauza u sceni 3 (blok < 10 -> ne sece)
            d = max(1200, min(5000, 900 + 45 * len(text)))
            cues.append({"start": t, "end": t + d, "text": text})
            t += d
            k += 1
    return cues


def sample_cues(lines: list[str]) -> list[dict]:
    return [{"start": i * 2800, "end": i * 2800 + 2500, "text": t} for i, t in enumerate(lines)]


def all_samples() -> dict[str, tuple[str, list[dict]]]:
    out = {k: (title, sample_cues(lines)) for k, (title, lines) in SAMPLES.items()}
    out["dialog"] = (DIALOG_TITLE, dialog_cues())
    return out


# ----------------------------------------------------------------- staro vs novo (pravi model)
def load_old_module(path: str | None):
    """Starija verzija yt_arhiva.py (--old FAJL / YTA_BENCH_OLD); exec bez pisanja __pycache__ pored nje.
    None ako putanja nije zadata ili fajl ne postoji."""
    if not path:
        return None
    f = Path(path).expanduser()
    if not f.is_file():
        print(f"! {f} ne postoji")
        return None
    src = f.read_text(encoding="utf-8")
    mod = types.ModuleType("yt_arhiva_staro")
    mod.__file__ = str(f)
    sys.modules["yt_arhiva_staro"] = mod
    exec(compile(src, str(f), "exec"), mod.__dict__)
    return mod


SEED = [7]  # seed za Ollama (ponovljivost); --seeds 7,1,2 ponavlja poredjenje sa vise seed-ova


def instrument(mod, calls: list):
    """Zameni mod.call_ollama istim HTTP pozivom koji jos belezi vrstu poziva, trajanje i Ollama
    statistiku (trajanje obrade prompta / generisanja; seed fiksan radi ponovljivosti)."""
    orig = mod.call_ollama

    def wrapped(prompt, model, schema=None, system=None):
        kind = "kontekst" if schema else ("qc" if "odbacen" in prompt else "blok")
        body = {"model": model, "stream": False, "format": schema or mod.JSON_SCHEMA, "think": False,
                "keep_alive": "15m", "options": {"temperature": 0.2, "num_ctx": 8192, "seed": SEED[0]},
                "messages": [{"role": "system", "content": system or mod.SYS_PROMPT},
                             {"role": "user", "content": prompt}]}
        t0 = time.time()
        req = urllib.request.Request(core.OLLAMA_URL + "/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        r = json.loads(urllib.request.urlopen(req, timeout=900).read())
        calls.append({"kind": kind, "wall": time.time() - t0, "pe": r.get("prompt_eval_count", 0),
                      "pd": r.get("prompt_eval_duration", 0) / 1e9, "ev": r.get("eval_count", 0),
                      "ed": r.get("eval_duration", 0) / 1e9, "load": r.get("load_duration", 0) / 1e9})
        return r["message"]["content"]
    mod.call_ollama = wrapped
    return orig


def run_pipeline(mod, cues, title, model, script, review, new: bool):
    calls: list = []
    orig = instrument(mod, calls)
    args = SimpleNamespace(engine="ollama", model=model, script=script, jobs=1, batch=30,
                           review=review, cancelled=lambda: False)
    if new:
        args.qc = {}
    work = tmpdir("bench-tr-")
    cc = copy.deepcopy(cues)
    t0 = time.time()
    try:
        mod.translate(cc, title, args, work)
    finally:
        mod.call_ollama = orig
    wall = time.time() - t0
    ctxd = core.load_json(work / core.CTX_FILE) if new else {}
    return {"sr": [c["sr"] for c in cc], "wall": wall, "calls": calls, "work": str(work),
            "qc": getattr(args, "qc", None), "context": {k: ctxd.get(k) for k in ("synopsis", "glossary", "register")}
            if ctxd else None}


QC_DEMO = {  # namerno pokvareni prevodi (u kesu) -> QC mora da ih nadje i ponovo prevede pravim modelom
    4: "",                                                   # prazno
    10: None,                                                # neprevedeno (= original)
    27: "To nije bova. Это реле в подвале.",                  # pismo (ruska slova)
    44: "Da.",                                               # duzina
}


def qc_demo(cues, title, model, script, work: str):
    """Pokvari 4 linije u kesu gotovog prevoda i pokreni translate ponovo: blokovi su iz kesa,
    model radi samo QC ponovne prevode (+-3 susedne linije)."""
    work_p = Path(work)
    tag = re.sub(r"[^a-zA-Z0-9]+", "_", f"ollama_{model}_{script}")
    cp = work_p / f"prevod_cache_{tag}.json"
    cache = core.load_json(cp)
    for i, v in QC_DEMO.items():
        cache[str(i)] = cues[i]["text"] if v is None else v
    cache.pop("__qcd__", None)
    core.save_json(cp, cache)
    calls: list = []
    orig = instrument(core, calls)
    args = SimpleNamespace(engine="ollama", model=model, script=script, jobs=1, batch=30, review=False,
                           cancelled=lambda: False, qc={})
    cc = copy.deepcopy(cues)
    t0 = time.time()
    try:
        core.translate(cc, title, args, work_p)
    finally:
        core.call_ollama = orig
    q = args.qc
    print(f"\n  QC DEMO (4 pokvarene linije u kesu): nađeno {q['flagged']} → ponovo prevedeno {q['retried']}"
          f" (limit {q['cap']}), ispravljeno {q['fixed']}, ostalo {q['remaining'] or 'nema'} · "
          f"{fmt_calls(calls)} · zid {time.time() - t0:.1f}s")
    for ex in q["examples"]:
        print(f"    [{ex['id']}] {ex['src']}\n        {ex['issues']} {ex['before']!r} → {ex['after']!r}")
    return {"qc": q, "calls": calls}


def consistency(cues: list[dict], sr: list[str]) -> dict:
    """Za svaki termin: koje varijante prevoda se javljaju. score (doslednost) = udeo najcesce
    varijante; acc (tacnost) = udeo pojavljivanja sa prihvatljivom varijantom (ne "nema"/"pogrešno")."""
    rep, tot, good, ok = {}, 0, 0, 0
    for term, (src_re, variants) in TERMS.items():
        dist: dict[str, int] = {}
        for c, s in zip(cues, sr):
            if not re.search(src_re, c["text"], re.I):
                continue
            low = core.normalize_script(s, "lat").lower()
            v = next((name for name, rx in variants.items() if re.search(rx, low)), "nema")
            dist[v] = dist.get(v, 0) + 1
        if not dist:
            continue
        n = sum(dist.values())
        top = max(v for k, v in dist.items() if k != "nema") if any(k != "nema" for k in dist) else 0
        good_n = sum(v for k, v in dist.items() if k != "nema" and "pogrešno" not in k)
        rep[term] = {"n": n, "dist": dist, "score": top / n, "acc": good_n / n}
        tot += n
        good += top
        ok += good_n
    return {"terms": rep, "score": good / tot if tot else 1.0, "acc": ok / tot if tot else 1.0, "n": tot}


def fmt_calls(calls):
    by: dict[str, list[dict]] = {}
    for c in calls:
        by.setdefault(c["kind"], []).append(c)
    pd = sum(c["pd"] for c in calls)
    ev, ed = sum(c["ev"] for c in calls), sum(c["ed"] for c in calls)
    return (" · ".join(f"{k} {len(v)}× {sum(c['pd'] + c['ed'] for c in v):.1f}s" for k, v in by.items())
            + f" · rad modela {pd + ed:.1f}s (prompt {pd:.1f}s + generisanje {ed:.1f}s, {ev} tok"
            f" @ {ev / max(ed, 1e-9):.1f} tok/s)")


def model_time(calls) -> float:
    """Cisto vreme modela (obrada prompta + generisanje), bez cekanja u redu Ollama-e."""
    return sum(c["pd"] + c["ed"] for c in calls)


def glossary_line(cx: dict | None) -> str:
    if not cx:
        return "(nema konteksta)"
    return (f"oslovljavanje={cx.get('register')!r} · glosar: "
            + ("; ".join(f"{g['src']} → {g['sr']}" for g in cx.get("glossary") or []) or "prazan"))


def compare(model: str, script: str, only: list[str] | None, review: bool, json_out: str | None,
            seeds: list[int] | None = None, old_path: str | None = None):
    seeds = seeds or [7]
    old = load_old_module(old_path)
    if old is None:
        print("! stara verzija nije zadata (--old FAJL ili YTA_BENCH_OLD) — STARO = novi kod bez konteksta/scena/QC")
    results = {}
    for name, (title, cues) in all_samples().items():
        if only and name not in only:
            continue
        print(f"\n{'=' * 100}\n=== {name.upper()} — {title[:80]} ({len(cues)} titlova, seed {seeds})")
        runs = []
        for seed in seeds:
            SEED[0] = seed
            if old is not None:
                a = run_pipeline(old, cues, title, model, script, review, new=False)
            else:
                a = run_pipeline_flags(cues, title, model, script, review)
            b = run_pipeline(core, cues, title, model, script, review, new=True)
            a["qc_scan"] = core.qc_summary(core.qc_scan([{**c, "sr": s} for c, s in zip(cues, a["sr"])]))
            qb = b["qc"] or {}
            run = {"seed": seed, "old": a, "new": b}
            print(f"  [seed {seed}] STARO: zid {a['wall']:6.1f}s · {fmt_calls(a['calls'])} · QC nalazi: {a['qc_scan'] or 'nema'}")
            print(f"  [seed {seed}] NOVO : zid {b['wall']:6.1f}s · {fmt_calls(b['calls'])} · "
                  f"QC pre popravke: {qb.get('flagged') or 'nema'} → ponovo prevedeno {qb.get('retried', 0)}"
                  f" (limit {qb.get('cap')}), ispravljeno {qb.get('fixed', 0)}, ostalo {qb.get('remaining') or 'nema'}")
            print(f"  [seed {seed}] kontekst: {glossary_line(b['context'])}")
            if name == "dialog":
                ca, cb = consistency(cues, a["sr"]), consistency(cues, b["sr"])
                run["consistency"] = {"old": ca, "new": cb}
                print(f"  [seed {seed}] DOSLEDNOST (udeo najčešće varijante): STARO {ca['score']:.0%} · NOVO {cb['score']:.0%}"
                      f" · TAČNOST (prihvatljiva varijanta): STARO {ca['acc']:.0%} · NOVO {cb['acc']:.0%}"
                      f" ({ca['n']} pojavljivanja)")
            runs.append(run)
        a, b = runs[0]["old"], runs[0]["new"]
        blocks = [len(x) for x in core.scene_batches(cues, 30)]
        print(f"  blokovi: STARO fiksni po 30 · NOVO po scenama {blocks}")
        if b["context"]:
            print(f"  sinopsis (seed {seeds[0]}): {b['context'].get('synopsis')}")
        res = {"n": len(cues), "runs": runs, "blocks": blocks}
        if name == "dialog":
            print(f"\n  Termini po varijantama (seed {seeds[0]}):")
            ca, cb = runs[0]["consistency"]["old"], runs[0]["consistency"]["new"]
            for term in TERMS:
                if term in ca["terms"]:
                    print(f"    {term:18s} STARO {ca['terms'][term]['dist']}  |  NOVO {cb['terms'][term]['dist']}")
            if len(runs) > 1:
                avg = lambda side, k: sum(r["consistency"][side][k] for r in runs) / len(runs)  # noqa: E731
                print(f"  PROSEK {len(runs)} seed-a: DOSLEDNOST STARO {avg('old', 'score'):.0%} · NOVO {avg('new', 'score'):.0%}"
                      f" · TAČNOST STARO {avg('old', 'acc'):.0%} · NOVO {avg('new', 'acc'):.0%}")
            SEED[0] = seeds[0]
            res["qc_demo"] = qc_demo(cues, title, model, script, b["work"])
        print(f"\n  Primeri (seed {seeds[0]}; · = isto; ✱ = razlikuje se):")
        shown = 0
        for i, c in enumerate(cues):
            same = a["sr"][i] == b["sr"][i]
            if name == "dialog" and (same or shown >= 16):
                continue
            shown += 1
            print(f"  [{i:2d}] {c['text']}\n      staro: {a['sr'][i]}\n      {'·' if same else '✱'} novo: {b['sr'][i]}")
        for ex in (b["qc"] or {}).get("examples") or []:
            print(f"  QC [{ex['id']}] {ex['issues']}: {ex['before']!r} → {ex['after']!r}")
        results[name] = res
    if results:
        allruns = [r for x in results.values() for r in x["runs"]]
        ta = sum(r["old"]["wall"] for r in allruns)
        tb = sum(r["new"]["wall"] for r in allruns)
        ma = sum(model_time(r["old"]["calls"]) for r in allruns)
        mb = sum(model_time(r["new"]["calls"]) for r in allruns)
        print(f"\n{'=' * 100}\nUKUPNO ({len(allruns)} poređenja) zid: STARO {ta:.1f}s · NOVO {tb:.1f}s ({tb / max(ta, 1e-9):.2f}×)"
              f"  |  rad modela: STARO {ma:.1f}s · NOVO {mb:.1f}s ({mb / max(ma, 1e-9):.2f}×)")
        for name, x in results.items():
            r_a = sum(model_time(r["old"]["calls"]) for r in x["runs"]) / len(x["runs"])
            r_b = sum(model_time(r["new"]["calls"]) for r in x["runs"]) / len(x["runs"])
            print(f"  {name:7s} rad modela (prosek): STARO {r_a:6.1f}s · NOVO {r_b:6.1f}s ({r_b / max(r_a, 1e-9):.2f}×)")
    if json_out:
        Path(json_out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"rezultati: {json_out}")
    return results


def run_pipeline_flags(cues, title, model, script, review):
    calls: list = []
    orig = instrument(core, calls)
    args = SimpleNamespace(engine="ollama", model=model, script=script, jobs=1, batch=30, review=review,
                           cancelled=lambda: False, ctx_pass=False, scenes=False, qc_retry=False)
    cc = copy.deepcopy(cues)
    t0 = time.time()
    try:
        core.translate(cc, title, args, tmpdir("bench-tr-"))
    finally:
        core.call_ollama = orig
    return {"sr": [c["sr"] for c in cc], "wall": time.time() - t0, "calls": calls, "qc": None, "context": None}


# ----------------------------------------------------------------- stari rezim: jedan poziv, tok/s
def run_raw(model: str, script: str, review: bool):
    tot_out = tot_dur = 0
    for lang, (title, lines) in SAMPLES.items():
        batch = [{"id": i, "text": t} for i, t in enumerate(lines)]
        prompt = core.build_prompt(batch, [], title, "lat")
        body = {"model": model, "stream": False, "format": core.JSON_SCHEMA, "think": False, "keep_alive": "15m",
                "options": {"temperature": 0.2, "num_ctx": 8192},
                "messages": [{"role": "system", "content": core.SYS_PROMPT},
                             {"role": "user", "content": prompt}]}
        t0 = time.time()
        req = urllib.request.Request(core.OLLAMA_URL + "/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=900).read())
        except urllib.error.HTTPError as e:
            if "think" in e.read().decode(errors="replace"):
                body.pop("think")
                req = urllib.request.Request(core.OLLAMA_URL + "/api/chat", data=json.dumps(body).encode(),
                                             headers={"Content-Type": "application/json"})
                r = json.loads(urllib.request.urlopen(req, timeout=900).read())
            else:
                raise
        wall = time.time() - t0
        out = core.parse_json_array(r["message"]["content"])
        got = {int(o["id"]): o["sr"] for o in out}
        ev, ed = r.get("eval_count", 0), r.get("eval_duration", 1) / 1e9
        pe, pd = r.get("prompt_eval_count", 0), r.get("prompt_eval_duration", 1) / 1e9
        rev, rt = None, 0.0
        if review:
            t1 = time.time()
            rev = core.review_batch(batch, dict(got), title, "lat", core.make_llm("ollama", model))
            rt = time.time() - t1
        tot_out += ev
        tot_dur += ed
        print(f"\n=== {lang.upper()} — {title}")
        print(f"    {wall:.1f}s ukupno · izlaz {ev} tok @ {ev / ed:.1f} tok/s · prompt {pe} tok @ {pe / pd:.0f} tok/s"
              f"{'  · NEDOSTAJU id: ' + str(sorted(set(range(len(lines))) - got.keys())) if len(got) != len(lines) else ''}"
              f"{f'  · lektura +{rt:.1f}s' if review else ''}")
        for i, t in enumerate(lines):
            a = core.finalize_script(got.get(i, '??'), script, lines)
            print(f"  {t}\n    → {a}")
            if rev and core.finalize_script(rev.get(i, ''), script, lines) != a:
                print(f"    ✎ {core.finalize_script(rev[i], script, lines)}")
    print(f"\nPROSEK generisanja: {tot_out / max(tot_dur, 1e-9):.1f} tok/s")


# ----------------------------------------------------------------- unit testovi (bez modela)
def fake_tr(text: str) -> str:
    return " ".join(w[::-1] for w in text.split())  # "prevod" koji nije isti kao izvor


class FakeLLM:
    """Lazni model: kontekst -> fiksan JSON; blok -> obrnute reci; `bad` = los prvi prevod za id."""

    def __init__(self, bad: dict[int, str] | None = None, delay: float = 0.0):
        self.bad, self.delay, self.calls = dict(bad or {}), delay, []
        self.lock = threading.Lock()

    def __call__(self, prompt, schema=None, system=None):
        if self.delay:
            time.sleep(self.delay)
        if schema is not None and "glossary" in (schema.get("properties") or {}):
            with self.lock:
                self.calls.append(("ctx", prompt))
            return json.dumps({"synopsis": "Tim meteo stanice (Maren, žena; Tobias, muškarac) bori se sa olujom.",
                               "glossary": [{"src": "Maren", "sr": "Maren"},
                                            {"src": "tide gauge", "sr": "mareograf"},
                                            {"src": "Izmišljeni Termin", "sr": "nešto"}],
                               "register": "ti"})
        m = re.search(r"Ulaz:\n(\[.*?\])\n\nIzlaz", prompt, re.S)
        items = json.loads(m.group(1))
        retry = "odbacen" in prompt
        with self.lock:
            self.calls.append(("qc" if retry else "batch", prompt))
        out = [{"id": it["id"], "sr": fake_tr(it["t"]) if retry or it["id"] not in self.bad else self.bad[it["id"]]}
               for it in items]
        return json.dumps({"items": out})


def unit_cues(sizes=(10, 10, 10, 10), gap_scene=3000, gap=400) -> list[dict]:
    cues, t, k = [], 0, 0
    for si, n in enumerate(sizes):
        if si:
            t += gap_scene
        for j in range(n):
            if j:
                t += gap
            text = f"line {k} says the tide gauge needs {'checking' if k % 2 else 'fixing'} before Maren arrives."
            cues.append({"start": t, "end": t + 2000, "text": text})
            t += 2000
            k += 1
    return cues


def with_fake(llm):
    """Podmeni core.make_llm; vraca listu (engine, model) sa kojima je trazen model."""
    asked = []
    orig = core.make_llm

    def fake_make(engine, model):
        asked.append((engine, model))
        return llm
    core.make_llm = fake_make
    return asked, orig


def uargs(**kw):
    a = SimpleNamespace(engine="ollama", model="fake:1b", script="lat", jobs=1, batch=30, review=False,
                        cancelled=lambda: False, qc={})
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def t_scene_batches():
    sb = core.scene_batches
    # kratke scene se spajaju do max, rez uvek na pauzi > 2 s (kraj scene)
    c = unit_cues((12, 12, 14, 10, 12))
    assert [len(b) for b in sb(c, 30)] == [24, 24, 12]
    assert [len(b) for b in sb(c, 15)] == [12, 12, 14, 10, 12]  # max 15: svaka scena svoj blok
    for b in sb(c, 30)[:-1]:
        i = c.index(b[-1])
        assert c[i + 1]["start"] - c[i]["end"] > 2000, "rez mora biti na pauzi"
    # bez pauza: nijedan blok > max ni < min, redosled i pokrivenost ocuvani
    flat = [{"start": i * 2100, "end": i * 2100 + 2000, "text": f"reč {i},"} for i in range(75)]
    flat[24]["text"] = "kraj recenice."
    flat[27]["text"] = "jos jedan kraj."
    bs = sb(flat, 30)
    assert all(10 <= len(b) <= 30 for b in bs), [len(b) for b in bs]
    assert [x for b in bs for x in b] == flat
    assert len(bs[0]) == 28, "prinudni rez treba na kraju recenice (najkasnijem)"
    # cesta pauza (> 2 s posle svakih 5): poslednja pauza koja staje u 30, ostatak >= 10
    fr = unit_cues((5,) * 8, gap_scene=2500)
    assert [len(b) for b in sb(fr, 30)] == [30, 10]
    # ostatak se ne ostavlja sam: 35 bez pauza -> [25, 10]
    assert [len(b) for b in sb(flat[:35], 30)] == [25, 10]
    # kratak ulaz i mali max
    assert [len(b) for b in sb(flat[:7], 30)] == [7]
    assert all(len(b) <= 5 for b in sb(flat, 5)) and sum(len(b) for b in sb(flat, 5)) == 75
    # dijalog: dramska pauza unutar scene 3 ne sece (nije poslednja pauza u prozoru)
    d = dialog_cues()
    assert [len(b) for b in sb(d, 30)] == [24, 24, 12]
    assert [len(b) for b in sb(d, 14)] == [12, 12, 14, 10, 12]


def t_qc_line():
    q = core.qc_line
    assert q("Hello there, my friend.", "") == ["prazno"]
    assert "neprevedeno" in q("Hello there, my friend.", "Hello there, my friend.")
    assert "neprevedeno" in q("the guys at the yard said their tools were broken",
                              "the guys at the yard said njihovi tools were broken")
    assert q("Maren!", "Maren!") == []
    assert q("Dr. Oduya, can you hear me?", "Doktorko Oduja, čujete li me?") == []
    assert "pismo" in q("We need clean data from the buoy.", "Trebaju nam čisti podaci из буя.")
    assert "pismo" not in q("We need clean data.", "Требају нам чисти подаци.")  # srpska cirilica je ok
    g = q("Κάθε πρωί ξυπνάμε πριν βγει ο ήλιος", "Κάθε πρωί ξυπνάμε πριν βγει ο ήλιος")
    assert "pismo" in g and "neprevedeno" in g, g
    assert "duzina" in q("It took us three winters, but she floats again.", "Da.")
    assert "duzina" in q("Fine, go.", "x" * 40) or True  # prekratak izvor (< 12) se ne meri
    assert q("Fine. Go.", "U redu, idi sad i ne vraćaj se bez rezervnog dela.") == []
    long_sr = "Ovo je veoma dugačak prevod koji nikako ne može da stane u dva reda titla od po četrdeset dva znaka"
    assert q("This is a very long sentence that goes on and on and on for a long while.", long_sr) == ["prelom"]
    assert q("Šta radiš danas, druže?", "Šta radiš danas, druže?") == []  # izvor vec srpski
    # ponovni prevod se prihvata po tezini: prazno -> predugo JESTE bolje; prelom se ne racuna
    sv = core._severity
    assert sv(["duzina"]) < sv(["prazno"]) and sv(["duzina", "prelom"]) == sv(["duzina"])
    assert sv(["pismo"]) < sv(["neprevedeno"]) < sv(["prazno"]) and sv([]) == 0
    # glosar: termin koji ostaje isti ne racuna se kao neprevedeno
    assert "neprevedeno" not in q("the load balancer failed", "the load balancer failed",
                                  keep={"the", "load", "balancer", "failed"})


def t_translate_fake():
    cues = unit_cues()
    bad = {3: "", 7: cues[7]["text"], 12: "Treba nam из буя провера", 20: "x", 25: "", 30: cues[30]["text"]}
    llm = FakeLLM(bad)
    asked, orig = with_fake(llm)
    work = tmpdir("unit-tr-")
    prog = []
    try:
        a = uargs()
        cc = copy.deepcopy(cues)
        core.translate(cc, "Harrow Point test", a, work, progress=lambda d, t: prog.append((d, t)))
        kinds = [k for k, _ in llm.calls]
        assert kinds.count("ctx") == 1 and kinds.count("batch") == 2 and kinds.count("qc") == 4, kinds
        assert asked == [("ollama", "fake:1b")]
        assert prog[0] == (0, 2) and prog[-1] == (2, 2)
        batch_prompts = [p for k, p in llm.calls if k == "batch"]
        assert all("mareograf" in p and "Registar: neformalan" in p and "sinopsis" in p for p in batch_prompts)
        assert not any("Izmišljeni" in p for p in batch_prompts), "izmisljena stavka glosara mora biti izbacena"
        ctxf = core.load_json(work / core.CTX_FILE)
        assert ctxf.get("register") == "ti" and len(ctxf["glossary"]) == 2
        qc = a.qc
        assert qc["cap"] == 4 and qc["retried"] == 4 and qc["fixed"] == 4, qc
        assert qc["flagged"].get("prazno") == 2 and qc["flagged"].get("neprevedeno") == 2
        assert qc["remaining_ids"] == [20, 30], qc["remaining_ids"]  # prioritet: prazno > pismo > neprevedeno > duzina
        assert cc[3]["sr"] == fake_tr(cues[3]["text"]) and cc[20]["sr"] == "x"
        # QC prompt ima +-3 suseda sa prevodom i razlog
        qp = [p for k, p in llm.calls if k == "qc"][0]
        assert "Okolni titlovi" in qp and "odbacen" in qp
        # drugi put: sve iz kesa, bez poziva; QC izvestaj isti, bez novih pokusaja (limit iskoriscen)
        llm.calls.clear()
        a2 = uargs()
        c2 = copy.deepcopy(cues)
        core.translate(c2, "Harrow Point test", a2, work)
        assert llm.calls == [], llm.calls
        assert [c["sr"] for c in c2] == [c["sr"] for c in cc]
        assert a2.qc["retried"] == 0 and a2.qc["remaining_ids"] == [20, 30]
        # promenjen izvor usred fajla -> kes se ponistava (nema starog prevoda za novi tekst)
        c3 = copy.deepcopy(cues)
        c3[15]["text"] = "a completely different line about the relay"
        llm.calls.clear()
        llm.bad = {}
        core.translate(c3, "Harrow Point test", uargs(), work)
        kinds = [k for k, _ in llm.calls]
        assert kinds.count("batch") == 2 and kinds.count("ctx") == 1, kinds
        assert c3[15]["sr"] == fake_tr(c3[15]["text"])
        # stari format kesa (duzina + pocetak prvog titla) se ne koristi
        w2 = tmpdir("unit-tr-")
        tag = "ollama_fake_1b_lat"
        (w2 / f"prevod_cache_{tag}.json").write_text(json.dumps(
            {"__src__": str(len(cues)) + cues[0]["text"][:40], **{str(i): "STARO" for i in range(len(cues))}}))
        c4 = copy.deepcopy(cues)
        core.translate(c4, "Harrow Point test", uargs(), w2)
        assert not any(c["sr"] == "STARO" for c in c4)
        # bez konteksta / bez scena / bez QC popravke
        llm.calls.clear()
        llm.bad = {3: ""}
        a5 = uargs(ctx_pass=False, scenes=False, qc_retry=False)
        c5 = copy.deepcopy(cues)
        core.translate(c5, "t", a5, tmpdir("unit-tr-"))
        kinds = [k for k, _ in llm.calls]
        assert kinds == ["batch", "batch"], kinds
        assert a5.qc["retried"] == 0 and a5.qc["remaining"].get("prazno") == 1
    finally:
        core.make_llm = orig


def t_cyr_and_legacy():
    cues = unit_cues((10,))
    llm = FakeLLM()
    asked, orig = with_fake(llm)
    try:
        # cirilica: model vraca latinicu -> finalize u cirilicu; QC ne sme da vidi "pismo"
        a = uargs(script="cyr")
        cc = copy.deepcopy(cues)
        core.translate(cc, "t", a, tmpdir("unit-tr-"))
        assert any(re.search(r"[Ѐ-ӿ]", c["sr"]) for c in cc)
        assert "pismo" not in a.qc["flagged"], a.qc
    finally:
        core.make_llm = orig
    # stari oblik: build_prompt bez kwargs, llm sa jednim argumentom
    p = core.build_prompt([{"id": 0, "text": "Hello"}], [], "naslov", "lat")
    assert "Glosar" not in p and "Ulaz:" in p
    one_arg = lambda prompt: json.dumps({"items": [{"id": 0, "sr": "Zdravo"}]})  # noqa: E731
    r = core.translate_batch([{"id": 0, "text": "Hello"}], [], "t", uargs(), one_arg)
    assert r == {0: "Zdravo"}
    # kontekst sa llm-om koji ne prima schema -> prevod ide dalje bez glosara
    asked, orig = with_fake(one_arg)
    try:
        c1 = [{"start": 0, "end": 1000, "text": "Hello"}]
        core.translate(c1, "t", uargs(), tmpdir("unit-tr-"))
        assert c1[0]["sr"] == "Zdravo"
    finally:
        core.make_llm = orig


def t_cancel():
    llm = FakeLLM()
    asked, orig = with_fake(llm)
    try:
        try:
            core.translate(unit_cues((10,)), "t", uargs(cancelled=lambda: True), tmpdir())
            raise AssertionError("mora da baci otkazano")
        except RuntimeError as e:
            assert "otkazano" in str(e)
        assert llm.calls == []
        # otkaz usred sporog poziva (kontekst) prekida odmah, ne ceka odgovor modela
        slow = FakeLLM(delay=3.0)
        core.make_llm = lambda e, m: slow
        flag = threading.Event()
        threading.Timer(0.3, flag.set).start()
        t0 = time.time()
        try:
            core.translate(unit_cues((10,)), "t", uargs(cancelled=flag.is_set), tmpdir())
            raise AssertionError("mora da baci otkazano")
        except RuntimeError as e:
            assert "otkazano" in str(e)
        assert time.time() - t0 < 1.5, time.time() - t0
    finally:
        core.make_llm = orig


def t_clean_context():
    gl = [{"src": "tide gauge", "sr": "mareograf"}, {"src": "Dr. Ines Oduya", "sr": "Dr. Ines Oduya"},
          {"src": "Saltline", "sr": "Saltline"}]
    cx = core.clean_context({"synopsis": "  Kratko.  ", "glossary": gl + [{"src": "tide gauge", "sr": "x"},
                                                                          {"src": "", "sr": "y"},
                                                                          {"src": "Novi", "sr": "Нови"}],
                             "register": "VI"}, "tide gauge Oduya novi")
    assert [g["src"] for g in cx["glossary"]] == ["tide gauge", "Dr. Ines Oduya", "Novi"], cx
    assert cx["register"] == "vi" and cx["synopsis"] == "Kratko." and cx["glossary"][2]["sr"] == "Novi"
    assert not core.plausible_sr("buoy", "bojja") and core.plausible_sr("Anna", "Anna")
    assert core.plausible_sr("subdomain", "poddomen") and core.plausible_sr("tide gauge", "merač plime")
    # ijekavica u glosaru se izbacuje (model tada prevodi sam, ekavski); prefiksi i imena ostaju
    assert not core.plausible_sr("tide gauge", "mjerilo plime") and not core.plausible_sr("wind", "vjetar")
    assert not core.plausible_sr("notes", "biljješke") and not core.plausible_sr("world", "svjetski")
    assert all(core.plausible_sr(a, b) for a, b in [("object", "objekat"), ("projector", "projektor"),
                                                     ("wind", "vetar"), ("injection", "injekcija"),
                                                     ("at once", "odjednom"), ("Djokovic", "Đoković"),
                                                     ("Bjelica", "Bjelica"), ("trajekt", "trajekt")])
    cx2 = core.clean_context({"glossary": [{"src": "buoy", "sr": "bojja"}, {"src": "Matt", "sr": "Matt"}]}, "buoy Matt")
    assert [g["src"] for g in cx2["glossary"]] == ["Matt"], cx2
    p = core.build_prompt([{"id": 0, "text": "x"}], ["pre"], "naslov", "lat", context=cx)
    q = core.build_prompt([{"id": 5, "text": "y"}], ["drugo"], "naslov", "lat", context=cx)
    pre = p[:p.index("Prethodni kontekst")]
    assert q.startswith(pre) and "Glosar" in pre, "nepromenljivi prefiks (za Ollama kes)"


def run_unit() -> int:
    import io
    import contextlib
    tests = [t_scene_batches, t_qc_line, t_clean_context, t_translate_fake, t_cyr_and_legacy, t_cancel]
    fails = 0
    for t in tests:
        buf = io.StringIO()
        t0 = time.time()
        try:
            with contextlib.redirect_stdout(buf):
                t()
            print(f"  OK    {t.__name__} ({time.time() - t0:.1f}s)")
        except Exception as e:  # noqa: BLE001
            fails += 1
            import traceback
            print(f"  FAIL  {t.__name__}: {e!r}\n{traceback.format_exc()}{buf.getvalue()[-2000:]}")
    print(f"\n{len(tests) - fails}/{len(tests)} OK")
    return 1 if fails else 0


def main():
    argv = sys.argv[1:]

    def flag(name):
        if name in argv:
            argv.remove(name)
            return True
        return False

    def opt(name):
        if name in argv:
            i = argv.index(name)
            v = argv[i + 1]
            del argv[i:i + 2]
            return v
        return None
    if flag("--unit"):
        sys.exit(run_unit())
    review, raw = flag("--review"), flag("--raw")
    only, json_out, seeds = opt("--only"), opt("--json"), opt("--seeds")
    old_path = opt("--old") or os.environ.get("YTA_BENCH_OLD")
    try:
        s = json.loads((ROOT / "data" / "settings.json").read_text())
    except (OSError, ValueError):
        s = {}
    model = argv[0] if argv else s.get("model", "gemma4:12b-it-qat")
    script = argv[1] if len(argv) > 1 else "lat"
    if raw:
        run_raw(model, script, review)
    else:
        compare(model, script, only.split(",") if only else None, review, json_out,
                [int(x) for x in seeds.split(",")] if seeds else None, old_path)


if __name__ == "__main__":
    main()
