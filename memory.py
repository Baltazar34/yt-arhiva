#!/usr/bin/env python3
"""Memorija ispravki (docs/memory-contract.md §2–§4): ispravke iz editora titla popravljaju buduce prevode i
whisper transkripte bez treninga modela — primeri, naucena pravila, prevodna memorija (TM), glosar, whisper termini.

  mem = Memory.load(DATA)                       # kes po (putanja, mtime_ns, size) oba fajla; thread-safe
  v = mem.view("tr", "sr", channel)             # MemView (tr: "sr" obuhvata sr-lat i sr-cyr, sve u latinici)
  v.examples([...]); v.glossary_for(corpus); v.hints(); v.exact(src); v.apply_rules(text)
  mem.view("stt", "sr").whisper_terms(400)

Podaci: DATA/corrections.jsonl (append-only log iz editora) + DATA/memory.json (glosar, stanja pravila,
iskljucene ispravke; atomski upis). Memory i MemView su nepromenljivi snimci; izmene (glossary_op, set_rule_state,
set_correction_disabled) upisuju memory.json i osvezavaju isti Memory objekat i keš.
"""
from __future__ import annotations

import difflib
import functools
import hashlib
import json
import math
import os
import re
import secrets
import threading
import time
import unicodedata
from pathlib import Path

import yt_arhiva as core  # samo core.normalize_script

CORR_NAME, MEM_NAME, SETTINGS_NAME = "corrections.jsonl", "memory.json", "settings.json"
GLOSSARY_MAX = 2000
TERM_MAX, NOTE_MAX, CHANNEL_MAX = 120, 300, 200
EX_FIELD_MAX = 200
RULES_DUMP_MAX = 500
TM_MIN_WORDS, TM_MIN_CHARS = 4, 20              # TM: izvor >= 4 reci (po razmacima) i >= 20 znakova
KINDS = ("tr", "stt")
STATES = ("auto", "on", "off")
RULE_CLASSES = ("ime", "pravopis", "gramatika", "termin", "glosar")

_TOK_RE = re.compile(r"\w+|[^\w\s]")
_W1_RE = re.compile(r"\w")
_WORD_RE = re.compile(r"\w+")
_LETTERS_RE = re.compile(r"[^\W\d_]+")
_IDX_RE = re.compile(r"[^\W\d_]{3,}")          # reci za primere: >= 3 slova
_LANG_RE = re.compile(r"^[a-z]{2,3}$")
_SENT_END = ".!?…:\"„“”»«"                      # posle ovoga rec je pocetak recenice
_APOS = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "`": "'", "´": "'", "′": "'"})
_EDGE = " \t\r\n\"'“”„‟«»‹›‚.,!?;:…-–—()[]{}¡¿*_/\\|~"
_ISO3 = {"srp": "sr", "scc": "sr", "ell": "el", "gre": "el", "eng": "en", "ita": "it", "spa": "es", "deu": "de",
         "ger": "de", "fra": "fr", "fre": "fr", "rus": "ru", "hrv": "hr", "bos": "bs", "por": "pt", "tur": "tr"}
_PH0 = 0xF0000                                   # privatni opseg za zamene u apply_rules (nije \w)
_PH_MAX = 0xFFFFD - _PH0
_PH_RE = re.compile("[\U000F0000-\U000FFFFD]")

# mali ugradjeni spisak stop-reci (samo reci >= 3 slova su bitne): en / sr / it / es / de / el
_STOP = frozenset(w.casefold() for w in """
the and for are but not you your yours with this that these those have has had was were will would can could
should shall they them their theirs there here what when where which who whom whose why how all any from into
onto out our ours its about just than then too very also been being does did doing done she her hers him his
himself herself itself myself yourself ourselves themselves some such only own same other more most much many
each few both nor off over under again further once let get got yes yeah okay going gonna wanna
ali ili ako kao što sta šta kad kada gde gdje jer već vec još jos samo sve svi sva svoj svoja svoje ovo ovaj
ova ono onaj ona oni one njih njegov njegova njen njena njihov mene tebe sebe nas vas njega nje nam vam joj ih
bio bila bilo bili biti sam smo ste jesam nije nisu neće nece hoće hoce može moze mogu treba koji koja koje
čiji ciji zato tako onda sada sad tamo ovde evo eto posle kroz bez prema između izmedju oko iznad ispod nego
niti čak cak veoma vrlo mnogo malo mi ti vi on bih bi će ce ćemo cemo ćete cete ćeš ces hajde dobro
che non per una uno con del della delle dei degli sono come anche più piu questo questa quello quella loro suo
sua nel nella alla allo dal dalla sul sulla cosa perché perche quando dove chi tutto tutti essere stato era
hanno abbiamo molto
que los las por para como más mas pero sus sobre este esta esto ese esa eso ellos ellas nosotros muy también
tambien hay está estan están ser fue han todo todos donde quien porque sin entre
der die das und den dem des ein eine einer eines einem einen nicht ist sind war mit von für fur auf aus bei
nach über uber unter auch als wie aber oder wenn dann noch nur schon sich ich sie wir ihr ihm ihn uns euch mich
dich dass daß hat haben wird werden kann können konnen sein
και του της την των τον στο στη στην στον στα στις στους για που από είναι ένα μια μία ότι δεν αυτό αυτή
αυτός αυτά εγώ εσύ εμείς εσείς αλλά όταν πολύ έχει έχω ήταν όπως μας σας τους τις ένας κάτι εδώ εκεί τώρα
πως πώς γιατί μόνο
""".split())
_STOP = _STOP | frozenset("".join(c for c in unicodedata.normalize("NFD", w) if unicodedata.category(c) != "Mn")
                          for w in _STOP)

