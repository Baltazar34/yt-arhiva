# yt-arhiva 3.2 — "Šta da uradim" + ceo sadržaj + tačno stanje (ugovor)

Cilj:
1. Na prvoj strani lista koraka sa checkbox-ovima: samo preuzimanje, ili i prevod, ili i transkodiranje, uz izbor formata.
2. Jasan UI i tačno stanje stavke: prevedeno, transkript bez prevoda, bez titla.
3. Podrazumevano preuzimanje celog sadržaja (sve audio trake i titlovi) sa jednim, najkvalitetnijim videom.
4. Transkript, prevod i ostali koraci su po izboru → podrazumevano je čekirano SAMO preuzimanje.
Pravila: malo klikova, NIJEDNA postojeća opcija se ne gubi (samo se premešta u "⚙ Sve opcije"). UI tekst srpski latinica
sa č ć š ž đ; komentari u kodu srpski bez dijakritike, kratko, kao okolni kod. Bez novih zavisnosti.

## A. Ceo sadržaj (podrazumevano preuzimanje)
- Video: SAMO jedan, najkvalitetniji (postojeći `build_selector` za `auto:best`).
- Audio: SVAKA posebna audio traka (svaki jezik × original/dub/default/descriptive), najbolji kvalitet te trake.
  Popravka: `audio_groups` ključ grupe mora uključiti ulogu (`audio_role`), da npr. engleski "descriptive" ne nestane uz
  engleski default. Duplikati iste trake u drugom kodeku/DRC se i dalje preskaču (isti zvuk, manji kvalitet).
- Titlovi: svi ručni CC na svim jezicima + automatski na originalnom jeziku (kao sada `subs: "all"`), kao SRT u `subs/`.
  NOVO: posle preuzimanja (i posle preuzimanja titlova), titlovi se ubacuju i kao trake u izvorni MKV
  (`ffmpeg -c copy`, bez rekodiranja; jezik ISO-639-2 iz postojećeg `ISO3`, naslov "English (CC)" / "English (auto)";
  default dispozicija nijedan; postojeći omot/poglavlja/metapodaci ostaju). Privremeni fajl u radnom folderu pa
  `os.replace`; neuspeh = upozorenje u logu, izvor ostaje netaknut. Ako izvor nije MKV (npr. samo audio m4a/mp3) → preskoči.
  Manifest: `source_subs: [{"key","lang","kind"}]` (šta je ubačeno). Ponovljeno ubacivanje ne duplira trake
  (ubacuju se samo titlovi kojih nema u `source_subs`; ako ih ima novih, remux iz originalnih traka + svih titlova).
- Podešavanje `embed_subs` (podrazumevano True) u ⚙ Podešavanjima ("Titlovi i u MKV izvoru").
- Opciono (NE podrazumevano, u "⚙ Sve opcije" → Titlovi): `subs: "all+tr"` = i YouTube automatski prevodi na sve jezike
  (upozorenje u hint-u: mnogo fajlova, YouTube ume da vrati 429). `sub_keys_for` za "all+tr": sve auto_captions ključeve.
- Poglavlja, omot (embed + jpg), metapodaci: kao sada. Opis i info: kao sada u `.yta/`.

## B. Kartica Preuzmi — "Šta da uradim"
Umesto kartica-recepata + reda knob-ova: kartica sa redovima. Svaki red = checkbox + naziv + (kad je čekiran) meniji
u istom redu (na ≤ 600px prelaze ispod naziva). Nečekiran red: prigušen, meniji skriveni. PODRAZUMEVANO čekiran je
samo red 1.
1. ☑ **Preuzmi** (zaključano, uvek) — `Šta`: "Ceo sadržaj — najbolji video + sve audio trake + svi titlovi"
   (podrazumevano) · "Samo audio" · "Samo titlovi". Za ceo sadržaj: `Kvalitet` (najbolji, do 2160p/1440p/1080p/720p/480p/360p);
   za samo audio: `Format` (ACONV_FULL; više traka → MKV kao sada).
