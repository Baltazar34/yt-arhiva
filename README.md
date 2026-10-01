# YT Arhiva

Lokalna arhiva YouTube videa na Mac-u (Apple Silicon). Radi sa tvojim YouTube nalogom (i sa Premium nalogom) preko
Chrome kolačića. Upravlja se iz pregledača na http://127.0.0.1:8790; panel prima samo lokalne zahteve.

- **Preuzmi — „Šta da uradim“:** lista koraka sa checkbox-ovima. Podrazumevano je čekirano samo preuzimanje celog
  sadržaja: najbolji video + svaka audio traka (i opisna) + svi titlovi, u MKV (titlovi i kao trake). Transkript
  (whisper), prevod, video sa titlom, Transkoduj (Toolbox) i Izvuci audio se dodaju po izboru; „⚙ Sve opcije“ ima punu
  formu. Radi i za plejliste i kanale.
- **Tačno stanje stavke:** 🟢 prevedeno · 🟠 transkript bez prevoda (+ **Prevedi**) · ⚪ nema titla (+ **Transkribuj**).
- **Prevod titla** na srpski (latinica ili ćirilica) i grčki: lokalno preko Ollama (`gemma4:12b-it-qat`,
  `translategemma:12b`) ili preko Gemini, Cloudflare ili Claude servisa.
- **Video sa titlom:** upečen ili meki titl (ffmpeg-full, VideoToolbox HEVC/H.264); samo audio sa omotom i tagovima.
- **Biblioteka** sa obradom koja se može ponavljati, **editor titla** i **Memorija ispravki** (ispravke popravljaju
  sledeće prevode i transkripte), **Toolbox** transkoder za lokalne fajlove.

## Instalacija

Treba macOS na Apple Silicon (M1 i noviji). Installer instalira `yt-dlp`, `deno` i `ffmpeg-full` preko Homebrew-a
(ako Homebrew-a nema, nudi da ga instalira), kopira program u `~/Applications/YT Arhiva/app` i pravi
`~/Applications/YT Arhiva.app`. `--dry-run` prikazuje plan bez izmena.

**Iz Release paketa:** sa stranice *Releases* preuzmi `yt-arhiva-3.2.1.tar.gz` i `yt-arhiva-3.2.1.tar.gz.sha256`
(uz njih je i PDF uputstvo), pa u Terminalu, u folderu gde su preuzeti:

```bash
shasum -a 256 -c yt-arhiva-3.2.1.tar.gz.sha256
tar -xzf yt-arhiva-3.2.1.tar.gz && cd yt-arhiva-3.2.1
./installer/install.sh
```

**Iz klona repoa:**

```bash
git clone https://github.com/Baltazar34/yt-arhiva && cd yt-arhiva && installer/install.sh
```

Posle instalacije:

1. Otvori **YT Arhiva** iz `~/Applications`. Panel se otvara u pregledaču.
2. Daj dozvole: **System Settings → Privacy & Security → Full Disk Access** za `YT Arhiva.app` (ili Terminal, ako panel
   pokrećeš iz njega), a na pitanje za Keychain „Chrome Safe Storage“ izaberi **Always Allow**.

Ollama, whisper.cpp i modeli (~15 GB za prevod, ~0,55 GB za whisper) preuzimaju se sami pri prvom prevodu odnosno
transkriptu; odmah ih instalira `install.sh --full --models --whisper`. Whisper modeli idu u
`~/Library/Application Support/YT Arhiva/whisper` (drugi folder: promenljiva `YTA_WHISPER_DIR`).

## Napomena / odgovornost

- YT Arhiva je alat za **ličnu arhivu** sadržaja za koji imaš pravo: tvoji snimci, sadržaj čiji autor to dozvoljava
  ili ono što ti zakon dozvoljava.
- **Ti odgovaraš** za poštovanje autorskih prava i Uslova korišćenja YouTube-a (Terms of Service) za sve što
  preuzimaš i obrađuješ. Autori programa ne odgovaraju za način na koji ga koristiš.