# zatvorene klase reci (sr latinica / en / el): predlozi, veznici, zamenice i klitike, pomocni glagoli, clanovi,
# parovi koji se razlikuju samo po akcentu/kvacici a imaju drugo znacenje (που/πού, sto/što). Zamena takvih reci
# zavisi od konteksta (registar ti/vi, rod, padez) -> klasa gramatika, nikad automatska zamena.
_FUNC = frozenset("""
u na za od do iz sa s k ka po o pri kod uz bez kroz pred pod nad među medju niz uz oko
i a ali ili da pa te jer nego no dok ako mada iako kad kada gde kako ko šta što sto čak cak li ne ni već još samo
ja ti on ona ono mi vi oni one ona me te ga ju je nas vas ih mu joj nam vam im mene tebe njega nje njemu njoj
njih njima nama vama se sebe sebi moj moja moje tvoj tvoja tvoje vaš vaša vaše vas naš naša naše njen njena
njegov njegova njihov ovaj ova ovo taj ta to onaj
sam si smo ste su bih bi bismo biste bio bila bilo bili bile budem bude jesam jesi jeste jesmo nisam nisi nije
nismo niste nisu ću ćeš će ćemo ćete hoću hoćeš hoće neću nećeš neće ce cu ces cemo cete
the an to of in on at by for with from and or but is are was were be been am you he she it we they him her us
them my your his its our their this that these those do does did have has had will would can could not no
ο η το οι τα του της των τον την τη τους τις ένας μια μία ένα ενός μιας και να θα δεν μη μην που πού πως πώς
ότι σε στο στη στην στον στα στις στους με για από ως εγώ εσύ αυτός αυτή αυτό εμείς εσείς αυτοί μου σου μας
σας ή είναι ήταν είμαι είσαι είμαστε είστε έχω έχει
""".split())

_LOCK = threading.RLock()                 # keš + upisi memory.json
_CACHE: dict[str, "_State"] = {}
_LINE_CACHE: dict[str, object] = {}       # sirov red jsonl -> _Row (ili False za pokvaren)
_LINE_CACHE_MAX = 250_000


# ================================================================ pomocne funkcije
def lang_family(code: str | None) -> str:
    """'sr-lat'/'sr-cyr'/'sr'/'srp' -> 'sr'; 'el-GR' -> 'el'; prazno -> 'und'."""
    b = re.split(r"[-_:]", (code or "").strip().lower(), maxsplit=1)[0] or "und"
    return _ISO3.get(b, b)


def norm_src(text: str | None) -> str:
    """Kljuc za poredjenje izvora: mala slova, skupljeni razmaci, ujednaceni apostrofi, bez interpunkcije na ivicama."""
    t = " ".join((text or "").translate(_APOS).lower().split())
    return t.strip(_EDGE)


def tm_key(src: str | None) -> tuple[bool, str] | None:
    """Kljuc prevodne memorije: (pitanje?, norm_src) ili None ako je izvor prekratak (< 4 reci ili < 20 znakova).
    Pitanje i izjava se ne mesaju (zavrsni ? / grcki ; ostaje u kljucu)."""
    if not src or sum(1 for t in src.split() if _W1_RE.search(t)) < TM_MIN_WORDS:
        return None
    k = norm_src(src)
    if len(k) < TM_MIN_CHARS:
        return None
    i = len(src)
    while i and not (src[i - 1].isalnum() or src[i - 1] == "_"):
        i -= 1
    return any(c in "?;\u037e\u061f" for c in src[i:]), k


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _sha12(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", "replace")).hexdigest()[:12]


def _lat(t: str) -> str:
    return core.normalize_script(t, "lat")


def _fold(s: str, dj: str = "dj") -> str:
    # bez dijakritika i malim slovima; đ se ne rastavlja u NFD pa se mapira posebno
    s = unicodedata.normalize("NFD", s.casefold().replace("đ", dj))
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _short(s: str | None, n: int = EX_FIELD_MAX) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n - 1] + "…"


def _is_word_tok(tok: str) -> bool:
    return bool(_W1_RE.match(tok))


def _sentence_start(text: str, pos: int) -> bool:
    before = text[:pos].rstrip()
    if before and before[-1] in "-–—":  # crta dijaloga ("- Zdravo"), ne crta u reci ("Obi-Wan")
        if not text[len(before):pos] and len(before) > 1 and not before[-2].isspace():
            return False
        before = before.rstrip("-–— ")
    return not before or before[-1] in _SENT_END


@functools.lru_cache(maxsize=200_000)
def _idx_fold(w: str) -> str:
    # grcki verzal nema akcente ("ΑΘΗΝΑ" = "αθήνα"); isto i srpski bez kvacica
    if w.isascii():
        return w
    return "".join(c for c in unicodedata.normalize("NFD", w) if unicodedata.category(c) != "Mn")


def _index_words(text: str | None) -> frozenset:
    """Reci za slicnost primera: mala slova, >= 3 slova, bez dijakritika, bez stop-reci."""
    return frozenset(f for f in (_idx_fold(w) for w in _IDX_RE.findall((text or "").casefold()))
                     if f not in _STOP)


# ================================================================ pravila iz para (§3.2)
def _common(m) -> bool:
    """Prva rec (re.Match ili None) je obicna rec (funkcijska ili stop-rec), ne ime."""
    return bool(m) and (m.group().casefold() in _FUNC or m.group().casefold() in _STOP)


def _case_only(wrong: str, right: str) -> bool:
    return wrong != right and wrong.casefold() == right.casefold()


def _lowers(wrong: str, right: str) -> bool:
    """Promena samo velikih/malih slova koja SPUSTA neko slovo ("Mislim" -> "mislim")."""
    return _case_only(wrong, right) and (len(wrong) != len(right) or any(
        x.isupper() and y.islower() for x, y in zip(wrong, right)))