2. ☐ **Transkript (whisper)** — originalni jezik, bez prevoda: `Jezik` (automatski / srpski / engleski / grčki / …
   LANG_NAMES), `Kada` ("samo kad YouTube nema titl" · "uvek"). proc: `sub_source` "whisper:run" (uvek) ili
   "auto-best" (kad nema), `whisper_lang`.
3. ☐ **Prevod** — `na` (srpski latinica / ćirilica / grčki), `Prevodilac` (postojeći spisak).
4. ☐ **Video sa titlom** — `Format` (MP4 sa upečenim titlom / MKV sa mekim titlovima / oba), `Kvalitet` (VQ; ne za meki),
   `Rezolucija`. Bez Prevoda koristi originalni titl. Disabled za Samo audio / Samo titlovi (sa objašnjenjem u title).
5. ☐ **Transkoduj (Toolbox)** — `Preset` (iz `/api/tb/presets`, bez audio-only), `Titl` (bez / meki / upečen).
   Disabled za Samo audio / Samo titlovi.
6. ☐ **Izvuci audio** — `Format` (M4A / MP3 / FLAC = audio_m4a / audio_mp3 / audio_flac). Disabled za Samo audio / titlovi.
- **Brzi izbor** (mali dugmići, jedan klik postavlja checkbox-ove): ⬇ Samo preuzmi (podrazumevano), 📝 + Transkript,
  🌐 + Prevod, 🎬 + Video sa titlom, 🎵 Muzika (samo audio MP3 320), 💬 Samo titlovi + Prevod. Označen je onaj koji
  odgovara stanju.
- **⚙ Sve opcije**: postojeća puna forma "Prilagođeno" (audio trake: sve/samo originalna, titlovi CC: svi / svi + YT
  automatski prevodi / najbolji / bez, titl za obradu, lektura, audio u izlazu, meki titlovi u izlazu, enkoder, audio kodek,
  zadrži izvor, folder) popunjena iz stanja; dok je otvorena ona je izvor istine za dl/proc (kao RID "custom" danas),
  redovi 5–6 i dalje važe. Zatvaranje prenosi nazad koliko može.
- Sažetak jednom rečenicom ispod (`describe()` proširen: transkript, after koraci) + 📁 folder kao sada.
- Pamćenje: localStorage `plan`. Prvo pokretanje (nema `plan`): SAMO red 1 (ceo sadržaj, najbolji). `SET.recipe` više ne
  čekira korake sam; Podešavanja "Podrazumevani izbor" dobija opciju "⬇ Samo preuzmi (ceo sadržaj)" koja je nov default.
  Jednokratna migracija u panel.py: ako settings.json ima stari default `recipe: "arhiva"` i nema `plan_migrated`,
  postavi `recipe: "samo"`, `plan_migrated: true`.
- Postojeći recepti i `/api/recipes` ostaju (kompatibilnost); dodaje se recept `samo` (dl ceo sadržaj, proc null),
  `RECIPE_IDS` i `#s_recipe` ga sadrže.

## C. Mapiranje na opcije posla (`commonOpts()` → `{dl, proc, after}`)
- dl: ceo sadržaj → `{video_fmt: "auto:best"|"auto:<h>", audio_fmt: "all", subs: "all"}`; samo audio →
  `{video_fmt: "audio", audio_fmt: "auto", audio_conv, subs: "none"}`; samo titlovi → `{video_fmt: "none", subs: "all"}`; out_dir.
- proc: null ako nisu čekirani Transkript, Prevod ni Video. Inače `{sub_source, whisper_lang?, target: Prevod ? <lat|cyr|el> :
  "none", engine, model, output: Video ? <burn|soft|both> : (Samo titlovi ? "srt" : "none"), encoder, vq, scale,
  audio_out: "orig", audio_enc: "aac192", subs_out: "sr+orig", keep_source: true}` (polja iz "Sve opcije" imaju prednost).
- after (NOVO): `{"toolbox": {"preset","sub","target"}, "audio": {"preset"}}` — samo čekirani.

