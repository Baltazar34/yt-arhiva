# yt-arhiva 3.1 — Memorija ispravki (ugovor)

Cilj: ispravke koje korisnik unese u editoru titla (v3) odmah popravljaju buduće prevode i whisper transkripte,
bez treninga modela. Sve lokalno, stdlib, bez novih zavisnosti. UI tekst: srpski latinica sa č ć š ž đ.

Podaci (u `DATA`, kao i do sada gitignored):
- `DATA/corrections.jsonl` — postojeći append-only log parova iz editora (v3-contract A). Stari redovi ostaju važeći.
- `DATA/memory.json` — novo: korisnički glosar, stanja pravila, isključene ispravke. Atomski upis (tmp + os.replace).

## 1. Zapis ispravki (panel.subs_save — proširenje)
- Par se i dalje upisuje samo za čistu izmenu teksta jedne linije (postojeća logika `uses[i] == 1`).
- Nov tip reda: `type` = `"tr"` za stavke kind `sr`/`tr` (kao do sada) i `"stt"` za stavke kind `whisper` i `auto`
  (transkript/automatski titl koji je korisnik ispravio). Kind `manual` i ostalo: bez para (kao do sada).
- Nova polja u redu: `type`, `channel` (manifest `channel` ili null), `src_lang` (za `tr`: osnovni jezik izvorne stavke,
  npr. "en"; za `stt`: null). Za `stt`: `lang` = osnovni jezik stavke (npr. "sr", "el", "en"), `src` = null,
  `engine`/`model` = null (ili "whisper" / ime modela ako je poznato).
- Stari redovi bez `type` se čitaju kao `"tr"`.
- Odgovor `POST /api/subs/save` dobija i `"memory": {"rules_active": N}` (broj aktivnih pravila posle upisa).

## 2. Modul `memory.py` (novo; stdlib; sme `import yt_arhiva as core` samo za `core.normalize_script`)
```python
class Memory:
    @classmethod
    def load(cls, data_dir: Path) -> "Memory"
        # čita corrections.jsonl + memory.json; keš u modulu po (putanja, mtime_ns, size) oba fajla; thread-safe;
        # pokvaren red u jsonl se preskače; nepostojeći fajlovi = prazna memorija.
    def view(self, kind: str, lang: str, channel: str | None = None) -> "MemView"
        # kind "tr": lang "sr" (obuhvata ispravke sr-lat i sr-cyr, sve se svodi na latinicu) ili "el" itd.
        # kind "stt": lang = osnovni jezik govora ("sr", "el", "en"...).
    def stats(self) -> dict      # {"total","by_lang","by_type","disabled","rules_active","glossary"}
    def dump(self, recent: int = 50) -> dict   # za GET /api/memory (vidi §5)
    # izmene memory.json (lock + atomski upis + invalidacija keša); nevalidan ulaz -> ValueError(poruka na srpskom)
    def glossary_op(self, op: str, entry: dict) -> list[dict]      # op: add | update | delete
    def set_rule_state(self, rule_id: str, state: str) -> dict     # state: auto | on | off
    def set_correction_disabled(self, corr_id: str, disabled: bool) -> dict

class MemView:
    empty: bool                                  # nema ničeg primenljivog za ovaj pogled
    def examples(self, src_texts: list[str], k: int = 6) -> list[dict]
        # najsličnije ranije ispravke istog tipa/jezika: [{"izvor","pogresno","ispravno"}] (svako polje <= 200 znakova)
    def glossary_for(self, corpus: str) -> list[dict]
        # tr: korisnički glosar [{"src","sr"}] (ključ "sr" i za el — isti oblik kao context["glossary"]),
        #     samo stavke čiji se src pojavljuje u corpus-u (case-insensitive) ili je u src-u ime (veliko slovo) iz corpus-a;
        #     scope "channel" samo za isti kanal.
    def hints(self, limit: int = 30) -> list[dict]
        # naučena pravila za prompt: [{"pogresno","ispravno"}] — aktivna + gramatička (count>=2, bez konflikta)
    def exact(self, src: str) -> str | None      # samo tr: TM pogodak (vidi §3.4)
    def apply_rules(self, text: str) -> tuple[str, int]   # deterministička aktivna pravila (§3.3); (novi tekst, broj zamena)
    def whisper_terms(self, max_chars: int) -> str        # samo stt: "Termin1, Termin2, ..." (<= max_chars) ili ""
```