def _candidates(a_txt: str, b_txt: str) -> list[tuple[str, str, bool]]:
    """replace blokovi (1–4 tokena sa obe strane) iz difflib-a nad tokenima -> [(wrong, right, lead)];
    lead = right pocinje velikim slovom na pocetku recenice (to veliko slovo je polozaj, ne ime)."""
    a = [(m.group(), m.start(), m.end()) for m in _TOK_RE.finditer(a_txt)]
    b = [(m.group(), m.start(), m.end()) for m in _TOK_RE.finditer(b_txt)]
    ta, tb = [x[0] for x in a], [x[0] for x in b]
    out: list[tuple[str, str, bool]] = []
    add = _cand_add(out, a_txt, b_txt)
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
        if op != "replace":
            continue
        # interpunkcija na ivicama ne ulazi u pravilo (pravilo je rec/fraza, tekst zadrzava svoje znakove)
        while i1 < i2 and not _is_word_tok(ta[i1]):
            i1 += 1
        while i2 > i1 and not _is_word_tok(ta[i2 - 1]):
            i2 -= 1
        while j1 < j2 and not _is_word_tok(tb[j1]):
            j1 += 1
        while j2 > j1 and not _is_word_tok(tb[j2 - 1]):
            j2 -= 1
        if not (1 <= i2 - i1 <= 4 and 1 <= j2 - j1 <= 4):
            continue
        # tacan podniz iz teksta (cuva "e-mail", "Obi-Wan"), razmaci skupljeni
        add(" ".join(a_txt[a[i1][1]:a[i2 - 1][2]].split()), " ".join(b_txt[b[j1][1]:b[j2 - 1][2]].split()),
            a[i1][1], b[j1][1])
        # fraza sa istim brojem reci: i svaka izmenjena rec posebno (ime ispravljeno uz susednu rec:
        # "kapitane Rejes" -> "kapetane Reyes" uci i "Rejes" -> "Reyes"; pojedinacna mala rec ostaje gramatika)
        wa = [k for k in range(i1, i2) if _is_word_tok(ta[k])]
        wb = [k for k in range(j1, j2) if _is_word_tok(tb[k])]
        if len(wa) == len(wb) >= 2:
            for x, y in zip(wa, wb):  # samo imena i pravopis (ostalo bi bila buka u savetima modelu)
                if ta[x] != tb[y] and (_fold(ta[x]) == _fold(tb[y]) or any(c.isdigit() for c in tb[y])
                                       or (tb[y][:1].isupper() and not _sentence_start(b_txt, b[y][1]))):
                    add(ta[x], tb[y], a[x][1], b[y][1])
    return out


def _cand_add(out: list, a_txt: str, b_txt: str):
    def add(wrong: str, right: str, pa: int, pb: int):
        if wrong == right or not (_LETTERS_RE.search(wrong) or _LETTERS_RE.search(right)):
            return  # samo cifre ("1" -> "2") nije pravilo nego ispravka sadrzaja
        if _lowers(wrong, right):
            return  # samo spusteno slovo ("Mislim" -> "mislim") nije pravilo: kvarilo bi pocetak recenice
        if (wrong[:1].islower() and right[:1] == wrong[:1].upper() and wrong[1:] == right[1:]
                and (_sentence_start(a_txt, pa) or _sentence_start(b_txt, pb))):
            return  # veliko slovo na pocetku recenice (i nove, posle dodate tacke) nije pravilo za tu rec
        # veliko slovo na pocetku recenice je polozaj (ne ime) kad je prva rec obicna ("On" -> "Ona", "da" -> "Jeste")
        # ili je samo drugi oblik iste reci ("Mozes" -> "Mozete", "Bio" -> "Bila")
        fw, fr = _WORD_RE.match(wrong), _WORD_RE.match(right)
        lead = (right[:1].isupper() and _sentence_start(b_txt, pb)
                and (_common(fw) or _common(fr) or (wrong[:1].isupper() and bool(fw) and bool(fr)
                                                   and _infl(fw.group().casefold(), fr.group().casefold()))))
        if not any(o[:2] == (wrong, right) for o in out):
            out.append((wrong, right, lead))
    return add


def _infl(a: str, b: str) -> bool:
    """Isti koren, razlika samo u nastavku (rod/padez/vreme: bio/bila, išao/išla, siguran/sigurna, njega/nju)."""
    p = len(os.path.commonprefix([a, b]))
    return p >= 2 and len(a) - p <= 3 and len(b) - p <= 3


def _classify(wrong: str, right: str, lead: bool = False) -> str:
    """ime | pravopis | termin | gramatika (docs/memory-contract.md §3.2, posle ispravki).
    Automatski smeju biti aktivni samo ime, pravopis i termin od vise reci; sve ostalo je gramatika.
    lead = right pocinje velikim slovom samo zato sto je na pocetku recenice ("On" -> "Ona", "da" -> "Jeste")."""
    w, r = wrong, right
    if lead and r[:1].isupper():
        r = r[:1].lower() + r[1:]
        if w[:1].isupper():
            w = w[:1].lower() + w[1:]
    ww, rw = _WORD_RE.findall(w.casefold()), _WORD_RE.findall(r.casefold())
    one = len(ww) == 1 and len(rw) == 1
    same = _fold(w) == _fold(r) or _fold(w, "d") == _fold(r, "d")
    if same and not _case_only(w, r):
        # ista rec drugacije napisana (Petrovic -> Petrović, cesto -> često); funkcijska rec sa drugim znacenjem
        # (που -> πού, sto -> što) zavisi od konteksta
        return "gramatika" if one and ww[0] in _FUNC else "pravopis"
    if any(c.isupper() for c in r) or any(c.isdigit() for c in r):
        return "ime"  # rec sa velikim slovom (Beograd, iPhone, TV) ili cifra
    if same:
        return "pravopis"  # npr. iphone -> iPhone
    if max(len(ww), len(rw)) >= 2:
        # vise reci: termin samo ako nije cista promena nastavaka/funkcijskih reci i ima bar jednu pravu rec (4+ slova)
        infl = len(ww) == len(rw) and all(x == y or _infl(x, y) or (x in _FUNC and y in _FUNC) for x, y in zip(ww, rw))
        if not infl and any(len(x) >= 4 and x not in _STOP and x not in _FUNC for x in set(ww) ^ set(rw)):
            return "termin"
    return "gramatika"  # pojedinacna rec, rod/padez/vreme, ti/vi, u/na — zavisi od konteksta


# ================================================================ redovi corrections.jsonl
class _Row:
    __slots__ = ("id", "ts", "type", "lang", "fam", "channel", "video_id", "file", "src", "src_lang",
                 "src_key", "before", "after", "raw_before", "raw_after", "noop", "cands", "words")


def _opt_str(v) -> str | None:
    if v is None or isinstance(v, (dict, list, bool)):
        return None
    v = str(v).strip()
    return v or None


