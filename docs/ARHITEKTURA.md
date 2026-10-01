# YT Arhiva — tehnička arhitektura (3.2.1)

Za programera koji preuzima projekat. Opisuje ono što kod radi u verziji iz fajla `VERSION` (3.2.1).
Detalji pojedinih delova su u ugovorima: [plan-contract.md](plan-contract.md) (3.2: „Šta da uradim“, ceo sadržaj,
stanje prevoda), [memory-contract.md](memory-contract.md) (3.1: memorija ispravki),
[v3-contract.md](v3-contract.md) (editor titla, lektura, plejliste, zdravlje kataloga, installer),
[toolbox-contract.md](toolbox-contract.md) (transkoder), [ux-v2.md](ux-v2.md) (raspored kartica, recepti).
Uputstvo za korisnika: [../README.md](../README.md) i [../installer/README.md](../installer/README.md).

Sadržaj: 1 Pregled · 2 Moduli · 3 Tok posla · 4 Model podataka · 5 HTTP API · 6 Bezbednost · 7 Podešavanja ·
8 Prevod · 9 Whisper · 10 Memorija ispravki · 11 Toolbox · 12 Prvo korišćenje · 13 Installer i build · 14 Testovi ·
15 Izdavanje verzije · 16 Poznata ograničenja

---

## 1. Pregled

- Lokalni web panel: Python 3.9+ samo stdlib (`http.server.ThreadingHTTPServer`, niti, `subprocess`), bez pip
  zavisnosti. Sluša samo na `127.0.0.1:<YTA_PORT>` (podrazumevano 8790).
- UI: jedna stranica `static/index.html` + tri obična skripta (`plan.js`, `toolbox.js`, `memory.js`), bez build koraka.
- Spoljni alati (pokreću se kao procesi):

| Alat | Za šta | Odakle |
|---|---|---|
| `yt-dlp` + `deno` | probe, preuzimanje, titlovi (`--js-runtimes deno`, kolačići iz browsera) | Homebrew |
| `ffmpeg` / `ffprobe` iz `ffmpeg-full` | remux, burn (libass), VideoToolbox, Toolbox | `/opt/homebrew/opt/ffmpeg-full/bin` ili `/usr/local/opt/ffmpeg-full/bin` (`core.find_ffmpeg`) |
| `whisper-cli` (whisper.cpp) | transkript (Metal) | Homebrew `whisper-cpp`; modeli u `WHISPER_DIR` (poglavlje 9) |
| `ollama` | lokalni prevod (Gemma 4, TranslateGemma) | Homebrew; HTTP `OLLAMA_HOST` (podrazumevano `http://127.0.0.1:11434`) |
| `claude` CLI | prevodilac „claude“ i lektura | opciono, zasebno instaliran i prijavljen (`claude -p`) |
| `security` | API ključevi iz Keychain-a | macOS |
| `osascript`, `open` | izbor fajla/foldera, otvaranje u Finder-u | macOS |
| `curl` | whisper modeli i VAD model sa Hugging Face-a | macOS |

Arhitektura u jednoj slici:

```
browser (index.html + plan.js/toolbox.js/memory.js)
   │  fetch JSON; tajmer 1 s: /api/jobs (svake sekunde na kartici Red, inače 3 s / 10 s),
   │  /api/status (5 s), /api/library (5 s na kartici Biblioteka, inače 30 s)
   ▼
panel.py  H(BaseHTTPRequestHandler)  ── Host/Origin + JSON zaštita
   │
   ├─ JOBS{id: Job} ── nit po poslu (run_job) ── limiteri LIM + folder_lock
   │      ├─ download:  yt-dlp → finalize_source → titlovi → (prevod) → (izlaz) → after koraci
   │      ├─ process:   iz arhive: titl / whisper → prevod → izlaz | lektura | audio_copy
   │      └─ transcode: toolbox.plan → ffmpeg
   ├─ yt_arhiva.py (core): prevod, kontekst, QC, kes, srt/ass, LLM pozivi, ćirilica
   ├─ memory.py: memorija ispravki (corrections.jsonl + memory.json)
   └─ toolbox.py: analyze / plan (čist modul)
   │
   ▼
disk: arhiva (<out_dir>/<stavka>/.yta/manifest.json …)  +  DATA (settings.json, jobs.json, …)
```

## 2. Moduli i odgovornosti

| Fajl | Odgovornost |
|---|---|
| `panel.py` | HTTP server i sve rute; podešavanja (`DEFAULTS`, `load_settings`, `save_settings`, jednokratna migracija `migrate_plan`); poslovi (`Job`, `run_job`, `start_job`, perzistencija u `jobs.json`); limiteri (`Limiter`, `slot`, `folder_lock`); probe videa i plejlisti/kanala; preuzimanje (`build_selector`, `do_download`, `finalize_source`, `salvage` za live); titlovi (`sub_keys_for`, `fetch_subs`, `embed_source_subs`); whisper (`whisper_media`, `do_whisper`, prompt); prevodni korak (`translate_cues`, `translate_step`); izlaz (`do_output`: SRT, meki titl MKV, burn); biblioteka i katalog (`library`, `catalog_index`, `cat_short`, `tr_summary`); batch akcije Biblioteke (`library_batch`); koraci posle preuzimanja (`validate_after`, `run_after_steps`); editor titla (`subs_get`, `subs_save`); lektura; audio iz arhive; plejliste (snimak, „nedostaje“, album); zdravlje kataloga; Ollama / Homebrew prvo korišćenje. |
| `yt_arhiva.py` (u panelu `core`) | Jezgro bez HTTP-a, i CLI (`yt-arhiva URL [--cyr] [--no-burn] [--engine ollama\|claude] …`). Probe i izbor titla (`orig_lang`, `pick_track`), json3 → cues, SRT/ASS upis, python-whisper rezerva (`whisper_cues`), sav prevod: `translate` (srpski), `translate_to` (drugi jezici, sada grčki), kontekst (`get_context`), scene (`scene_batches`), QC (`qc_pass`), keš, LLM pozivi (`call_ollama`, `call_gemini`, `call_cloudflare`, `call_claude`, `call_tg`, `make_llm`, lanac), lektura (`review_translation`), pismo (`finalize_script`, `to_cyr`, `normalize_script`), `wrap_lines`. |
| `memory.py` | Memorija ispravki: čita `corrections.jsonl` + `memory.json`, gradi pravila, TM, primere, whisper termine; `Memory.load(DATA)`, `MemView`. Samo stdlib; od jezgra koristi samo `core.normalize_script`. Ugovor: [memory-contract.md](memory-contract.md). |
| `toolbox.py` | Čist modul transkodera (bez HTTP-a, niti i importa panela): `PRESETS`, `DEFAULT_SPEC`, `analyze`, `plan`, `output_dims`, `default_out_path`. Panel ga uvozi lenjo (`tb()`). Ugovor: [toolbox-contract.md](toolbox-contract.md). |
| `static/index.html` | Ceo UI i glavni skript: kartice Preuzmi / Red / Biblioteka / Memorija / Toolbox / Podešavanja (hash rute), editor titla, forme za obradu, helperi `$`, `esc`, `api`, `LS` (localStorage sa prefiksom `yta.`). |
| `static/plan.js` | Kartica „Šta da uradim“: čisti helperi (stanje ↔ opcije posla, brzi izbor, pravila isključivanja redova, sažetak, bedž stanja prevoda) izloženi kao `window.__planTest`; kontroler preko `ctx`. Učitava se PRE glavnog skripta. |
| `static/toolbox.js` | UI Toolbox kartice (`window.__tbTest`). Učitava se posle glavnog skripta. |
| `static/memory.js` | UI kartice Memorija i „＋ Glosar“ u editoru (`window.__memTest`). Učitava se posle glavnog skripta. |
| `static/uputstvo.html`, `static/uputstvo/*.png` | Korisničko uputstvo sa slikama ekrana; link „? Pomoć“ u zaglavlju panela otvara `/static/uputstvo.html` u novom tabu. |
| `installer/` | `install.sh`, `uninstall.sh`, `build.sh`, `launcher.sh`, zajedničko `common.sh`, korisničko `README.md` (poglavlje 13). |
| `tests/` | Testovi i benchmark-ovi (poglavlje 14). |

localStorage ključevi UI-ja (`yta.` + ključ): `tab`, `plan` (redovi „Šta da uradim“), `plan_custom` (polja
„⚙ Sve opcije“ koja redovi nemaju), `knobs` (samo se čita: dugmići recepata iz 3.1 kao početne vrednosti), `jf`
(filter Reda), `lib.libKind` / `lib.libSr` / `lib.libSort` (i `lib.libPl`, koji se upisuje ali ne vraća), `libView`,
`tcPreset` (Toolbox preset u Biblioteci), `lkEng` (prevodilac lekture), `mem.type` (filter kartice Memorija).