- **Kolačići i ključevi ostaju lokalno.** Chrome kolačiće čita yt-dlp na tvom Mac-u (ključ „Chrome Safe Storage“ iz
  Keychain-a) i šalje ih samo YouTube-u, kao i pregledač. API ključevi (Gemini, Cloudflare) su u macOS Keychain-u,
  ne upisuju se u fajlove, ne pojavljuju se u logu i ne šalju se pregledaču.
- **Panel sluša samo na `127.0.0.1`** (nije dostupan sa mreže) i odbija zahteve sa drugih sajtova (provera `Host` i
  `Origin`). Detalji: [SECURITY.md](SECURITY.md).
- Na internet idu: preuzimanje sa YouTube-a, alati i modeli pri prvoj upotrebi, i tekst titla — ali samo kad za
  prevod ili lekturu sam izabereš Gemini, Cloudflare ili Claude. Lokalni prevod i transkript rade na tvom Mac-u.

## Zavisnosti i licence

Nijedna od ovih komponenti nije deo repoa ni paketa: installer i panel ih instaliraju iz zvaničnih izvora i
pokreću kao zasebne programe. Za svaku važi njena licenca, odnosno njeni uslovi korišćenja.

| Komponenta | Licenca | Kako se dobija |
|---|---|---|
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Unlicense | Homebrew (installer) |
| [deno](https://deno.com) | MIT | Homebrew (installer) |
| [ffmpeg-full](https://ffmpeg.org) | GPL-3.0-or-later | Homebrew (installer); ne pakuje se uz YT Arhivu, panel poziva instalirani `ffmpeg` |
| [whisper.cpp](https://github.com/ggml-org/whisper.cpp) | MIT | Homebrew `whisper-cpp`, pri prvom transkriptu ili `--full` |
| [Ollama](https://ollama.com) | MIT | Homebrew, pri prvom lokalnom prevodu ili `--full` |
| `gemma4:12b-it-qat` (Gemma 4) | Apache-2.0 | `ollama pull`, pri prvom lokalnom prevodu ili `--models` |
| `translategemma:12b` (TranslateGemma) | Gemma Terms of Use | `ollama pull` (prevod „Kvalitetno“), pri prvom prevodu ili `--models` |
| whisper modeli (`ggml-large-v3-turbo-q5_0` i drugi, iz OpenAI Whisper) | MIT | Hugging Face, pri prvom transkriptu ili `--whisper` |
| Silero VAD model (`ggml-silero-v5.1.2`) | MIT | Hugging Face, uz whisper |

Opcioni cloud prevodioci (Gemini, Cloudflare, Claude) rade sa tvojim nalogom i ključem, po uslovima tih servisa.

## Dokumentacija

- **Uputstvo za korisnike:** [static/uputstvo.html](static/uputstvo.html) — u panelu dugme **Pomoć**; uz paket i kao
  `YT-Arhiva-Uputstvo-<verzija>.pdf`.
- **Instalacija detaljno** (opcije, dozvole, API ključevi, uklanjanje): [installer/README.md](installer/README.md).
- **Za programere:** [runbook.md](runbook.md) (pokretanje iz klona, testovi, build, izdavanje) i
  [docs/ARHITEKTURA.md](docs/ARHITEKTURA.md) (arhitektura, API, podaci).
- **Bezbednost i prijava ranjivosti:** [SECURITY.md](SECURITY.md).

## Ručno pokretanje (razvoj)

```bash
python3 panel.py                                  # http://127.0.0.1:8790
YTA_PORT=8830 YTA_DATA=/tmp/yta python3 panel.py  # drugi port i poseban folder podataka
```

`YTA_DATA` je folder za podešavanja, red poslova, memoriju i snimke plejlista. Folder arhive se ne menja: uzima se iz
podešavanja (podrazumevano `~/Movies/YT-Arhiva`). Whisper modeli su u `~/Library/Application Support/YT Arhiva/whisper`
(`YTA_WHISPER_DIR` zadaje drugi folder).

## Licenca

[MIT](LICENSE). Alati i modeli koje program koristi imaju svoje licence (tabela iznad); ne isporučuju se uz ovaj kod.