def _row_from(d: dict) -> _Row | None:
    rb, ra = d.get("before"), d.get("after")
    if not isinstance(rb, str) or not isinstance(ra, str) or not rb.strip() or not ra.strip():
        return None
    r = _Row()
    r.ts = _opt_str(d.get("ts"))
    r.type = (_opt_str(d.get("type")) or "tr").lower()     # stari redovi bez type = "tr"
    r.lang = _opt_str(d.get("lang")) or "und"
    r.fam = lang_family(r.lang)
    r.channel = _opt_str(d.get("channel"))
    r.video_id = _opt_str(d.get("video_id"))
    r.file = _opt_str(d.get("file"))
    r.src_lang = _opt_str(d.get("src_lang"))
    r.raw_before, r.raw_after = rb, ra
    r.id = _sha12(f"{r.ts or ''}|{r.file or ''}|{rb}|{ra}")
    before, after = " ".join(rb.split()), " ".join(ra.split())
    if r.fam == "sr":
        before, after = _lat(before), _lat(after)
    src = _opt_str(d.get("src"))
    if src:
        src = " ".join(src.split())
        if r.src_lang and lang_family(r.src_lang) == "sr":
            src = _lat(src)
    r.src = src
    r.src_key = tm_key(src)
    r.before, r.after = before, after
    r.noop = before == after
    r.cands = [] if r.noop else _candidates(before, after)
    r.words = _index_words(src or before)
    return r


def _parse_line(ln: str) -> _Row | None:
    ln = ln.strip()
    if not ln:
        return None
    hit = _LINE_CACHE.get(ln)
    if hit is not None:
        return hit or None
    try:
        d = json.loads(ln)
    except ValueError:
        d = None
    row = _row_from(d) if isinstance(d, dict) else None
    if len(_LINE_CACHE) >= _LINE_CACHE_MAX:
        _LINE_CACHE.clear()
    _LINE_CACHE[ln] = row or False
    return row


def _read_rows(p: Path) -> list[_Row]:
    try:
        raw = p.read_bytes()
    except OSError:
        return []
    # red po red, strogo UTF-8: red sa losim bajtom se preskace (ne ulazi sa U+FFFD u pravila/TM);
    # deli se po b"\n", ne splitlines(): U+2028 moze biti unutar JSON stringa (ensure_ascii=False)
    out = []
    for bl in raw.split(b"\n"):
        try:
            ln = bl.decode("utf-8")
        except UnicodeDecodeError:
            continue
        r = _parse_line(ln)
        if r is not None:
            out.append(r)
    return out


# ================================================================ memory.json
def _read_mem(p: Path) -> tuple[dict, bool]:
    """(sadrzaj, pokvaren) — nepostojeci fajl = prazno, nije pokvaren."""
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, False
    except OSError:
        return {}, True
    try:
        d = json.loads(raw)
    except ValueError:
        return {}, True
    return (d, False) if isinstance(d, dict) else ({}, True)


def _write_atomic(p: Path, d: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    finally:
        if tmp.exists():
            tmp.unlink()


def _txt(v, name: str, mx: int) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list, bool)):
        raise ValueError(f"{name} mora biti tekst")
    v = " ".join(str(v).split())
    if len(v) > mx:
        raise ValueError(f"{name} je predugačak (najviše {mx} znakova)")
    return v


def _clean_entry(e: dict) -> dict:
    """Validacija stavke glosara (§4) -> normalizovana stavka (bez id/created); nevalidno -> ValueError."""
    if not isinstance(e, dict):
        raise ValueError("stavka glosara mora biti objekat")
    typ = _txt(e.get("type"), "tip", 8).lower() or "tr"
    if typ not in KINDS:
        raise ValueError('tip mora biti "tr" (prevod) ili "stt" (whisper)')
    lang = _txt(e.get("lang"), "jezik", 16)
    lang = lang_family(lang) if lang else ""
    if not _LANG_RE.match(lang):
        raise ValueError("jezik mora biti kod od 2–3 mala slova (npr. sr, el, en)")
    src = _txt(e.get("src"), "izvorni oblik (src)", TERM_MAX)
    tgt = _txt(e.get("tgt"), "prevod/termin (tgt)", TERM_MAX)
    wrong = _txt(e.get("wrong"), "pogrešan oblik (wrong)", TERM_MAX)
    note = _txt(e.get("note"), "napomena", NOTE_MAX)
    scope = _txt(e.get("scope"), "opseg", 16).lower() or "global"
    if scope not in ("global", "channel"):
        raise ValueError('opseg mora biti "global" ili "channel"')
    channel = _txt(e.get("channel"), "kanal", CHANNEL_MAX) or None
    if scope == "channel" and not channel:
        raise ValueError("opseg „kanal“ traži naziv kanala")
    if scope == "global":
        channel = None
    if lang == "sr":
        tgt, wrong = _lat(tgt), _lat(wrong)
    if typ == "tr":
        if not src:
            raise ValueError("izvorni oblik (src) je obavezan za prevodni glosar")
        if not tgt:
            raise ValueError("prevod (tgt) je obavezan")
        wrong = ""
    else:
        if not tgt:
            raise ValueError("tačan termin (tgt) je obavezan")
        if wrong and wrong == tgt:
            raise ValueError("pogrešan oblik je isti kao tačan termin")
        src = ""
    return {"id": None, "type": typ, "lang": lang, "src": src, "tgt": tgt, "wrong": wrong, "scope": scope,
            "channel": channel, "note": note, "created": None}


def _dup_key(e: dict) -> tuple:
    # wrong ulazi u kljuc da bi isti whisper termin mogao imati vise pogresnih oblika (za tr je uvek "")
    return (e["type"], e["lang"], e["src"].casefold(), e["tgt"], e["scope"], e["channel"], e["wrong"].casefold())


def _san_mem(raw: dict) -> dict:
    """memory.json -> {"glossary": [...], "rules": {...}, "disabled": [...]} (+ nepoznati kljucevi ostaju)."""
    gl, seen = [], set()
    for e in raw["glossary"] if isinstance(raw.get("glossary"), list) else []:
        if not isinstance(e, dict) or not isinstance(e.get("id"), str) or not e["id"] or e["id"] in seen:
            continue
        try:
            c = _clean_entry(e)
        except ValueError:
            continue
        c["id"], c["created"] = e["id"], _opt_str(e.get("created"))
        seen.add(c["id"])
        gl.append(c)
    rules = raw.get("rules") if isinstance(raw.get("rules"), dict) else {}
    rules = {k: v for k, v in rules.items() if isinstance(k, str) and v in ("on", "off")}
    dis = raw.get("disabled") if isinstance(raw.get("disabled"), list) else []
    dis = sorted({x for x in dis if isinstance(x, str) and x})
    return {**raw, "glossary": gl, "rules": rules, "disabled": dis}


