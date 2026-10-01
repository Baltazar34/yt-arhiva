# yt-arhiva UX v2 — "preuzmi brzo, obradi kad hoćeš"

Cilj: alat za PREUZIMANJE na Mac-u (i sa YouTube Premium nalogom), sa OBAVEZNIM srpskim
prevodom titla, i opcionim transkodiranjem odmah (batch) ili kasnije (batch iz biblioteke).
Mora biti lako i brzo: nalepi link → jedan klik → gotovo. Sve napredno postoji, ali je sklopljeno.
Ne gubiti postojeće opcije (samo ih sakriti iza "⚙ Detalji"/"Prilagođeno").

Postojeći kod: `panel.py` (stdlib HTTP + poslovi), `yt_arhiva.py` (titlovi, prevod, srt/ass),
`static/index.html` (trenutni UI, jedna duga stranica), `static/toolbox.js` (Toolbox, ugovor
`docs/toolbox-contract.md`), `toolbox.py`. Sve postojeće API rute ostaju.

## 1. Raspored: kartice (hash rute), ne jedna duga stranica
Header: "YT Arhiva" · kartice **⤓ Preuzmi** (#preuzmi) · **▶ Red** (#red, značka = broj aktivnih) ·
**📚 Biblioteka** (#biblioteka, značka = broj stavki) · **🛠 Toolbox** (#toolbox) · **⚙** (podešavanja, fioka/drawer
desno ili posebna kartica #podesavanja). Desno: status pilule (Prevod/Ollama, Nalog, Whisper).
Samo jedna kartica vidljiva. Poslednja kartica se pamti u localStorage (try/catch).
Kada se posao doda → kratka "toast" poruka ("Dodato u red: 12") sa linkom "Prikaži red"; NE prebacivati automatski karticu.

## 2. ⤓ Preuzmi
1. **Polje za linkove** (jedan red, raste): "Nalepi link — video, plejlista ili kanal (može više odjednom)".
   Paste → odmah dodaje (bez klika); Enter → dodaje; dugme "Dodaj". Duplikati se ne dodaju (flash postojećeg reda).
2. **Recept** (segmentirana dugmad, veliko i jasno; podrazumevano iz podešavanja `recipe`, default "arhiva"):
   - 📦 **Arhiva** — najbolji video + sve audio trake + svi titlovi + srpski prevod (SRT u arhivi). Bez rekodiranja.
   - 🎬 **Arhiva + video sa titlom** — isto + MP4 za gledanje (HEVC VideoToolbox, upečen srpski titl, originalni zvuk).
   - 🎵 **Muzika / podkast** — samo audio (MP3 320 podrazumevano), omot i tagovi; bez titlova i prevoda.
   - 💬 **Samo titlovi** — svi titlovi + srpski prevod kao SRT fajl, bez videa.
   - ⚙ **Prilagođeno** — otvara pune menije (postojeća polja preuzimanja i obrade).
   Ispod recepta: rečenica-sažetak ("Najbolji video · sve audio trake · svi titlovi · prevod: srpski latinica
   (gemma4:12b-it-qat) → SRT") i najviše 2–3 mala inline izbora relevantna za recept:
   pismo (latinica/ćirilica) za recepte sa prevodom; kvalitet (najbolji / do 2160p / 1080p / 720p) za video recepte;
   format zvuka (MP3 320 / MP3 V0 / M4A / Opus / FLAC / original) za muziku.
3. **Lista "Spremno za preuzimanje"** — KOMPAKTNI redovi (ne velike kartice):
   checkbox · mala sličica · naslov + kanal · trajanje · značke (npr. "4K★", "HDR", "13 audio", "11 CC",
   "LIVE", "✓ u biblioteci: 1080p · SR prevod" / "delimično") · status ("proveravam…", greška crvenom).
   - Stavke koje već postoje u biblioteci (izvor postoji) su podrazumevano ODČEKIRANE sa značkom "✓ imaš".
   - "⚙" na redu otvara detalje za taj video (tačan video format, audio trake, titl za prevod, force) — koristi
     postojeću logiku probe kartice; izmena važi samo za taj red (per-item override).
   - Plejlista/kanal = grupa: zaglavlje (naslov, broj, "u biblioteci X/Y", sve/ništa/samo novi, filter, sklopi),
     ispod kompaktni redovi stavki (bez per-item probe; prikaz do ~2000 redova glatko).
   - Link koji je deo plejliste (watch?v=…&list=…): na redu dugme "cela plejlista".
   - **Probe pojedinačne stavke plejliste (obavezno):** klik na red stavke (ili "⚙") pokreće
     `POST /api/probe {url}` za tu stavku (lenjo, keširano u memoriji; ponovni klik ne pravi novi zahtev) i
     rasklapa ispod reda detalje: najveći kvalitet + lista video formata, HDR/Premium, **audio trake** (jezik,
     original/dub, kodek), **titlovi** (ručni CC po jezicima + auto original), trajanje, i šta od toga već postoji
     u biblioteci (`archive` iz probe odgovora). U istim detaljima su per-item izbori (tačan format, audio,
     titl za prevod, force) — isto kao "⚙" za pojedinačan video; izbori idu kao items[i].opts.
     Posle probe red dobija značke (npr. "4K★ · 13 audio · 11 CC").
   - Dugme **"Proveri sve"** u zaglavlju grupe: probe svih (ili samo čekiranih) stavki u pozadini, najviše 3
     istovremeno, sa napretkom "proveljeno 12/155" i dugmetom "Stani"; značke se pune kako stižu.
     Probe nije uslov za preuzimanje (posao sam radi probe ako ga nema).
4. **Donja traka akcija (sticky)**: "Preuzmi (N)" primarno · "Očisti listu" · sažetak ("N stavki · recept Arhiva").
   Klik šalje SVE izabrane u red jednim pozivom (`POST /api/jobs/batch`), redovi nestaju iz liste, toast.

## 3. ▶ Red
Kompaktni redovi: sličica · naslov · jedna traka ukupnog napretka (izračunata iz faza) · tekst trenutne faze
("Preuzimanje 42% · 12 MB/s", "Prevod 3/10", "Burn 55%") · značke tipa (PREUZIMANJE/OBRADA/TOOLBOX, LIVE) ·
dugmad (Otkaži / Zaustavi snimanje / Ponovi / Folder / Detalji). "Detalji" otvara 4 faze + log.
Filteri: Aktivni · Završeni · Greške. "Očisti završene". Zaglavlje: zauzeti slotovi (preuzimanja, prevod, whisper, enkodiranje).

## 4. 📚 Biblioteka (katalog)
Traka: pretraga (naslov/kanal/link/ID) · tip (sve/video/samo audio/bez izvora) · plejlista · prevod (svi/sa SR/bez SR) ·
sortiranje · statistika. Kompaktni redovi sa checkbox-om: sličica (lokalna ako postoji) · naslov · značke
(izvor 1080p AV1 ★ · 13 audio · 12 CC · **SR ✓** ili "bez prevoda" · izlazi N · veličina) · "⚙" (detalji +
postojeći formular "Obradi…" za taj jedan) · "Folder".
"Izaberi sve prikazane" checkbox u zaglavlju.
**Batch traka (sticky, pojavi se kad je nešto izabrano)** — "Izabrano N":
- **Prevedi** (latinica/ćirilica; preskače stavke koje već imaju isti prevod) → obrada {output none}
- **Video sa titlom** (burn HEVC MP4; srpski titl; ako nema prevoda — prvo prevede) → obrada {output burn}
- **MKV sa titlovima** (bez rekodiranja) → obrada {output soft}
- **Transkoduj…** → izbor Toolbox preseta + "srpski titl: ne / meki / upečen" → transcode poslovi nad izvorima
- **Izvuci audio** (M4A / MP3 / FLAC) → Toolbox preset audio_*
- **Otvori foldere** (najviše 5)
Posle klika: toast "Dodato N poslova, preskočeno M (razlog)".

## 5. 🛠 Toolbox
Postojeći `static/toolbox.js` (lokalni fajlovi) u kartici #toolbox. Iz Biblioteke "⚙ → U Toolbox" dodaje izvor.

## 6. Podešavanja
Postojeća + `recipe` (podrazumevani recept), `script` (lat/cyr), prevodilac, `auto_translate` (bool, default true —
recepti Arhiva/Arhiva+video/Samo titlovi prevode; isključivanjem se prevod izbacuje iz recepata).

## 7. Backend (panel.py) — novo / izmene
- `GET /api/recipes` → `[{"id","icon","label","desc","dl":{...},"proc":{...}|null,"knobs":["script","quality"|"audio_conv"]}]`
  - arhiva: dl {video_fmt:"auto:best", audio_fmt:"all", subs:"all"}, proc {sub_source:"auto-best", target:<script>, output:"none", keep_source:true}
  - arhiva_video: dl isto; proc {sub_source:"auto-best", target:<script>, output:"burn", encoder:"hevc", vq:65, scale:"src", audio_out:"orig", audio_enc:"aac192", subs_out:"sr+orig", keep_source:true}
  - muzika: dl {video_fmt:"audio", audio_fmt:"auto", audio_conv:"mp3", subs:"none"}, proc null
  - titlovi: dl {video_fmt:"none", subs:"all"}, proc {sub_source:"auto-best", target:<script>, output:"srt"}
  - `<script>` = SETTINGS.script; ako `auto_translate` false → proc.target = "none" (i output none → proc null za arhiva).
- `POST /api/jobs/batch`: stavke mogu imati svoj `"opts"` (per-item override) koji se DUBOKO spaja preko zajedničkih opts (dl i proc posebno). Postojeće ponašanje ostaje.
- `get_cues` sa "auto-best": ako nema nijednog titla i NEMA izvora (npr. recept Samo titlovi) → ne greška; faza Titl "YouTube nema titl", prevod/izlaz preskočeni, posao "done". Ako izvor postoji → whisper (kao sada).
- `POST /api/library/batch {"folders":[...], "action", "params"}` → `{"jobs":[ids], "skipped":[{"folder","reason"}]}`:
  - `translate {target}` → process posao {sub_source auto-best, target, output none}; preskoči ako već postoji sr fajl za taj target.
  - `video {output: burn|soft|both, target, encoder?, vq?}` → process posao (ako nema izvora-videa → skip "nema videa").
  - `toolbox {preset, sub: none|soft|burn, target?}` → transcode posao nad `folder/source` sa spec = PRESETS[preset].spec + out_dir=folder + suffix "."+preset; ako sub≠none: koristi najnoviji `subs/sr-<target>*.srt` (sub_src "file", sub_file, translate off, sub_mode); ako ga nema → skip "nema srpskog prevoda — prvo Prevedi".
  - `audio {preset: audio_m4a|audio_mp3|audio_flac}` → transcode posao (samo audio).
  - `open` → otvori do 5 foldera.
  Sve rute ostaju pod Host/Origin + JSON zaštitom; folderi moraju biti `under_roots`.
- `GET /api/library` stavke dobijaju `"sr": ["lat"|"cyr", ...]` (koji prevodi postoje) i `"has_video": bool`.
- Podešavanja: `recipe` (str, "arhiva"), `auto_translate` (bool, true).

## 8. Prevod (yt_arhiva.py) — kvalitet (obavezno)
Zadržati potpis `translate(cues, ctx, args, work, progress=None)` i `make_llm`, `build_prompt` kompatibilno.
- **Kontekst pre prevoda**: jedan LLM poziv po videu (naslov + opis + ~150 prvih titlova) → JSON
  `{"synopsis": str (2–4 rečenice, srpski), "glossary": [{"src","sr"}] (imena, termini, do 40), "register": "vi"|"ti"}`;
  kešira se u `work/context.json`; ubacuje se u svaki batch prompt (sinopsis + glosar + oslovljavanje).
- **Batch po scenama**: sečenje na pauzi > 2 s, min 10 / max `args.batch` (30) titlova, umesto fiksnih blokova.
- **QC posle prevoda** (bez dodatnog modela): prazno, neprevedeno (isto kao izvor a izvor nije srpski), mešano pismo,
  odnos dužine sr/izvor van [0.3, 2.5], > 2 reda posle prelamanja ili red > 42 znaka posle wrap-a → linije koje padnu
  (osim prelamanja) se ponovo prevode pojedinačno sa ±3 linije konteksta (najviše 10% linija); izveštaj `log()`
  + vraća se preko `args.qc` (dict) ako postoji.
- Keš prevoda mora ostati kompatibilan (ili se bezbedno poništi) — ne sme vratiti pogrešan prevod za promenjen izvor.
- `tests/bench_translate.py` proširiti: meri pre/posle (kontekst+glosar) na istim uzorcima.
