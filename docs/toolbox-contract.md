# Toolbox (Mac transkoder) — ugovor između modula

Cilj: sekcija "Toolbox" u yt-arhiva panelu za transkodiranje BILO KOG lokalnog video/audio fajla
(i izvora iz Arhive) na Mac-u, sa opcijama u padajućim menijima, presetima, opcionim titlom
(ugrađena traka / spoljni fajl / whisper) i opcionim lokalnim prevodom titla na srpski.

Podrazumevano ništa se ne "burnuje" i ne prevodi dok korisnik to ne izabere.

Alati: `ffmpeg`/`ffprobe` iz `/opt/homebrew/opt/ffmpeg-full/bin/` (ffmpeg 9, libass, zimg,
libx264, libx265, libsvtav1, VideoToolbox: hevc_videotoolbox, h264_videotoolbox,
prores_videotoolbox). Mac: Apple Silicon.

## Fajlovi
- `toolbox.py` — ČIST modul (bez HTTP, bez niti, bez importa `panel`): presets, `analyze`, `plan`,
  `output_dims`, `default_out_path`. Samo stdlib.
- `tests/test_toolbox.py` — generiše sintetičke medije ffmpeg-om (lavfi) i proverava `analyze` + `plan`
  izvršavanjem ffmpeg komandi na kratkim klipovima (2–3 s). Pokretanje: `python3 tests/test_toolbox.py`.
- `static/toolbox.js` — UI sekcije; učitava se iz `index.html` posle glavnog skripta.
- `panel.py` — rute `/api/tb/*`, job tip `transcode`, priprema titla (ekstrakcija / whisper / prevod).

## toolbox.py API

```python
PRESETS: list[dict]   # [{"id", "label", "desc", "spec": {...kompletan spec...}}]
DEFAULT_SPEC: dict     # kompletan spec sa podrazumevanim vrednostima (= preset "arhiva")

def analyze(path: str, ffprobe: str) -> dict
def output_dims(spec: dict, a: dict) -> tuple[int, int]          # posle scale-a (za ASS PlayRes)
def default_out_path(src: str, spec: dict, a: dict) -> str        # ne sme biti == src; ne gazi postojeći fajl (dodaje " (2)", " (3)"…)
def plan(src: str, spec: dict, a: dict, ffmpeg: str, out_path: str, workdir: str,
         sub_file: str | None = None) -> dict
```

### analyze(path) → dict
```
{
 "path", "name", "size": int, "container": str (format_name), "duration": float, "bitrate": int|None,
 "video": null | {"index": abs_idx, "codec", "profile", "width", "height", "fps": float, "pix_fmt",
            "bit_depth": int, "hdr": "sdr"|"hdr10"|"hlg"|"dolby_vision",
            "color_primaries", "color_transfer", "color_space", "interlaced": bool, "bitrate": int|None},
 "audio": [{"n": 0.., "index": abs_idx, "codec", "channels", "layout", "sample_rate", "bitrate",
            "lang", "title", "default": bool}],
 "subs":  [{"n": 0.., "index": abs_idx, "codec", "lang", "title", "text": bool, "forced": bool, "default": bool}],
 "chapters": int,
 "warnings": [str]
}
```
- `n` = redni broj među strimovima istog tipa (za `-map 0:a:n` / `0:s:n`).
- attached_pic (cover art) NIJE video.
- `text` = true za subrip, ass, ssa, mov_text, webvtt, text; false za hdmv_pgs_subtitle, dvd_subtitle, dvb_subtitle.
- HDR: transfer smpte2084 → hdr10, arib-std-b67 → hlg, DOVI side data → dolby_vision.

### spec (JSON, sve vrednosti su stringovi/brojevi/bool/liste kao ispod)
```
container:  "mkv"|"mp4"|"mov"|"m4a"|"mp3"|"flac"
vcodec:     "copy"|"hevc_vt"|"h264_vt"|"prores_vt"|"x265"|"x264"|"av1"|"none"
vq:         int   # VT: -q:v 1..100 ; x264/x265/av1: CRF
vbitrate:   ""|"8M"… # ako je zadat: -b:v umesto kvaliteta (VT, x264, x265, av1)
vpreset:    "medium"… (x264/x265) | "6"… (svt-av1 preset 0..13)
prores_profile: "proxy"|"lt"|"standard"|"hq"|"4444"
scale:      "src"|"2160"|"1440"|"1080"|"720"|"576"|"480"   # samo smanjivanje
fps:        "src"|"60"|"50"|"30"|"25"|"24"
depth:      "auto"|"8"|"10"
hdr:        "keep"|"sdr"          # sdr = tonemap (zscale+tonemap hable) u BT.709
deint:      "off"|"auto"|"yadif"|"bwdif"   # auto = bwdif samo ako je izvor interlaced
audio_tracks: "all"|"first"|[n, n…]
acodec:     "copy"|"aac"|"opus"|"ac3"|"eac3"|"flac"|"alac"|"pcm"|"mp3"
abitrate:   "128k"|"192k"|"256k"|"320k"…   # za lossy
channels:   "keep"|"2"|"6"
loudnorm:   "off"|"-16"|"-23"     # EBU R128 jedan prolaz, po traci
subs_keep:  "all"|"none"          # postojeće titl trake (konvertuj po kontejneru)
sub_src:    "none"|"track:N"|"file"|"whisper"   # izvor za dodavanje / burn / prevod
sub_file:   ""                    # putanja ako je sub_src == "file"
translate:  "off"|"lat"|"cyr"
engine:     "ollama"|"claude";  model: "gemma4:12b-it-qat"
sub_mode:   "soft"|"burn"
trim_start: ""|"hh:mm:ss[.ms]";  trim_end: ""|"hh:mm:ss[.ms]"
chapters:   bool
out_dir:    ""  (prazno = pored izvora);  suffix: ".transkod"
```