## 3. Tok posla

### 3.1 Vrste poslova

| `Job.kind` | Pravi ga | Funkcija |
|---|---|---|
| `download` | `POST /api/jobs`, `/api/jobs/batch`, `/api/playlists/<id>/missing` | `run_download_job` |
| `process` | `POST /api/process`, `library_batch` (translate, video, transcribe, lektura, audio_copy) | `run_process_job`; `proc.action` `lektura` → `run_lektura`, `audio_copy` → `run_audio_copy` |
| `transcode` | `POST /api/tb/jobs`, `library_batch` (toolbox, audio), koraci posle preuzimanja | `run_transcode_job` (sa `opts._lib` = folder stavke: pod `folder_lock`, izlaz ide u manifest) |

Svaki posao ima 4 faze (`STAGES`): `download`, `subs`, `translate`, `output`. Status faze: `pending`, `waiting`
(čeka slot), `running`, `done`, `skipped`, `error`, `cancelled`; uz to `pct` (0–100 ili `null`) i `info` (tekst).
Toolbox posao koristi iste faze: `download` = analiza (ffprobe), `subs`/`translate` = priprema titla, `output` = ffmpeg.

### 3.2 Stanja posla

`queued` → `running` → `done` | `error` | `cancelled`. `run_job` odmah prelazi u `running`; čekanje na slot se vidi
kao faza u statusu `waiting` („čeka slobodan slot…“). Posao čuva poslednjih 400 redova loga (`deque`).

- **Otkaži** (`/cancel`): `cancel` događaj + SIGTERM grupi procesa posla; `Limiter.acquire` i `job.check()` bacaju
  `Cancelled`.
- **Zaustavi** (`/stop`, live): `stop_live` + SIGINT procesima posla (tada je to yt-dlp); snimljeno se spaja
  (`salvage`) i posao nastavlja.
- **Ponovi** (`/retry`): isti `Job` ponovo kroz `run_job` (faze, izlazi i `after_jobs` se brišu). Preuzimanje ide u
  isti folder ako manifest ima isti ID; završen `video.*` u `.yta/dl/` se samo finalizuje, a yt-dlp nastavlja delove.
  409 ako posao (ili njegova nit preuzimanja) još radi.
- **Ukloni** (`/remove`): samo kad posao ne radi (inače 409).

### 3.3 Preuzimanje (`run_download_job`)

1. `opts` se normalizuju (`normalize_opts`: stari ravni oblik → `{dl, proc}`). Ako postoje `opts.after` i
   `proc.keep_source` je `false`, izvor se ipak zadržava (koraci posle rade iz njega).
2. Probe: iz keša `PROBES[url]` (popunjava ga `/api/probe`) ili `yt-dlp -J` pod slotom `probe`.
3. Folder stavke: `<out_dir>/<upload_date> - <naslov> [<id>]` (`item_dir`, `core.safe_name`, najviše 90 znakova).
   Za plejlistu sa `dl.subfolder` ide u podfolder sa imenom plejliste. `remember_root(out_dir)` upisuje koren.
4. Pod `folder_lock`: `init_manifest` (manifest + `.yta/info.json` + `subs/`), stavka plejliste se dodaje u
   `playlists`, pa `_download_and_process`:
   - nit `do_download` (ako `video_fmt != "none"`) pod slotom `download`: `yt-dlp -f <selektor> --merge-output-format mkv
     -N <fragments> --embed-metadata --embed-thumbnail --write-thumbnail --convert-thumbnails jpg`, uz
     `--embed-chapters` za video i `-x` + `AUDIO_CONV` za samo audio sa jednom trakom (više traka → MKV bez
     konverzije); `--audio-multistreams` kad ima više traka;
     live: `--live-from-start` (najavljen: `--wait-for-video 30`). Napredak se čita iz `--progress-template`.
     Ako je izvor iste vrste već u arhivi i nema `dl.force`, preuzimanje se preskače („već u arhivi“).
     `finalize_source`: kad ima više od jedne audio trake, upisuje nazive i jezike traka (remux `-c copy`; omot
     ostaje MKV prilog preko `copy_maps`); premešta izvor u `<stem> [id].source.<ext>` (obično `.mkv`; samo audio:
     `.audio.<ext>`), sličicu u `.yta/thumb.jpg`, i upisuje manifest (`source`, `kind`, `video`, `audio`,
     `source_subs: []`);
   - istovremeno (nije live): `sub_keys_for` + `fetch_subs` (jedan yt-dlp poziv, json3 → SRT u `subs/`), pa ako
     postoji `proc` i titl nije `whisper:*`: `get_cues` + `translate_step` dok se video još skida;
   - čeka nit preuzimanja; greška u titlu/prevodu se baca tek posle (video ipak završi u arhivi);
   - live: re-probe posle kraja streama i tek tada titlovi;
   - `embed_source_subs`: titlovi i kao trake u MKV izvoru (osim ako će obrada obrisati izvor);
   - bez `proc`: `translate` i `output` su `skipped` („kasnije (Arhiva → Obradi)“);
   - sa `proc`: `get_cues` (whisper ako treba) → `translate_step` → `do_output` → `finish_source`.
5. Posle izlaska iz `folder_lock`: `run_after_steps` (ako `opts.after` i posao nije otkazan).

Selektor (`build_selector`): `auto:best` → `bv*+<sve audio grupe>` uz rezerve; `auto:<h>` → `bv*[height<=h]`;
`id:<fid>` → tačan format; `audio_fmt` `all` = sve audio grupe (`audio_groups`: najbolji format po (jezik, uloga),
DRC i slabiji kodek iste uloge se preskaču), `auto` = najbolji jedan, `id:<fid>` = tačan.

Titlovi (`sub_keys_for`, `dl.subs`, bez ključa = `auto`): `all` = svi ručni CC + automatski na originalnom jeziku;
`all+tr` = i svi YouTube automatski prevodi; `auto` = jedan po `core.pick_track` (ručni srpski > ručni originalni >
bilo koji ručni, engleski prvi > automatski originalni); `none`; ili tačan `manual:<k>` / `auto:<k>`. Titl koji traži
`proc.sub_source` se dodaje ako ga YouTube ima. `fetch_subs` preuzima samo titlove kojih još nema u manifestu, jednim
yt-dlp pozivom, uz `--sleep-subtitles 1` kad ih je više od 6.

### 3.4 Obrada (`proc`)

`get_cues` bira titl: `none` → bez titla; ključ stavke ili `auto-best` → `choose_sub` (ručni srpski > ručni
originalni > ručni engleski/prvi > automatski > whisper); `whisper:run` → uvek whisper; ako titla nema, whisper iz
izvora. `translate_step`: ako je titl već srpski (ručni sr/sh ili prevod) samo pismo; `target: "none"` → original;
inače prevod (poglavlje 8). `do_output` (`proc.output`):

| `output` | Rezultat |
|---|---|
| `none` | ništa (titl/prevod ostaje u `subs/`) |
| `srt` | `<stem> [id].<tag>.srt` pored izvora |
| `soft` | `<stem> [id].<tag>[.audio-…].mkv`: video i audio `-c copy`, titlovi `srt` |
| `burn` | `….mp4` (AAC, `mov_text`, `+faststart`) ili `….burn.mkv` uz `audio_enc: "copy"`; ASS kroz `ass=` filter |
| `both` | `soft` + `burn` |

Enkoderi: `hevc` (`hevc_videotoolbox -q:v`, 10-bit izvor → `p010le` main10), `h264` (`h264_videotoolbox`), `x264`,
`x265` (CRF). HDR izvor: za `h264`/`x264` tonemap u SDR (`TONEMAP_SDR`), inače HDR ostaje, a titl dobija prigušenu
belu. `scale` = visina (samo naniže). `audio_out`: `orig` | `all` | `idx:<n>` | `fid:<format_id>` | `lang:<kod>`
(`pick_audio`). `subs_out`: `sr` (samo glavni) | `sr+orig` (+ izvorni titl prevoda) | `sr+all` (+ svi titlovi).
`finish_source` briše izvor samo uz `keep_source: false` I napravljen video izlaz.

### 3.5 Koraci posle preuzimanja (`opts.after`, 3.2)

