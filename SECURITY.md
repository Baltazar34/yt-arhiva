# Bezbednost

## Prijava ranjivosti

Ranjivost prijavi privatno, preko GitHub-a: kartica **Security** ovog repoa → **Report a vulnerability**
(GitHub private vulnerability reporting). Ne otvaraj javni issue za ranjivost.

U prijavi navedi verziju (fajl `VERSION`), korake za ponavljanje i šta napadač može da postigne. Bezbednosne
ispravke se rade za poslednju objavljenu verziju.

## Šta panel radi lokalno

- **Samo `127.0.0.1`.** Panel sluša samo na lokalnoj adresi (port 8790 ili `YTA_PORT`) i nije dostupan sa mreže.
- **Host/Origin zaštita.** Svaki zahtev mora imati `Host` tačno `127.0.0.1:<port>` ili `localhost:<port>`, a `Origin`
  prazan ili isti taj panel; inače 403 (zaštita od DNS rebinding-a i tuđih sajtova). POST zahtevi moraju biti
  `Content-Type: application/json`, a panel ne šalje CORS zaglavlja, pa ih tuđa stranica ne može poslati.
  `/api/media` dodatno odbija zahteve koji nisu sa istog sajta (`Sec-Fetch-Site`).
- **Putanje.** Rute za biblioteku, titlove i medije rade samo sa fajlovima unutar foldera arhive. Izuzetak su rute
  Toolbox-a (`/api/tb/*`), koje namerno primaju bilo koji lokalni fajl (transkoder za fajlove sa diska).
- **Kolačići se samo čitaju, lokalno.** yt-dlp čita kolačiće Chrome-a na tvom Mac-u (ključ „Chrome Safe Storage“ iz
  Keychain-a, uz tvoje odobrenje); baza pregledača se ne menja. Ako umesto pregledača izabereš `cookies.txt`, yt-dlp
  ga koristi na mestu (i u njemu osvežava kolačiće). Panel kolačiće ne kopira, ne čuva u svojim podacima i ne šalje
  nikome osim YouTube-u (preko yt-dlp).
- **API ključevi** (Gemini, Cloudflare) se čitaju iz macOS Keychain-a ili iz okruženja; ne upisuju se u fajlove, ne
  pojavljuju se u logu i ne šalju se pregledaču (`/api/status` javlja samo da li ključ postoji).
- **Spoljni programi** (yt-dlp, ffmpeg, whisper-cli, ollama, claude) se pokreću bez shell-a, sa listom argumenata;
  URL za yt-dlp mora počinjati sa `http://` ili `https://`.

Tehnički detalji: [docs/ARHITEKTURA.md, poglavlje 6](docs/ARHITEKTURA.md#6-bezbednost).