### plan(...) → dict
```
{"cmd": [ffmpeg, ...], "out": out_path, "warnings": [str], "errors": [str]}
```
- Ako `errors` nije prazan, `cmd` je [] i posao se ne pokreće (npr. "burn traži rekodiranje videa",
  "PCM ne ide u MP4", "loudnorm ne ide uz audio copy").
- `sub_file` (apsolutna putanja U `workdir`): pripremljen titl (srt ili ass) za izabrani izvor kad je
  sub_src ∈ {file, whisper} ili je translate ≠ off, ili kad je track:N tekstualni i sub_mode=burn.
  Za burn: filter koristi RELATIVNO ime (`ass=burn.ass` / `subtitles=sub.srt`), panel pokreće ffmpeg sa `cwd=workdir`.
  Za soft: dodatni `-i sub_file`, map, kodek po kontejneru (srt za mkv, mov_text za mp4/mov),
  metadata language (`srp` ako je preveden) i title.
- Slikovni titl (PGS/DVD) + burn: `overlay` iz `[0:s:N]` (bez sub_file); prevod slikovnog titla nije moguć → error.
- Redosled video filtera: deint → (overlay slikovnog titla) → tonemap → scale → fps → (ass burn tekstualnog titla).
- Trim + burn: `-ss` POSLE `-i` (tačni tajming titla), inače `-ss` pre `-i` (brzo). `trim_end` → `-to`/`-t` korektno.
- Uvek: `-hide_banner -loglevel error -nostats -progress pipe:1 -y`, `-map_metadata 0`, chapters po specu.
- HEVC u mp4/mov → `-tag:v hvc1`. h264_vt je 8-bit (10-bit izvor → 8-bit, upozorenje za HDR bez tonemapa).
- 10-bit: hevc_vt `-profile:v main10 -pix_fmt p010le`; x265 `yuv420p10le`; av1 `yuv420p10le`.
- HDR keep: prenesi color tagove; x265 dodaj `hdr10=1` i color parametre u `-x265-params`.
- mp4/mov: tekstualni titlovi → mov_text; slikovni se izbacuju uz upozorenje.
- Audio-only kontejneri (m4a/mp3/flac): bez videa i titlova; mp3/flac samo prva izabrana traka.
- Nikad ne prepisuje izvor (out == src → error).

## HTTP API (panel.py)
- `GET  /api/tb/presets` → PRESETS
- `POST /api/tb/choose-files {"kind": "media"|"sub"}` → `{"paths": [...]}` (macOS dijalog, više fajlova)
- `POST /api/tb/analyze {"path"}` → analyze()
- `POST /api/tb/command {"path", "spec"}` → `{"cmd": "string za prikaz", "out", "warnings", "errors"}`
- `POST /api/tb/jobs {"path", "spec"}` → job (kind "transcode"; stages download=Ulaz, subs=Titl, translate=Prevod, output=Transkod)
- Postojeće: `POST /api/choose-folder {"current"}` → `{"path"}`

## static/toolbox.js
- Definiše `window.initToolbox()` (poziva se jednom posle učitavanja) i `window.tbAddPath(path)`.
- Renderuje u `<div id="tb">` unutar `<section class="card" id="secTb">` (postoji u index.html).
- Koristi globalne helpere iz index.html: `$`, `$$`, `esc`, `api`, `opt`, `fmtB`, `fmtD`, `modelOptionsHtml()`, `SET`,
  CSS klase: `card head item grid f wide dis fieldset legend actions est b ok warn plain small muted row grow thumb pre.log`.
- Po fajlu kartica: info (kontejner, trajanje, veličina, video codec/res/fps/bit depth/HDR/interlaced, audio i titl trake),
  preset select + "Prilagođeno", polja po grupama Video / Audio / Titlovi i prevod / Izlaz,
  audio trake kao checkbox-ovi (+ "sve"), dugmad "Prikaži komandu" (pre + upozorenja/greške), "Transkoduj", "Ukloni".
  Gore: "Dodaj fajlove…", polje za nalepljene putanje, "Primeni prvu na sve", "Transkoduj sve".
- Polja koja nemaju smisla za izabrani kodek/kontejner su onemogućena (npr. kvalitet za copy, prores profil samo za prores_vt).