`{"toolbox": {"preset", "sub", "target"}, "audio": {"preset"}}` — validira `validate_after` (preset iz
`toolbox.PRESETS`; `sub` ∈ none/soft/burn; `target` ∈ lat/cyr/el, samo uz titl; audio preset ∈ `audio_m4a`,
`audio_mp3`, `audio_flac`; upečen titl uz preset koji kopira video → 400; nepoznat ključ → 400). Posle uspešnog
preuzimanja `run_after_steps` za toolbox pa audio zove `lib_transcode` i pokreće transcode posao:
`job.after_jobs` (ID-jevi) ili `job.after_skipped` (`[{step, label, reason}]`, npr. „nema prevoda … — prvo Prevedi“).
Greška u tom koraku ne obara preuzimanje.

### 3.6 Limiteri, slotovi, zaključavanja

| Limiter (`LIM`) | Veličina | Faza koja čeka |
|---|---|---|
| `download` | `parallel_downloads` (3) | download |
| `probe` | 4 (fiksno) | download |
| `whisper` | 1 (fiksno) | subs |
| `translate` | `parallel_translate` (1) — i lektura | translate |
| `encode` | `parallel_encodes` (2) — burn, meki MKV, Toolbox | output |

- `save_settings` odmah menja veličinu `download`/`encode`/`translate`.
- `folder_lock`: jedan posao po stavci arhive (deljeni `.yta/dl`, `raw`, izlazi). Upis tagova albuma koristi
  `try_folder_lock` (bez čekanja: „stavka je zauzeta“).
- `MLOCK`: upisi manifesta (`update_manifest` = čitaj–izmeni–atomski upiši).
- `FETCH_LOCK`: jedna Homebrew instalacija ili `ollama pull` iz posla u isto vreme (dugme „Preuzmi model“ u
  Podešavanjima, `/api/ollama/pull`, ide mimo njega; dok ono radi, novo preuzimanje se ne pokreće).

### 3.7 `jobs.json`

`_persist_loop` upisuje sve poslove (sa logom) najviše jednom u ~1 s posle izmene (`persist_soon`). Na startu
`load_jobs` ih čita; posao koji je bio `queued`/`running` postaje `error` („prekinuto (panel restartovan) — klikni
Ponovi“). Poslovi ostaju dok ih korisnik ne ukloni.

Oblik posla (`Job.to_dict`): `id`, `kind`, `url`, `title`, `thumb`, `vid`, `live`, `state`, `error`, `folder`,
`outputs`, `created`, `finished`, `stages`, `opts`, `after_jobs`, `after_skipped`, i `log_tail` (lista) ili `log`
(`jobs.json`, `/api/jobs/<id>/log`).

### 3.8 Opcije posla (`opts`)

| Deo | Ključevi |
|---|---|
| `dl` | `video_fmt` (`auto:best`, `auto:<visina>`, `id:<fid>`, `audio`, `none`), `audio_fmt` (`all`, `auto`, `id:<fid>`), `audio_conv` (`best`, `mp3`, `mp3v0`, `m4a`, `opus`, `flac`), `subs` (vidi 3.3), `out_dir`, `subfolder`, `force` |
| `proc` (`null` = bez obrade) | `sub_source`, `whisper_lang`, `target` (`lat`, `cyr`, `el`, `none`), `engine`, `model`, `review` (`"on"`), `output`, `encoder`, `vq`, `scale`, `audio_out`, `audio_enc` (`copy`, `aac<kb/s>`), `subs_out`, `keep_source`; interno `action` (`lektura`, `audio_copy`), `draft` |
| `after` | vidi 3.5 |
| interno (`_` prefiks) | `_title`, `_thumb`, `_id`, `_live`, `_playlist`; Toolbox: `path`, `spec`, `_lib`, `_sub_name`, `_sub_script` |

Batch (`item_opts`): zajedničke opcije + opcije stavke; `dl`, `proc` i `after` se spajaju posebno, `{"proc": null}`
za stavku = ta stavka bez obrade.

## 4. Model podataka

### 4.1 Stavka arhive (folder)

Ime foldera je `safe_name("<YYYYMMDD> - <naslov> [<id>]")`, najviše 90 znakova (vidi 16); `<naslov>` u imenima
fajlova je `safe_name(naslov)` (najviše 90 znakova) i iza njega uvek stoji `[<id>]`.

```
<koren>/<YYYYMMDD> - <naslov> [<id>]/
  <naslov> [<id>].source.mkv          izvor: video + sve audio trake (+ titlovi kao trake, poglavlja, omot)
  <naslov> [<id>].audio.<ext>         izvor kad je preuzet samo audio
  <naslov> [<id>].<tag>….mp4|mkv|srt  izlazi obrade / Toolbox-a / audio_copy (svi u manifest "outputs")
  subs/*.srt                          svi titlovi i prevodi
  .yta/manifest.json                  manifest (4.2) — stavka postoji samo ako on postoji
  .yta/info.json                      ceo yt-dlp info (opis za kontekst prevoda, re-probe live-a)
  .yta/thumb.jpg                      lokalna sličica (/api/thumb)
  .yta/format_id.txt                  format_id-jevi koje je yt-dlp stvarno preuzeo
  .yta/dl/, .yta/dl-audio/            radni folder preuzimanja (briše se posle finalize_source)
  .yta/raw/                           json3 titlovi pre konverzije
  .yta/work/<job>/                    privremeni fajlovi posla (whisper wav, ASS, ubacivanje titlova)
  .yta/cache/<ključ titla>/           keš prevoda (8.6)
  .yta/history/<ime>.<YYYYmmdd-HHMMSS>.srt   prethodne verzije titla (editor, lektura)
```

### 4.2 `.yta/manifest.json`

```json
{
  "version": 1, "url": "https://www.youtube.com/watch?v=XXXXXXXXXXX", "id": "XXXXXXXXXXX",
  "title": "…", "channel": "…", "duration": 1234.0, "thumbnail": "https://…", "upload_date": "20260101",
  "language": "en", "live_status": "not_live",
  "source": "… [XXXXXXXXXXX].source.mkv", "kind": "video",
  "video": {"format_id": "…", "width": 3840, "height": 2160, "codec": "vp9", "fps": 30, "hdr": false,
            "premium": false, "duration": 1234.0},
  "audio": [{"index": 0, "lang": "en", "original": true, "variant": "original", "role": "original",
             "codec": "opus", "channels": 2, "abr": 130, "format_id": "…", "title": "English — original",
             "label": "English — original · opus 130k"}],
  "subs": [{"key": "manual:en", "kind": "manual", "lang": "en", "file": "subs/en.srt", "count": 512, "name": "English"}],
  "source_subs": [{"key": "manual:en", "lang": "en", "kind": "manual"}],
  "outputs": ["… [XXXXXXXXXXX].sr-lat.mp4"],
  "playlists": [{"id": "PL…", "title": "…"}],
  "updated": 1767225600.0
}
```

- `source`: ime fajla u folderu ili `null` (izvor obrisan / nije preuzet). `kind`: `video` | `audio`.
- `video`: `null` za samo audio ili izvor bez video trake. Stari manifesti bez ključa `video` = video.
- `audio[]`: `index` (redni broj audio trake u izvoru), `lang` (osnovni kod), `original`, `variant`
  (`original`/`dub`/""), `role` (`original` | `dub` | `default` | `descriptive`, iz YouTube `language_preference`
  10/5/−10 i `format_note`; stari manifesti bez `role` → izvodi `with_role`), `codec`, `channels`, `abr`,
  `format_id`, `title` (upisan i u fajl), `label`. `"audio": []` = izvor bez zvuka (`has_audio: false`).
- `source_subs`: titlovi već ubačeni kao trake u MKV izvor (ponovno ubacivanje dodaje samo nove).
- `rebuilt`: postoji kad je manifest obnovljen iz fajlova (`/api/health/rebuild`).

### 4.3 Stavke `subs`

| `kind` | `key` | `file` |
|---|---|---|
| `manual` (YouTube CC) | `manual:<lang>` | `subs/<lang>.srt` |
| `auto` (YouTube ASR / auto prevod) | `auto:<lang>` ili `auto:<lang>-orig` | `subs/<lang>.auto.srt` |
| `whisper` | `whisper` | `subs/whisper.srt` |
| `sr` (srpski prevod) | `sr:<lat\|cyr>:<engine>:<model>[:rev]:<izvorni ključ>` | `subs/sr-<lat\|cyr>.<izvor>.<engine>_<model>[_rev].srt` |
| `tr` (drugi jezik, npr. el) | `tr:<lang>:<engine>:<model>:<izvorni ključ>` | `subs/<lang>.<izvor>.<engine>_<model>[_rev].srt` |
| lektura (`sr`/`tr`) | `…:<engine>:<model>:lektura:<izvorni ključ>` | `subs/…<engine>-lektura_<model>.srt` |
| obnovljen manifest | `sr:<t>:rebuilt:<stem>` itd. (`sub_entry_from_file`) | postojeći fajl |

