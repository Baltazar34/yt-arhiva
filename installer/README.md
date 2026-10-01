# YT Arhiva — instalacija na Mac

Za macOS na Apple Silicon (M1 i noviji) sa Homebrew-om (installer ga nudi ako ga nema). Sve se radi iz aplikacije
**Terminal**. Uputstvo za korišćenje programa je u `static/uputstvo.html` (u panelu dugme **Pomoć**), a uz paket i
kao `YT-Arhiva-Uputstvo-<verzija>.pdf`.

## 1. Instalacija

```bash
tar -xzf yt-arhiva-3.2.1.tar.gz
cd yt-arhiva-3.2.1
./installer/install.sh
```

Ili iz klona repoa (isti installer):

```bash
git clone https://github.com/Baltazar34/yt-arhiva && cd yt-arhiva && ./installer/install.sh
```

Installer:

1. proverava da li je Mac arm64 i koliko ima mesta na disku,
2. instalira Homebrew ako ga nema (pita pre toga), pa `yt-dlp deno ffmpeg-full` (ono što je već instalirano
   preskače); `ollama` i `whisper-cpp` panel instalira sam pri prvom prevodu odnosno transkriptu (ili odmah: `--full`),
3. kopira aplikaciju u `~/Applications/YT Arhiva/app` (podaci idu u `~/Applications/YT Arhiva/data`),
4. pravi **`~/Applications/YT Arhiva.app`** — dvoklik pokreće panel u pozadini (ako već ne radi)
   i otvara http://127.0.0.1:8790,
5. po želji: pokretanje pri prijavi, Ollama modeli, whisper model.

Šta se preuzima kasnije, pri prvoj upotrebi (Podešavanja → „Alati i modeli“ = automatski, podrazumevano):

| Kada | Šta | Veličina |
|---|---|---|
| prvi lokalni prevod | Ollama + `gemma4:12b-it-qat` i `translategemma:12b` | 7,2 + 8,1 = 15,3 GB |
| prvi transkript | whisper.cpp + `large-v3-turbo-q5_0`, VAD model; za grčki/srpski i `large-v3-q5_0` | ~0,55 GB; 1,08 GB |

Kad sam preuzima Ollama model, panel traži bar 3 GB slobodnog prostora više od veličine modela; installer upozorava
kad je za modele slobodno manje od 18 GB.
Modeli i arhiva mogu na eksterni disk (`--models-dir`, Podešavanja → Folder arhive). Whisper modeli idu u
`~/Library/Application Support/YT Arhiva/whisper`; drugi folder zadaje promenljiva `YTA_WHISPER_DIR` (može i u `yta.env`).

Installer može da se pokrene više puta: ažurira kod, ne dira podešavanja, arhivu ni modele,
i **ne pravi `.app` ponovo** ako se ništa bitno nije promenilo (da dozvole ostanu).

| Opcija | Šta radi |
|---|---|
| `--dry-run` | samo ispisuje šta bi uradio, ništa ne menja |
| `--full` | odmah instalira i `ollama` i `whisper-cpp` (inače ih panel instalira pri prvom korišćenju) |
| `--prefix DIR` | druga lokacija umesto `~/Applications/YT Arhiva` (tada je `.app` u `DIR`) |
| `--app-path PATH` | gde ide `YT Arhiva.app` |
| `--autostart` / `--no-autostart` | uključi / isključi pokretanje pri prijavi (LaunchAgent `com.ytarhiva.panel`, bez otvaranja browsera) |
| `--models` / `--no-models` | preuzme `gemma4:12b-it-qat` (7,2 GB) i `translategemma:12b` (8,1 GB), prvo pokaže slobodan prostor / ne pita za modele. Traži već instaliran `ollama` (ili `--full`), inače installer staje |
| `--models-dir DIR` | folder za Ollama modele (`OLLAMA_MODELS`), npr. na eksternom disku; upisuje se u `yta.env` aplikacije (podešavanja shell-a se ne menjaju). Važi kada Ollama pokreće panel ili installer; ako Ollama već radi (Ollama.app, `brew services`), on koristi svoj folder |
| `--whisper [IME]` | preuzme whisper.cpp model (podrazumevano `large-v3-turbo-q5_0`, ~0,55 GB) |
| `--port N` | drugi port panela (podrazumevano 8790) |
| `--no-brew` | preskoči Homebrew (samo proveri da li alati postoje) |
| `--rebuild-app` | napravi `.app` iznova (dozvole tada treba dati ponovo) |
| `--open`, `--yes` | otvori aplikaciju na kraju; „da" na sva pitanja (modeli samo uz `--models`) |
| `--force` | nastavi i kada Mac nije Apple Silicon (nije podržano) |