## 3. Algoritmi (memory.py)
### 3.1 Normalizacija
- Srpski (`lang` sr-lat / sr-cyr / sr): `before`/`after` se pri učitavanju svode na latinicu (`core.normalize_script(t, "lat")`).
- Ključ za poređenje izvora (`norm_src`): mala slova, razmaci skupljeni, apostrofi ujednačeni (’ -> '), skinuti
  interpunkcija i navodnici sa početka i kraja.
- `corr_id` = prvih 12 hex sha1 od `ts|file|before|after` (stabilan i za stare redove).

### 3.2 Pravila iz ispravki
- Za svaki neisključen red: tokeni `re.findall(r"\w+|[^\w\s]", t)`; `difflib.SequenceMatcher(None, a, b, autojunk=False)`;
  svaki `replace` opcode sa 1–4 tokena sa obe strane (ne samo interpunkcija) = kandidat `wrong -> right` (tačan podniz
  teksta bez interpunkcije na ivicama, razmaci skupljeni).
- Nije kandidat (3.1.0): samo cifre; promena koja samo SPUŠTA veliko slovo ("Mislim" -> "mislim"); podizanje prvog
  slova na početku rečenice ("hvala" -> "Hvala"). Podizanje usred rečenice ("kenobi" -> "Kenobi") jeste kandidat (ime).
- Grupisanje po (type, lang_porodica, wrong, right): `count`, `channels` (skup), `last_ts`, `corr_ids`.
- `rule_id` = 12 hex sha1 od `type|lang|wrong|right`.
- Konflikt: isti (type, lang, wrong) ispravljen u različite `right` -> nijedno od njih nije automatski aktivno.
- Klasa pravila (3.1.0, redom):
  - `pravopis`: ista reč posle skidanja dijakritika/akcenata i malih slova, a nije samo promena velikih slova
    (Cacak -> Čačak, cesto -> često, Μαρέν -> Μάρεν); izuzetak: funkcijska reč sa drugim značenjem (που -> πού,
    sto -> što) je `gramatika`;
  - `ime`: right ima reč sa velikim početnim slovom koje NIJE samo početak rečenice, ili cifru (dva -> 2). Veliko
    slovo na početku rečenice se ne računa kad je prva reč obična (funkcijska ili stop-reč: "On" -> "Ona",
    "da" -> "Jeste"); "Petar" -> "Marko" na početku rečenice ostaje ime;
  - `termin`: bar 2 reči sa bar jedne strane, bar jedna izmenjena reč od 4+ slova koja nije stop/funkcijska reč, i
    nije čista promena nastavaka/funkcijskih reči (bio siguran -> bila sigurna je gramatika);
  - `gramatika`: sve ostalo — pojedinačna mala reč -> mala reč (kompjuter -> računar, bio -> bila, išao -> išla,
    ti -> vi, u -> na, je -> su, το -> τη), rod/padež/vreme. Zavisi od konteksta.
  - Funkcijske reči: ugrađen spisak zatvorenih klasa sr/en/el (`memory._FUNC`).
- Aktivno automatski: `count >= 2` i bez konflikta i klasa `ime`, `pravopis` ili `termin` (gramatika nikad — samo savet
  modelu u hints/primerima). Korisnik može da prepiše: `on` (uvek, i posle 1 pojave i za gramatiku), `off` (nikad).
  Stanje u `memory.json` `rules: {rule_id: "on"|"off"}` (`auto` = obriši ključ).
- STT glosar stavka sa nepraznim `wrong` pravi pravilo klase `glosar` koje je aktivno (dok se stavka ne obriše).

### 3.3 Primena pravila (`apply_rules`)
- Samo aktivna pravila tog pogleda (type + lang porodica), duža `wrong` prvo; poklapanje cele reči/fraze:
  `(?<!\w)` + re.escape(wrong) + `(?!\w)`, osetljivo na velika/mala slova; ako `wrong` počinje malim slovom, isto i za
  varijantu sa velikim početnim slovom (zamena tada dobija veliko početno slovo). Ne primenjuje se dva puta na isto
  mesto (jedan prolaz po pravilu, bez kaskade rezultat -> novo pravilo).
- Idempotentno (3.1.0): kad je `wrong` cela reč unutar `right` ("nikole" -> "Nikole Tesle", glosar "Kessler" ->
  "Tom Kessler"), mesto gde tekst već ima `right` (bez obzira na velika/mala slova) se ne dira:
  `apply(apply(x)) == apply(x)` za takva pravila (ne važi za lanac a -> b, b -> c).
- Pravilo Veliko -> malo ("Mister" -> "gospodin", samo ručno "Uvek") na početku rečenice zadržava veliko slovo.

### 3.4 Prevodna memorija (TM, `exact`)
- Samo `type` tr sa nepraznim `src`; izvor sa >= 4 reči (po razmacima: "I'm tired." su 2) i >= 20 znakova `norm_src`;
  poklapanje po (`kanal`, pitanje?, `norm_src`): ista vrsta završetka (? ili grčko ; prema ostalom) i ISTI kanal
  (ispravka bez kanala važi samo za video bez kanala). Najnovija neisključena ispravka pobeđuje. Vraća `after`
  (latinica za srpski). `memory.tm_key(src)` daje ključ.

### 3.5 Primeri (`examples`)
- Kandidati: neisključeni redovi istog tipa i lang porodice. Tekst za poređenje: `src` (ako postoji) inače `before`.
- Skor: zbir idf (log((N+1)/(df+1)) + 1) zajedničkih reči (mala slova, >= 3 slova, bez stop-reči en/sr/it/es/de/el — mali
  ugrađen spisak) + 0,5·max_idf ako je isti kanal; skor 0 -> ne ulazi. Isti (izvor, ispravno) samo jednom. Top k,
  deterministički redosled (skor, pa novije).

### 3.6 Whisper termini (`whisper_terms`)
- Izvori, po prioritetu: STT glosar (`term`) za taj jezik (isti kanal ili global) → `right` aktivnih stt pravila klase
  ime/termin/pravopis/glosar → reči sa velikim početnim slovom iz `after` stt ispravki kojih nema u `before`.
  Isti kanal ispred, pa count, pa novije. Jedinstveno bez obzira na velika/mala slova. Spaja se sa ", " dok ne pređe max_chars.

## 4. Korisnički glosar (`memory.json` `glossary`)
- Stavka: `{"id","type":"tr"|"stt","lang","src","tgt","wrong","scope":"global"|"channel","channel","note","created"}`.
  - tr: `src` (izvorni oblik, obavezno) → `tgt` (prevod, obavezno); `lang` = ciljni: "sr" ili "el".
  - stt: `tgt` = tačan termin (obavezno); `wrong` = kako ga whisper pogrešno čuje (opciono → pravilo); `src` prazno;
    `lang` = jezik govora ("sr", "el", "en"...).
  - scope "channel" traži neprazan `channel`.
- Validacija: dužine src/tgt/wrong <= 120, note <= 300, lang `^[a-z]{2,3}$`, najviše 2000 stavki; duplikat
  (type, lang, src.lower(), tgt, scope, channel) -> ValueError. `id` = 10 hex (secrets.token_hex(5)).
- Za srpski tr `tgt` se čuva u latinici (normalize_script).

## 5. API (panel.py; sve POST rute: postojeća Host/Origin + application/json zaštita)
- `GET /api/memory?recent=50` → `Memory.dump()`:
  `{"stats": {...}, "glossary": [...], "rules": [{"id","type","lang","wrong","right","count","channels":[...],
    "class","conflict":bool,"state":"auto|on|off","active":bool,"last_ts"}], "recent": [{"id","ts","type","lang",
    "channel","video_id","file","src","before","after","disabled":bool}], "settings": {"mem_tr","mem_stt"}}`
  (pravila sortirana: aktivna prvo, pa count opadajuće; najviše 500; recent najnoviji prvi).
- `POST /api/memory/glossary {"op":"add"|"update"|"delete","entry":{...}}` → `{"ok":true,"glossary":[...]}`;
  update/delete po `entry.id` (nepostojeći -> 404 `{"error": "..."}`); nevalidno -> 400.
- `POST /api/memory/rule {"id","state"}` → `{"ok":true,"rule":{...}}` (nepostojeći id -> 404).
- `POST /api/memory/correction {"id","disabled":bool}` → `{"ok":true}` (nepostojeći id -> 404).
- `GET /api/corrections/stats` ostaje; dobija i `rules_active`.
- Podešavanja (DEFAULTS): `"mem_tr": True` (memorija u prevodu i lekturi), `"mem_stt": True` (memorija u whisper-u i
  u automatskim titlovima kao izvoru prevoda).

## 6. Primena u prevodu (yt_arhiva.py; memorija stiže kao `args.mem` = MemView ili None — duck typing, bez importa)
- Bez `args.mem` (ili `args.mem.empty`) ponašanje je bajt-identično kao do sada (CLI, testovi).
- `translate()` (srpski):
  - posle `get_context` i `tg_glossary`: korisnički glosar `mem.glossary_for(naslov + svi izvorni titlovi)` ulazi u
    `context["glossary"]` (korisnik pobeđuje za isti src; ne ide kroz tg_glossary); ako konteksta nema, a glosar/pravila
    postoje -> `context = {"synopsis": "", "glossary": [...], "register": ""}`.
  - `context["hints"] = mem.hints()`; `context_block` ih ispisuje posle glosara:
    "Ranije ispravke korisnika — ne ponavljaj ove greške (pogrešno → ispravno): [...]" (JSON).
  - primeri po bloku: `build_prompt(..., examples=mem.examples([tekstovi bloka]))` i `merge_c(..., examples=...)`;
    ispisuju se u promenljivom delu prompta, pre "Ulaz": "Primeri ranijih ispravki korisnika (uči stil, termine i
    gramatiku; primeni samo ako se odnosi na ove titlove): [...]". QC ponovni prevod (retranslate_line) takođe dobija primere.
  - na kraju (posle qc_pass, i za linije iz keša): za svaki titl `exact(src)` (ako postoji) pa `apply_rules`; za ćirilicu
    se radi u latinici pa nazad u ćirilicu, i samo ako je nešto promenjeno. Keep skup imena (ostaju latinicom) je iz
    CELOG izvora videa (`script_keep` svih izvornih titlova) + latinične reči koje je linija već imala + nove reči sa
    mešanim slovima (iPhone); linija bez ćirilice ostaje latinična (`mem_post(..., keep=...)`). Rezultat u keš NE ide
    (keš ostaje čist prevod modela; memorija se primenjuje svaki put).
  - izveštaj: `args.mem_report` (dict, ako postoji) = `{"examples": broj blokova sa primerima, "glossary": n, "hints": n,
    "exact": n, "rules": n}`; `glossary`/`hints` su 0 kad nijedan prompt nije poslat (sve iz keša, tg, cloud_down).
  - `args.keep_lines` (panel `translate_cues`; CLI/testovi bez njega = kao ranije): kad se izvor promeni, keš zadržava
    prevode linija čiji se tekst po indeksu nije promenio (`__lines__` u kešu); ponovo se prevode samo blokovi sa
    promenjenim linijama (npr. stt pravilo na auto titlu).