Zajednička polja: `key`, `kind`, `lang`, `file`, `count`, `name`. Prevod dodaje `src` (ključ izvornog titla),
`engine`, `model`, `date` (YYYY-MM-DD). Lektura dodaje `draft`, `changed`, opciono `fallback` (koji servis je
uskočio). Editor posle čuvanja dodaje `edited` (unix vreme). `key_engine_model` za stare stavke čita engine/model
iz ključa.

### 4.4 Izvedena polja Biblioteke (`library()`, `cat_short`)

Bez ffprobe, iz manifesta i postojanja fajlova: `tr_state` (`translated` = bar jedan prevod `sr`/`tr` ili ručni
srpski CC; `transcript` = ima titl/transkript bez prevoda; `none`), `translated[]` (`target`, `engine`, `model`,
`date`, `lektura`, `file`, `name`; najnoviji prvi), `transcripts[]` (`kind`, `lang`, `file`), `sr` (pisma postojećih
prevoda), `tr`, `sr_cc`, `has_video`, `has_audio`, `kind`, `size`, `source_size`, `thumb_local`, `outputs`
(samo postojeći). `catalog_index()` = ID → stavke (za „U arhivi već imaš“ i plejliste).

### 4.5 Koreni biblioteke

`lib_roots()` = `out_dir` iz podešavanja + spisak iz `library_roots.json` (svaki folder u koji je nešto preuzeto, i
`out_dir` Toolbox-a). Biblioteka pretražuje samo prvi nivo svakog korena.

### 4.6 DATA folder (`YTA_DATA`, inače `<repo>/data`; installer: `<prefix>/data`)

| Fajl | Sadržaj |
|---|---|
| `settings.json` | podešavanja (poglavlje 7) + `plan_migrated` |
| `jobs.json` | svi poslovi sa logom (3.7) |
| `library_roots.json` | dodatni koreni biblioteke (lista putanja) |
| `corrections.jsonl` | append-only parovi iz editora titla: `ts`, `video_id`, `folder`, `file`, `lang`, `engine`, `model`, `src`, `before`, `after`, `type` (`tr`/`stt`), `channel`, `src_lang` |
| `memory.json` | korisnički glosar, stanja pravila (`rules: {id: "on"\|"off"}`), isključene ispravke (`disabled`) |
| `playlists/<id>.json` | snimak plejliste/kanala (tab kanala ide u ključ, npr. `<id>_shorts`): `entries`, `history`, `removed` |
| `probe_cache.json` | ffprobe trajanja za zdravlje kataloga (ključ putanja\|mtime_ns\|veličina) |
| `ollama.log` | izlaz `ollama serve` koji je pokrenuo panel |
| `tb/<job>/` | radni folder Toolbox posla (briše se), `tb/preview` za `/api/tb/command` |
| `panel.log`, `panel-<port>.pid`, `launchagent.log` | pravi ih `launcher.sh` / LaunchAgent, ne panel |

## 5. HTTP API

Sve rute vraćaju JSON (osim `/`, `/static/*`, `/api/thumb`, `/api/media`). Greške: `{"error": "…"}` sa 400 (loš
zahtev), 403 (Host/Origin, media van panela), 404, 409 (posao radi), 415 (nije `application/json`), 500; uz to 416
(neispravan `Range` za `/api/media`) i 405 (HEAD na drugu rutu, bez tela). Izuzetak u GET rutama `/api/settings`,
`/api/recipes`, `/api/jobs`, `/api/library`, `/api/thumb`, `/api/status`, `/api/tb/presets` nije uhvaćen: veza se
zatvara bez odgovora (vidi 16).

### 5.1 GET

| Ruta | Parametri | Odgovor |
|---|---|---|
| `/`, `/index.html` | — | `static/index.html` |
| `/static/<fajl>` | — | fajl iz `static/` i podfoldera (bez izlaska iz foldera); tip po ekstenziji: `.js`, `.css`, `.html`, `.png`, ostalo `application/octet-stream` |
| `/api/settings` | — | trenutna podešavanja |
| `/api/recipes` | — | recepti (`samo`, `arhiva`, `arhiva_video`, `muzika`, `titlovi`) sa `dl`, `proc`, `knobs`, `default` |
| `/api/status` | — | `ollama {running, models, pull, problem}`, `limits {ime: {n, used}}`, `whisper_cli`, `whisper_model_ready`, `ffmpeg`, `keys {gemini, cloudflare}` (samo da li ključ postoji), `tg_ready` |
| `/api/jobs` | — | lista poslova (najnoviji prvi) |
| `/api/jobs/<id>/log` | — | `{"log": [...]}` (za nepoznat ID prazna lista, ne 404) |
| `/api/library` | — | stavke biblioteke (4.4) |
| `/api/thumb` | `f` = folder | `.yta/thumb.jpg` (JPEG) |
| `/api/subs` | `f`, `file` (rel. `.srt`) | `{entry, cues, src, src_entry, media}` za editor |
| `/api/media` | `f`, `file` | medijski fajl iz foldera stavke (ekstenzija iz `MEDIA_TYPES`: mkv, mp4, m4v, webm, mov, mp3, m4a, opus, ogg, flac, mka, wav, aac) sa `Range` (200/206/416); i `HEAD` |
| `/api/corrections/stats` | — | `{total, by_lang, path, rules_active}` |
| `/api/memory` | `recent` (podr. 50; nije broj → 400) | `Memory.dump()` + `settings {mem_tr, mem_stt}` |
| `/api/playlists` | — | snimci: `id`, `title`, `count`, `have`, `missing`, `removed`, `fetched_at`, `url`, `uploader` |
| `/api/health` | — | `{items: [{folder, title, id, issues}], orphans: [{folder, id}], checked_at, total}` |
| `/api/tb/presets` | — | `toolbox.PRESETS` |

`HEAD` radi samo za `/api/media`; ostalo 405.

### 5.2 POST (telo je JSON objekat)

| Ruta | Telo | Šta radi |
|---|---|---|
| `/api/probe` | `url`, `playlist` | video → `summarize` (+ `in_list`); plejlista/kanal → `probe_list` (stavke sa `state` have/missing/new, `removed`, `new_count`, tabovi kanala) |
| `/api/jobs` | `url`, `opts` | jedan posao preuzimanja → posao |
| `/api/jobs/batch` | `items [{url, title, thumbnail, id, live_status, opts}]`, `opts` ili `recipe`, `playlist` | posao po stavci; sve stavke se provere pre prvog posla → `{jobs}` |
| `/api/jobs/<id>/cancel` · `stop` · `retry` · `remove` · `open` | — | vidi 3.2; `open` otvara folder posla → `{ok}`; nepoznat ID → 404 |
| `/api/process` | `folder`, `proc` | obrada stavke iz arhive → posao |
| `/api/library/batch` | `action`, `folders`, `params` | akcija nad stavkama → `{jobs, skipped [{folder, title, reason}]}` (+ `opened`) |
| `/api/subs/save` | `folder`, `file`, `cues [{i, start, end, text}]` | atomski upis titla, rezerva u `.yta/history/`, parovi u `corrections.jsonl` → `{ok, changed, pairs_saved, backup, memory {rules_active}}` |
| `/api/memory/glossary` | `op` (`add`/`update`/`delete`), `entry` | glosar → `{ok, glossary}` |
| `/api/memory/rule` | `id`, `state` (`auto`/`on`/`off`) | stanje pravila → `{ok, rule}` |
| `/api/memory/correction` | `id`, `disabled` | uključi/isključi ispravku → `{ok, correction}` |
| `/api/playlists/<id>/missing` | `opts` ili `recipe` | preuzmi stavke snimka kojih nema u katalogu → `{jobs}` |
| `/api/playlists/<id>/album` | `m3u`, `tags` | tagovi albuma (samo audio stavke, `-c copy`) + `.m3u8` → `{tagged, m3u, skipped}` |
| `/api/health/rebuild` | — | minimalan manifest za foldere bez njega → `{rebuilt, errors}` |
| `/api/tb/choose-files` | `kind` (`sub` ili video/audio) | macOS dijalog → `{paths}` |
| `/api/tb/analyze` | `path` | `toolbox.analyze` (keš po mtime) |
| `/api/tb/command` | `path`, `spec` | pregled komande → `{cmd, out, warnings, errors}` |
| `/api/tb/jobs` | `path`, `spec` | transcode posao |
| `/api/settings` | ključevi iz `DEFAULTS` | sačuvaj (7) → podešavanja |
| `/api/choose-folder` | `current` | macOS dijalog → `{path}` |
| `/api/open-path` | `path` | otvori putanju iz arhive u Finder-u |
| `/api/open-out` | `path` (podr. `out_dir`) | napravi i otvori folder arhive |
| `/api/ollama/start` | — | `ollama serve` ako ne radi → `{running}` |
| `/api/ollama/pull` | `model` (podr. `model` iz podešavanja) | `ollama pull` u pozadini; stanje u `/api/status` `ollama.pull` |