Primer — sve odjednom, modeli na eksternom disku:

```bash
./installer/install.sh --full --autostart --models --models-dir "/Volumes/<disk>/ollama-models"
```

## 2. Dozvole: odobri jednom, važi zauvek

Pri prvom preuzimanju sa tvojim YouTube nalogom macOS pita za dozvole. Dovoljno je jednom,
ako se izabere prava opcija:

**a) Keychain „Chrome Safe Storage"** (čitanje Chrome kolačića za tvoj YouTube nalog)
- U prozoru *„security wants to use your confidential information stored in "Chrome Safe Storage""*
  ukucaj lozinku za prijavu na Mac i klikni **Always Allow / Uvek dozvoli** — ne samo „Allow".
- „Allow" važi samo tog puta; zato se pitanje ponavlja.
- Ako je već kliknuto „Allow": otvori **Keychain Access** → pretraga `Chrome Safe Storage` → dvoklik →
  kartica **Access Control** → `+` → `Cmd+Shift+G` → `/usr/bin/security` → **Save Changes**.
- Napomena: dozvola važi za alat `/usr/bin/security`, koji yt-dlp koristi. Ne menja se sa
  ažuriranjem Homebrew-a, pa se ne pita ponovo.
- Imaj na umu: posle „Always Allow" svaki program pokrenut pod tvojim nalogom može preko
  `security` da pročita ključ Chrome kolačića bez pitanja. Ako to ne želiš, klikći „Allow" svaki put
  ili u Podešavanjima panela koristi `cookies.txt` umesto browsera.

**b) Pristup fajlovima: „data from other apps", eksterni disk, Full Disk Access**
- Panel pokrenut iz `YT Arhiva.app` (ili pri prijavi) dobija dozvole na ime **YT Arhiva**.
  Kada se pokrene iz Terminala, dozvole dobija **Terminal**.
- Trajno rešenje: **System Settings → Privacy & Security → Full Disk Access** → `+` →
  `~/Applications/YT Arhiva.app` → uključi. Posle toga pitanja za druge aplikacije i diskove prestaju.
- Kada je dozvola data dok panel radi: `~/Applications/YT\ Arhiva/app/installer/launcher.sh --restart`
  (ponovo pokreće panel preko `YT Arhiva.app`, pa dozvole ostaju na ime aplikacije).