- `translate_to()` (grčki): primeri + glosar (`"Glossary (always translate exactly like this): [...]"`) + hints u
  `gen_prompt`; na kraju `exact` + `apply_rules`; isti `mem_report`.
- `review_translation()` (lektura Claude/Gemini): primeri + glosar u `lektura_prompt`; na kraju `apply_rules`.

## 7. Primena u whisper-u (panel.py)
- `whisper_prompt_for(lang, title, channel) -> str | None` (nova, testabilna funkcija): dosadašnji prompt po jeziku
  (ako je `whisper_prompt` uključen) + `mem.whisper_terms(...)` ako je `mem_stt` uključen (max 400 znakova latinica,
  250 grčki; ukupno <= 600 znakova). Ako je `whisper_prompt` isključen a memorija ima termine -> samo termini.
- `whisper_media(..., channel=None)`: posle parsiranja `apply_rules` na svaki titl (stt pogled za taj jezik); srpski red
  se radi kroz latinicu samo kad ima više ćiriličnih nego latiničnih slova (`is_cyr_line`); u ćirilici ostaju latinicom
  latinične reči reda, imena koja u ćiriličnim redovima transkripta stoje latinicom (`cyr_keep`) i nove reči sa
  mešanim slovima (iPhone);
  `job.say("memorija: N zamena u transkriptu, M termina u promptu")` kad ima nečega. `do_whisper` prosleđuje kanal.