Akcije `/api/library/batch` (`LIB_ACTIONS`):

| `action` | `params` | Razlozi preskakanja (`skipped`) |
|---|---|---|
| `translate` | `target` (lat/cyr/el), `engine`, `model`, `review`, `force` | već ima prevod / srpski titl sa YouTube-a |
| `video` | `output` (burn/soft/both), `target` (lat/cyr/el/none), `encoder`, `vq`, `scale`, `audio_out`, `audio_enc`, `subs_out`, LLM | nema videa (postojeći prevod za `target` se koristi direktno, bez ponovnog prevoda) |
| `toolbox` | `preset`, `sub` (none/soft/burn), `target` | nema izvora / videa / prevoda |
| `audio` | `preset` (`audio_m4a`/`audio_mp3`/`audio_flac`) | nema izvora |
| `transcribe` | `lang` (2–3 slova ili auto), `force` | već ima whisper transkript; nema izvora zvuka |
| `lektura` | `target`, `engine` (claude/gemini), `model` (claude: sonnet/opus; gemini: `gemini-…`), `force` | nema prevoda za lekturu; već ima lekturu |
| `audio_copy` | — | nema izvora; izvor nema zvuk |
| `open` | — | najviše 5 foldera odjednom |

Za sve akcije: nepoznata vrednost parametra → 400 za ceo zahtev; folder koji nije pod korenom biblioteke („nije u
arhivi“), bez manifesta ili prazna putanja ide u `skipped`; isti folder dva puta se obrađuje jednom.

## 6. Bezbednost

- Server sluša samo na `127.0.0.1`.
- `_local_ok` za GET, HEAD i POST: `Host` mora biti tačno `127.0.0.1:<PORT>` ili `localhost:<PORT>`, a `Origin`
  prazan ili `http://` + jedan od njih (zaštita od DNS rebinding-a i tuđih sajtova) → inače 403.
- POST traži `Content-Type: application/json` (415): stranica sa drugog sajta to ne može da pošalje bez CORS
  preflight-a, a panel ne šalje CORS zaglavlja. Telo mora biti JSON objekat.
- `/api/media` dodatno odbija `Sec-Fetch-Site` koji nije `same-origin`/`none` (`<video src>` sa tuđeg sajta ne šalje
  Origin); `X-Content-Type-Options: nosniff`.
- Putanje: `under_roots(p)` = putanja (posle `resolve`) je koren biblioteke ili unutar njega. `item_folder` traži
  folder unutar korena (ne sam koren); `rel_in` dozvoljava samo fajl unutar foldera stavke (bez `../`, `\0`).
  Proveru imaju `/api/process`, `/api/library/batch`, `/api/subs*`, `/api/media`, `/api/thumb`, `/api/open-path`,
  `/api/open-out`. Toolbox rute (`/api/tb/*`) namerno primaju bilo koji lokalni fajl (transkoder za sve fajlove).
- URL za yt-dlp mora počinjati sa `http://` ili `https://` (argument koji počinje sa `-` ne prolazi).
- API ključevi: iz okruženja (`GEMINI_API_KEY`, `CLOUDFLARE_API_TOKEN`; ima prednost) ili iz Keychain-a
  (`security find-generic-password`, servisi `yt-arhiva-gemini`, `yt-arhiva-cf-token`); nikad se ne loguju niti
  šalju UI-ju (`/api/status` javlja samo da li postoje). Gemini ključ ide u zaglavlje, ne u URL.
- UI tekst iz podataka ubacuje u HTML kroz helper `esc()`.

## 7. Podešavanja (`DEFAULTS`, `settings.json`)

`save_settings` prima samo ključeve iz `DEFAULTS` (ostale tiho preskače), vrednost pretvara u tip podrazumevane
vrednosti (bool preko `as_bool`: `"1"`, `"true"`, `"da"`, `"yes"`, `"on"`), a `recipe` (`RECIPE_IDS` + `custom`),
`script` (`lat`/`cyr`) i `engine` (`ENGINES`) proverava; nepoznata vrednost se tiho preskače. Broj koji nije broj
(npr. `"fragments": "x"`) → 500. Upisuje ceo `SETTINGS` u `settings.json` (uključujući `plan_migrated`).

Forma Podešavanja nema polja za `output`, `encoder`, `quality` i `whisper_quality_model`; menjaju se samo kroz
`POST /api/settings` ili `settings.json`.

| Ključ | Podrazumevano | Značenje |
|---|---|---|
| `browser` | `chrome` | browser za `--cookies-from-browser`; `none` = bez kolačića |
| `cookies_file` | `""` | `cookies.txt` umesto browsera (ima prednost) |
| `out_dir` | `~/Movies/YT-Arhiva` | glavni koren arhive |
| `parallel_downloads` | 3 | limiter preuzimanja |
| `fragments` | 8 | yt-dlp `-N` (paralelni fragmenti jednog preuzimanja) |
| `parallel_encodes` | 2 | limiter enkodiranja (burn, meki MKV, Toolbox) |
| `parallel_translate` | 1 | limiter prevoda i lekture |
| `engine` | `synergy` | podrazumevani prevodilac: `synergy`, `ollama`, `gemini`, `cloudflare`, `claude`, `tg` |
| `model` | `gemma4:12b-it-qat` | Ollama model za `ollama`/`synergy` |
| `cf_account` | `""` | Cloudflare Account ID (token je u Keychain-u) |
| `script` | `lat` | pismo srpskog prevoda: `lat` / `cyr` |
| `output` | `none` | početni izlaz u formi „⚙ Sve opcije“ (samo UI) |
| `encoder` | `hevc` | enkoder za burn kad ga `proc` ne navede; početna vrednost u UI-ju |
| `quality` | 65 | VideoToolbox `-q:v` (hevc/h264) kad ga `proc` ne navede (i akcija `video` u Biblioteci); početna vrednost u UI-ju |
| `keep_original` | `true` | početno „Zadrži izvor“ u „⚙ Sve opcije“ (`proc.keep_source` iz forme); samo UI |
| `whisper_model` | `large-v3-turbo-q5_0` | whisper.cpp model (`ggml-<ime>.bin`) |
| `auto_fetch` | `true` | Homebrew instalacija Ollama/whisper-cpp i `ollama pull` pri prvom korišćenju (whisper modeli se preuzimaju i bez njega, vidi 12) |
| `whisper_quality_model` | `large-v3-q5_0` | tačniji (sporiji) model za jezike iz `whisper_quality_langs` |
| `whisper_quality_langs` | `el,sr` | jezici za kvalitet model (`""` = nikad) |
| `whisper_vad` | `true` | Silero VAD (`--vad`) |
| `whisper_prompt` | `true` | prompt po jeziku (pismo, imena iz naslova) |
| `mem_tr` | `true` | memorija ispravki u prevodu i lekturi |
| `mem_stt` | `true` | memorija u whisper-u i u automatskom titlu pre prevoda |
| `dl_audio` | `all` | početne audio trake u „⚙ Sve opcije“ (`auto` = samo originalna) |
| `dl_subs` | `all` | početni titlovi u „⚙ Sve opcije“ |
| `proc_when` | `later` | početno stanje obrade u „⚙ Sve opcije“ (`now` = forma počinje sa obradom) |
| `recipe` | `samo` | „Podrazumevani izbor“: čekirani koraci pri prvom otvaranju; recept za `/missing` bez opcija (`custom` → `samo`); oznaka `default` u `/api/recipes` |
| `auto_translate` | `true` | recepti Arhiva / Arhiva + video / Samo titlovi uključuju prevod |
| `embed_subs` | `true` | YouTube titlovi i kao trake u MKV izvoru |

`plan_migrated` nije u `DEFAULTS`: upisuje ga jednokratna migracija 3.2 (stari podrazumevani `recipe: "arhiva"` →
`samo`); loš `settings.json` se ne prepisuje.

## 8. Prevod

### 8.1 Prevodioci (`ENGINES`)

