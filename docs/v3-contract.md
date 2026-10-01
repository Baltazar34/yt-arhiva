# yt-arhiva v3 — ugovor (backend ↔ frontend ↔ installer)

Postojeće rute i ponašanje ostaju. UI tekst: srpski latinica sa č ć š ž đ. Sve POST rute:
Host/Origin + application/json zaštita kao i do sada; putanje moraju proći `under_roots`.

## A. Editor titla + ispravke za obuku
- `GET /api/subs?f=<folder>&file=<rel u folderu, npr. subs/sr-lat.x.srt>` →
  `{"entry": <manifest subs stavka ili null>, "cues": [{"i","start","end","text"}],
    "src": [{"i","start","end","text"}] | null, "src_entry": <stavka izvora> | null,
    "media": "<rel putanja izvora (video/audio) ili null>"}`
  - `src` = titl iz kog je prevod nastao (`entry.src` ključ); poravnanje po indeksu (isti broj titlova kao kod prevoda).
- `POST /api/subs/save {"folder","file","cues":[{"i","start","end","text"}]}` →
  `{"ok": true, "changed": N, "pairs_saved": M, "backup": "<rel>"}`
  - Prethodna verzija ide u `.yta/history/<ime>.<YYYYmmdd-HHMMSS>.srt`; fajl se atomski prepisuje; manifest `count` osvežen.
  - Za svaku promenjenu liniju prevoda upisuje se par u `DATA/corrections.jsonl`:
    `{"ts","video_id","folder","file","lang":"sr-lat|sr-cyr|el|…","engine","model","src","before","after"}`
    (engine/model iz ključa stavke; `src` = izvorni tekst iste linije ako postoji).
- `GET /api/corrections/stats` → `{"total": N, "by_lang": {...}, "path": "<putanja>"}` (za prikaz "N ispravki za obuku").
- `GET /api/media?f=<folder>&file=<rel>` → fajl izvora sa podrškom za HTTP `Range` (206, Accept-Ranges, Content-Range,
  Content-Type po ekstenziji: mkv video/x-matroska, mp4 video/mp4, webm, mp3 audio/mpeg, m4a audio/mp4, opus audio/ogg, flac).
  Samo fajlovi unutar `f` i unutar `under_roots`. (Za pregled u editoru; Chrome ne pušta sve MKV/HEVC — UI to toleriše.)

## B. Lektura (Claude ili Gemini) postojećeg prevoda
- `POST /api/library/batch {"action":"lektura","folders":[...],"params":{"target":"lat|cyr|el","engine":"claude|gemini","model":"sonnet|opus|gemini-…"}}`
  → process posao po stavci: uzme NAJNOVIJI prevod za `target` + njegov izvor, lektura u blokovima (min. izmene:
  gramatika, rod/padež, značenje prema izvoru, imena dosledno), rezultat = NOV prevod `…<engine>-lektura…srt`,
  manifest stavka kind `sr` (lat/cyr) ili `tr` (el), ključ `…:lektura:<src key>`, ime "… — Claude lektura".
  Skip razlozi: "nema prevoda za lekturu", "već ima lekturu" (osim `force`).
  - Claude ide preko `claude -p` (instaliran i prijavljen `claude` CLI, već postoji `core.call_claude`); u testovima MOCK.
  - Faze posla: subs = učitavanje, translate = lektura (napredak po bloku), output = skipped.
- `yt_arhiva.py`: `review_translation(src_cues, draft_cues, title, lang, llm, args, progress=None) -> list[str]`
  (poravnanje 1:1, JSON {"items":[{"id","t"}]}, za srpski `finalize_script`, za grčki provera pisma).

## C. Plejliste: snimak, stanje, album
- Pri svakom `probe_list` (plejlista/kanal) snima se `DATA/playlists/<playlist_id>.json`:
  `{"id","title","url","uploader","fetched_at","entries":[{"id","title","duration","pos"}], "history":[{"fetched_at","count"}], "removed":[{"id","title","last_seen"}]}`
  - `removed` = ID-jevi koji su bili u prethodnom snimku, a sada ih nema (sklonjeni sa YouTube-a); čuvaju se.