def _read_settings(data_dir: Path) -> dict:
    s = {}
    try:
        d = json.loads((data_dir / SETTINGS_NAME).read_text(encoding="utf-8"))
        s = d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        pass

    def b(v):
        return v.strip().lower() in ("1", "true", "da", "yes", "on") if isinstance(v, str) else bool(v)
    return {"mem_tr": b(s.get("mem_tr", True)), "mem_stt": b(s.get("mem_stt", True))}


# ================================================================ snimak stanja
class _Rule:
    __slots__ = ("id", "type", "lang", "wrong", "right", "count", "channels", "last_pos", "last_ts", "corr_ids",
                 "lclass", "cls", "conflict", "gl", "gl_global", "gl_ch", "state", "active", "free")

    def __init__(self, typ, lang, wrong, right):
        self.id = _sha12(f"{typ}|{lang}|{wrong}|{right}")
        self.type, self.lang, self.wrong, self.right = typ, lang, wrong, right
        self.count, self.channels, self.last_pos, self.last_ts, self.corr_ids = 0, set(), -1, None, []
        self.gl, self.gl_global, self.gl_ch = False, False, set()
        self.lclass = self.cls = "termin"
        self.conflict, self.state, self.active = False, "auto", False
        self.free = False  # bar jedna pojava gde veliko pocetno slovo u right nije samo pocetak recenice

    def auto(self) -> bool:
        # automatski samo ime, pravopis i termin od vise reci (gramatika = samo savet modelu)
        return self.count >= 2 and not self.conflict and self.lclass in ("ime", "pravopis", "termin")


def _sig_one(p: Path):
    try:
        s = os.stat(p)
    except OSError:
        return None
    return (s.st_mtime_ns, s.st_size, s.st_ino)


def _sig(data_dir: Path) -> tuple:
    return (_sig_one(data_dir / CORR_NAME), _sig_one(data_dir / MEM_NAME))


class _State:
    def __init__(self, rows: list[_Row], memd: dict, sig: tuple):
        self.sig, self.rows = sig, rows
        m = _san_mem(memd)
        self.glossary: list[dict] = m["glossary"]
        self.states: dict[str, str] = m["rules"]
        self.disabled: set[str] = set(m["disabled"])
        self.row_pos: dict[str, int] = {r.id: i for i, r in enumerate(rows)}
        self._lock = threading.Lock()
        self._bases: dict[tuple, _Base] = {}
        self._views: dict[tuple, MemView] = {}
        self._build_rules()

    def _build_rules(self):
        groups: dict[tuple, _Rule] = {}
        for pos, r in enumerate(self.rows):
            if r.noop or not r.cands or r.id in self.disabled:
                continue
            for wrong, right, lead in r.cands:
                k = (r.type, r.fam, wrong, right)
                g = groups.get(k) or groups.setdefault(k, _Rule(*k))
                g.count += 1
                g.free = g.free or not lead
                if r.channel:
                    g.channels.add(r.channel)
                g.last_pos, g.last_ts = pos, r.ts
                g.corr_ids.append(r.id)
        # "Kapitane Rejes" (pocetak recenice) i "kapitane Rejes" su isto pravilo: spoji u oblik malim slovom
        # (apply_rules pokriva i veliko pocetno slovo); bez para malim slovom (npr. ime) ostaje kako jeste
        for k in [k for k in groups if k[2][:1].isupper() and k[3][:1].isupper()]:
            lk = (k[0], k[1], k[2][:1].lower() + k[2][1:], k[3][:1].lower() + k[3][1:])
            if lk != k and lk in groups:
                g, lg = groups.pop(k), groups[lk]
                lg.count += g.count
                lg.free = lg.free or g.free
                lg.channels |= g.channels
                lg.corr_ids += [c for c in g.corr_ids if c not in lg.corr_ids]
                if g.last_pos > lg.last_pos:
                    lg.last_pos, lg.last_ts = g.last_pos, g.last_ts
        rights: dict[tuple, set] = {}
        for (typ, lang, wrong, right) in groups:
            rights.setdefault((typ, lang, wrong), set()).add(right)
        gl_keys = set()
        for e in self.glossary:  # stt glosar sa wrong -> pravilo klase glosar
            if e["type"] != "stt" or not e["wrong"]:
                continue
            k = ("stt", e["lang"], e["wrong"], e["tgt"])
            g = groups.get(k) or groups.setdefault(k, _Rule(*k))
            g.gl = True
            if e["scope"] == "global":
                g.gl_global = True
            else:
                g.gl_ch.add(e["channel"])
            gl_keys.add(("stt", e["lang"], e["wrong"]))
            rights.setdefault(("stt", e["lang"], e["wrong"]), set()).add(e["tgt"])
        for g in groups.values():
            g.lclass = _classify(g.wrong, g.right, lead=g.count > 0 and not g.free)
            g.cls = "glosar" if g.gl else g.lclass
            k = (g.type, g.lang, g.wrong)
            # konflikt: isti wrong -> razliciti right; glosar pobedjuje naucena pravila
            g.conflict = not g.gl and (len(rights[k]) > 1 or k in gl_keys)
            g.state = self.states.get(g.id, "auto")
            g.active = g.state == "on" or (g.state != "off" and (g.gl or g.auto()))
        self.rules: list[_Rule] = list(groups.values())
        self.rule_by_id: dict[str, _Rule] = {g.id: g for g in self.rules}

    # --- pogled
    @staticmethod
    def in_view(g: _Rule, channel: str | None) -> bool:
        # pravilo samo iz glosara sa opsegom kanala ne postoji u drugim kanalima
        return g.count > 0 or g.gl_global or (channel is not None and channel in g.gl_ch)

    @staticmethod
    def active_in(g: _Rule, channel: str | None) -> bool:
        if g.state in ("on", "off"):
            return g.state == "on"
        if g.gl and (g.gl_global or (channel is not None and channel in g.gl_ch)):
            return True
        return g.auto()

    def base(self, kind: str, fam: str) -> "_Base":
        b = self._bases.get((kind, fam))
        if b is None:
            with self._lock:
                b = self._bases.get((kind, fam))
                if b is None:
                    b = self._bases[(kind, fam)] = _Base(self, kind, fam)
        return b

    def view(self, kind: str, fam: str, channel: str | None) -> "MemView":
        k = (kind, fam, channel)
        v = self._views.get(k)
        if v is None:
            v = MemView(self, kind, fam, channel)
            with self._lock:
                v = self._views.setdefault(k, v)
        return v

    # --- izlaz za API
    @staticmethod
    def rule_dict(g: _Rule) -> dict:
        # scope "channel": aktivno samo kroz stavke glosara za kanal (u drugim kanalima se ne primenjuje)
        scope = "channel" if g.gl and not g.gl_global and g.state != "on" and not g.auto() else "all"
        return {"id": g.id, "type": g.type, "lang": g.lang, "wrong": g.wrong, "right": g.right, "count": g.count,
                "channels": sorted(g.channels | g.gl_ch), "class": g.cls, "conflict": g.conflict,
                "state": g.state, "active": g.active, "last_ts": g.last_ts, "scope": scope}

    def recent_dict(self, r: _Row) -> dict:
        return {"id": r.id, "ts": r.ts, "type": r.type, "lang": r.lang, "channel": r.channel,
                "video_id": r.video_id, "file": r.file, "src": r.src, "before": r.raw_before,
                "after": r.raw_after, "disabled": r.id in self.disabled}

    def rules_active(self) -> int:
        return sum(1 for g in self.rules if g.active)