| `engine` | Šta radi | Paralelno / blok |
|---|---|---|
| `synergy` (Kvalitetno, „C“) | TranslateGemma nacrt + Gemma prevod sa kontekstom, pa Gemma spaja (`merge_c`) | 1 / 30 |
| `ollama` (Brzo) | Gemma 4 sama (JSON šema), kontekst, scene, QC | 1 / 30 |
| `gemini` | lanac Gemini → Cloudflare → lokalna Gemma (za srpski) | 1 / 60 |
| `cloudflare` | lanac Cloudflare → lokalna Gemma | 1 / 60 |
| `claude` | `claude -p --model sonnet\|opus` | 4 / 60 |
| `tg` | samo TranslateGemma; za srpski se prebacuje na `synergy` | 1 / 30 |

`engine_model` bira model koji ide uz prevodioca (cloud podrazumevano: `gemini-3.5-flash-lite`,
`@cf/google/gemma-4-26b-a4b-it`; claude `sonnet`; tg `translategemma:12b` ili `YTA_TG_MODEL`). U lancu: minutni
limit (429 / `RESOURCE_EXHAUSTED` bez „per day“) → čekanje `retryDelay` (bez njega 20 s, najviše 60 s), najviše 3
puta; dnevni limit, 401/403 ili nema ključa (Cloudflare: i bez Account ID-ja) → sledeći servis.

### 8.2 Srpski (`core.translate`)

1. Keš (8.6); prevode se samo blokovi sa linijama kojih nema u kešu.
2. `synergy`: TranslateGemma nacrt za sve potrebne linije (`tg_lines`: numerisani blokovi od 12, nedostajući broj →
   pojedinačno; keš `tg_sr_cache.json` posle svakog bloka).
3. Kontekst (`get_context`, jedan poziv sa `CTX_SCHEMA`, do 2 pokušaja, prvih 150 titlova): `synopsis` (ko govori,
   pol — za rod),
   `glossary` (do 40 imena/termina; `clean_context` izbacuje stavke kojih nema u tekstu i sumnjive oblike:
   udvojen suglasnik, ijekavica), `register` (`ti`/`vi`). Keš `context.json` (ključ: verzija, engine, model, naslov,
   uzorak). Neuspeh konteksta ne obara prevod.
4. `synergy`: termine iz glosara koje je Gemma prevela prevodi TranslateGemma (`tg_glossary`, keš `tg_glossary.json`).
5. Memorija: korisnički glosar (pobeđuje za isti `src`) i naučena pravila (`hints`) se dodaju u kontekst.
6. Blokovi po scenama (`scene_batches`): najviše `batch` titlova (30 lokalno, 60 za Gemini/Cloudflare/Claude), rez
   na pauzi > 2 s, najmanje 10 u bloku. Prompt:
   naslov + opis (do 600 znakova), kontekst, 6 prethodnih titlova, 2 sledeća ako pauza posle bloka nije duža od
   2 s, primeri iz memorije, ulaz kao JSON. Model piše latinicom.
7. `translate_batch`: do 3 pokušaja; sitna rupa u ID-jevima → dopuna pojedinačno; inače deljenje bloka na pola.
   `synergy` → `merge_c` (TranslateGemma = osnova, Gemma = pomoćni; greška spajanja → ostaje Gemma).
   `review: "on"` (ne-synergy) → `review_batch` (lektura istim modelom).
8. QC (`qc_pass`): `qc_line` traži `prazno`, `pismo` (strana slova), `neprevedeno`, `duzina` (odnos < 0,3 ili > 2,5);
   `prelom` (> 2 reda / > 42 znaka) je samo informacija. Najviše 10% linija ide na ponovni prevod (sa ±3 suseda);
   novi prevod se prihvata samo ako je ukupna težina problema manja. Pokušane linije su u `__qcd__`.
9. Memorija posle prevoda: TM (`exact`) pa `apply_rules`; ne ide u keš.
10. Pismo: `finalize_script` — latinica normalizovana; ćirilica deterministički (`to_cyr`), a imena/brendovi iz izvora
    (veliko slovo, 3+ slova) i reči sa q/w/x/y ostaju latinicom. Panel na kraju `wrap_lines`: tekst duži od 42
    znaka deli na dva reda što sličnije dužine (redovi mogu ostati duži od 42).

### 8.3 Grčki i drugi jezici (`core.translate_to`, `TARGETS`)

`synergy`/`tg`: prvo TranslateGemma nacrt (`tg_<lang>_cache.json`); `tg` uzima nacrt, `synergy` ga daje Gemmi da
ispravi samo greške (`gen_prompt`). Cloud: lanac bez lokalne Gemme; kad je cloud nedostupan, ostatak radi lokalni
TranslateGemma. `_gen_ok` odbacuje red bez grčkih slova, sa ćirilicom ili mešanom reči → nacrt ili TranslateGemma za
taj red. Bez QC prolaza. Memorija: glosar, pravila i primeri u promptu; TM i pravila na kraju.

### 8.4 Gde se poziva iz panela

`translate_step` (posao) → `translate_cues`: `engine_model`, `ensure_ollama` za lokalne modele (prvo korišćenje),
`args.keep_lines=True`, memorija (`mem_attach`), slot `translate`, napredak po bloku, izveštaj memorije u log.
Rezultat: nova stavka `sr`/`tr` u `subs` (4.3). Za automatski titl kao izvor prvo se primene stt pravila memorije
(fajl na disku ostaje isti). Toolbox prevodi istom funkcijom, sa kešom u radnom folderu posla (`DATA/tb/<job>`,
briše se na kraju) i upisuje prevod i pored izlaza (`.sr.srt` / `.el.srt`).

### 8.5 Lektura (v3)

`library_batch` `lektura` → `run_lektura` (slot `translate`, pod `folder_lock`): najnoviji prevod za `target` koji
nije lektura (ako takvog nema, najnovija lektura), i njegov izvor (različit broj titlova → poravnanje po vremenu);
`core.review_translation` u blokovima od 40 (`LEKTURA_BLOCK`), Claude (`sonnet`/`opus`) ili Gemini (lanac do Cloudflare/Gemme; `fallback` u stavci). Model menja što manje; red koji model izostavi ostaje nacrt.
Rezultat je nova stavka (`…:lektura:…`), prethodna lektura ide u `.yta/history/`.

### 8.6 Keš prevoda (`.yta/cache/<ključ izvornog titla>/`)

| Fajl | Ključ / sadržaj |
|---|---|
| `prevod_cache_<engine>_<model>_<pismo>[_rev].json` | srpski: `"<indeks>": prevod`, `__src__` (otisak celog izvora), `__lines__` (heš linija), `__qcd__` |
| `prevod_<lang>_<engine>_<model>.json` | drugi jezici, isti oblik |
| `context.json` | kontekst (8.2/3) |
| `tg_sr_cache.json`, `tg_<lang>_cache.json` | TranslateGemma nacrti |
| `tg_glossary.json` | TranslateGemma prevodi termina glosara |

Promena izvora (`__src__`): uz `keep_lines` (panel) ostaju prevodi linija čiji je tekst po indeksu isti; CLI i
testovi bez toga počinju ispočetka. Upis je atomski (tmp + `os.replace`), posle svakog bloka, pa prekid ne gubi
urađeno. Memorija nikad ne ulazi u keš.

### 8.7 LLM pozivi

`call_ollama`: `/api/chat`, `format` = JSON šema, `think: false` (bez njega ako model ne podržava),
`temperature 0.2`, `num_ctx 8192` (isti za sve pozive, da Ollama ne učitava model ponovo), `keep_alive 15m`.
TranslateGemma (`call_tg`): zvanični format prompta, `temperature 0.1`. `ask()` prekida čekanje odgovora čim je posao
otkazan.

## 9. Whisper

`whisper_media` (posao preuzimanja/obrade preko `do_whisper`, Toolbox direktno), pod slotom `whisper` (1):

1. ffmpeg izvlači izabranu audio traku u mono 16 kHz WAV (`.part` pa preimenovanje).
2. `whisper-cli` na PATH-u; ako ga nema → `brew install whisper-cpp` (uz `auto_fetch`); ako i to ne uspe → python
   whisper rezerva (`core.whisper_bin()`: `YTA_WHISPER_BIN`, putanja ili ime komande, inače `whisper` na PATH-u;
   model `turbo`).
3. Model `WHISPER_DIR/ggml-<whisper_model>.bin`; ako ga nema, preuzima se `curl`-om sa Hugging Face-a
   (nastavlja prekinuto, provera minimalne veličine). `WHISPER_DIR` (`core.whisper_models_dir()`, isti izbor u
   `installer/common.sh`, `yta_whisper_dir`): `YTA_WHISPER_DIR` ako je zadat (launcher ga prenosi i iz `yta.env`;
   tako se koristi folder u kome modeli već postoje, bez ponovnog preuzimanja), inače
   `~/Library/Application Support/YT Arhiva/whisper`.
