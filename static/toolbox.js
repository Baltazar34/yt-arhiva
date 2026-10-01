// ================================================================ TOOLBOX (Mac transkoder) — UI sekcije #secTb
// Obican skript (bez modula), ucitava se posle glavnog skripta iz index.html i koristi njegove
// globalne helpere: $, $$, esc, api, opt, fmtB, fmtD, modelOptionsHtml, SET. Ugovor: docs/toolbox-contract.md
(() => {
"use strict";

const SPEC_KEYS = ["container", "vcodec", "vq", "vbitrate", "vpreset", "prores_profile", "scale", "fps", "depth", "hdr", "deint",
  "audio_tracks", "acodec", "abitrate", "channels", "loudnorm", "subs_keep", "sub_src", "sub_file", "translate", "engine", "model",
  "sub_mode", "trim_start", "trim_end", "chapters", "out_dir", "suffix"];
// polja koja preset ne menja (vezana za fajl / korisnika)
const KEEP = ["out_dir", "trim_start", "trim_end", "sub_file", "engine", "model"];
// njihova izmena ne pravi "Prilagođeno"
const NO_CUSTOM = [...KEEP, "suffix", "model_full"];
const FALLBACK = {container: "mkv", vcodec: "hevc_vt", vq: 65, vbitrate: "", vpreset: "medium", prores_profile: "hq", scale: "src",
  fps: "src", depth: "auto", hdr: "keep", deint: "auto", audio_tracks: "all", acodec: "copy", abitrate: "192k", channels: "keep",
  loudnorm: "off", subs_keep: "all", sub_src: "none", sub_file: "", translate: "off", engine: "ollama", model: "gemma4:12b-it-qat",
  sub_mode: "soft", trim_start: "", trim_end: "", chapters: true, out_dir: "", suffix: ".transkod"};

const AUDIO_ONLY = ["m4a", "mp3", "flac"];
const LOSSY = ["aac", "opus", "ac3", "eac3", "mp3"];
const SCALES = [[2160, "2160p (4K)"], [1440, "1440p"], [1080, "1080p (Full HD)"], [720, "720p (HD)"], [576, "576p (PAL)"], [480, "480p"]];
const O = {
  container: [["mkv", "MKV — sve trake i titlovi (preporuka)"], ["mp4", "MP4 — najkompatibilnije (TV, telefon)"],
    ["mov", "MOV — Apple / montaža (ProRes)"], ["m4a", "M4A — samo audio (AAC/ALAC)"], ["mp3", "MP3 — samo audio"],
    ["flac", "FLAC — samo audio, bez gubitka"]],
  vcodec: [["copy", "Kopiraj bez rekodiranja (bez gubitka, najbrže)"], ["hevc_vt", "HEVC — VideoToolbox (HW, brzo)"],
    ["h264_vt", "H.264 — VideoToolbox (HW, najkompatibilnije)"], ["prores_vt", "ProRes — VideoToolbox (montaža, veliki fajl)"],
    ["x265", "HEVC — x265 softverski (manji fajl, sporo)"], ["x264", "H.264 — x264 softverski"],
    ["av1", "AV1 — SVT-AV1 softverski (najmanji fajl, sporo)"], ["none", "Bez videa"]],
  vbitrate: [["", "Bez — po kvalitetu (preporuka)"], ...["2M", "3M", "5M", "8M", "10M", "12M", "16M", "20M", "25M", "35M", "50M", "80M"]
    .map(v => [v, v.replace("M", " Mb/s") + " (ciljni bitrate)"])],
  prores_profile: [["proxy", "Proxy (najmanji)"], ["lt", "LT"], ["standard", "422 Standard"], ["hq", "422 HQ (preporuka)"], ["4444", "4444 (najveći)"]],
  fps: [["src", "Kao izvor"], ["60", "60 fps"], ["50", "50 fps"], ["30", "30 fps"], ["25", "25 fps"], ["24", "24 fps"]],
  depth: [["auto", "Automatski (kao izvor)"], ["8", "8-bit"], ["10", "10-bit"]],
  hdr: [["keep", "Zadrži HDR (metapodaci boja)"], ["sdr", "Pretvori u SDR (tonemap, BT.709)"]],
  deint: [["off", "Isključeno"], ["auto", "Automatski (bwdif ako je izvor prepleten)"], ["yadif", "yadif (brzo)"], ["bwdif", "bwdif (kvalitetnije)"]],
  acodec: [["copy", "Kopiraj bez rekodiranja"], ["aac", "AAC (najkompatibilnije)"], ["opus", "Opus (najbolji odnos kvalitet/veličina)"],
    ["ac3", "AC-3 (Dolby Digital)"], ["eac3", "E-AC-3 (Dolby Digital Plus)"], ["flac", "FLAC (bez gubitka)"],
    ["alac", "ALAC (bez gubitka, Apple)"], ["pcm", "PCM (nekompresovano)"], ["mp3", "MP3"]],
  abitrate: ["96k", "128k", "160k", "192k", "224k", "256k", "320k", "384k", "448k", "640k"].map(v => [v, v.replace("k", " kb/s")]),
  channels: [["keep", "Kao izvor"], ["2", "Stereo (2.0, downmix)"], ["6", "5.1"]],
  loudnorm: [["off", "Isključeno"], ["-16", "−16 LUFS (web, telefon)"], ["-23", "−23 LUFS (EBU R128, TV)"]],
  subs_keep: [["all", "Zadrži sve (konvertuj po kontejneru)"], ["none", "Izbaci postojeće"]],
  translate: [["off", "Bez prevoda"], ["lat", "Srpski — latinica"], ["cyr", "Srpski — ćirilica"]],
  sub_mode: [["soft", "Meki titl (uključuje se u plejeru)"], ["burn", "Burn — upečen u video (rekodiranje)"]],
};
const TBQ = {
  vt: [["80", "Skoro bez gubitka (q80, vrlo veliki fajl)"], ["75", "Vrlo visok (q75)"], ["70", "Visok (q70)"], ["65", "Arhivski (q65, preporuka)"],
    ["60", "Dobar (q60)"], ["55", "Srednji (q55)"], ["45", "Manji fajl (q45)"], ["35", "Mali fajl (q35)"]],
  sw: [["16", "Skoro bez gubitka (CRF 16)"], ["18", "Vizuelno bez gubitka (CRF 18)"], ["20", "Arhivski (CRF 20, preporuka)"],
    ["22", "Vrlo dobar (CRF 22)"], ["23", "Dobar (CRF 23)"], ["24", "Manji fajl (CRF 24)"], ["26", "Još manji fajl (CRF 26)"], ["28", "Mali fajl (CRF 28)"]],
  av1: [["20", "Vrlo visok (CRF 20)"], ["24", "Visok (CRF 24)"], ["28", "Arhivski (CRF 28)"], ["30", "Dobar (CRF 30, preporuka)"], ["32", "Srednji (CRF 32)"],
    ["36", "Manji fajl (CRF 36)"], ["40", "Mali fajl (CRF 40)"]],
};
const VQ_DEF = {vt: "65", sw: "20", av1: "30"};
const VP = {
  x26: [["ultrafast", "ultrafast (najbrže, najveći fajl)"], ["superfast", "superfast"], ["veryfast", "veryfast (brzo)"], ["faster", "faster"],
    ["fast", "fast"], ["medium", "medium (preporuka)"], ["slow", "slow (manji fajl)"], ["slower", "slower"], ["veryslow", "veryslow"], ["placebo", "placebo (najsporije)"]],
  av1: Array.from({length: 14}, (_, i) => [String(i), i + ({0: " (ekstremno sporo)", 4: " (sporo, manji fajl)", 6: " (preporuka)", 8: " (brže)", 10: " (brzo)", 13: " (najbrže)"}[i] || "")]),
};
const VP_DEF = {x26: "medium", av1: "6"};
const VCS = {hevc_vt: "HEVC VT", h264_vt: "H.264 VT", prores_vt: "ProRes", x265: "x265", x264: "x264", av1: "AV1"};
const ACS = {aac: "AAC", opus: "Opus", ac3: "AC-3", eac3: "E-AC-3", flac: "FLAC", alac: "ALAC", pcm: "PCM", mp3: "MP3"};
const CN = {hevc: "HEVC", h264: "H.264", av1: "AV1", vp9: "VP9", vp8: "VP8", prores: "ProRes", mpeg2video: "MPEG-2", mpeg4: "MPEG-4",
  vc1: "VC-1", dnxhd: "DNxHD", aac: "AAC", opus: "Opus", ac3: "AC-3", eac3: "E-AC-3", dts: "DTS", truehd: "TrueHD", flac: "FLAC",
  alac: "ALAC", mp3: "MP3", mp2: "MP2", vorbis: "Vorbis", subrip: "SRT", ass: "ASS", ssa: "SSA", mov_text: "mov_text", webvtt: "WebVTT",
  hdmv_pgs_subtitle: "PGS", dvd_subtitle: "VobSub", dvb_subtitle: "DVB"};
const HDRN = {hdr10: "HDR10", hlg: "HLG", dolby_vision: "Dolby Vision"};
// sta moze u koji kontejner (isto kao toolbox.V_OK / A_OK); A_OK = izlazni kodek, *_COPY = kodek izvora za copy
const V_BAD = {mp4: ["prores_vt"], mov: ["av1"]};
const V_COPY = {mp4: ["h264", "hevc", "av1", "vp9", "mpeg4", "mpeg2video", "mpeg1video", "mjpeg"],
  mov: ["h264", "hevc", "prores", "mpeg4", "mpeg2video", "mpeg1video", "mjpeg", "dnxhd", "png", "qtrle", "rawvideo"]};
const A_OK = {mp3: ["mp3"], flac: ["flac"], m4a: ["aac", "alac"], mp4: ["aac", "opus", "ac3", "eac3", "flac", "alac", "mp3"],
  mov: ["aac", "ac3", "eac3", "alac", "pcm", "mp3"]};
const A_COPY = {mp3: ["mp3"], flac: ["flac"], m4a: ["aac", "alac"], mp4: ["aac", "mp3", "mp2", "ac3", "eac3", "alac", "opus", "flac", "dts"],
  mov: ["aac", "mp3", "mp2", "ac3", "eac3", "alac"]};
const A_DEF = {mp3: "mp3", flac: "flac", m4a: "aac"};

const vqFam = vc => ({hevc_vt: "vt", h264_vt: "vt", x264: "sw", x265: "sw", av1: "av1"})[vc] || null;
const vpFam = vc => ({x264: "x26", x265: "x26", av1: "av1"})[vc] || null;
const cn = c => CN[c] || (!c ? "?" : c.startsWith("pcm_") ? "PCM" : c.toUpperCase());
const fpsTxt = f => f ? String(+(+f).toFixed(3)) : "";
const baseName = p => String(p || "").replace(/\/+$/, "").split("/").pop() || String(p || "");
const chLabel = (n, lay) => { const l = String(lay || "").replace(/\(.*\)/, ""); return /^\d\.\d$/.test(l) ? l : ({1: "mono", 2: "stereo", 6: "5.1", 8: "7.1"})[n] || (n ? n + "ch" : ""); };
const langOf = t => (t.lang || "und").toLowerCase();

// ---------------------------------------------------------------- cisti helperi (testiraju se u tests/toolbox_ui_check.js)
// Putanje iz nalepljenog teksta: novi red / tab uvek odvaja; razmak odvaja samo ispred nove apsolutne putanje
// (/, ~/, "/…, '/…, file://), pa putanje sa razmacima rade i bez navodnika. Navodnici i "\ " (Terminal) rade.
function parsePaths(text) {
  const s = String(text ?? ""), out = [];
  let cur = "", q = "", quoted = false;
  const startsPath = j => { const c = s[j], d = s[j + 1];
    return c === "/" || (c === "~" && d === "/") || ((c === '"' || c === "'") && (d === "/" || d === "~")) || /^file:\/\//i.test(s.slice(j, j + 7)); };
  const push = () => {
    let p = quoted ? cur : cur.trim();
    if (/^file:\/\//i.test(p)) { p = p.replace(/^file:\/\/(localhost)?/i, ""); try { p = decodeURIComponent(p); } catch { /* ostaje kako jeste */ } }
    if (p && !out.includes(p)) out.push(p);
    cur = ""; quoted = false;
  };
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (q) { if (c === q) q = ""; else cur += c; continue; }
    if (c === "\\" && i + 1 < s.length && !"\r\n".includes(s[i + 1])) { cur += s[++i]; continue; }
    if ((c === '"' || c === "'") && !cur.trim() && !quoted) { cur = ""; q = c; quoted = true; continue; }
    if (c === "\n" || c === "\r" || c === "\t") { push(); continue; }
    if (c === " " || c === " ") {
      let j = i; while (j < s.length && (s[j] === " " || s[j] === " ")) j++;
      if (quoted || j >= s.length || "\r\n\t".includes(s[j]) || startsPath(j)) push(); else cur += s.slice(i, j);
      i = j - 1; continue;
    }
    cur += c;
  }
  push();
  return out;
}

// "90" / "1:30" / "1:02:03,5" -> sekunde; "" -> null; neispravno -> NaN
function toSec(v) {
  v = String(v ?? "").trim().replace(",", ".");
  if (!v) return null;
  const p = v.split(":");
  if (p.length > 3 || !p.every((x, i) => (i === p.length - 1 ? /^\d+(\.\d+)?$/ : /^\d+$/).test(x))) return NaN;
  return p.reduce((acc, x) => acc * 60 + parseFloat(x), 0);
}
function fmtHms(sec) {
  const ms = Math.round(sec * 1000), p2 = n => String(n).padStart(2, "0"), r = ms % 1000;
  return `${p2(Math.floor(ms / 3600000))}:${p2(Math.floor(ms % 3600000 / 60000))}:${p2(Math.floor(ms % 60000 / 1000))}` + (r ? "." + String(r).padStart(3, "0") : "");
}
// -> "hh:mm:ss[.ms]" | "" | null (neispravno)
function normTime(v) { const s = toSec(v); return s === null ? "" : Number.isNaN(s) ? null : fmtHms(s); }

// polja kartice (obican objekat) -> kompletan spec sa tipovima iz ugovora
function specFromFields(f) {
  const s = {};
  for (const k of SPEC_KEYS) s[k] = String(f[k] ?? FALLBACK[k] ?? "").trim();
  s.suffix = String(f.suffix ?? FALLBACK.suffix).replace(/\s+$/, "");  // " (kopija)" zadrzava vodeci razmak
  const vq = parseInt(f.vq, 10);
  s.vq = Number.isFinite(vq) ? vq : FALLBACK.vq;
  s.chapters = typeof f.chapters === "string" ? f.chapters === "true" : !!(f.chapters ?? FALLBACK.chapters);
  s.audio_tracks = f.audio_all ? "all" : [...new Set((f.audio_n || []).map(Number).filter(n => Number.isInteger(n) && n >= 0))].sort((x, y) => x - y);
  const mf = String(f.model_full || ""), i = mf.indexOf("|");
  if (i > 0) { s.engine = mf.slice(0, i); s.model = mf.slice(i + 1); }
  else { s.engine = String(f.engine || FALLBACK.engine); s.model = String(f.model || FALLBACK.model); }
  for (const k of ["trim_start", "trim_end"]) { const t = normTime(s[k]); if (t !== null) s[k] = t; }
  return s;
}

// vrednosti onemogucenih polja -> neutralne, da backend ne dobije nesto sto korisnik ne vidi
function effectiveSpec(s0, a) {
  const s = {...s0, audio_tracks: Array.isArray(s0.audio_tracks) ? [...s0.audio_tracks] : s0.audio_tracks};
  const ao = AUDIO_ONLY.includes(s.container), v = a ? a.video : undefined;
  // bez video izlaza (audio kontejner ili izvor bez videa) polja titlova su onemogucena
  if (ao || v === null) Object.assign(s, {vcodec: "none", sub_src: "none", subs_keep: "none"});
  if (["copy", "none"].includes(s.vcodec)) Object.assign(s, {vbitrate: "", scale: "src", fps: "src", depth: "auto", hdr: "keep", deint: "off"});
  if (s.vcodec === "prores_vt") s.vbitrate = "";
  if (s.vcodec === "h264_vt") s.depth = "8";  // VT H.264 je samo 8-bit
  else if (!["hevc_vt", "x265", "x264", "av1"].includes(s.vcodec)) s.depth = "auto";
  if (v && (!v.hdr || v.hdr === "sdr")) s.hdr = "keep";
  if (s.acodec === "copy") Object.assign(s, {loudnorm: "off", channels: "keep"});
  if (s.sub_src !== "file") s.sub_file = "";
  const tr = a && /^track:\d+$/.test(s.sub_src) ? (a.subs || []).find(t => "track:" + t.n === s.sub_src) : null;
  if (tr && !tr.text) s.translate = "off";
  if (s.sub_src === "none") Object.assign(s, {translate: "off", sub_mode: "soft"});
  return s;
}

// kljuc trake = jezik + redni broj unutar istog jezika (eng#1, eng#2 …)
function trackKeys(list) { const c = {}; return (list || []).map(t => { const l = langOf(t); c[l] = (c[l] || 0) + 1; return l + "#" + c[l]; }); }

// "Primeni prvu na sve": audio trake i titl traku prevedi na odgovarajuce trake drugog fajla (po jeziku).
// sub_src null = ostavi kako je na ciljnoj kartici.
function mapTracks(src, dst, s) {
  const r = {audio_tracks: s.audio_tracks, sub_src: s.sub_src};
  const sa = src.audio || [], da = dst.audio || [];
  if (Array.isArray(s.audio_tracks) && s.audio_tracks.length) {
    const sk = trackKeys(sa), dk = trackKeys(da), want = new Set();
    for (const n of s.audio_tracks) {
      const i = sa.findIndex(t => t.n === n), j = i >= 0 ? dk.indexOf(sk[i]) : -1;
      if (j >= 0) want.add(da[j].n);
    }
    r.audio_tracks = want.size ? [...want].sort((x, y) => x - y) : da.length ? [da[0].n] : [];
  } else if (s.audio_tracks === "first") r.audio_tracks = da.length ? [da[0].n] : [];
  const m = /^track:(\d+)$/.exec(s.sub_src || "");
  if (m) {
    const ss = src.subs || [], ds = dst.subs || [], i = ss.findIndex(t => t.n === +m[1]);
    r.sub_src = null;
    if (i >= 0) {
      const t = ss[i], dk = trackKeys(ds);
      let j = dk.indexOf(trackKeys(ss)[i]);
      if (j >= 0 && !!ds[j].text !== !!t.text) j = -1;
      if (j < 0) j = ds.findIndex(x => langOf(x) === langOf(t) && !!x.text === !!t.text);
      if (j >= 0) r.sub_src = "track:" + ds[j].n;
    }
  } else if (s.sub_src === "file") r.sub_src = null;
  return r;
}

// provere pre slanja (backend radi pune provere u plan())
function clientCheck(s, a) {
  const errors = [], warnings = [], dur = (a && a.duration) || 0;
  const t0 = toSec(s.trim_start), t1 = toSec(s.trim_end), ok0 = t0 !== null && !Number.isNaN(t0), ok1 = t1 !== null && !Number.isNaN(t1);
  if (Number.isNaN(t0)) errors.push("Početak isečka nije ispravno vreme (hh:mm:ss, mm:ss ili sekunde).");
  if (Number.isNaN(t1)) errors.push("Kraj isečka nije ispravno vreme (hh:mm:ss, mm:ss ili sekunde).");
  if (ok0 && ok1 && t1 <= t0) errors.push("Kraj isečka mora biti posle početka.");
  if (ok0 && dur && t0 >= dur) errors.push(`Početak isečka je posle kraja fajla (${fmtD(dur)}).`);
  if (ok1 && dur && t1 > dur + 0.5) warnings.push(`Kraj isečka je posle kraja fajla (${fmtD(dur)}) — ide do kraja.`);
  if (s.sub_src === "file" && !String(s.sub_file || "").trim()) errors.push("Izaberi fajl titla (ili drugi izvor titla).");
  const nA = s.audio_tracks === "all" ? (a ? (a.audio || []).length : 1) : s.audio_tracks === "first" ? 1 : (s.audio_tracks || []).length;
  if (AUDIO_ONLY.includes(s.container) && !nA) errors.push("Audio kontejner bez ijedne audio trake — izaberi bar jednu traku.");
  else if (!nA && (s.vcodec === "none" || (a && !a.video))) errors.push("Izlaz bi bio prazan — nema ni videa ni zvuka.");
  return {errors, warnings};
}

function summarize(s, a) {
  a = a || {audio: [], subs: []};
  const P = [], v = a.video, ao = AUDIO_ONLY.includes(s.container);
  if (ao || !v || s.vcodec === "none") P.push("bez videa");
  else if (s.vcodec === "copy") P.push("video: kopija");
  else {
    const q = s.vcodec === "prores_vt" ? String(s.prores_profile).toUpperCase() : s.vbitrate ? s.vbitrate.replace(/M$/i, " Mb/s")
      : (vqFam(s.vcodec) === "vt" ? "q" : "CRF ") + s.vq;
    P.push([VCS[s.vcodec] || s.vcodec, q, s.scale !== "src" ? s.scale + "p" : "", s.fps !== "src" ? s.fps + " fps" : "",
      s.hdr === "sdr" && v.hdr && v.hdr !== "sdr" ? "HDR→SDR" : ""].filter(Boolean).join(" "));
  }
  let nA = s.audio_tracks === "all" ? (a.audio || []).length : s.audio_tracks === "first" ? Math.min(1, (a.audio || []).length) : s.audio_tracks.length;
  if (["mp3", "flac"].includes(s.container)) nA = Math.min(nA, 1);
  P.push(!nA ? "bez zvuka" : `${nA} audio → ${s.acodec === "copy" ? "kopija" : (ACS[s.acodec] || s.acodec) + (LOSSY.includes(s.acodec) ? " " + s.abitrate : "")}` +
    (s.acodec !== "copy" && s.loudnorm !== "off" ? ` · ${s.loudnorm} LUFS` : ""));
  if (!ao && v) {
    if (s.sub_src === "none") P.push(s.subs_keep === "none" ? "bez titlova" : (a.subs || []).length ? `titlovi iz izvora: ${a.subs.length}` : "");
    else {
      const m = /^track:(\d+)$/.exec(s.sub_src), t = m && (a.subs || []).find(x => x.n === +m[1]);
      const src = m ? `S${+m[1] + 1}${t ? " " + (t.lang || "und") : ""}` : s.sub_src === "file" ? "spoljni fajl" : "whisper";
      P.push(`titl: ${src}${s.translate !== "off" ? " → srpski " + (s.translate === "cyr" ? "(ćirilica)" : "(latinica)") : ""} · ${s.sub_mode === "burn" ? "burn" : "meki"}`);
    }
  }
  if (s.trim_start || s.trim_end) P.push(`isečak ${s.trim_start || "početak"}–${s.trim_end || "kraj"}`);
  P.push("→ " + String(s.container).toUpperCase());
  return P.filter(Boolean).join(" · ");
}

function contName(a) {
  const ext = ((String(a.name || a.path || "").match(/\.([a-z0-9]{2,4})$/i) || [])[1] || "").toUpperCase(), c = String(a.container || "");
  if (c.includes("matroska")) return ext === "WEBM" ? "WebM" : "MKV";
  if (c.includes("mp4") || c.includes("mov")) return ext || "MP4";
  const f = c.split(",")[0];
  return ({mpegts: "MPEG-TS", avi: "AVI", flac: "FLAC", mp3: "MP3", wav: "WAV", ogg: "OGG", asf: "WMV/ASF", mxf: "MXF"})[f] || (f || ext).toUpperCase();
}
const audioLabel = (t, short) => [`A${t.n + 1}`, t.lang || "und", cn(t.codec), chLabel(t.channels, t.layout),
  t.bitrate ? Math.round(t.bitrate / 1000) + " kb/s" : "", !short && t.title ? `„${t.title}“` : "", !short && t.default ? "podrazumevana" : ""].filter(Boolean).join(" · ");
const subLabel = (t, short) => [`S${t.n + 1}`, t.lang || "und", cn(t.codec), t.text ? "tekst" : "slika", t.forced ? "forced" : "",
  !short && t.title ? `„${t.title}“` : ""].filter(Boolean).join(" · ");

function cardHtml() {
  const f = (k, label, cls = "") => `<div class="f ${cls}"><label>${label}</label><select data-t="${k}"></select></div>`;
  return `<div class="item tbitem"><div class="body" style="min-width:0">
    <div class="ttl"></div><div class="path"></div><div class="small muted meta"></div><div class="badges"></div><div class="awarn"></div>
    <div class="forms" hidden>
      <div class="grid">
        <div class="f"><label>Preset</label><select data-t="preset"></select><div class="hint pdesc"></div></div>
      </div>
      <fieldset class="tv"><legend>Video</legend><div class="grid">
        ${f("vcodec", "Video kodek")}${f("vq", "Kvalitet")}${f("vbitrate", "Ciljni bitrate (umesto kvaliteta)")}
        ${f("vpreset", "Brzina enkodera")}${f("prores_profile", "ProRes profil")}${f("scale", "Rezolucija (samo smanjenje)")}
        ${f("fps", "Frejmrejt")}${f("depth", "Dubina boje")}${f("hdr", "HDR")}${f("deint", "Deinterlace")}
      </div><div class="vnote"></div></fieldset>
      <fieldset class="ta"><legend>Audio</legend><div class="grid">
        <div class="f wide"><label>Trake</label><div class="tracks"></div></div>
        ${f("acodec", "Audio kodek")}${f("abitrate", "Bitrate")}${f("channels", "Kanali")}${f("loudnorm", "Normalizacija glasnoće")}
      </div><div class="anote"></div></fieldset>
      <fieldset class="ts"><legend>Titlovi i prevod</legend><div class="grid">
        ${f("subs_keep", "Postojeći titlovi")}${f("sub_src", "Titl za dodavanje / burn / prevod")}
        <div class="f wide subfile" hidden><label>Fajl titla (SRT / ASS / VTT)</label>
          <div class="row"><input type="text" data-t="sub_file" class="grow" placeholder="/putanja/do/titla.srt"><button class="sm pickSub">Izaberi…</button></div></div>
        ${f("translate", "Prevod")}${f("model_full", "Prevodilac")}${f("sub_mode", "Način")}
      </div><div class="snote"></div></fieldset>
      <fieldset class="to"><legend>Izlaz</legend><div class="grid">
        ${f("container", "Kontejner")}
        <div class="f"><label>Početak isečka</label><input type="text" data-t="trim_start" placeholder="hh:mm:ss — prazno = od početka"></div>
        <div class="f"><label>Kraj isečka</label><input type="text" data-t="trim_end" placeholder="hh:mm:ss — prazno = do kraja"></div>
        <div class="f"><label>Poglavlja</label><label class="chk"><input type="checkbox" data-t="chapters"> <span class="chtxt">zadrži poglavlja</span></label></div>
        <div class="f wide"><label>Folder izlaza</label>
          <div class="row"><input type="text" data-t="out_dir" class="grow" placeholder="prazno = pored izvornog fajla"><button class="sm pickDir">Izaberi…</button></div></div>
        <div class="f"><label>Sufiks imena</label><input type="text" data-t="suffix" placeholder=".transkod"></div>
      </div><div class="onote"></div></fieldset>
      <div class="tbres" hidden></div>
    </div>
    <div class="actions"><span class="est"></span><button class="sm rm">Ukloni</button><button class="sm cmd" disabled>Prikaži komandu</button><button class="primary go" disabled>Transkoduj</button></div>
  </div></div>`;
}

// ---------------------------------------------------------------- DOM
const CSS = `
#tb .tbbar{border-bottom:1px solid var(--line)}
#tb textarea.paths{min-height:38px;height:38px;padding-top:8px;min-width:220px}
#tb .tbitem{grid-template-columns:1fr}
#tb .path{font-size:12px;color:var(--muted);word-break:break-all;margin:-2px 0 2px}
#tb .f label.chk{text-transform:none;letter-spacing:0;color:var(--text);font-weight:500;font-size:13px;margin:4px 0 0}
#tb .tracks{display:flex;flex-direction:column;gap:2px}
#tb .tracks .chk{margin-top:2px}
#tb .hint{font-size:12px;color:var(--muted);margin-top:4px}
#tb .vnote:empty,#tb .anote:empty,#tb .snote:empty,#tb .onote:empty,#tb .awarn:empty{display:none}
#tb .vnote,#tb .anote,#tb .snote,#tb .onote{margin-top:8px;display:flex;flex-direction:column;gap:3px}
#tb .vnote .note,#tb .anote .note,#tb .snote .note,#tb .onote .note,#tb .awarn .note{margin-top:0}
#tb .tbres{margin-top:12px;display:flex;flex-direction:column;gap:6px}
#tb .tbres .note{margin-top:0}
#tb select option:disabled{color:var(--muted)}
#secJobs,#secTb{scroll-margin-top:64px}`;
const EMPTY = '<div class="empty">Nema fajlova. Dodaj video ili audio sa diska / spoljnog diska, nalepi putanje, ili iz Arhive dugmetom „U Toolbox“.</div>';
let PRESETS = [], presetsReady = null, barTimer = 0;
const PM = id => PRESETS.find(p => p.id === id);
const defPreset = () => PM("arhiva") || PRESETS[0] || null;
const F = (card, k) => $(`[data-t="${k}"]`, card);
const flash = el => { el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1300); };
const readyCards = () => $$("#tb .tbitem").filter(c => c._a);
const barDefault = () => { const p = defPreset(); return `Podrazumevano: preset „${p ? p.label : "Arhiva"}“ — ništa se ne upeče (burn) i ne prevodi dok to ne izabereš.`; };
function barMsg(t) {
  const el = $("#tb .tbbar .est"); if (!el) return;
  el.textContent = t; clearTimeout(barTimer);
  barTimer = setTimeout(() => { el.textContent = barDefault(); }, 6000);
}

function en(card, k, on) {
  const el = F(card, k); if (!el) return;
  el.disabled = !on;
  const f = el.closest(".f"); if (f) f.classList.toggle("dis", !on);
}
// postavi vrednost selecta; ako opcija ne postoji a dat je label — dodaj je (nikad ne gubimo vrednost iz preseta)
function setSel(el, v, label) {
  v = String(v ?? "");
  if (![...el.options].some(o => o.value === v)) { if (!label) return false; el.insertAdjacentHTML("beforeend", opt(v, label)); }
  el.value = v; return true;
}
function fillVq(card, want) {
  const el = F(card, "vq"), vc = F(card, "vcodec").value;
  const fam = vqFam(vc) || (want != null && +want > 51 ? "vt" : card._vqFam || "vt");
  if (want == null || want === "") want = fam === card._vqFam && el.value ? el.value : VQ_DEF[fam];
  if (fam !== card._vqFam || !el.options.length) { el.innerHTML = TBQ[fam].map(([v, l]) => opt(v, l)).join(""); card._vqFam = fam; }
  setSel(el, want, fam === "vt" ? `Ručno (q${want})` : `Ručno (CRF ${want})`);
}
function fillVpreset(card, want) {
  const el = F(card, "vpreset"), vc = F(card, "vcodec").value;
  const fam = vpFam(vc) || (want != null && /^\d+$/.test(String(want)) ? "av1" : card._vpFam || "x26");
  const fits = x => x != null && x !== "" && (fam === "av1") === /^\d+$/.test(String(x));
  if (!fits(want)) want = fam === card._vpFam && el.value ? el.value : VP_DEF[fam];
  if (fam !== card._vpFam || !el.options.length) { el.innerHTML = VP[fam].map(([v, l]) => opt(v, l)).join(""); card._vpFam = fam; }
  setSel(el, want, want + " (ručno)");
}
function setAudio(card, v) {
  const all = $('[data-at="all"]', card), boxes = $$('[data-at]:not([data-at="all"])', card);
  const sel = v === "all" ? null : v === "first" ? [0] : Array.isArray(v) ? v.map(Number) : null;
  boxes.forEach(b => { b.checked = !sel || sel.includes(+b.dataset.at); });
  if (all) all.checked = boxes.length > 0 && boxes.every(b => b.checked);
}
function readFields(card) {
  const f = {};
  $$("[data-t]", card).forEach(el => { f[el.dataset.t] = el.type === "checkbox" ? el.checked : el.value; });
  const boxes = $$('[data-at]:not([data-at="all"])', card), all = $('[data-at="all"]', card);
  f.audio_all = !boxes.length || !!(all && all.checked);
  f.audio_n = boxes.filter(b => b.checked).map(b => +b.dataset.at);
  delete f.preset;
  return f;
}
const rawSpec = card => specFromFields(readFields(card));
const readSpec = card => effectiveSpec(rawSpec(card), card._a);

// primeni spec na karticu; skip = kljucevi koji se ne diraju; from = analiza izvorne kartice (mapiranje traka po jeziku)
function applySpec(card, s, skip = [], from = null) {
  const P = k => F(card, k), use = k => k in s && !skip.includes(k);
  card._auto = {};
  if (use("container")) setSel(P("container"), s.container);
  if (use("vcodec")) setSel(P("vcodec"), s.vcodec);
  fillVq(card, use("vq") ? s.vq : undefined);
  fillVpreset(card, use("vpreset") ? s.vpreset : undefined);
  if (use("vbitrate")) setSel(P("vbitrate"), s.vbitrate, String(s.vbitrate).replace(/M$/i, " Mb/s") + " (ručno)");
  for (const k of ["prores_profile", "fps", "depth", "hdr", "deint", "acodec", "channels", "loudnorm", "subs_keep", "translate", "sub_mode"])
    if (use(k)) setSel(P(k), s[k]);
  if (use("abitrate")) setSel(P("abitrate"), s.abitrate, String(s.abitrate).replace(/k$/i, " kb/s") + " (ručno)");
  if (use("scale") && !setSel(P("scale"), s.scale)) P("scale").value = "src";
  if (use("engine") && use("model") && s.engine && s.model) setSel(P("model_full"), `${s.engine}|${s.model}`, `${s.model} (${s.engine})`);
  for (const k of ["trim_start", "trim_end", "out_dir", "suffix", "sub_file"]) if (use(k)) P(k).value = s[k] ?? "";
  if (use("chapters")) P("chapters").checked = !!s.chapters;
  const m = from ? mapTracks(from, card._a, s) : {audio_tracks: s.audio_tracks, sub_src: s.sub_src};
  if (use("audio_tracks") && m.audio_tracks != null) setAudio(card, m.audio_tracks);
  if (use("sub_src") && m.sub_src != null && !setSel(P("sub_src"), m.sub_src)) P("sub_src").value = "none";
}

function presetDesc(card) {
  const v = F(card, "preset").value, p = PM(v === "custom" ? card._preset : v);
  $(".pdesc", card).textContent = v === "custom" ? `Ručno izmenjeno${p ? " (polazni preset: " + p.label + ")" : ""}.` : (p && p.desc) || "";
}
function markCustom(card) {
  const el = F(card, "preset");
  if (el.value === "custom") return;
  if (!$('option[value="custom"]', el)) el.insertAdjacentHTML("beforeend", opt("custom", "Prilagođeno"));
  el.value = "custom"; presetDesc(card);
}
function setPreset(card, id) {
  const el = F(card, "preset"), c = $('option[value="custom"]', el);
  if (id === "custom") return markCustom(card);
  if (c) c.remove();
  el.value = id; card._preset = id; presetDesc(card);
}
function choosePreset(card, id) {
  const p = PM(id); if (!p) return;
  applySpec(card, p.spec || {}, KEEP);
  setPreset(card, id);
}

// izabrana opcija nije dozvoljena u kontejneru -> fallback (pamti se zelja korisnika);
// kad ponovo postane dozvoljena -> vrati je. Vraca "switch" | "restore" | "".
function autoSel(card, k, fallback) {
  const el = F(card, k), cur = el.selectedOptions[0], A = card._auto || (card._auto = {});
  if (cur && cur.disabled) { if (!A[k]) A[k] = el.value; el.value = fallback; flash(el); return "switch"; }
  const o = A[k] && A[k] !== el.value && [...el.options].find(x => x.value === A[k]);
  if (o && !o.disabled) { el.value = A[k]; delete A[k]; flash(el); return "restore"; }
  return "";
}
function note(list, cls = "note") { return list.filter(Boolean).map(t => `<div class="${cls}">${cls === "err" ? "✕" : "⚠"} ${esc(t)}</div>`).join(""); }

// omogucavanje / onemogucavanje polja + napomene
function sync(card) {
  const a = card._a, P = k => F(card, k), v = a.video;
  const cont = P("container").value, ao = AUDIO_ONLY.includes(cont), hasV = !!v && !ao;
  // video
  const vcopyOk = !v || !V_COPY[cont] || V_COPY[cont].includes(v.codec), vn = [];
  for (const o of P("vcodec").options) o.disabled = o.value === "copy" ? !vcopyOk : (V_BAD[cont] || []).includes(o.value);
  if (hasV) {
    const was = (P("vcodec").selectedOptions[0] || {textContent: ""}).textContent.split(" (")[0], r = autoSel(card, "vcodec", "hevc_vt");
    if (r) { fillVq(card); fillVpreset(card); }
    if (r === "switch") vn.push(`${was} ne ide u ${cont.toUpperCase()}${was.startsWith("Kopiraj") ? " za " + cn(v.codec) : ""} — prebačeno na HEVC VideoToolbox.`);
  }
  const vc = P("vcodec").value, re = hasV && !["copy", "none"].includes(vc), isHdr = !!v && !!v.hdr && v.hdr !== "sdr";
  en(card, "vcodec", hasV);
  en(card, "vbitrate", re && vc !== "prores_vt");
  en(card, "vq", re && vc !== "prores_vt" && !P("vbitrate").value);
  en(card, "vpreset", re && !!vpFam(vc));
  en(card, "prores_profile", re && vc === "prores_vt");
  en(card, "scale", re && P("scale").options.length > 1);
  en(card, "fps", re); en(card, "deint", re);
  en(card, "depth", re && ["hevc_vt", "x265", "x264", "av1"].includes(vc));
  en(card, "hdr", re && isHdr);
  $(".tv", card).classList.toggle("off", !hasV);
  if (!v) vn.push("Izvor nema video traku.");
  else if (ao) vn.push("Audio kontejner — video i titlovi se izostavljaju.");
  else if (vc === "copy") vn.push("Kopija videa: rezolucija, frejmrejt, HDR i filteri ostaju kao u izvoru." + (P("trim_start").value ? " Isečak počinje na najbližem ključnom frejmu." : ""));
  if (re && v.interlaced && P("deint").value === "off") vn.push("Izvor je prepleten (interlaced) — uključi deinterlace.");
  if (re && vc === "h264_vt" && (v.bit_depth || 8) > 8) vn.push("H.264 VideoToolbox je 8-bit — 10-bit izvor ide u 8-bit" + (isHdr && P("hdr").value === "keep" ? "; HDR bez tonemapa izgleda isprano (izaberi SDR)." : "."));
  if (re && isHdr && P("hdr").value === "keep" && P("depth").value === "8" && !P("depth").disabled) vn.push("HDR u 8-bit gubi kvalitet — ostavi dubinu na automatski / 10-bit ili pretvori u SDR.");
  $(".vnote", card).innerHTML = note(vn);
  // audio
  const boxes = $$('[data-at]:not([data-at="all"])', card), all = $('[data-at="all"]', card);
  const sel = boxes.length && all.checked ? a.audio : a.audio.filter(t => boxes.some(b => b.checked && +b.dataset.at === t.n));
  const badCopy = A_COPY[cont] ? sel.filter(t => !A_COPY[cont].includes(t.codec) && !(cont === "mov" && /^pcm_/.test(t.codec || ""))) : [];
  for (const o of P("acodec").options) o.disabled = o.value === "copy" ? badCopy.length > 0 : !!A_OK[cont] && !A_OK[cont].includes(o.value);
  const an = [], hasA = sel.length > 0;
  const was = P("acodec").value;
  if (hasA && autoSel(card, "acodec", A_DEF[cont] || "aac") === "switch") {
    an.push(was === "copy" ? `Kopija ${[...new Set(badCopy.map(t => cn(t.codec)))].join(", ")} trake ne ide u ${cont.toUpperCase()} — prebačeno na ${ACS[P("acodec").value]}.`
      : `${ACS[was] || was} ne ide u ${cont.toUpperCase()} — prebačeno na ${ACS[P("acodec").value]}.`);
  }
  const ac = P("acodec").value;
  en(card, "acodec", hasA);
  en(card, "abitrate", hasA && LOSSY.includes(ac));
  en(card, "channels", hasA && ac !== "copy");
  en(card, "loudnorm", hasA && ac !== "copy");
  if (!a.audio.length) an.push("Izvor nema audio traka.");
  else if (!sel.length) an.push("Nijedna traka nije izabrana — izlaz bez zvuka.");
  if (["mp3", "flac"].includes(cont) && sel.length > 1) an.push(`${cont.toUpperCase()}: u izlaz ide samo prva izabrana traka.`);
  $(".anote", card).innerHTML = note(an);
  // titlovi
  const subsOk = hasV, src = P("sub_src").value, has = subsOk && src !== "none";
  const tr = /^track:\d+$/.test(src) ? a.subs.find(t => "track:" + t.n === src) : null, img = !!tr && !tr.text;
  for (const o of P("sub_src").options) if (o.value === "whisper") o.disabled = !a.audio.length;
  if (img && P("translate").value !== "off") P("translate").value = "off";
  en(card, "subs_keep", subsOk && a.subs.length > 0);
  en(card, "sub_src", subsOk);
  en(card, "translate", has && !img);
  en(card, "model_full", has && !img && P("translate").value !== "off");
  en(card, "sub_mode", has);
  $(".subfile", card).hidden = !(subsOk && src === "file");
  $(".ts", card).classList.toggle("off", !subsOk);
  const sn = [];
  if (img) sn.push("Slikovni titl (PGS/DVD) ne može da se prevede — za prevod izaberi whisper ili spoljni fajl. Burn ide preko overlay-a.");
  if (has && P("sub_mode").value === "burn" && !re) sn.push("Burn traži rekodiranje videa — izaberi video kodek (npr. HEVC VideoToolbox).");
  if (has && img && P("sub_mode").value === "soft" && ["mp4", "mov"].includes(cont)) sn.push("MP4/MOV ne podržava slikovne meke titlove — izaberi MKV ili burn.");
  if (subsOk && P("subs_keep").value === "all" && ["mp4", "mov"].includes(cont) && a.subs.some(t => !t.text)) sn.push("MP4/MOV: slikovni titlovi iz izvora se izostavljaju (tekstualni idu kao mov_text).");
  if (has && src === "whisper") sn.push("Whisper lokalno transkribuje prvu izabranu audio traku.");
  $(".snote", card).innerHTML = note(sn);
  // izlaz
  en(card, "chapters", a.chapters > 0);
  const cc = clientCheck(rawSpec(card), a);
  $(".onote", card).innerHTML = note(cc.errors.filter(e => /isečka/.test(e)), "err") + note(cc.warnings);
}

function updateEst(card) { $(".est", card).textContent = summarize(readSpec(card), card._a); }

function showRes(card, r, withCmd) {
  const el = $(".tbres", card), h = [];
  if (r.ok) h.push(`<div><span class="b ok">${esc(r.ok)}</span></div>`);
  h.push(note(r.errors || [], "err"), note(r.warnings || []));
  if (r.out) h.push(`<div class="outs">Izlaz: ${esc(r.out)}</div>`);
  if (withCmd && r.cmd) h.push(`<pre class="log">${esc(r.cmd)}</pre>`);
  el.innerHTML = h.join(""); el.hidden = !el.innerHTML.trim(); el.style.opacity = "";
}
async function showCmd(card) {
  if (!card._cmdOpen) return;
  const spec = readSpec(card), cc = clientCheck(spec, card._a), seq = card._seq = (card._seq || 0) + 1;
  if (cc.errors.length) return showRes(card, cc);
  $(".tbres", card).style.opacity = ".55";
  let r; try { r = await api("/api/tb/command", {path: card._path, spec}); } catch (e) { r = {errors: [e.message]}; }
  if (seq !== card._seq || !card._cmdOpen) return;
  showRes(card, {...r, warnings: [...cc.warnings, ...(r.warnings || [])]}, true);
}
function toggleCmd(card) {
  card._cmdOpen = !card._cmdOpen;
  $(".cmd", card).textContent = card._cmdOpen ? "Sakrij komandu" : "Prikaži komandu";
  if (card._cmdOpen) showCmd(card); else { card._seq = (card._seq || 0) + 1; showRes(card, {}); }
}
function changed(card) {
  sync(card); updateEst(card);
  clearTimeout(card._t);
  if (card._cmdOpen) card._t = setTimeout(() => showCmd(card), 350);
  else showRes(card, {});
}
const scrollJobs = () => { const j = $("#secJobs"); if (j) j.scrollIntoView({behavior: "smooth", block: "start"}); };

async function tbSubmit(card, o = {}) {
  if (!card._a) return false;
  const btn = $(".go", card), spec = readSpec(card), cc = clientCheck(spec, card._a), sig = JSON.stringify(spec);
  if (cc.errors.length) { showRes(card, cc); return false; }
  if (card._sent === sig) {
    if (o.batch) { showRes(card, {ok: "Već dodato u red (ista podešavanja)"}); return null; }
    if (!confirm("Isti posao (ista podešavanja) je već dodat u red. Dodati ponovo?")) return false;
  }
  btn.disabled = true; btn.textContent = "Proveravam…";
  try {
    const r = await api("/api/tb/command", {path: card._path, spec}), warnings = [...cc.warnings, ...(r.warnings || [])];
    if (r.errors && r.errors.length) { showRes(card, {...r, warnings}, card._cmdOpen); return false; }
    await api("/api/tb/jobs", {path: card._path, spec});
    card._sent = sig;
    showRes(card, {ok: "Dodato u red ✓", out: r.out, warnings, cmd: r.cmd}, card._cmdOpen);
    if (typeof refreshJobs === "function") refreshJobs();
    if (!o.batch) scrollJobs();
    return true;
  } catch (e) { showRes(card, {errors: [e.message]}); return false; }
  finally { btn.disabled = false; btn.textContent = "Transkoduj"; }
}
async function submitAll(btn) {
  const cards = readyCards();
  if (!cards.length) return barMsg("Nema analiziranih fajlova.");
  btn.disabled = true;
  let n = 0, skip = 0, firstBad = null;
  for (const c of cards) { const r = await tbSubmit(c, {batch: true}); if (r) n++; else if (r === null) skip++; else if (!firstBad) firstBad = c; }
  btn.disabled = false;
  barMsg(`${n} od ${cards.length} dodato u red` + (skip ? ` · ${skip} već u redu (ista podešavanja)` : "") +
    (firstBad ? " — pogledaj napomene na karticama." : "."));
  if (firstBad) firstBad.scrollIntoView({behavior: "smooth", block: "center"}); else scrollJobs();
}
function applyFirstToAll() {
  const cards = readyCards();
  if (cards.length < 2) return barMsg("Treba bar dve analizirane kartice.");
  const src = cards[0], s = rawSpec(src), pv = F(src, "preset").value;
  for (const c of cards.slice(1)) {
    applySpec(c, s, ["trim_start", "trim_end", "sub_file"], src._a);
    c._preset = src._preset;
    setPreset(c, pv);
    changed(c); flash(c);
  }
  barMsg(`Podešavanja prve kartice primenjena na još ${cards.length - 1} (trake uparene po jeziku).`);
}

function renderInfo(card, a) {
  const v = a.video, B = [];
  $(".ttl", card).textContent = a.name || baseName(card._path);
  $(".path", card).textContent = a.path || card._path;
  $(".meta", card).textContent = [contName(a), fmtD(a.duration), fmtB(a.size), a.bitrate ? (a.bitrate / 1e6).toFixed(1) + " Mb/s" : "",
    a.chapters ? a.chapters + " poglavlja" : ""].filter(Boolean).join(" · ");
  if (v) {
    B.push(`<span class="b">${esc(cn(v.codec))}${v.profile ? " " + esc(v.profile) : ""}</span>`);
    B.push(`<span class="b">${esc(v.width)}×${esc(v.height)}${v.fps ? " · " + esc(fpsTxt(v.fps)) + " fps" : ""}</span>`);
    B.push(`<span class="b plain">${esc(v.bit_depth || 8)}-bit${v.pix_fmt ? " · " + esc(v.pix_fmt) : ""}</span>`);
    B.push(v.hdr && v.hdr !== "sdr" ? `<span class="b warn">${esc(HDRN[v.hdr] || "HDR")}</span>` : '<span class="b plain">SDR</span>');
    if (v.interlaced) B.push('<span class="b warn">Prepleten (interlaced)</span>');
  } else B.push('<span class="b warn">Bez videa — samo audio</span>');
  for (const t of a.audio) B.push(`<span class="b" title="${esc(audioLabel(t))}">${esc(audioLabel(t, true))}</span>`);
  for (const t of a.subs) B.push(`<span class="b ${t.text ? "" : "plain"}" title="${esc(subLabel(t))}">${esc(subLabel(t, true))}</span>`);
  if (!a.subs.length && v) B.push('<span class="b plain">bez titlova</span>');
  $(".badges", card).innerHTML = B.join("");
  $(".awarn", card).innerHTML = note(a.warnings || []);
}

function buildForm(card, a) {
  const P = k => F(card, k), v = a.video;
  for (const k of ["container", "vcodec", "vbitrate", "prores_profile", "fps", "depth", "hdr", "deint", "acodec", "abitrate", "channels",
    "loudnorm", "subs_keep", "translate", "sub_mode"]) P(k).innerHTML = O[k].map(([x, l]) => opt(x, l)).join("");
  if (v && v.fps) P("fps").options[0].textContent = `Kao izvor (${fpsTxt(v.fps)} fps)`;
  if (v && v.hdr && v.hdr !== "sdr") P("hdr").options[0].textContent = `Zadrži ${HDRN[v.hdr] || "HDR"}`;
  P("scale").innerHTML = opt("src", "Kao izvor" + (v ? ` (${v.width}×${v.height})` : "")) +
    SCALES.filter(([h]) => v && h < Math.min(v.width, v.height)).map(([h, l]) => opt(h, l)).join("");
  P("sub_src").innerHTML = opt("none", "Bez dodatnog titla") +
    (a.subs.length ? '<optgroup label="Traka iz fajla">' + a.subs.map(t => opt("track:" + t.n, subLabel(t) + (t.text ? "" : " — bez prevoda"))).join("") + "</optgroup>" : "") +
    '<optgroup label="Drugi izvor">' + opt("file", "Spoljni fajl (SRT / ASS / VTT)…") + opt("whisper", "Lokalni whisper (transkripcija zvuka)") + "</optgroup>";
  P("model_full").innerHTML = modelOptionsHtml();
  P("preset").innerHTML = PRESETS.length ? PRESETS.map(p => opt(p.id, p.label)).join("") : opt("", "Podrazumevano");
  $(".tracks", card).innerHTML = !a.audio.length ? '<div class="small muted">Izvor nema audio traka.</div>' :
    `<label class="chk"><input type="checkbox" data-at="all"> <b>Sve trake</b>&nbsp;<span class="muted">(${a.audio.length})</span></label>` +
    a.audio.map(t => `<label class="chk"><input type="checkbox" data-at="${esc(t.n)}"> ${esc(audioLabel(t))}</label>`).join("");
  $(".chtxt", card).textContent = a.chapters ? `zadrži poglavlja (${a.chapters})` : "izvor nema poglavlja";
  // podrazumevano: preset "arhiva", bez prevoda, bez burna, bez dodatnog titla
  const p = defPreset(), eng = (typeof SET === "object" && SET) || {};
  applySpec(card, {...FALLBACK, ...((p && p.spec) || {}), translate: "off", sub_src: "none", sub_mode: "soft", sub_file: "",
    trim_start: "", trim_end: "", engine: eng.engine || FALLBACK.engine, model: eng.model || FALLBACK.model});
  setPreset(card, p ? p.id : "");
  card.addEventListener("change", e => {
    const t = e.target, k = t.dataset.t;
    if (k === "preset") { choosePreset(card, t.value); return changed(card); }
    if (t.dataset.at !== undefined) {
      const bs = $$('[data-at]:not([data-at="all"])', card), all = $('[data-at="all"]', card);
      if (t.dataset.at === "all") bs.forEach(b => { b.checked = t.checked; }); else all.checked = bs.every(b => b.checked);
    }
    if (k === "vcodec" || k === "acodec") delete (card._auto || {})[k];
    if (k === "vcodec") { fillVq(card); fillVpreset(card); }
    if (k === "sub_mode" && t.value === "burn" && ["copy", "none"].includes(P("vcodec").value) && v && !AUDIO_ONLY.includes(P("container").value)) {
      P("vcodec").value = "hevc_vt"; fillVq(card); fillVpreset(card); flash(P("vcodec"));
    }
    if ((k && !NO_CUSTOM.includes(k)) || t.dataset.at !== undefined) markCustom(card);
    changed(card);
    if (k === "sub_src" && t.value === "file" && !P("sub_file").value.trim()) pickSub(card);
  });
  card.addEventListener("input", e => { if (e.target.matches('input[type="text"]')) changed(card); });
  $(".pickSub", card).onclick = () => pickSub(card);
  $(".pickDir", card).onclick = async () => {
    try { const r = await api("/api/choose-folder", {current: P("out_dir").value}); if (r.path) { P("out_dir").value = r.path; changed(card); } }
    catch (e) { showRes(card, {errors: [e.message]}); }
  };
  $(".cmd", card).onclick = () => toggleCmd(card);
  $(".go", card).onclick = () => tbSubmit(card);
  $(".forms", card).hidden = false;
  $(".cmd", card).disabled = $(".go", card).disabled = false;
  sync(card); updateEst(card);
}
async function pickSub(card) {
  try {
    const r = await api("/api/tb/choose-files", {kind: "sub"}), p = (r.paths || [])[0];
    if (!p) return;
    F(card, "sub_file").value = p; F(card, "sub_src").value = "file";
    markCustom(card); changed(card);
  } catch (e) { showRes(card, {errors: [e.message]}); }
}

async function tbAddPath(path, o = {}) {
  path = String(path ?? "").trim();
  const list = $("#tb .tblist");
  if (!path || !list) return null;
  const dup = $$(".tbitem", list).find(c => c._path === path || c._src === path);
  if (dup && dup._err) dup.remove();  // neuspela analiza (npr. disk nije bio povezan) -> probaj ponovo
  else if (dup) { flash(dup); dup.scrollIntoView({behavior: "smooth", block: "nearest"}); return dup; }
  $$(".empty", list).forEach(e => e.remove());
  const tmp = document.createElement("div"); tmp.innerHTML = cardHtml();
  const card = tmp.firstElementChild;
  card._path = card._src = path;
  list.appendChild(card);
  $(".ttl", card).textContent = baseName(path);
  $(".path", card).textContent = path;
  $(".meta", card).textContent = "analiziram (ffprobe)…";
  $(".rm", card).onclick = () => { card.remove(); if (!$(".tbitem", list)) list.innerHTML = EMPTY; };
  if (o.scroll !== false) setTimeout(() => { if (card.isConnected) { card.scrollIntoView({behavior: "smooth", block: "nearest"}); flash(card); } }, 80);
  let a;
  try { [a] = await Promise.all([api("/api/tb/analyze", {path}), presetsReady]); }
  catch (e) { card._err = true; $(".meta", card).innerHTML = `<span class="err">${esc(e.message)}</span>`; return card; }
  if (!card.isConnected) return card;
  // isti fajl pod drugim imenom (~/…, //, putanja iz dijaloga) -> backend vraca normalizovan a.path
  const same = a.path && $$(".tbitem", list).find(c => c !== card && c._path === a.path);
  if (same && !same._err) { card.remove(); flash(same); same.scrollIntoView({behavior: "smooth", block: "nearest"}); return same; }
  if (same) same.remove();
  a = {...a, video: a.video || null, audio: a.audio || [], subs: a.subs || [], chapters: a.chapters || 0};
  card._a = a;
  if (a.path) card._path = a.path;
  renderInfo(card, a);
  buildForm(card, a);
  return card;
}
async function addMany(paths) {
  if (!paths.length) return;
  await Promise.all(paths.map(p => tbAddPath(p, {scroll: false})));
}

function initToolbox() {
  const root = $("#tb");
  if (!root || root._tb) return;
  root._tb = true;
  if (!document.getElementById("tbcss")) { const st = document.createElement("style"); st.id = "tbcss"; st.textContent = CSS; document.head.appendChild(st); }
  root.innerHTML = `
    <div class="pad tbbar">
      <div class="row" style="align-items:flex-start">
        <button class="primary" data-tb="choose">Dodaj fajlove…</button>
        <textarea class="grow paths" data-tb="paths" rows="1" placeholder="…ili nalepi putanje (jednu ili više; novi red, razmak ili navodnici) — Enter = Dodaj"></textarea>
        <button data-tb="add">Dodaj</button>
      </div>
      <div class="actions">
        <span class="est"></span>
        <button class="sm" data-tb="apply" title="Kopira podešavanja sa prve kartice na ostale (audio i titl trake uparuje po jeziku; isečak ostaje po fajlu)">Primeni prvu na sve</button>
        <button class="sm primary" data-tb="all">Transkoduj sve</button>
      </div>
    </div>
    <div class="tblist">${EMPTY}</div>`;
  const B = k => $(`[data-tb="${k}"]`, root), ta = B("paths");
  B("choose").onclick = async () => {
    try { const r = await api("/api/tb/choose-files", {kind: "media"}); await addMany(r.paths || []); }
    catch (e) { barMsg("Izbor fajlova: " + e.message); }
  };
  const addTyped = () => { const ps = parsePaths(ta.value); if (!ps.length) return; ta.value = ""; addMany(ps); };
  B("add").onclick = addTyped;
  ta.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); addTyped(); } });
  B("apply").onclick = applyFirstToAll;
  B("all").onclick = () => submitAll(B("all"));
  $(".est", root).textContent = "učitavam presete…";
  presetsReady = api("/api/tb/presets")
    .then(r => { PRESETS = (Array.isArray(r) ? r : r.presets || []).filter(p => p && p.id); $(".est", root).textContent = barDefault(); })
    .catch(e => { PRESETS = []; $(".est", root).textContent = "Preseti nisu učitani (" + e.message + ") — koriste se ugrađena podrazumevana podešavanja."; });
}

window.initToolbox = initToolbox;
window.tbAddPath = tbAddPath;
window.__tbTest = {SPEC_KEYS, FALLBACK, KEEP, parsePaths, toSec, normTime, specFromFields, effectiveSpec, mapTracks, clientCheck, summarize,
  cardHtml, contName, audioLabel, subLabel, readSpec, rawSpec};
})();
