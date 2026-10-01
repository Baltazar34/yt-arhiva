# Runbook: YT Arhiva (3.2.1)

Vodič za programera: pokretanje iz klona, okruženje, testovi, build i izdavanje. Arhitektura, sve HTTP rute,
podešavanja i model podataka: [docs/ARHITEKTURA.md](docs/ARHITEKTURA.md). Instalacija za korisnike:
[installer/README.md](installer/README.md).

## Zahtevi
- macOS na Apple Silicon, Python 3.9+ (samo standardna biblioteka, bez pip zavisnosti), Homebrew.
- Osnovni alati: `brew install yt-dlp deno ffmpeg-full`. Za transkript i lokalni prevod još `whisper-cpp` i `ollama`
  (uz uključen `auto_fetch` panel ih instalira sam pri prvom korišćenju), i modeli:
  `ollama pull gemma4:12b-it-qat` (za Kvalitetno i `ollama pull translategemma:12b`).
- Za testove: `node` (JS provere), opciono `shellcheck` (bez njega se ta provera u `test_installer.sh` preskače).

## Pokretanje iz klona
```bash
git clone https://github.com/Baltazar34/yt-arhiva && cd yt-arhiva
python3 panel.py                                   # http://127.0.0.1:8790, podaci u ./data
YTA_PORT=8830 YTA_DATA=/tmp/yta-dev python3 panel.py   # druga instanca: svoj port i svoj folder podataka
```
- Chrome kolačići: program iz kog se panel pokreće (npr. Terminal) treba **Full Disk Access**, a na pitanje za
  Keychain „Chrome Safe Storage“ izaberi **Always Allow**. Bez toga: u Podešavanjima drugi browser ili `cookies.txt`.
- Pažnja: folder arhive (`out_dir`) se ne menja sa `YTA_DATA` — podrazumevano je `~/Movies/YT-Arhiva`. Za probe ga
  prvo u Podešavanjima promeni na test folder.
- CLI bez panela: `python3 yt_arhiva.py URL [--cyr] [--no-burn] [--engine ollama|claude]` (`--help` za sve opcije).
- Instalirana aplikacija (posle `installer/install.sh`):
  `~/Applications/YT\ Arhiva/app/installer/launcher.sh --status | --stop | --restart`;
  log `~/Applications/YT Arhiva/data/panel.log`.