- `translate_step`: ako je izvorna stavka kind `auto` i `mem_stt`: stt pravila za `base_lang(entry.lang)` se primene na
  cues u memoriji pre prevoda (fajl na disku se ne menja).
- `translate_cues(..., channel=None)` i `run_lektura` postavljaju `args.mem` (tr pogled: "sr" za lat/cyr, inače target)
  kad je `mem_tr` uključen i pogled nije prazan, i `args.mem_report = {}`; posle prevoda `job.say(...)` kratak izveštaj.

## 8. UI (static/memory.js + izmene u static/index.html)
- Nova kartica **Memorija** (`#memorija`) posle Biblioteke: pregled (broj ispravki po jeziku/tipu, aktivna pravila),
  Glosar (tabela + dodavanje/izmena/brisanje; tip Prevod/Whisper, jezik, kanal ili sve), Naučena pravila (pogrešno → ispravno,
  broj, klasa, konflikt, stanje: Automatski / Uvek / Nikad), Poslednje ispravke (izvor, pre, posle, kanal; prekidač
  "koristi"). Pretraga/filter po tekstu i tipu. Prazna stanja objašnjavaju kako se memorija puni (editor titla).
- Podešavanja: `s_mem_tr`, `s_mem_stt` (Uključena / Isključena) uz postojeća whisper podešavanja.
- Editor titla: dugme "＋ Glosar": mini forma popunjena selekcijom (prevod → `tgt`, selekcija u izvornoj koloni → `src`;
  za whisper/auto stavke tip stt, `tgt` = selekcija); posle čuvanja toast. Toast posle "Sačuvaj" pominje aktivna pravila
  (`memory.rules_active`) i link na Memoriju.