- Odgovor `probe_list` dobija po stavci `"state": "have"|"missing"|"new"` (new = nije bila u prethodnom snimku) i
  na vrhu `"removed": [...]`, `"new_count"`.
- `GET /api/playlists` → lista snimaka: `[{"id","title","count","have","missing","removed","fetched_at","url"}]`
  (have/missing po katalogu, računato pri zahtevu).
- `POST /api/playlists/<id>/missing` → posao preuzimanja za sve `missing` (isti opts kao u telu: `{"opts":{dl,proc}}` ili `{"recipe":"arhiva"}`),
  vraća `{"jobs":[...]}`.
- `POST /api/playlists/<id>/album {"m3u": true, "tags": true}` → za stavke plejliste koje su u biblioteci kao
  SAMO AUDIO: upis tagova bez rekodiranja (ffmpeg -c copy u privremeni pa zamena): album=naslov plejliste,
  track=N/M po `pos`, artist=kanal (ako nema), album_artist=uploader plejliste; i `<folder plejliste ili out_dir>/<naslov>.m3u8`
  (relativne putanje, redosled po `pos`, #EXTINF trajanje i naslov). Vraća `{"tagged": N, "m3u": "<putanja>", "skipped":[...]}`.

## D. Zdravlje kataloga
- `GET /api/health` → `{"items":[{"folder","title","issues":["izvor nedostaje","izvor skraćen (82%)","titl nedostaje: subs/x.srt","izlaz nedostaje: …"]}], "orphans":[{"folder","id"}], "checked_at"}`
  - "skraćen" = ffprobe trajanje izvora < 98% manifest `duration` (keš po putanji+mtime+veličini da se ffprobe ne ponavlja).
  - orphans = folderi u root-ovima sa `[<11 znakova id>]` u imenu i bez `.yta/manifest.json`.
- `POST /api/health/rebuild` → za orphans napravi minimalan manifest iz fajlova (id iz imena, source = najveći video/audio
  fajl, subs/*.srt kao stavke manual/auto po imenu) → `{"rebuilt": N}`.

## E. Audio iz postojeće arhive (bez ponovnog preuzimanja)
- `POST /api/library/batch {"action":"audio_copy","folders":[...]}` → posao: originalna audio traka izvora
  (`manifest.audio[].original`) izvučena BEZ rekodiranja (aac→`.m4a`, opus→`.opus` ili `.mka`, ostalo `.mka`),
  sa omotom (`.yta/thumb.jpg` ako postoji) i tagovima (title, artist=kanal, date) → `<stem> [id].audio-orig.<ext>`, dodaje se u outputs.
  Skip: "nema izvora", "izvor nema zvuk".

## F. Uloga audio trake
- `manifest.audio[].role`: "original" | "dub" | "default" | "descriptive" (iz `language_preference`: 10 original, 5 default,
  -10 descriptive, inače dub ako format_note sadrži "dub"); UI prikazuje pored jezika.

## G. Installer (poseban folder `installer/`, ne dira ostale fajlove)
- `installer/install.sh`: macOS arm64 provera; Homebrew (ponudi instalaciju ako nema); `brew install yt-dlp deno ffmpeg-full whisper-cpp ollama`
  (preskače instalirano); kopira aplikaciju u `~/Applications/YT Arhiva/app` (ili `--prefix`); pravi `~/Applications/YT Arhiva.app`
  (osacompile ili shell .app bundle) koji pokreće panel u pozadini (ako ne radi) i otvara `http://127.0.0.1:8790`; opciono
  LaunchAgent za start pri prijavi (`--autostart`); opciono `ollama pull gemma4:12b-it-qat translategemma:12b` (pita, `--models`);
  opciono whisper model; `OLLAMA_MODELS` lokacija (pita / `--models-dir`); upozorenje za prostor na disku; `--dry-run` ništa ne menja.
- `installer/uninstall.sh` (ne briše arhivu ni modele osim uz `--purge`), `installer/build.sh` → `dist/yt-arhiva-<ver>.tar.gz`
  (+ opciono `.pkg` preko pkgbuild ako postoji), `installer/README.md` (srpski, koraci za korisnika, ključevi u Keychain-u).
- Test: `install.sh --dry-run`, build tar.gz, raspakovati u temp i proveriti sadržaj; .app napraviti u temp `--prefix`.