## Okruženje
| Promenljiva | Podrazumevano | Značenje |
|---|---|---|
| `YTA_PORT` | `8790` | port panela (sluša samo na `127.0.0.1`) |
| `YTA_DATA` | `<repo>/data` | podešavanja, red poslova, memorija ispravki, snimci plejlista |
| `YTA_WHISPER_DIR` | `~/Library/Application Support/YT Arhiva/whisper` | whisper i VAD modeli ([ARHITEKTURA §9](docs/ARHITEKTURA.md#9-whisper)) |
| `YTA_WHISPER_BIN` | `whisper` na PATH-u | rezervni python whisper, kad whisper.cpp ne uspe |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | adresa Ollama servera |
| `OLLAMA_MODELS` | `~/.ollama/models` | folder za Ollama modele (npr. eksterni disk) |
| `GEMINI_API_KEY`, `CLOUDFLARE_API_TOKEN` | — | ključevi (imaju prednost nad Keychain-om) |

Ostale (`YTA_TG_MODEL`, `YTA_CF_ACCOUNT`, launcher i build): [ARHITEKTURA §14](docs/ARHITEKTURA.md#14-testovi).
`data/` i `dist/` su u `.gitignore`.

## Testovi (pre svakog commit-a i pre izdavanja)
```bash
python3 tests/test_panel_api.py && python3 tests/test_v3_api.py && python3 tests/test_memory_api.py && python3 tests/test_plan_api.py
python3 tests/test_memory.py && python3 tests/test_memory_integration.py && python3 tests/test_first_use.py
python3 tests/test_toolbox.py
node tests/plan_ui_check.js && node tests/memory_ui_check.js && node tests/toolbox_ui_check.js
bash tests/test_installer.sh
```
- Bez mreže i bez pravih modela; API testovi pokreću panel kao poseban proces sa privremenim `YTA_DATA`.
- Portovi testova: 8797, 8811, 8818–8819, 8823, 8824 (`YTA_TEST_PORT` menja port); podrazumevani 8790 ne koriste.
- Filter: `python3 tests/test_plan_api.py embed`; `YTA_TEST_KEEP=1` čuva privremeni folder.
- Prevod sa pravim modelom (kvalitet + brzina): `python3 tests/bench_translate.py gemma4:12b-it-qat lat [--review]`.
- Sintaksa: `python3 -c "import ast;[ast.parse(open(f).read()) for f in ('panel.py','yt_arhiva.py','memory.py','toolbox.py')]"`

## Build
- `bash installer/build.sh` → `dist/yt-arhiva-<VERSION>.tar.gz` (+ `.sha256`, + `.pkg` ako postoji `pkgbuild`,
  + `YT-Arhiva-Uputstvo-<VERSION>.pdf` ako postoji Google Chrome; `--no-pdf` ga preskače, `--no-pkg` preskače `.pkg`).
- `bash installer/build.sh --with-tests` uključuje i `tests/`.

## Izdavanje verzije
Ceo postupak: [ARHITEKTURA §15](docs/ARHITEKTURA.md#15-izdavanje-verzije). Ukratko:
1. `VERSION`, pa verzija u `README.md`, `installer/README.md` i `static/uputstvo.html`.
2. Svi testovi iznad.
3. `bash installer/build.sh`.
4. Commit, tag `git tag -a v<VERSION> -m "YT Arhiva <VERSION>"`, push; GitHub Release sa fajlovima iz `dist/`
   (`.tar.gz`, `.sha256`, PDF uputstva; `.pkg` po želji).

## Installer
- `installer/install.sh` (`--full` = odmah i Ollama + whisper-cpp, `--models`, `--whisper`, `--autostart`,
  `--dry-run`, `--prefix`). Bez `--full` panel ih preuzima pri prvom korišćenju (`auto_fetch`).
- Chrome kolačići: Full Disk Access za „YT Arhiva“ (ili Terminal ako panel radi iz Terminala) + Keychain
  „Chrome Safe Storage“ → Always Allow.

## Dijagnostika
- Posao: log u kartici Red; `data/jobs.json`; `curl -s 127.0.0.1:8790/api/status` (limiti, Ollama, whisper, ključevi).
  Ako `/api/status` ne vrati ništa, a panel radi: proveri `ffmpeg-full` (bez njega ta ruta pada bez odgovora).
- Koraci posle preuzimanja: u JSON-u posla `after_jobs` (pokrenuti) i `after_skipped` (preskočeni, razlog).
- Titlovi u MKV izvoru: manifest `source_subs`; `ffprobe -v error -show_entries stream=index,codec_type:stream_tags=language,title <izvor>.mkv`.
- Omot: `ffprobe -v error -show_entries stream=codec_type:stream_disposition=attached_pic <izvor>.mkv` — omot mora
  biti `attached_pic=1`, ne druga obična video traka (izvori iz 3.1 sa više audio traka to još imaju).
- Biblioteka → Zdravlje (`GET /api/health`): izvor nedostaje/skraćen, titl/izlaz nedostaje, folderi bez indeksa →
  „Obnovi indeks“ (`POST /api/health/rebuild`).
- Memorija: sumnjivo pravilo → kartica Memorija, stanje „Nikad“ (ili isključi ispravku „koristi“).
- Ollama: `data/ollama.log`; „disk sa modelima nije povezan“ = `OLLAMA_MODELS` na eksternom disku koji nije prikačen.
- Cookies greška → Full Disk Access za program iz kog radi panel, Keychain „Chrome Safe Storage“ → Always Allow.

## Rollback
- Kod: prethodni tag ili commit; za instaliranu aplikaciju `install.sh` iz paketa prethodne verzije (ne dira
  podešavanja, arhivu ni modele).
- Podaci: migracija 3.2 jednom upiše u `data/settings.json` `plan_migrated: true` i promeni `recipe: "arhiva"` u
  `"samo"`; za stariju verziju `recipe` po potrebi vrati u Podešavanjima.