- Čisti helperi izloženi kao `window.__memTest` (kao `__tbTest` u toolbox.js) radi testa bez DOM-a.

## 9. Testovi
- `tests/test_memory.py` — memory.py (pravila, konflikt, klase, primena sa velikim slovom, TM, primeri, whisper termini,
  isključene ispravke, glosar CRUD + validacija, ćirilica→latinica, keš i invalidacija, pokvaren jsonl).
- `tests/test_memory_integration.py` — yt_arhiva sa lažnim LLM-om: primeri/glosar/hints u promptovima, post-primena,
  ćirilica, grčki, lektura, bez memorije = isti promptovi kao pre.
- `tests/test_memory_api.py` — panel sa privremenim YTA_DATA (kao test_v3_api): stt parovi iz editora, /api/memory*,
  zaštita POST ruta, podešavanja, `whisper_prompt_for`.
- `tests/memory_ui_check.js` — helperi iz static/memory.js (node, bez DOM-a).
- Postojeći testovi moraju ostati zeleni: test_v3_api.py, test_panel_api.py, test_toolbox.py, test_first_use.py,
  toolbox_ui_check.js, test_installer.sh.

## 10. Odstupanja od prvobitnog ugovora (stanje 3.1.0)
Implementacija:
- Duplikat u glosaru uključuje i `wrong` (isti whisper termin sme imati više pogrešnih oblika).
- `glossary_for`: stavka za kanal pobeđuje globalnu za isti `src`.
- Panel: `SafeMem` omotač — greška u memoriji nikad ne obara posao (upozorenje u log, neutralan rezultat).
- `GET /api/memory` `rules[]` ima i `"scope": "all"|"channel"` (`channel` = aktivno samo kroz stavke glosara za kanal;
  UI tada prikazuje „za kanal“, ne „aktivno“).
Posle pregleda koda (ispravke, VERSION 3.1.0):
- §3.2 klase: prag „zajednički prefiks >= 3“ ne hvata najčešće rodne/registarske ispravke (bio/bila, ti/vi, u/na),
  pa su postajale automatska pravila i kvarile sve buduće prevode. Sada je automatski dozvoljeno samo ime, pravopis i
  termin od više reči; ostalo je gramatika (savet modelu). Promena samo velikih slova koja spušta slovo nije pravilo.
- §3.3 idempotentnost (bez „Nikole Tesle Tesle“) i čuvanje velikog slova na početku rečenice.
- §3.4 TM: >= 4 reči i >= 20 znakova, ista vrsta završetka (pitanje/izjava), isti kanal.
- §6 ćirilica: keep skup imena iz celog izvora (ne samo `[src]`), latinična linija ostaje latinična.
- §6 `mem_report`: glosar/pravila „u promptu“ samo kad je prompt poslat. `keep_lines` keš po liniji (panel).
- §7 `mem_apply`: pismo reda po većini slova; keep kao gore.
- §2 `Memory.load`: red jsonl sa nevalidnim UTF-8 se preskače (ranije je ulazio sa U+FFFD).
- Installer: `memory.py` je u `build.sh`, `install.sh` i `tests/test_installer.sh`.