- Ako se pojavi pitanje za eksterni disk („files on a removable volume"), klikni **Allow**; to se pamti.

**Zašto se ne pita ponovo:** installer ne pravi `.app` iznova pri ažuriranju, pa je macOS
prepoznaje kao istu aplikaciju. Ako se `.app` obriše ili napravi sa `--rebuild-app`, stari unos u
Full Disk Access ukloni sa `−` i dodaj novu aplikaciju.

## 3. API ključevi u Keychain-u (Gemini, Cloudflare)

Ključevi se ne upisuju u fajlove; panel ih čita iz Keychain-a:

```bash
security add-generic-password -a "$USER" -s yt-arhiva-gemini   -U -w   # pa nalepi Gemini API ključ
security add-generic-password -a "$USER" -s yt-arhiva-cf-token -U -w   # Cloudflare API token
```

`-w` na kraju znači da `security` traži ključ bez prikazivanja na ekranu. `-U` menja postojeći ključ.
Cloudflare Account ID se upisuje u panelu (⚙ Podešavanja). Provera:
`security find-generic-password -a "$USER" -s yt-arhiva-gemini >/dev/null && echo postoji`.
Brisanje: `security delete-generic-password -a "$USER" -s yt-arhiva-gemini`.

## 4. Svakodnevno

- Pokretanje: `YT Arhiva.app` (Finder → Applications u korisničkom folderu, Launchpad ili Spotlight).
- Terminal: `~/Applications/YT\ Arhiva/app/installer/launcher.sh` sa `--status`, `--stop`, `--restart`, `--no-browser`.
  Bez opcija pokreće panel iz Terminala (dozvole tada traži Terminal); `--restart` ga pokreće preko `YT Arhiva.app`.
  `--no-dialog` (ili `YTA_NO_DIALOG=1`): bez `.app` i bez prozora sa greškom, za skripte i testove.
- Prozor sa greškom („Panel se nije pokrenuo") prikazuje samo `YT Arhiva.app` otvorena dvoklikom. Pri prijavi
  (LaunchAgent) i kod `--restart` greška ide u log panela.
- Log panela: `~/Applications/YT Arhiva/data/panel.log`.
- Okruženje: `~/Applications/YT Arhiva/yta.env` (`YTA_DATA`, `YTA_APP_BUNDLE`; uz `--port` i `--models-dir` i
  `YTA_PORT`, `OLLAMA_MODELS`). Promenljive iz Terminala
  imaju prednost, npr. `YTA_PORT=8811 launcher.sh`.

## 5. Uklanjanje

```bash
~/Applications/YT\ Arhiva/app/installer/uninstall.sh            # app, .app i LaunchAgent
~/Applications/YT\ Arhiva/app/installer/uninstall.sh --purge    # + pita za podatke, arhivu i modele
```

Bez `--purge` ostaju arhiva videa, modeli (Ollama i whisper), podešavanja i ispravke za obuku. Uz `--purge` pita se za
svaku stavku posebno (samo u Terminalu). Za brisanje arhive treba ukucati `OBRIŠI`. Obrisano ide u Kantu
ako je moguće. Folderi kao `~`, `~/Movies` ili ceo disk (`/Volumes/<disk>`) se nikad ne brišu automatski.
Za instalaciju sa drugim `--prefix` isti `--prefix` daj i za `uninstall.sh`.

## 6. Za razvoj: paket

```bash
./installer/build.sh               # dist/yt-arhiva-<VERSION>.tar.gz (+ .sha256), .pkg ako postoji pkgbuild
                                   # i dist/YT-Arhiva-Uputstvo-<VERSION>.pdf ako postoji Google Chrome
./installer/build.sh --no-pdf      # bez PDF uputstva (--no-pkg: bez .pkg)
./installer/build.sh --with-tests  # uključuje tests/
bash tests/test_installer.sh       # test installera u privremenom HOME (bez launchctl i bez otvaranja .app, port 8818)
```

`.pkg` instalira kod u `/Applications/YT Arhiva/app` (druga lokacija: `--pkg-location`). Posle toga, kao
običan korisnik: `"/Applications/YT Arhiva/app/installer/install.sh" --prefix "/Applications/YT Arhiva"`.
Tada `.app` ide u `~/Applications`, a podaci u `~/Library/Application Support/YT Arhiva`.
Uklanjanje: `"/Applications/YT Arhiva/app/installer/uninstall.sh" --prefix "/Applications/YT Arhiva"`, pa
`sudo rm -rf "/Applications/YT Arhiva/app" && sudo pkgutil --forget com.ytarhiva.app.pkg`.

PDF uputstva pravi Google Chrome bez prozora (`--headless=new --print-to-pdf`) iz `static/uputstvo.html`; bez
Chrome-a build samo javi da je PDF preskočen. Drugi Chrome: `YTA_CHROME=/putanja/do/chrome ./installer/build.sh`.
Slike ekrana u `static/uputstvo/` su sa izmišljene demo arhive.