## D. Backend (panel.py)
- `opts.after` validacija u `POST /api/jobs`, `/api/jobs/batch`, `/api/playlists/<id>/missing` istim pravilima kao
  `library_batch` toolbox/audio (preset postoji, sub ∈ none/soft/burn, target ∈ lat/cyr/el/null, upečen + preset koji
  kopira video → 400 sa istom porukom, nepoznat ključ → 400).
- `run_download_job`: posle uspešnog `_download_and_process` i POSLE izlaska iz `folder_lock`: za toolbox pa audio →
  `lib_transcode(...)`; Job → `start_job`, `job.after_jobs.append(id)`, `job.say("posle preuzimanja: <preset> → posao <id>")`;
  string → `job.say("<korak> preskočen: <razlog>")`. Greška tu ne obara uspešno preuzimanje. `after_jobs` u JSON-u posla.
- `proc.whisper_lang` se poštuje (već postoji u get_cues); transkript bez prevoda: target "none", output "none" → whisper.srt
  ostaje u `subs/` (i SRT pored ako je Samo titlovi).
- Library stavka (`library()`): `tr_state: "translated"|"transcript"|"none"`;
  `translated: [{"target":"lat|cyr|el","engine","model","date","lektura":bool,"file"}]`,
  `transcripts: [{"kind":"manual|auto|whisper","lang","file"}]` — iz manifest `subs` (bez ffprobe). "translated" = bar
  jedan prevod (kind sr/tr) ili srpski ručni CC (is_sr_cc); "transcript" = ima titl/transkript a nema prevod; "none" = ništa.
- Isto stanje u "U arhivi već imaš" (probe kartica, `cat_short`/katalog): `tr_state` + kratki spisak.
- `/api/library/batch` nova akcija `transcribe` (params: `lang` opciono, `force`): proc `{"sub_source":"whisper:run",
  "whisper_lang", "target":"none","output":"none","keep_source":true}`; skip "već ima whisper transkript" (osim force),
  "nema izvora zvuka".
- Posao (`/api/jobs`): koraci već postoje (download/subs/translate/output); dodati `after_jobs`.

## E. UI (static/plan.js nov + static/index.html)
- plan.js: čisti helperi (stanje ↔ opts, brzi izbor ↔ stanje, disabled pravila, describe sa transkriptom i after,
  stanje stavke → bedž) izloženi kao `window.__planTest`; render checkliste.
- Bedž stanja (isti helper svuda: lista za preuzimanje "U arhivi već imaš", Red/posao na kraju, Biblioteka red i detalji):
  🟢 "Prevedeno: SR lat ✓ · Kvalitetno · datum" (više prevoda: kratko "SR lat ✓ · EL ✓", lektura "✓ lektura");
  🟠 "Transkript bez prevoda: sr whisper" / "en CC" / "en auto" + dugme **Prevedi** (jedan klik, batch translate za tu
  stavku sa trenutnim izborom jezika); ⚪ "Nema titla" + dugme **Transkribuj** (ako ima izvor zvuka).
  Sve korisničke vrednosti kroz esc().
- Biblioteka: filter **Stanje** (Sve / Prevedeno / Samo transkript bez prevoda / Bez titla) — stari "Sa/Bez SR prevoda"
  ostaju kao stavke istog menija (ne gube se); batch dugme **Transkribuj**.
- Red: posao preuzimanja sa after koracima prikazuje i njih (linkovi na poslove).
- Podešavanja: `s_embed_subs` (Titlovi i u MKV izvoru: Da/Ne), "Podrazumevani izbor" sa novom opcijom samo.
- Pristupačnost: checkbox-ovi sa `<label>`, fokus redom, `aria-disabled`/title objašnjava zašto je red isključen.