class _Base:
    """Deo pogleda koji ne zavisi od kanala: redovi, pravila, indeks za primere, TM, whisper reci."""

    def __init__(self, st: _State, kind: str, fam: str):
        self.rows: list[_Row] = [r for r in st.rows
                                 if r.type == kind and r.fam == fam and not r.noop and r.id not in st.disabled]
        self.rules = [g for g in st.rules if g.type == kind and g.lang == fam]
        post: dict[str, list[int]] = {}
        for i, r in enumerate(self.rows):
            for w in r.words:
                post.setdefault(w, []).append(i)
        n = len(self.rows)
        self.post = post
        self.idf = {w: math.log((n + 1) / (len(p) + 1)) + 1 for w, p in post.items()}
        self.max_idf = math.log((n + 1) / 2) + 1 if n else 0.0
        self.tm: dict[tuple, str] = {}
        if kind == "tr":
            for r in self.rows:  # redosled fajla: najnovija ispravka pobedjuje; kljuc (kanal, pitanje?, izvor)
                if r.src_key:
                    self.tm[(r.channel, *r.src_key)] = r.after
        # stt: reci sa velikim pocetnim slovom iz after kojih nema u before (imena koja whisper promasi)
        self.caps: dict[str, list] = {}
        if kind == "stt":
            for i, r in enumerate(self.rows):
                bw = set(_LETTERS_RE.findall(r.before))
                for m in _LETTERS_RE.finditer(r.after):
                    w = m.group()
                    if (len(w) < 3 or not w[0].isupper() or w in bw or w.casefold() in _STOP
                            or _sentence_start(r.after, m.start())):
                        continue
                    c = self.caps.setdefault(w.casefold(), [w, 0, set(), -1])
                    c[0], c[1], c[3] = w, c[1] + 1, i
                    if r.channel:
                        c[2].add(r.channel)