4. Jezik: `proc.whisper_lang`, inače jezik videa iz manifesta, inače `detect_lang` (`whisper-cli -dl`).
5. Jezik iz `whisper_quality_langs` → `whisper_quality_model` (ako se ne preuzme, ostaje osnovni model).
6. `whisper_vad` → Silero VAD model `ggml-silero-v5.1.2.bin` (`--vad -vm`); neuspeh → bez VAD-a.
7. Prompt (`whisper_prompt_for`): rečenica po jeziku (sr, el, en, es, it, de; srpski = latinica) + imena iz naslova,
   pa termini iz memorije (`mem_stt`; do 400 znakova, grčki 250; ukupno do 600).
8. `whisper-cli -m <model> -f <wav> -l <jezik|auto> [--vad …] [--prompt …] -t 8 -osrt -of <work>/whisper -ml 80
   -sow -pp`; ako ne uspe → python whisper rezerva.
9. Posle: stt pravila memorije na svaki red (srpski ćirilični red kroz latinicu i nazad).
10. `do_whisper` upisuje `subs/whisper.srt`, stavka `{"key": "whisper", "kind": "whisper", "lang": …}`.

Transkript bez prevoda: `proc {"sub_source": "whisper:run", "whisper_lang", "target": "none", "output": "none"}`
(red „Transkript“ u „Šta da uradim“, akcija `transcribe`, dugme Transkribuj).

## 10. Memorija ispravki

Ceo opis: [memory-contract.md](memory-contract.md) (§10 su odstupanja 3.1.0). Ukratko:

- Izvor: `POST /api/subs/save` upisuje par samo za čistu izmenu teksta jedne linije: `type: "tr"` za prevode
  (`sr`/`tr`), `stt` za `whisper`/`auto`.
- `memory.py` iz parova gradi pravila (difflib, 1–4 tokena), klase `ime`/`pravopis`/`termin`/`gramatika`/`glosar`.
  Automatski se primenjuju samo `ime`, `pravopis` i `termin` od više reči sa `count >= 2` i bez konflikta;
  `gramatika` ide modelu samo kao savet. Korisnik menja stanje (`auto`/`on`/`off`) u kartici Memorija.
- TM (`exact`): isti kanal, izvor >= 4 reči i >= 20 znakova. Primeri: idf sličnost, do 6 po bloku.
- Panel: `mem_view` + `SafeMem` — greška u memoriji nikad ne obara posao (upozorenje u logu).
- `Memory.load` kešira po DATA folderu, sa potpisom (mtime_ns, veličina, inode) oba fajla; izmene su atomske.

## 11. Toolbox

Ceo opis: [toolbox-contract.md](toolbox-contract.md). Panel: `tb_analyze` (keš po mtime), `tb_prepare_subs`
(titl iz trake / spoljni fajl / whisper, opcioni prevod, ASS za burn), `toolbox.plan` → ffmpeg pod slotom
`encode`. Preseti: `arhiva`, `apple`, `kompat`, `mali`, `av1`, `prores`, `remux`, `audio_m4a`, `audio_mp3`,
`audio_flac`. Iz Biblioteke i kao korak posle preuzimanja: `lib_transcode` (izlaz u folder stavke, sufiks
`.<preset>`, originalna audio traka umesto prve za preset sa jednom trakom; titl je najnoviji prevod za `target`
(lat/cyr/el) iz `subs/`, a za lat/cyr bez prevoda ručni srpski CC u izabranom pismu; bez toga → „nema prevoda … —
prvo Prevedi“).

## 12. Prvo korišćenje (`auto_fetch`)

- `brew_install` (Ollama, whisper-cpp): ako alata nema na PATH-u i `auto_fetch` je uključen → `brew install`
  (Homebrew iz `/opt/homebrew/bin/brew`, `/usr/local/bin/brew` ili PATH), napredak u fazi posla. Isključen
  `auto_fetch` ili nema Homebrew-a → jasna greška sa komandom.
- `ensure_ollama`: instalacija, `ollama serve` ako ne radi (`start_ollama`: ako `OLLAMA_MODELS` pokazuje na
  nepovezan `/Volumes/…` disk, koristi `~/.ollama/models`), pa `pull_for_job` ako modela nema: provera prostora
  (`gemma4:12b-it-qat` 7,2 GB, `translategemma:12b` 8,1 GB, ostali 8 GB, + 3 GB rezerve), `ollama pull` sa
  procentom u fazi. Isključen `auto_fetch` a modela nema → greška („u Podešavanjima klikni 'Preuzmi model'“).
- Whisper modeli i VAD (`_fetch_model`) se preuzimaju `curl`-om kad nedostaju (ako postoji `whisper-cli`),
  nezavisno od `auto_fetch`.
- Na startu panela: samo ako je `engine` `ollama`, `start_ollama` u pozadini (uz podrazumevani `synergy` Ollama se
  pokreće tek u prvom poslu prevoda).

## 13. Installer i build

| Skripta | Šta radi |
|---|---|
| `installer/build.sh [--with-tests] [--no-pkg] [--no-pdf] [--out DIR] [--pkg-location DIR]` | proveri sintaksu (bash, Python AST), spakuje `panel.py yt_arhiva.py memory.py toolbox.py static docs installer README.md LICENSE SECURITY.md runbook.md VERSION` (+ `tests` uz `--with-tests`) bez `__pycache__`, `.DS_Store` i `data/` → `dist/yt-arhiva-<VERSION>.tar.gz` + `.sha256`; `.pkg` (ako postoji `pkgbuild`) instalira kod u `<pkg-location>/app`, podrazumevano `/Applications/YT Arhiva/app`; `dist/YT-Arhiva-Uputstvo-<VERSION>.pdf` iz `static/uputstvo.html` preko headless Google Chrome-a (ako postoji; putanja `YTA_CHROME`), bez Chrome-a ili uz `--no-pdf` se preskače |
| `installer/install.sh` | provera macOS arm64 i prostora; Homebrew (nudi instalaciju); `brew install` onih od `yt-dlp deno ffmpeg-full` kojih nema (uz `--full` i `ollama whisper-cpp`, inače ih panel preuzima pri prvom korišćenju); kopija aplikacije u `<prefix>/app` (isti spisak kao build, plus `tests/` ako postoji; rsync `--delete`, bez `data/`); `yta.env` (`YTA_DATA`, `YTA_APP_BUNDLE`, `YTA_PORT`, `OLLAMA_MODELS`); `YT Arhiva.app` (osacompile applet, ad-hoc potpis; ne pravi se ponovo ako je nepromenjen, da macOS dozvole ostanu); LaunchAgent `com.ytarhiva.panel` (`--autostart`, `--no-autostart`); opciono Ollama modeli (`--models`, `--models-dir`) i whisper model (`--whisper [IME]`); `--dry-run` ništa ne menja; ostale opcije: `--prefix`, `--app-path`, `--port`, `--no-brew`, `--no-models`, `--rebuild-app`, `--open`, `--yes`, `--force` (`--help`) |
| `installer/launcher.sh` | čita `yta.env` (samo `YTA_*`/`OLLAMA_*`, okruženje ima prednost), dopunjuje PATH (Homebrew, `~/.local/bin`, `ffmpeg-full`), pokreće `panel.py` u pozadini sa logom `DATA/panel.log` i pid fajlom, čeka `/api/settings`, otvara browser; `--status`, `--stop`, `--restart`, `--no-browser`, `--no-dialog`; LaunchAgent i `--restart` pokreću panel preko `.app` (dozvole na ime „YT Arhiva“) |
| `installer/uninstall.sh [--prefix DIR] [--app-path PATH] [--dry-run] [--purge]` | zaustavlja panel, uklanja app, `.app` i LaunchAgent; podatke, arhivu i modele samo uz `--purge` i potvrdu za svaku stavku (samo u terminalu) |
| `installer/common.sh` | zajedničke funkcije (bash 3.2), zaštićene putanje, `yta.env` upis |

Podrazumevane putanje: `~/Applications/YT Arhiva/app` (kod), `…/data` (DATA), `…/yta.env`,
`~/Applications/YT Arhiva.app`; kada `<prefix>` nije upisiv (posle `.pkg`): podaci u
`~/Library/Application Support/YT Arhiva/`. Whisper modeli su uvek u `WHISPER_DIR` (poglavlje 9), nezavisno od
`<prefix>`. Detalji za korisnika: [../installer/README.md](../installer/README.md).

## 14. Testovi