## F. Testovi
- `tests/test_plan_api.py` (port 8824, privremeni YTA_DATA, lažni yt-dlp/ffmpeg obrazac kao test_v3_api/test_panel_api):
  after validacija (400), after koraci nad gotovom stavkom (pozovi funkciju koja ih pokreće ili lažno preuzimanje),
  transcribe akcija (skip razlozi), tr_state/translated/transcripts u library i katalogu, migracija recipe arhiva→samo,
  audio_groups sa descriptive trakom, embed_subs remux nad pravim malim MKV-om napravljenim ffmpeg-om (lavfi testsrc+sine)
  + 2 SRT → ffprobe pokazuje 2 subtitle trake sa jezicima; drugi poziv ne duplira; neuspeh ostavlja izvor netaknut.
- `tests/plan_ui_check.js` (node, bez DOM-a): helperi iz plan.js.
- Svi postojeći testovi ostaju zeleni (test_memory*, test_v3_api, test_panel_api, test_toolbox, test_first_use,
  memory_ui_check.js, toolbox_ui_check.js, test_installer.sh). Installer pakuje plan.js ako ima spisak static fajlova.

## G. Stvarno stanje i odstupanja (stanje 3.2.0)
Implementacija:
- Red Transkript je isključen uz „Samo titlovi“ (bez zvuka whisper nema šta da sluša; YouTube titlovi se ionako preuzimaju).
- Forma „⚙ Sve opcije“ ima i „Jezik govora (whisper)“ (= `Jezik` iz reda 2), aktivan samo za `auto-best` / `whisper:run`.
Posle pregleda koda (ispravke, VERSION 3.2.0):
- §A omot: `-map 0 -c copy` je MKV omot (prilog, `attached_pic`) upisivao kao drugu, običnu video traku. Ubacivanje
  titlova (`embed_source_subs`) i upis naziva audio traka (`finalize_source`, više traka) sada mapiraju `0:V/a/s/t`,
  a omot izdvoje iz izvora i vrate sa `-attach` (mimetype/filename kao u izvoru). Provera pre `os.replace` poredi i
  `attached_pic`. Izvori koje je 3.1 već tako upisao (više audio traka) se ne popravljaju sami.
- §D `keep_source: false` + video izlaz + čekiran red 5/6: izvor se NE briše (koraci posle rade iz njega);
  log „izvor se zadržava…“, sažetak „izvor se zadržava (treba koracima posle)“. Bez koraka posle izbor važi kao pre.
- §D preskočen korak posle preuzimanja ide i u `after_skipped: [{"step","label","reason"}]` u JSON-u posla; Red ga
  prikazuje kao ⚠ bedž pored linkova „posle:“ (ne samo red u logu).
- §B „⚙ Sve opcije“ pamti polja koja redovi ne modeluju (localStorage `plan_custom`: audio trake, titlovi, lektura,
  audio i meki titlovi u izlazu, enkoder, audio kodek, zadrži izvor) i vraća ih pri otvaranju i posle ponovnog
  učitavanja; bez zapamćenog → početne vrednosti iz Podešavanja (`dl_audio`, `dl_subs`, `keep_original`).
  Izmena tih podešavanja zamenjuje zapamćeno polje (i u otvorenoj formi). Brzi izbor u Sve opcije vraća vrednosti preseta.
  Jezik govora bez vrednosti u stanju = „automatski“ (forma ne zadržava stari izbor, red Transkript se ne čekira sam).
- §B brzi izbor „📝 + Transkript“ postavlja `Kada` = „uvek“ (`whisper:run`); sa „samo kad YouTube nema titl“ skoro
  svaki video (YouTube auto titl) ne bi dobio transkript. Ručno čekiran red i dalje počinje sa „samo kad nema“.
- §E „Transkribuj“ samo kad izvor ima zvuk: `has_audio` u `library()` i `cat_short` (false samo kad manifest ima
  `audio: []`; stari manifest bez ključa = ima zvuk, isto kao `lib_transcribe`); bedž tada objašnjava u title.
- §F `test_plan_api.py`: omot mora ostati prilog (`attached_pic` 1, jedna obična video traka), `after_skipped`,
  `t_after_keeps_source_unit`, `has_audio`; `plan_ui_check.js`: 📝 → `whisper:run`, bedž bez zvuka, sažetak izvora.