# ================================================================ javni API
class MemView:
    """Memorija za jedan tip (tr/stt), jezik i kanal. Nepromenljiv snimak; bezbedno deliti medju nitima."""

    def __init__(self, st: _State, kind: str, fam: str, channel: str | None):
        self.kind, self.lang, self.channel = kind, fam, channel
        self._b = b = st.base(kind, fam)
        self._gloss = [e for e in st.glossary if e["type"] == kind and e["lang"] == fam
                       and (e["scope"] == "global" or (channel is not None and e["channel"] == channel))]
        self._rules = [g for g in b.rules if st.in_view(g, channel)]
        self._active = sorted((g for g in self._rules if st.active_in(g, channel)),
                              key=lambda g: (-len(g.wrong), g.wrong, g.right))
        self._rx: list | None = None
        self.empty: bool = not b.rows and not self._gloss and not self._active

    @property
    def rules_active(self) -> int:
        return len(self._active)

    def examples(self, src_texts: list[str], k: int = 6) -> list[dict]:
        """Najslicnije ranije ispravke: [{"izvor","pogresno","ispravno"}] (svako polje <= 200 znakova)."""
        b = self._b
        if k <= 0 or not b.rows:
            return []
        if isinstance(src_texts, str):
            src_texts = [src_texts]
        q: set[str] = set()
        for t in src_texts or []:
            if isinstance(t, str):
                q |= _index_words(t)
        scores: dict[int, float] = {}
        for w in q:
            p = b.post.get(w)
            if p:
                wi = b.idf[w]
                for i in p:
                    scores[i] = scores.get(i, 0.0) + wi
        if not scores:
            return []
        if self.channel is not None:
            bonus = 0.5 * b.max_idf
            for i in scores:
                if b.rows[i].channel == self.channel:
                    scores[i] += bonus
        out, seen = [], set()
        for i in sorted(scores, key=lambda i: (-scores[i], -i)):
            r = b.rows[i]
            key = (r.src or "", r.after)
            if key in seen:
                continue
            seen.add(key)
            out.append({"izvor": _short(r.src), "pogresno": _short(r.before), "ispravno": _short(r.after)})
            if len(out) >= k:
                break
        return out

    def glossary_for(self, corpus: str) -> list[dict]:
        """tr: korisnicki glosar [{"src","sr"}] za stavke koje se pojavljuju u corpus-u (ili ime iz src-a)."""
        if self.kind != "tr" or not self._gloss or not corpus or not isinstance(corpus, str):
            return []
        low = corpus.casefold()
        names = {w for w in _LETTERS_RE.findall(corpus) if len(w) >= 3 and w[0].isupper()}
        out, seen = [], set()
        # kanal ispred globalnog (isti src: korisnik za kanal pobedjuje), inace redosled unosa
        for e in sorted(self._gloss, key=lambda e: e["scope"] != "channel"):
            k = e["src"].casefold()
            if k in seen:
                continue
            hit = k in low and re.search(r"(?<!\w)" + re.escape(k) + r"(?!\w)", low)
            if not hit:
                hit = any(w in names for w in _LETTERS_RE.findall(e["src"]) if len(w) >= 3 and w[0].isupper())
            if hit:
                seen.add(k)
                out.append({"src": e["src"], "sr": e["tgt"]})
        return out

    def hints(self, limit: int = 30) -> list[dict]:
        """Naucena pravila za prompt: aktivna + gramaticka (count >= 2, bez konflikta)."""
        act = {g.id for g in self._active}
        c = [g for g in self._rules if g.state != "off" and (
            g.id in act or (g.lclass == "gramatika" and g.count >= 2 and not g.conflict))]
        c.sort(key=lambda g: (-g.count, -g.last_pos, g.wrong, g.right))
        return [{"pogresno": g.wrong, "ispravno": g.right} for g in c[:max(0, limit)]]

    def exact(self, src: str) -> str | None:
        """Samo tr: prevodna memorija — ispravljen prevod istog izvora (>= 4 reci i >= 20 znakova, ista vrsta
        zavrsetka ? / ostalo) iz ISTOG kanala (ispravka bez kanala samo za video bez kanala); najnoviji pobedjuje."""
        if self.kind != "tr" or not src or not isinstance(src, str) or not self._b.tm:
            return None
        k = tm_key(src)
        return self._b.tm.get((self.channel, *k)) if k else None

    def _compiled(self) -> list:
        if self._rx is None:
            rx = []
            for g in self._active:
                w = g.wrong
                alts = [w]
                if w[:1].islower() and w[:1].upper() != w[:1]:
                    alts.append(w[:1].upper() + w[1:])
                pat = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(a) for a in alts) + r")(?!\w)")
                # gde se wrong (cela rec) nalazi unutar right: "kessler" u "tom kessler" -> tekst koji vec ima
                # right na tom mestu se ne dira (idempotentnost: bez "Tom Tom Kessler")
                wl, rl = w.lower(), g.right.lower()
                offs = [] if wl == rl else [m.start() for m in re.finditer(
                    r"(?<!\w)" + re.escape(wl) + r"(?!\w)", rl)]
                rx.append((pat, alts, w, g.right, g.right[:1].upper() + g.right[1:], rl, len(wl), offs))
            self._rx = rx
        return self._rx

    def apply_rules(self, text: str) -> tuple[str, int]:
        """Aktivna pravila (duza wrong prvo, cela rec/fraza, jedan prolaz bez kaskade) -> (novi tekst, broj zamena).
        Idempotentno za pravila koja prosiruju wrong (tekst koji vec ima right se ne menja); veliko slovo na
        pocetku recenice ostaje."""
        if not text or not isinstance(text, str) or not self._active or _PH_RE.search(text):
            return text, 0
        subs: list[str] = []

        def has_right(s: str, a: int, b: int, rl: str, nw: int, offs: list[int]) -> bool:
            for o in offs:
                st, en = a - o, b + len(rl) - o - nw
                if (st >= 0 and en <= len(s) and s[st:en].lower() == rl
                        and (st == 0 or not _W1_RE.match(s[st - 1])) and (en == len(s) or not _W1_RE.match(s[en]))):
                    return True
            return False

        def one(wrong, right, cap, rl, nw, offs):
            def rep(m):
                got = m.group(0)
                if offs and has_right(m.string, m.start(), m.end(), rl, nw, offs):
                    return got
                new = right if got == wrong else cap
                if got[:1].isupper() and new[:1].islower() and _sentence_start(m.string, m.start()):
                    new = new[:1].upper() + new[1:]  # pocetak recenice ostaje velikim slovom
                if new == got or len(subs) >= _PH_MAX:
                    return got
                subs.append(new)  # mesto se zakljucava: sledeca pravila ga ne vide
                return chr(_PH0 + len(subs) - 1)
            return rep

        cur = text
        for pat, alts, wrong, right, cap, rl, nw, offs in self._compiled():
            if any(a in cur for a in alts):  # brza provera pre regex-a
                cur = pat.sub(one(wrong, right, cap, rl, nw, offs), cur)
        if not subs:
            return text, 0
        return _PH_RE.sub(lambda m: subs[ord(m.group(0)) - _PH0], cur), len(subs)

    def whisper_terms(self, max_chars: int) -> str:
        """Samo stt: "Termin1, Termin2, ..." (<= max_chars) — glosar, pa naucena pravila, pa imena iz ispravki."""
        if self.kind != "stt" or max_chars <= 0:
            return ""
        ch = self.channel
        cands = []
        for i, e in enumerate(self._gloss):
            cands.append((0, 0 if e["scope"] == "channel" else 1, -1, -i, e["tgt"]))
        for g in self._active:
            if g.cls in ("ime", "termin", "pravopis", "glosar"):
                same = ch is not None and (ch in g.channels or ch in g.gl_ch)
                cands.append((1, 0 if same else 1, -g.count, -g.last_pos, g.right))
        for w, cnt, chans, last in self._b.caps.values():
            cands.append((2, 0 if ch is not None and ch in chans else 1, -cnt, -last, w))
        cands.sort(key=lambda c: c[:4])
        out, seen, total = [], set(), 0
        for c in cands:
            t = c[4].strip()
            k = t.casefold()
            if not t or k in seen:
                continue
            add = len(t) + (2 if out else 0)
            if total + add > max_chars:
                break
            seen.add(k)
            out.append(t)
            total += add
        return ", ".join(out)