Sve se pokreće iz korena repoa; bez mreže i bez pravih modela (osim benchmark-a). `test_*.py` (osim
`test_first_use.py`) primaju filter: argumenti posle imena fajla biraju testove čiji naziv sadrži neki od tih tekstova.
`YTA_TEST_TMP` = gde se pravi privremeni folder (API i memory testovi), `YTA_TEST_KEEP=1` = ne briši ga (Toolbox:
`TB_KEEP=1`); `TB_FFMPEG_DIR` = drugi folder sa `ffmpeg-full` (`test_panel_api`, `test_v3_api`, `test_plan_api`,
`test_toolbox`).

| Fajl | Šta proverava | Pokretanje | Port |
|---|---|---|---|
| `tests/test_panel_api.py` | recepti, batch akcije Biblioteke, preskakanje, opcije po stavci, zaštita | `python3 tests/test_panel_api.py` | 8797 |
| `tests/test_v3_api.py` | editor + ispravke, lektura (lažni LLM), plejliste, zdravlje, audio iz arhive, uloga trake, `Range` | `python3 tests/test_v3_api.py` | 8811 |
| `tests/test_memory_api.py` | stt/tr parovi, `/api/memory*`, zaštita, `mem_tr`/`mem_stt`, whisper prompt | `python3 tests/test_memory_api.py` | 8823 (odbija 8790) |
| `tests/test_plan_api.py` | `opts.after`, titlovi u MKV izvoru (pravi mali MKV sa omotom), `all+tr`, `audio_groups`, `tr_state`, `transcribe`, recept `samo` i migracija | `python3 tests/test_plan_api.py` | 8824 |
| `tests/test_installer.sh` | sintaksa/shellcheck, build (`--no-pkg`) i sadržaj tar-a, `--dry-run`, zaštita tuđih foldera, instalacija u privremeni HOME, launcher, uninstall | `bash tests/test_installer.sh [RADNI_FOLDER]` | 8818 i 8819 |
| `tests/test_toolbox.py` | `analyze` + `plan` i stvarni ffmpeg nad sintetičkim medijima (`TB_KEEP=1` čuva izlaze) | `python3 tests/test_toolbox.py` | — |
| `tests/test_first_use.py` | prvo korišćenje: lažni `brew` i `ollama`, napredak i greške | `python3 tests/test_first_use.py` | — (u procesu) |
| `tests/test_memory.py` | `memory.py`: pravila, klase, TM, primeri, glosar, keš, pokvareni fajlovi, 20.000 redova | `python3 tests/test_memory.py` | — |
| `tests/test_memory_integration.py` | memorija u promptovima i posle prevoda (lažni LLM), bez memorije isti promptovi | `python3 tests/test_memory_integration.py` | — |
| `tests/plan_ui_check.js` | helperi `static/plan.js` bez DOM-a | `node tests/plan_ui_check.js` | — |
| `tests/memory_ui_check.js` | helperi `static/memory.js` | `node tests/memory_ui_check.js` | — |
| `tests/toolbox_ui_check.js` | helperi `static/toolbox.js` | `node tests/toolbox_ui_check.js` | — |
| `tests/bench_translate.py` | benchmark prevoda (kvalitet + brzina); `--unit` = brzi testovi bez modela | `python3 tests/bench_translate.py [model] [lat\|cyr]` | — (pravi Ollama) |
| `tests/bench_synergy.py` | A/B Gemma / TranslateGemma / sinergija | `python3 tests/bench_synergy.py` | — (pravi Ollama) |

Port testa menja `YTA_TEST_PORT`. Podrazumevani port panela (8790) testovi ne koriste, da ne smetaju panelu koji radi.
API testovi pokreću panel kao poseban proces sa `YTA_DATA` (privremeni), `YTA_PORT` i `YTA_NO_OPEN=1` (akcija
`open` u Biblioteci ne otvara Finder); `test_v3_api`, `test_memory_api` i `test_plan_api` stavljaju na PATH lažni
`yt-dlp` (`test_v3_api` i `test_memory_api` i `claude`, `test_memory_api` i `whisper-cli`), a `test_v3_api` i
`test_memory_api` LLM pozive menjaju lažnim funkcijama. `test_panel_api` nema lažne alate: poslovi idu na
`http://127.0.0.1:9/…`, pa pravi yt-dlp (ako je instaliran) odmah padne.
`test_installer.sh` radi sa `YTA_NO_DIALOG=1` (launcher i `.app` ne prikazuju dijalog) i `YTA_LA_DIR` (LaunchAgent u
privremeni folder, bez `launchctl`). Za API testove (osim `test_memory_api`) i Toolbox testove treba `ffmpeg-full`,
za JS provere `node`, za `shellcheck` proveru u `test_installer.sh` `shellcheck` (bez njega se preskače).

Ostale promenljive okruženja: `YTA_PORT` (podr. 8790), `YTA_DATA` (podr. `<repo>/data`), `YTA_WHISPER_DIR`
(podr. `~/Library/Application Support/YT Arhiva/whisper`, poglavlje 9), `YTA_WHISPER_BIN` (rezervni python whisper,
podr. `whisper` na PATH-u), `OLLAMA_HOST`,
`OLLAMA_MODELS`, `YTA_TG_MODEL`, `YTA_CF_ACCOUNT` (Cloudflare Account ID kad je `cf_account` u podešavanjima prazan),
`GEMINI_API_KEY`, `CLOUDFLARE_API_TOKEN`; launcher: `YTA_ENV_FILE`, `YTA_NO_BROWSER`, `YTA_NO_DIALOG`, `YTA_PYTHON`,
`YTA_APP_BUNDLE`; build: `YTA_CHROME` (Chrome za PDF uputstva).

## 15. Izdavanje verzije

1. `VERSION`: jedan red, dozvoljeni znakovi `0-9 A-Z a-z . + -` (proverava `build.sh`). Čitaju ga `build.sh`,
   `install.sh` (ispis) i `test_installer.sh`; panel ga ne čita.
2. Verzija upisana ručno: `README.md` i `installer/README.md` (`yt-arhiva-<verzija>.tar.gz` u primerima),
   `static/uputstvo.html` (naslov, poglavlje „Verzija“ i imena paketa); odstupanja u ugovoru te verzije.
3. Svi testovi iz poglavlja 14 (bez benchmark-a).
4. `bash installer/build.sh` → `dist/yt-arhiva-<VERSION>.tar.gz` (+ `.sha256`, + `.pkg` ako postoji `pkgbuild`,
   + `YT-Arhiva-Uputstvo-<VERSION>.pdf` ako postoji Google Chrome). `dist/` je u `.gitignore`.
5. Commit u `main` i tag (nije automatizovan): `git tag -a v<VERSION> -m "YT Arhiva <VERSION>"` na commit iz kog je
   napravljen paket; zatim GitHub Release za taj tag sa fajlovima iz `dist/` (`.tar.gz`, `.sha256`, PDF uputstva;
   `.pkg` po želji).

## 16. Poznata ograničenja

- Ime foldera stavke je `safe_name("<datum> - <naslov> [<id>]")` sa najviše 90 znakova: kod naslova dužeg od 65
  znakova `[<id>]` se delimično ili potpuno odseca. Posledice: (a) dva videa sa istim datumom i istih prvih 79 znakova
  naslova (posle `safe_name`) dobijaju isti folder: `init_manifest` prepisuje `url`, `id` i naslov u manifestu
  podacima drugog videa, a `do_download` izvor prvog vidi kao „već u arhivi“ (titlovi istih ključeva se takođe ne
  preuzimaju ponovo); (b) zdravlje kataloga (`scan_orphans`) ne prepoznaje takav folder kad nema manifest. Imena
  fajlova (`item_stem`) uvek imaju `[<id>]`.
- Panel ne čita `VERSION` (nema rute ni prikaza u `index.html`); verzija u `static/uputstvo.html` je upisana ručno.
- `/api/status` bez `ffmpeg-full`: `core.find_ffmpeg()` baca izuzetak van `try`, pa veza ostaje bez odgovora, a UI
  prikazuje „server ne odgovara“ umesto poruke da nedostaje ffmpeg.
- Izvori iz 3.1 sa više audio traka imaju omot kao drugu video traku (stari remux); 3.2 ih ne popravlja sam.
- `/api/tb/*` prima bilo koji lokalni fajl (zaštita je samo Host/Origin + JSON).
- `YTA_NO_OPEN` utiče samo na akciju `open` u Biblioteci; `/api/open-path`, `/api/open-out` i `/api/jobs/<id>/open`
  uvek zovu `open`.
- Chrome kolačići rade samo kad panel ima macOS dozvole (Keychain „Chrome Safe Storage“, Full Disk Access) — vidi
  [../installer/README.md](../installer/README.md).
