#!/usr/bin/env python3
"""A/B: Gemma 4 sam vs TranslateGemma sam vs sinergija (nacrt + lektura, dva nacrta + izbor).

  python3 tests/bench_synergy.py                 # svi uzorci, sve metode
  python3 tests/bench_synergy.py en_dialog A     # jedan uzorak, jedna metoda (G/T/A/B)

Metode:
  G  gemma4 JSON batch sa sazetkom i glosarom (generalista)
  T  translategemma blok po blok (specijalista, zvanicni prompt)
  A  T nacrt -> gemma4 lektura (gramatika, imena, oslovljavanje, rod) sa glosarom
  B  G i T nezavisno -> gemma4 bira/spaja po liniji
Modeli se ne mesaju po batch-u: prvo sav posao jednog modela, pa drugog (jedna zamena u memoriji).
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

OLLAMA = "http://127.0.0.1:11434"
GEN = "gemma4:12b-it-qat"
TG = "translategemma:12b"
OUT = Path(__file__).resolve().parents[1] / "data" / "bench_synergy.json"

LANGS = {"en": "English", "el": "Greek", "it": "Italian", "es": "Spanish"}

SAMPLES = {
    "en_boat": ("en", "Documentary about restoring an old sailing boat in Maine", [
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
    "el_fish": ("el", "Ντοκιμαντέρ για τη ζωή των ψαράδων στην Κρήτη", [
        "Κάθε πρωί ξυπνάμε πριν βγει ο ήλιος",
        "και βγαίνουμε με τη βάρκα μέχρι το ακρωτήρι.",
        "Ο πατέρας μου έλεγε ότι η θάλασσα δεν συγχωρεί",
        "όποιον δεν τη σέβεται.",
        "Τα τελευταία χρόνια τα ψάρια είναι όλο και λιγότερα,",
        "και οι νέοι φεύγουν για την Αθήνα.",
        "Εγώ όμως δεν μπορώ να φανταστώ τη ζωή μου αλλού.",
    ]),
    "it_chef": ("it", "Intervista a uno chef di Bologna sulla pasta fresca", [
        "Guardi, la sfoglia non si improvvisa.",
        "Mia nonna diceva che ci vuole pazienza,",
        "farina buona e uova freschissime,",
        "e soprattutto non bisogna avere fretta.",
        "Oggi tutti vogliono fare le cose in cinque minuti,",
        "ma così non viene fuori niente di buono.",
        "Il ragù poi, quello è un altro discorso.",
    ]),
    "es_startup": ("es", "Charla sobre emprendimiento en Buenos Aires", [
        "Cuando arrancamos con la empresa no teníamos un peso,",
        "trabajábamos desde la cocina de mi vieja",
        "y la verdad es que nadie creía en nosotros.",
        "Pero bueno, a veces hay que tirarse a la pileta",
        "aunque no sepas si hay agua.",
        "Hoy somos cuarenta personas",
        "y seguimos laburando con la misma garra del primer día.",
    ]),
    # originalni tekst: imena i termini se ponavljaju (doslednost), rod broda, formalno obracanje
    "en_dialog": ("en", "Interview with harbor master Elena Reyes about the rescue boat Nightjar", [
        "Captain Reyes, thank you for joining us today.",
        "It's my pleasure. The Nightjar has been with us for twelve years.",
        "Most people don't realize she was built by volunteers.",
        "The Harbor Authority paid for the engine, nothing else.",
        "When did you first take her out on a real call?",
        "Winter of 2016. A fishing trawler lost power near the breakwater.",
        "The waves were higher than the wheelhouse.",
        "My deputy, Tom Kessler, was at the helm that night.",
        "He's the calmest person I've ever worked with.",
        "We got a line on the trawler after about forty minutes.",
        "Did the Harbor Authority change anything after that?",
        "They finally approved a second crew for night shifts.",
        "Tom trained every one of them himself.",
        "And the Nightjar? Is she still in service?",
        "She is, although her hull needs work before next season.",
        "We're hoping the town council will cover the repairs.",
        "If not, we'll do what we always do: ask the volunteers.",
        "Captain, what would you tell someone who wants to join?",
        "Come to the boathouse on a Tuesday. Bring boots, not excuses.",
        "Thank you, Captain Reyes. And good luck with the Nightjar.",
    ]),
}

JSON_SCHEMA = {"type": "object", "required": ["items"], "properties": {"items": {
    "type": "array", "items": {"type": "object", "required": ["id", "sr"],
                               "properties": {"id": {"type": "integer"}, "sr": {"type": "string"}}}}}}
CTX_SCHEMA = {"type": "object", "required": ["synopsis", "glossary", "register", "speakers"], "properties": {
    "synopsis": {"type": "string"}, "register": {"type": "string", "enum": ["vi", "ti"]},
    "speakers": {"type": "array", "items": {"type": "object", "required": ["name", "gender"], "properties": {
        "name": {"type": "string"}, "gender": {"type": "string", "enum": ["m", "z", "?"]}}}},
    "glossary": {"type": "array", "items": {"type": "object", "required": ["src", "sr"],
                                            "properties": {"src": {"type": "string"}, "sr": {"type": "string"}}}}}}

SYS_SR = ("Ti si vrhunski prevodilac i lektor titlova na srpski jezik (ekavica, latinica sa č ć š ž đ). "
          "Prevod je prirodan, gramaticki besprekoran (rod, broj, padez, glagolski vid), kao kod obrazovanog "
          "izvornog govornika iz Srbije. Imena i termini dosledno po glosaru. Odgovaras samo JSON-om.")


def chat(model: str, messages: list[dict], fmt=None, temperature=0.2, think=False) -> tuple[str, float]:
    body = {"model": model, "stream": False, "messages": messages, "keep_alive": "20m",
            "options": {"temperature": temperature, "num_ctx": 8192}}
    if fmt is not None:
        body["format"] = fmt
    if think is not None and model == GEN:
        body["think"] = think
    t0 = time.time()
    req = urllib.request.Request(OLLAMA + "/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    r = json.loads(urllib.request.urlopen(req, timeout=900).read())
    return r["message"]["content"], time.time() - t0


def items_of(txt: str) -> dict[int, str]:
    d = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
    return {int(o["id"]): str(o["sr"]).strip() for o in d.get("items", [])}


# ---------------------------------------------------------------- kontekst (gemma4)
def context(title: str, lines: list[str]) -> dict:
    prompt = (f"Video: \"{title}\"\nTitlovi (izvod):\n" + "\n".join(lines[:150]) +
              "\n\nNapravi za prevodioca: synopsis (2-4 recenice na srpskom); glossary SAMO vlastitih imena (ljudi, "
              "brodova, firmi, proizvoda, mesta) koja se ne prevode — sr = isti oblik kao original (ne prevodi "
              "opste pojmove i institucije); speakers: ko govori i njegov rod (m/z/?), da glagoli u 1. licu budu "
              "u pravom rodu; register ('vi' ako je formalno/intervju, inace 'ti').")
    txt, _ = chat(GEN, [{"role": "system", "content": SYS_SR}, {"role": "user", "content": prompt}], CTX_SCHEMA)
    return json.loads(txt)


def ctx_block(c: dict) -> str:
    g = "; ".join(f"{x['src']} = {x['sr']}" for x in c.get("glossary", [])[:40])
    sp = "; ".join(f"{x['name']} ({'žensko' if x['gender'] == 'z' else 'muško' if x['gender'] == 'm' else '?'})"
                   for x in c.get("speakers", [])[:10])
    return (f"Sazetak: {c.get('synopsis', '')}\nImena (ne prevoditi): {g}\nGovornici i rod: {sp}\n"
            f"Oslovljavanje: {'vi (formalno)' if c.get('register') == 'vi' else 'ti'}")


# ---------------------------------------------------------------- metode
def m_gemma(src_lang: str, title: str, lines: list[str], c: dict) -> dict[int, str]:
    items = [{"id": i, "t": t} for i, t in enumerate(lines)]
    prompt = (f"Video: \"{title}\"\n{ctx_block(c)}\n\nPrevedi titlove sa jezika {LANGS[src_lang]} na srpski. "
              "Recenice se protezu kroz vise titlova — prevedi celinu i rasporedi po istim id-jevima. "
              f"Ulaz:\n{json.dumps(items, ensure_ascii=False)}\n\nIzlaz: {{\"items\": [{{\"id\", \"sr\"}}]}}")
    txt, _ = chat(GEN, [{"role": "system", "content": SYS_SR}, {"role": "user", "content": prompt}], JSON_SCHEMA)
    return items_of(txt)


def tg_prompt(src_lang: str, text: str) -> str:
    # zvanicni format TranslateGemma (jedna user poruka, dva prazna reda pre teksta)
    sl = LANGS[src_lang]
    return (f"You are a professional {sl} ({src_lang}) to Serbian (sr-Latn) translator. Your goal is to accurately "
            f"convey the meaning and nuances of the original {sl} text while adhering to Serbian grammar, "
            f"vocabulary, and cultural sensitivities.\nProduce only the Serbian translation, without any additional "
            f"explanations or commentary. Please translate the following {sl} text into Serbian:\n\n\n{text}")


def m_tg(src_lang: str, lines: list[str], block: int = 12) -> dict[int, str]:
    """Numerisani blokovi (poravnanje + kontekst unutar bloka); nedostajuci brojevi -> pojedinacno."""
    out: dict[int, str] = {}
    for b0 in range(0, len(lines), block):
        chunk = lines[b0:b0 + block]
        text = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(chunk))
        txt, _ = chat(TG, [{"role": "user", "content": tg_prompt(src_lang, text)}], temperature=0.1)
        got = {}
        for ln in txt.strip().split("\n"):
            mm = re.match(r"^\s*(\d+)[.)]\s*(.+)$", ln)
            if mm and 1 <= int(mm.group(1)) <= len(chunk):
                got[int(mm.group(1)) - 1] = mm.group(2).strip()
        out.update({b0 + i: g for i, g in got.items()})
        for i, ln in enumerate(chunk):  # rezerva: pojedinacno samo za nedostajuce
            if i in got:
                continue
            t1, _ = chat(TG, [{"role": "user", "content": tg_prompt(src_lang, ln)}], temperature=0.1)
            out[b0 + i] = " ".join(t1.split())
    return out


def m_postedit(src_lang: str, title: str, lines: list[str], draft: dict[int, str], c: dict) -> dict[int, str]:
    items = [{"id": i, "src": t, "nacrt": draft.get(i, "")} for i, t in enumerate(lines)]
    prompt = (f"Video: \"{title}\"\n{ctx_block(c)}\n\nOvo je nacrt prevoda specijalizovanog prevodioca (jezik izvora: "
              f"{LANGS[src_lang]}). Nacrt je gramaticki uglavnom ispravan — NE prepravljaj ga stilski. Menjaj SAMO:\n"
              "1) imena koja su promenjena ili prevedena → vrati ih tacno kao u listi imena;\n"
              "2) rod glagola/prideva u 1. licu po rodu govornika (npr. 'radio' → 'radila' ako govori zena);\n"
              "3) oslovljavanje po uputstvu (vi/ti) dosledno;\n"
              "4) ocigledne greske znacenja u odnosu na izvor (pogresna rec, pogresno vreme glagola, izostavljeno).\n"
              "Sve ostalo prepisi NEPROMENJENO, slovo po slovo. Ne spajaj i ne seli tekst izmedju id-jeva.\n"
              f"Ulaz:\n{json.dumps(items, ensure_ascii=False)}\n\nIzlaz: {{\"items\": [{{\"id\", \"sr\"}}]}}")
    txt, _ = chat(GEN, [{"role": "system", "content": SYS_SR}, {"role": "user", "content": prompt}], JSON_SCHEMA)
    got = items_of(txt)
    return {i: got.get(i) or draft.get(i, "") for i in range(len(lines))}


def m_fuse(src_lang: str, title: str, lines: list[str], a: dict[int, str], b: dict[int, str], c: dict) -> dict[int, str]:
    items = [{"id": i, "src": t, "A": a.get(i, ""), "B": b.get(i, "")} for i, t in enumerate(lines)]
    prompt = (f"Video: \"{title}\"\n{ctx_block(c)}\n\nZa svaku liniju imas dva prevoda (A i B) sa jezika "
              f"{LANGS[src_lang]}. Napravi najbolju srpsku verziju: izaberi bolju ili spoji najbolje delove, "
              "ispravi gramatiku, ujednaci imena po glosaru. Ne seli tekst izmedju id-jeva.\n"
              f"Ulaz:\n{json.dumps(items, ensure_ascii=False)}\n\nIzlaz: {{\"items\": [{{\"id\", \"sr\"}}]}}")
    txt, _ = chat(GEN, [{"role": "system", "content": SYS_SR}, {"role": "user", "content": prompt}], JSON_SCHEMA)
    got = items_of(txt)
    return {i: got.get(i) or a.get(i, "") for i in range(len(lines))}


def m_c(src_lang: str, title: str, lines: list[str], base: dict[int, str], helper: dict[int, str], c: dict) -> dict[int, str]:
    """T je osnova (gramatika), G je pomocni prevod (znacenje/kontekst); menja se samo gde treba."""
    items = [{"id": i, "src": t, "osnova": base.get(i, ""), "pomocni": helper.get(i, "")} for i, t in enumerate(lines)]
    prompt = (f"Video: \"{title}\"\n{ctx_block(c)}\n\nZa svaku liniju: izvor ({LANGS[src_lang]}), 'osnova' (gramaticki "
              "pouzdan prevod) i 'pomocni' (prevod koji bolje zna kontekst). Rezultat je OSNOVA, osim:\n"
              "1) ako se osnova i pomocni razlikuju po ZNACENJU — proveri izvor i uzmi tacno znacenje (pravilnim srpskim);\n"
              "2) imena tacno po listi imena (i pravilan padez: 'sa Nightjarom');\n"
              "3) rod u 1. licu po rodu govornika; 4) oslovljavanje dosledno.\n"
              "Ako je osnova tacna, prepisi je NEPROMENJENU. Ne seli tekst izmedju id-jeva.\n"
              f"Ulaz:\n{json.dumps(items, ensure_ascii=False)}\n\nIzlaz: {{\"items\": [{{\"id\", \"sr\"}}]}}")
    txt, _ = chat(GEN, [{"role": "system", "content": SYS_SR}, {"role": "user", "content": prompt}], JSON_SCHEMA)
    got = items_of(txt)
    return {i: got.get(i) or base.get(i, "") for i in range(len(lines))}


# ---------------------------------------------------------------- tok
def run(names: list[str], methods: str):
    res, times = {}, {}
    # faza 1: gemma4 (kontekst + G)
    t0 = time.time()
    ctxs = {n: context(SAMPLES[n][1], SAMPLES[n][2]) for n in names}
    times["kontekst"] = time.time() - t0
    G = {}
    if set(methods) & {"G", "B", "C"}:
        t0 = time.time()
        G = {n: m_gemma(SAMPLES[n][0], SAMPLES[n][1], SAMPLES[n][2], ctxs[n]) for n in names}
        times["G"] = time.time() - t0
    # faza 2: translategemma (jedna zamena modela)
    T = {}
    if set(methods) & {"T", "A", "B", "C"}:
        t0 = time.time()
        T = {n: m_tg(SAMPLES[n][0], SAMPLES[n][2]) for n in names}
        times["T"] = time.time() - t0
    # faza 3: gemma4 lektura / spajanje
    A, B = {}, {}
    if "A" in methods:
        t0 = time.time()
        A = {n: m_postedit(SAMPLES[n][0], SAMPLES[n][1], SAMPLES[n][2], T[n], ctxs[n]) for n in names}
        times["A (+T)"] = time.time() - t0 + times.get("T", 0)
    if "B" in methods:
        t0 = time.time()
        B = {n: m_fuse(SAMPLES[n][0], SAMPLES[n][1], SAMPLES[n][2], G[n], T[n], ctxs[n]) for n in names}
        times["B (+G+T)"] = time.time() - t0 + times.get("G", 0) + times.get("T", 0)
    C = {}
    if "C" in methods:
        t0 = time.time()
        C = {n: m_c(SAMPLES[n][0], SAMPLES[n][1], SAMPLES[n][2], T[n], G[n], ctxs[n]) for n in names}
        times["C (+G+T)"] = time.time() - t0 + times.get("G", 0) + times.get("T", 0)
    for n in names:
        src_lang, title, lines = SAMPLES[n]
        res[n] = {"context": ctxs[n], "lines": [
            {"src": ln, **{k: v[n].get(i, "") for k, v in (("G", G), ("T", T), ("A", A), ("B", B), ("C", C)) if v}}
            for i, ln in enumerate(lines)]}
        print(f"\n=== {n} — {title}\n    glosar: {ctx_block(ctxs[n]).splitlines()[1][:200]}")
        for row in res[n]["lines"]:
            print(f"  {row['src']}")
            for k in "GTABC":
                if k in row:
                    print(f"    {k}: {row[k]}")
    n_lines = sum(len(SAMPLES[n][2]) for n in names)
    print("\nVREME (s, ukupno za", n_lines, "linija):", {k: round(v, 1) for k, v in times.items()})
    OUT.write_text(json.dumps({"results": res, "times": times}, ensure_ascii=False, indent=1))
    print("rezultat:", OUT)


if __name__ == "__main__":
    names = [a for a in sys.argv[1:] if a in SAMPLES] or list(SAMPLES)
    methods = next((a for a in sys.argv[1:] if set(a) <= set("GTABC")), "GTAB")
    run(names, methods)