class Memory:
    """Memorija ispravki za jedan DATA folder. Memory.load() je jeftin (keš); izmene osvezavaju i ovaj objekat."""

    def __init__(self, data_dir: Path, state: _State):
        self.data_dir = Path(data_dir)
        self._st = state

    @classmethod
    def load(cls, data_dir: Path) -> "Memory":
        d = Path(data_dir).expanduser().absolute()
        return cls(d, _load_state(d))

    # --- citanje
    def view(self, kind: str, lang: str, channel: str | None = None) -> MemView:
        if kind not in KINDS:
            raise ValueError('vrsta memorije mora biti "tr" (prevod) ili "stt" (whisper)')
        if not isinstance(lang, str) or not lang.strip():
            raise ValueError("jezik je obavezan")
        channel = _opt_str(channel) if isinstance(channel, str) else None
        return self._st.view(kind, lang_family(lang), channel)

    def stats(self) -> dict:
        st = self._st
        by_lang, by_type, dis = {}, {}, 0
        for r in st.rows:
            by_lang[r.lang] = by_lang.get(r.lang, 0) + 1
            by_type[r.type] = by_type.get(r.type, 0) + 1
            dis += r.id in st.disabled
        return {"total": len(st.rows), "by_lang": by_lang, "by_type": by_type, "disabled": dis,
                "rules_active": st.rules_active(), "glossary": len(st.glossary)}

    def dump(self, recent: int = 50) -> dict:
        try:
            recent = max(0, min(int(recent), 5000))
        except (TypeError, ValueError):
            raise ValueError("recent mora biti broj") from None
        st = self._st
        rules = sorted(st.rules, key=lambda g: (not g.active, -g.count, -g.last_pos, g.wrong, g.right))
        rec, seen = [], set()
        for r in reversed(st.rows):
            if len(rec) >= recent:
                break
            if r.id in seen:
                continue
            seen.add(r.id)
            rec.append(st.recent_dict(r))
        return {"stats": self.stats(), "glossary": [dict(e) for e in st.glossary],
                "rules": [st.rule_dict(g) for g in rules[:RULES_DUMP_MAX]], "recent": rec,
                "settings": _read_settings(self.data_dir)}

    # --- izmene memory.json
    def _mutate(self, fn) -> _State:
        with _LOCK:
            cur = _load_state(self.data_dir)
            mp = self.data_dir / MEM_NAME
            raw, bad = _read_mem(mp)
            memd = _san_mem(raw)
            fn(cur, memd)  # validacija + izmena; ValueError/KeyError pre upisa
            if bad and mp.exists():  # pokvaren memory.json se ne gazi bez kopije
                os.replace(mp, mp.with_name(f"{mp.name}.bad-{time.strftime('%Y%m%d-%H%M%S')}"))
            _write_atomic(mp, memd)
            new = _State(cur.rows, memd, (cur.sig[0], _sig_one(mp)))
            _CACHE[str(self.data_dir)] = new
            self._st = new
            return new

    def glossary_op(self, op: str, entry: dict) -> list[dict]:
        if op not in ("add", "update", "delete"):
            raise ValueError("nepoznata operacija (dozvoljeno: add, update, delete)")
        if not isinstance(entry, dict):
            raise ValueError("stavka glosara mora biti objekat")

        def fn(cur, memd):
            gl = memd["glossary"]
            if op == "add":
                if len(gl) >= GLOSSARY_MAX:
                    raise ValueError(f"glosar je pun (najviše {GLOSSARY_MAX} stavki)")
                e = _clean_entry(entry)
                if any(_dup_key(g) == _dup_key(e) for g in gl):
                    raise ValueError("ista stavka već postoji u glosaru")
                ids = {g["id"] for g in gl}
                e["id"] = secrets.token_hex(5)
                while e["id"] in ids:
                    e["id"] = secrets.token_hex(5)
                e["created"] = _now()
                gl.append(e)
                return
            gid = entry.get("id")
            i = next((n for n, g in enumerate(gl) if isinstance(gid, str) and g["id"] == gid), None)
            if i is None:
                raise KeyError("stavka glosara ne postoji")
            if op == "delete":
                gl.pop(i)
                return
            editable = ("type", "lang", "src", "tgt", "wrong", "scope", "channel", "note")
            e = _clean_entry({**gl[i], **{k: v for k, v in entry.items() if k in editable}})
            if any(_dup_key(g) == _dup_key(e) for n, g in enumerate(gl) if n != i):
                raise ValueError("ista stavka već postoji u glosaru")
            e["id"], e["created"] = gl[i]["id"], gl[i].get("created")
            gl[i] = e

        return [dict(e) for e in self._mutate(fn).glossary]

    def set_rule_state(self, rule_id: str, state: str) -> dict:
        if state not in STATES:
            raise ValueError("stanje pravila mora biti auto, on ili off")

        def fn(cur, memd):
            if not isinstance(rule_id, str) or rule_id not in cur.rule_by_id:
                raise KeyError("pravilo ne postoji")
            if state == "auto":
                memd["rules"].pop(rule_id, None)
            else:
                memd["rules"][rule_id] = state

        new = self._mutate(fn)
        return new.rule_dict(new.rule_by_id[rule_id])

    def set_correction_disabled(self, corr_id: str, disabled: bool) -> dict:
        if not isinstance(disabled, bool):
            raise ValueError("disabled mora biti true ili false")

        def fn(cur, memd):
            if not isinstance(corr_id, str) or corr_id not in cur.row_pos:
                raise KeyError("ispravka ne postoji")
            s = set(memd["disabled"])
            if disabled:
                s.add(corr_id)
            else:
                s.discard(corr_id)
            memd["disabled"] = sorted(s)

        new = self._mutate(fn)
        return new.recent_dict(new.rows[new.row_pos[corr_id]])


def _load_state(d: Path) -> _State:
    key = str(d)
    with _LOCK:
        sig = _sig(d)  # pre citanja: dopisan red posle stat-a samo izazove ponovno citanje sledeci put
        st = _CACHE.get(key)
        if st is not None and st.sig == sig:
            return st
        rows = _read_rows(d / CORR_NAME)
        memd, _bad = _read_mem(d / MEM_NAME)
        st = _State(rows, memd, sig)
        _CACHE[key] = st
        return st


def clear_cache() -> None:
    """Za testove: zaboravi sve snimke i parsirane redove."""
    with _LOCK:
        _CACHE.clear()
        _LINE_CACHE.clear()
