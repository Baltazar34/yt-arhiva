// ================================================================ PLAN — "Šta da uradim" (kartica Preuzmi) + bedž stanja prevoda/transkripta
// Obican skript (bez modula), ucitava se PRE glavnog skripta iz index.html. Cisti helperi ne diraju DOM
// (tests/plan_ui_check.js preko window.__planTest); kontroler (mount/render) radi samo preko ctx.root i ctx kuka.
// Ugovor: docs/plan-contract.md §B, §C, §E
(() => {
"use strict";

// sopstveni esc (isti kao u index.html) — plan.js se ucitava pre glavnog skripta
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const opt = (v, l, sel, dis) => `<option value="${esc(v)}"${sel ? " selected" : ""}${dis ? " disabled" : ""}>${esc(l)}</option>`;
const arr = x => Array.isArray(x) ? x : [];
const has = (list, v) => list.some(([k]) => k === v);
const p2 = x => String(x).padStart(2, "0");

// ---------------------------------------------------------------- izbori (vrednosti = opcije posla, ugovor §C)
const WHAT = [["full", "Ceo sadržaj — najbolji video + sve audio trake + svi titlovi"], ["audio", "Samo audio"], ["subs", "Samo titlovi"]];
const QUALITY = [["best", "najbolji"], ["2160", "do 2160p"], ["1440", "do 1440p"], ["1080", "do 1080p"], ["720", "do 720p"], ["480", "do 480p"], ["360", "do 360p"]];
// isto kao ACONV_FULL u index.html (proverava test)
const ACONV = [["best", "Original bez rekodiranja (Opus/AAC)"], ["mp3", "MP3 320 kb/s"], ["mp3v0", "MP3 V0 (VBR, najbolji)"],
  ["m4a", "M4A AAC 256 kb/s"], ["opus", "Opus 160 kb/s"], ["flac", "FLAC (ne poboljšava izvor)"]];
const WLANGS = [["", "automatski"], ["sr", "srpski"], ["en", "engleski"], ["el", "grčki"], ["de", "nemački"], ["fr", "francuski"],
  ["it", "italijanski"], ["es", "španski"], ["pt", "portugalski"], ["ru", "ruski"], ["uk", "ukrajinski"], ["hr", "hrvatski"],
  ["bs", "bosanski"], ["mk", "makedonski"], ["bg", "bugarski"], ["sl", "slovenački"], ["pl", "poljski"], ["cs", "češki"],
  ["hu", "mađarski"], ["ro", "rumunski"], ["nl", "holandski"], ["sv", "švedski"], ["tr", "turski"], ["ar", "arapski"],
  ["hi", "hindi"], ["ja", "japanski"], ["ko", "korejski"], ["zh", "kineski"]];
const TX_WHEN = [["missing", "samo kad YouTube nema titl"], ["always", "uvek"]];
const TARGETS = [["lat", "srpski latinica"], ["cyr", "srpski ćirilica"], ["el", "grčki"]];
const VID_FMT = [["burn", "MP4 sa upečenim titlom"], ["soft", "MKV sa mekim titlovima"], ["both", "oba (MP4 + MKV)"]];
// isto kao VQ u index.html (proverava test)
const VQ_VT = [["75", "Vrlo visok (q75, veliki fajl)"], ["65", "Arhivski (q65, preporuka)"], ["55", "Dobar (q55)"], ["45", "Manji fajl (q45)"]];
const VQ_SW = [["18", "Vizuelno bez gubitka (CRF 18)"], ["20", "Arhivski (CRF 20, preporuka)"], ["23", "Dobar (CRF 23)"], ["26", "Manji fajl (CRF 26)"]];
const SCALES = [["src", "kao izvor"], ["2160", "2160p"], ["1440", "1440p"], ["1080", "1080p"], ["720", "720p"], ["480", "480p"]];
const TB_SUB = [["none", "bez titla"], ["soft", "meki titl"], ["burn", "upečen titl"]];
const AU_PRESETS = [["audio_m4a", "M4A (AAC 256)"], ["audio_mp3", "MP3 320"], ["audio_flac", "FLAC"]];
const STEPS = ["tx", "tr", "vid", "tb", "au"];  // redovi 2–6 (red 1 = preuzimanje, uvek)
const ROWS = {dl: ["⬇", "Preuzmi"], tx: ["📝", "Transkript (whisper)"], tr: ["🌐", "Prevod"], vid: ["🎬", "Video sa titlom"],
  tb: ["🛠", "Transkoduj (Toolbox)"], au: ["🎵", "Izvuci audio"]};
const QUICK = [
  {id: "samo", icon: "⬇", label: "Samo preuzmi", what: "full", on: [],
   title: "Podrazumevano: ceo sadržaj (najbolji video, sve audio trake, svi titlovi), bez obrade"},
  // whisper uvek: sa "samo kad nema titl" vecina videa (YouTube auto titl) ne bi dobila transkript
  {id: "tx", icon: "📝", label: "+ Transkript", what: "full", on: ["tx"], set: {tx_when: "always"},
   title: "Ceo sadržaj + whisper transkript (uvek) na originalnom jeziku, bez prevoda"},
  {id: "tr", icon: "🌐", label: "+ Prevod", what: "full", on: ["tr"], title: "Ceo sadržaj + prevod titla"},
  {id: "vid", icon: "🎬", label: "+ Video sa titlom", what: "full", on: ["tr", "vid"], title: "Ceo sadržaj + prevod + video sa titlom za gledanje"},
  {id: "muzika", icon: "🎵", label: "Muzika", what: "audio", on: [], set: {aconv: "mp3"}, title: "Samo audio, MP3 320, omot i tagovi"},
  {id: "titlovi", icon: "💬", label: "Samo titlovi + Prevod", what: "subs", on: ["tr"], title: "Svi titlovi kao SRT + prevod, bez videa"},
];
const DEF = {v: 1, what: "full", quality: "best", aconv: "mp3", tx: false, tx_lang: "", tx_when: "missing", tr: false, tr_target: "lat",
  tr_engine: "", vid: false, vid_fmt: "burn", vid_vq: "65", vid_scale: "src", tb: false, tb_preset: "arhiva", tb_sub: "none",
  au: false, au_preset: "audio_m4a", all: false};
const CONV = {mp3: "MP3 320 kb/s", mp3v0: "MP3 V0", m4a: "M4A AAC 256 kb/s", opus: "Opus 160 kb/s", flac: "FLAC", best: "original (Opus/AAC)"};
const ENC = {hevc: "HEVC VideoToolbox", h264: "H.264 VideoToolbox", x265: "HEVC x265", x264: "H.264 x264"};
const TGT_LBL = {lat: "srpski latinica", cyr: "srpski ćirilica", el: "grčki"};
const AU_LBL = Object.fromEntries(AU_PRESETS);

// ---------------------------------------------------------------- stanje
const vqTable = enc => ["x265", "x264"].includes(enc) ? VQ_SW : VQ_VT;
function vqDefault(env) {
  const t = vqTable(env.encoder), q = String(env.quality ?? "");
  return has(t, q) ? q : t === VQ_SW ? "20" : "65";
}
// localStorage / stari zapis -> uvek ispravno stanje (nepoznato = podrazumevano)
function normState(raw, env = {}) {
  const r = raw && typeof raw === "object" ? raw : {}, s = {...DEF};
  s.tr_target = ["lat", "cyr"].includes(env.script) ? env.script : "lat";
  s.vid_vq = vqDefault(env);
  const pick = (k, list) => { const v = r[k]; if ((typeof v === "string" || typeof v === "number") && has(list, String(v))) s[k] = String(v); };
  pick("what", WHAT); pick("quality", QUALITY); pick("aconv", ACONV); pick("tx_lang", WLANGS); pick("tx_when", TX_WHEN);
  pick("tr_target", TARGETS); pick("vid_fmt", VID_FMT); pick("vid_vq", vqTable(env.encoder)); pick("vid_scale", SCALES);
  pick("tb_sub", TB_SUB); pick("au_preset", AU_PRESETS);
  for (const k of [...STEPS, "all"]) s[k] = r[k] === true;
  if (typeof r.tr_engine === "string" && /^[a-z]+\|[^\s<>"'`]{0,200}$/.test(r.tr_engine)) s.tr_engine = r.tr_engine;
  if (typeof r.tb_preset === "string" && /^[A-Za-z0-9_-]{1,40}$/.test(r.tb_preset) && !r.tb_preset.startsWith("audio_")) s.tb_preset = r.tb_preset;
  return s;
}
const whatOf = dl => { const v = String((dl && dl.video_fmt) || "auto:best"); return v === "audio" ? "audio" : v === "none" ? "subs" : "full"; };
const WHY_SHORT = {audio: "nije moguće uz Samo audio", subs: "nije moguće uz Samo titlove"};
// zasto je red iskljucen (prazno = moze)
function disabledWhy(what, o = {}) {
  const w = {};
  if (what === "audio") {
    w.vid = "Samo audio nema video — Video sa titlom traži „Ceo sadržaj“.";
    w.tb = "Samo audio nema video za transkodiranje — format zvuka biraš u redu Preuzmi.";
    w.au = "Već preuzimaš samo audio — format zvuka biraš u redu Preuzmi.";
  } else if (what === "subs") {
    w.tx = "Samo titlovi ne preuzima zvuk, pa whisper nema šta da sluša (svi YouTube titlovi se ionako preuzimaju).";
    w.vid = "Samo titlovi — nema videa za titl.";
    w.tb = w.au = "Samo titlovi — nema videa ni zvuka.";
  }
  if (o.noToolbox && !w.tb) w.tb = "Toolbox preseti nisu dostupni (starija verzija servera).";
  return w;
}
function effective(s, what, why) {
  why = why || disabledWhy(what);
  const e = {};
  for (const k of STEPS) e[k] = !!s[k] && !why[k];
  return e;
}
function splitEngine(v) {  // "ollama|hf.co/x|y:q4" -> ["ollama", "hf.co/x|y:q4"]
  const s = String(v || ""), i = s.indexOf("|");
  return i < 0 ? [s || "synergy", ""] : [s.slice(0, i), s.slice(i + 1)];
}
const engineOf = (s, env = {}) => s.tr_engine || `${env.engine || "synergy"}|${env.model || ""}`;

// stanje -> {dl, proc} (ugovor §C); after posebno (afterOf)
function toOpts(st, env = {}) {
  const s = normState(st, env), what = s.what;
  const dl = what === "audio" ? {video_fmt: "audio", audio_fmt: "auto", audio_conv: s.aconv, subs: "none"}
    : what === "subs" ? {video_fmt: "none", subs: "all"}
    : {video_fmt: s.quality === "best" ? "auto:best" : "auto:" + s.quality, audio_fmt: "all", subs: "all"};
  const e = effective(s, what);
  if (!e.tx && !e.tr && !e.vid) return {dl, proc: null};
  const [engine, model] = splitEngine(engineOf(s, env));
  const proc = {sub_source: e.tx && s.tx_when === "always" ? "whisper:run" : "auto-best"};
  if (e.tx && s.tx_lang) proc.whisper_lang = s.tx_lang;
  Object.assign(proc, {target: e.tr ? s.tr_target : "none", engine, model,
    output: e.vid ? s.vid_fmt : what === "subs" ? "srt" : "none",
    encoder: env.encoder || "hevc", vq: +s.vid_vq, scale: s.vid_scale,
    audio_out: "orig", audio_enc: "aac192", subs_out: "sr+orig", keep_source: env.keep_original !== false});
  return {dl, proc};
}
const isVideoPreset = p => !!p && typeof p === "object" && !!p.id && !String(p.id).startsWith("audio_")
  && !["m4a", "mp3", "flac"].includes((p.spec || {}).container) && (p.spec || {}).vcodec !== "none";
const copiesVideo = p => !!p && ["copy", "none"].includes((p.spec || {}).vcodec);
function tbPresetOf(s, presets) {  // izabrani video preset (ili zamena kad ga server nema)
  const ps = arr(presets).filter(isVideoPreset);
  if (!ps.length) return {id: s.tb_preset, p: null};
  const p = ps.find(x => x.id === s.tb_preset) || ps.find(x => x.id === "arhiva") || ps[0];
  return {id: p.id, p};
}
// upecen titl uz preset koji samo kopira video -> server vraca 400; koristi se meki
const tbSubOf = (s, p) => s.tb_sub === "burn" && copiesVideo(p) ? "soft" : s.tb_sub;
const noToolboxOf = presets => Array.isArray(presets) && !presets.some(isVideoPreset);
// koraci posle preuzimanja: {"toolbox": {preset, sub, target}, "audio": {preset}} — samo cekirani (ugovor §C)
function afterOf(st, what, env = {}) {
  const s = normState(st, env), why = disabledWhy(what || s.what, {noToolbox: noToolboxOf(env.presets)}), a = {};
  if (s.tb && !why.tb) {
    const {id, p} = tbPresetOf(s, env.presets), sub = tbSubOf(s, p);
    a.toolbox = {preset: id, sub, target: sub === "none" ? null : s.tr_target};
  }
  if (s.au && !why.au) a.audio = {preset: s.au_preset};
  return a.toolbox || a.audio ? a : null;
}
function matchQuick(s, what) {
  what = what || s.what;
  const e = effective(s, what), q = QUICK.find(x => x.what === what && STEPS.every(k => x.on.includes(k) === e[k]));
  return q ? q.id : "";
}
function applyQuick(s, id) {
  const q = QUICK.find(x => x.id === id);
  if (!q) return {...s};
  const o = {...s, what: q.what, ...(q.set || {})};
  for (const k of STEPS) o[k] = q.on.includes(k);
  return o;
}
// "⚙ Sve opcije" -> redovi (koliko moze; redovi 5–6 se ne diraju)
function fromOpts(o, st, env = {}) {
  const s = {...normState(st, env)}, d = (o && o.dl) || {}, p = o && o.proc, v = String(d.video_fmt || "auto:best");
  s.what = whatOf(d);
  if (s.what === "full" && v.startsWith("auto:") && has(QUALITY, v.slice(5))) s.quality = v.slice(5);
  if (s.what === "audio" && has(ACONV, String(d.audio_conv || ""))) s.aconv = d.audio_conv;
  if (!p || typeof p !== "object") { s.tx = s.tr = s.vid = false; return normState(s, env); }
  const src = p.sub_source || "auto-best", tgt = p.target || "none", out = p.output || "none";
  // auto-best uz vec cekiran "samo kad nema" = isto ponasanje -> red ostaje cekiran
  const keep = s.tx && s.tx_when === "missing";
  s.tx = src === "whisper:run" || (src === "auto-best" && (keep || !!p.whisper_lang || (tgt === "none" && ["none", "srt"].includes(out))));
  if (s.tx) s.tx_when = src === "whisper:run" ? "always" : "missing";
  if ("whisper_lang" in p ? has(WLANGS, String(p.whisper_lang)) : s.tx) s.tx_lang = "whisper_lang" in p ? String(p.whisper_lang) : "";
  s.tr = tgt !== "none";
  if (s.tr && has(TARGETS, tgt)) s.tr_target = tgt;
  if (p.engine) {  // isti kao podrazumevani iz Podesavanja -> "" (prati Podesavanja)
    const v = `${p.engine}|${p.model || ""}`;
    s.tr_engine = v === engineOf({tr_engine: ""}, env) ? "" : v;
  }
  s.vid = ["burn", "soft", "both"].includes(out);
  if (s.vid) s.vid_fmt = out;
  if (p.vq != null && has(vqTable(env.encoder), String(p.vq))) s.vid_vq = String(p.vq);
  if (has(SCALES, String(p.scale || ""))) s.vid_scale = String(p.scale);
  return normState(s, env);
}
// "Podrazumevani izbor" iz Podesavanja -> stanje (prvo pokretanje i izmena podesavanja)
function planFromRecipe(rid, env = {}) {
  const tr = env.auto_translate !== false;
  if (rid === "arhiva" && env.plan_migrated !== true) rid = "samo";  // stari podrazumevani (pre 3.2) -> samo preuzimanje
  const m = {arhiva: {tr}, arhiva_video: {tr, vid: true, vid_fmt: "burn"}, muzika: {what: "audio", aconv: "mp3"},
    titlovi: {what: "subs", tr}, custom: {all: true}}[rid] || {};
  return normState({what: "full", ...m}, env);
}
// samo koraci iz recepta (meniji ostaju kako ih je korisnik podesio)
function recipePatch(rid, env = {}) {
  const r = planFromRecipe(rid, env), o = {what: r.what, all: r.all};
  for (const k of STEPS) o[k] = r[k];
  if (r.what === "audio") o.aconv = r.aconv;
  if (r.vid) o.vid_fmt = r.vid_fmt;
  return o;
}
function shortLabel(s, what, all) {
  what = what || s.what;
  const e = effective(s, what), parts = [all ? "⚙ Sve opcije" : {full: "Ceo sadržaj", audio: "Samo audio", subs: "Samo titlovi"}[what]];
  if (!all) { if (e.tx) parts.push("Transkript"); if (e.tr) parts.push("Prevod"); if (e.vid) parts.push("Video sa titlom"); }
  if (e.tb) parts.push("Toolbox");
  if (e.au) parts.push("Audio");
  return parts.join(" + ");
}
const langName = c => (WLANGS.find(([k]) => k === c) || [])[1] || String(c || "");
// jedna recenica: sta ce se uraditi (preuzimanje, transkript, prevod, izlaz, koraci posle); env.tx = cekiran red Transkript
function describe(o, env = {}) {
  o = o || {};
  const d = o.dl || {}, p = o.proc, a = o.after || {}, v = String(d.video_fmt || "auto:best"), parts = [];
  const noVid = v === "audio" || v === "none";
  if (v === "audio") parts.push(`Samo audio · ${CONV[d.audio_conv || "best"] || d.audio_conv}${d.audio_fmt === "all" ? " (sve trake → MKV)" : ""} · omot i tagovi`);
  else if (v === "none") parts.push("Bez videa");
  else parts.push(v === "auto:best" ? "Najbolji video" : v.startsWith("auto:") ? `Video do ${v.slice(5)}p` : "Izabrani video format");
  if (!noVid) parts.push(d.audio_fmt === "all" ? "sve audio trake" : "originalna audio traka");
  const subs = {all: "svi titlovi", "all+tr": "svi titlovi + YouTube automatski prevodi", none: "bez titlova"}[d.subs] || "najbolji titl";
  parts.push(subs + (env.embed && !noVid && d.subs !== "none" ? " (i u MKV)" : ""));
  if (!p) parts.push(v === "audio" && d.subs === "none" ? "bez prevoda" : "bez prevoda (kasnije: Biblioteka → Prevedi)");
  else {
    const src = p.sub_source || "auto-best", tgt = p.target && p.target !== "none" ? p.target : "", out = p.output || "none";
    const wl = p.whisper_lang ? langName(p.whisper_lang) : "jezik automatski";
    const tx = src === "whisper:run" ? `transkript: whisper (${wl})`
      : src === "auto-best" && (p.whisper_lang || env.tx || (!tgt && out === "none")) ? `transkript: whisper samo ako YouTube nema titl (${wl})` : "";
    const who = tgt === "el" ? "grčki " : tgt ? "srpski " : "originalni ";
    const outT = {none: "SRT u arhivi", srt: "SRT fajl", soft: "MKV sa mekim titlovima",
      burn: `MP4 za gledanje (${ENC[p.encoder || env.encoder || "hevc"] || p.encoder}, upečen ${who}titl, ${p.audio_out === "all" ? "sve audio trake" : "originalni zvuk"})`,
      both: "MP4 sa upečenim titlom + MKV sa mekim"}[out] || out;
    if (tx) parts.push(tx);
    if (tgt) parts.push(`prevod: ${TGT_LBL[tgt] || tgt}${env.modelName ? ` (${env.modelName(p)})` : ""} → ${outT}`);
    else if (out !== "none") parts.push(`bez prevoda (originalni titl) → ${outT}`);
    else parts.push(tx ? "bez prevoda" : "bez prevoda (originalni titl)");
  }
  if (!noVid && (!p || ["none", "srt"].includes(p.output || "none")) && !a.toolbox) parts.push("bez rekodiranja");
  if (a.toolbox) {
    const t = a.toolbox, sub = t.sub && t.sub !== "none" ? ` + ${t.sub === "burn" ? "upečen" : "meki"} titl (${TGT_LBL[t.target] || t.target || "prevod"})` : "";
    parts.push(`posle: Toolbox „${env.presetLabel ? env.presetLabel(t.preset) : t.preset}“${sub}`);
  }
  if (a.audio) parts.push(`posle: audio ${AU_LBL[a.audio.preset] || a.audio.preset}`);
  // server tada ne brise izvor (koraci posle rade iz njega)
  if ((a.toolbox || a.audio) && p && p.keep_source === false && ["burn", "soft", "both"].includes(p.output || "none"))
    parts.push("izvor se zadržava (treba koracima posle)");
  return parts.join(" · ");
}

// ---------------------------------------------------------------- stanje prevoda/transkripta stavke (bedž)
const TGT_SHORT = {lat: "SR lat", cyr: "SR ćir", el: "EL"};
const ENG_SHORT = {synergy: "Kvalitetno", ollama: "Brzo", tg: "TranslateGemma", gemini: "Gemini", cloudflare: "Cloudflare", claude: "Claude"};
const KIND_SHORT = {manual: "CC", auto: "auto", whisper: "whisper"};
const TR_STATES = ["translated", "transcript", "none"];
function baseLang(c) {
  const b = String(c ?? "").trim().toLowerCase().split(/[-_]/)[0];
  return b === "und" || !/^[a-z]{2,3}$/.test(b) ? "" : b;
}
const isSrCc = s => !!s && s.kind === "manual" && ["sr", "sh"].includes(baseLang(s.lang));
const srTarget = s => { const t = String(s.key || "").split(":")[1]; return ["lat", "cyr"].includes(t) ? t : /ćirilica|cyr/i.test([s.name, s.file].join(" ")) ? "cyr" : "lat"; };
function keyEM(s) {  // engine/model: iz polja, inace iz kljuca 'sr:<t>:<engine>:<model>[:rev|:lektura]:<src>'
  if (s.engine || s.model) return [String(s.engine || ""), String(s.model || "")];
  let k = String(s.key || "");
  if (!/^(sr|tr):/.test(k)) return ["", ""];
  if (s.src && k.endsWith(":" + s.src)) k = k.slice(0, -String(s.src).length - 1);
  const p = k.split(":").slice(2);
  while (p.length && ["rev", "lektura"].includes(p[p.length - 1])) p.pop();
  if (!p.length || p[0] === "rebuilt") return ["", ""];
  return [p[0], s.src ? p.slice(1).join(":") : (p[1] || "")];
}
// stavka biblioteke / "U arhivi vec imas" (cat_short) -> {state, translated, transcripts, srcc, src};
// stariji server bez tr_state: izracunaj iz subs (ili iz brojeva u cat_short)
function trInfo(it) {
  const o = it && typeof it === "object" ? it : {};
  const subs = arr(o.subs).filter(s => s && typeof s === "object");
  let tr = Array.isArray(o.translated) ? o.translated.filter(x => x && typeof x === "object") : null;
  let tx = Array.isArray(o.transcripts) ? o.transcripts.filter(x => x && typeof x === "object") : null;
  if (!tr) {
    tr = subs.filter(s => s.kind === "sr" || s.kind === "tr").map(s => {
      const [engine, model] = keyEM(s);
      return {target: s.kind === "sr" ? srTarget(s) : baseLang(s.lang) || String(s.lang || ""), engine, model, date: s.date || null,
        lektura: /:lektura:/.test(String(s.key || "")), file: s.file || "", name: s.name || ""};
    }).reverse();  // manifest: najnoviji na kraju -> najnoviji prvi (kao server)
    if (!subs.length) {  // cat_short stariji: sr = pisma, tr = broj srpskih prevoda
      tr = arr(o.sr).filter(t => typeof t === "string").map(t => ({target: t}));
      if (!tr.length && typeof o.tr === "number" && o.tr > 0) tr = [{target: ""}];
    }
  }
  if (!tx) {
    tx = subs.filter(s => ["manual", "auto", "whisper"].includes(s.kind)).map(s => ({kind: s.kind, lang: s.lang || "", file: s.file || ""}));
    if (!subs.length && typeof o.subs === "number" && o.subs > 0) tx = Array.from({length: Math.min(o.subs, 99)}, () => ({kind: "", lang: ""}));
  }
  const srcc = !!o.sr_cc || subs.some(isSrCc) || tx.some(isSrCc);
  const state = TR_STATES.includes(o.tr_state) ? o.tr_state : tr.length || srcc ? "translated" : tx.length ? "transcript" : "none";
  // snd: izvor ima zvuk (server has_audio; stariji server bez polja -> pretpostavi da ima)
  return {state, translated: tr, transcripts: tx, srcc, src: !!(o.source || o.media), snd: o.has_audio !== false};
}
function fmtDay(d) {  // "2026-09-30" / "20260930" / epoch -> "30.09.2026."
  if (d == null || d === "") return "";
  if (typeof d === "number" || /^\d{9,13}(\.\d+)?$/.test(String(d))) {
    let n = +d; if (n < 1e12) n *= 1000;
    const x = new Date(n);
    return isNaN(x) ? "" : `${p2(x.getDate())}.${p2(x.getMonth() + 1)}.${x.getFullYear()}.`;
  }
  const m = /^(\d{4})-?(\d\d)-?(\d\d)/.exec(String(d));
  return m ? `${m[3]}.${m[2]}.${m[1]}.` : "";
}
const tgtShort = t => TGT_SHORT[t] || (t ? String(t).toUpperCase() : "");
const engShort = e => ENG_SHORT[e] || String(e || "");
// obican tekst bedza (escape u trBadge): {cls, icon, text, title}
function trText(info) {
  const i = info && typeof info === "object" ? info : trInfo(null);
  if (i.state === "translated") {
    const T = arr(i.translated), tg = [...new Set(T.map(x => tgtShort(x.target)).filter(Boolean))], lek = T.some(x => x.lektura);
    let text;
    if (!tg.length) text = i.srcc ? "Prevedeno: SR CC ✓ (YouTube)" : "Prevedeno ✓";
    else if (tg.length === 1) {
      const x = T.find(y => tgtShort(y.target) === tg[0]);
      text = ["Prevedeno: " + tg[0] + " ✓", engShort(x.engine), fmtDay(x.date)].filter(Boolean).join(" · ");
    } else text = "Prevedeno: " + tg.map(t => t + " ✓").join(" · ");
    if (lek) text += " · ✓ lektura";
    const title = T.map(x => [tgtShort(x.target) || "prevod", x.name || [engShort(x.engine), x.model].filter(Boolean).join(" "),
      fmtDay(x.date), x.lektura ? "lektura" : "", x.file].filter(Boolean).join(" · ")).join("\n")
      || (i.srcc ? "Srpski titl sa YouTube-a (ručni CC)" : "Ima prevod");
    return {cls: "ok", icon: "🟢", text, title};
  }
  if (i.state === "transcript") {
    const X = arr(i.transcripts), rank = {manual: 0, whisper: 1, auto: 2};
    const lbl = x => [baseLang(x.lang), KIND_SHORT[x.kind] || ""].filter(Boolean).join(" ");
    const best = [...X].sort((a, b) => (rank[a.kind] ?? 3) - (rank[b.kind] ?? 3))[0], bl = best ? lbl(best) : "";
    const text = "Transkript bez prevoda" + (bl ? ": " + bl + (X.length > 1 ? ` +${X.length - 1}` : "") : X.length ? ` (${X.length})` : "");
    return {cls: "warn", icon: "🟠", text, title: X.map(x => (lbl(x) || "titl") + (x.file ? " · " + x.file : "")).join("\n") || "Ima titl, nema prevoda"};
  }
  return {cls: "plain", icon: "⚪", text: "Nema titla",
    title: i.src && i.snd === false ? "Nema titla — izvor nema zvuk, whisper nema šta da sluša" : "Nema titla ni transkripta"};
}
// o: {folder, src, pending, tgt (naziv jezika prevoda), nobtn}
function trBadge(info, o = {}) {
  const i = info && typeof info === "object" ? info : trInfo(null), t = trText(i);
  let h = `<span class="b trb ${t.cls}" title="${esc(t.title)}"><span aria-hidden="true">${t.icon}</span> ${esc(t.text)}</span>`;
  if (o.folder && !o.nobtn) {
    const f = esc(o.folder);
    if (o.pending) h += '<span class="stt spin small">u redu…</span>';
    else if (i.state === "transcript")
      h += `<button type="button" class="sm trbtn" data-trb="translate" data-f="${f}" title="${esc(`Prevedi na ${o.tgt || "srpski"} — jedan posao u redu`)}">Prevedi</button>`;
    else if (i.state === "none" && (o.src ?? i.src) && i.snd !== false)  // Transkribuj samo uz izvor zvuka
      h += `<button type="button" class="sm trbtn" data-trb="transcribe" data-f="${f}" title="Whisper transkript na originalnom jeziku — jedan posao u redu">Transkribuj</button>`;
  }
  return `<span class="trs">${h}</span>`;
}

// ---------------------------------------------------------------- checklista (HTML)
// c: {what, all, presets, enc, modelOptions (html <option>), engine (trenutni engine|model), noToolbox}
function rowsHtml(s, c = {}) {
  const what = c.what || s.what, all = !!c.all, why = disabledWhy(what, {noToolbox: c.noToolbox ?? noToolboxOf(c.presets)});
  const e = effective(s, what, why), q = matchQuick(s, what);
  const sel = (k, list, cur, dis = () => false) => `<select data-pk="${k}">${list.map(([v, l]) => opt(v, l, v === cur, dis(v))).join("")}</select>`;
  const knob = (lbl, inner) => `<label class="knob">${esc(lbl)} ${inner}</label>`;
  const row = (id, on, menus, o = {}) => {
    const [ic, lbl] = ROWS[id], dis = !!o.why, lock = !!o.lock, full = o.lock || o.why || "", wid = "plw_" + id;
    return `<div class="prow ${on ? "on" : "off"}${dis ? " dis" : ""}${lock ? " lock" : ""}" data-row="${id}"${full ? ` title="${esc(full)}"` : ""}>`
      + `<label class="pchk"><input type="checkbox" data-pc="${id}"${on ? " checked" : ""}${dis || lock ? ' aria-disabled="true"' : ""}${full ? ` aria-describedby="${wid}"` : ""}>`
      + `<span class="pi" aria-hidden="true">${ic}</span><span class="pn">${esc(lbl)}</span></label>`
      + (on && menus ? `<span class="pmenus">${menus}</span>` : "")
      + (dis ? `<span class="pwhy" aria-hidden="true">${esc(WHY_SHORT[what] || full)}</span>` : "")
      + (full ? `<span class="vh" id="${wid}">${esc(full)}</span>` : "")
      + "</div>";
  };
  const head = `<div class="plh"><h2 id="planH">Šta da uradim</h2>`
    + `<span class="plq" role="group" aria-label="Brzi izbor">${QUICK.map(x => `<button type="button" class="qk" data-q="${x.id}" aria-pressed="${x.id === q}" title="${esc(x.title)}"><span aria-hidden="true">${x.icon}</span> ${esc(x.label)}</button>`).join("")}</span>`
    + `<button type="button" class="sm pall${all ? " on" : ""}" data-pall aria-expanded="${all}" aria-controls="custom" title="Puna forma: audio trake, titlovi, titl za obradu, lektura, enkoder, audio kodek, folder…">⚙ Sve opcije</button></div>`;
  const R = [];
  if (!all) {
    const m1 = knob("Šta", sel("what", WHAT, s.what))
      + (s.what === "full" ? knob("Kvalitet", sel("quality", QUALITY, s.quality)) : s.what === "audio" ? knob("Format", sel("aconv", ACONV, s.aconv)) : "");
    const m2 = knob("Jezik", sel("tx_lang", WLANGS, s.tx_lang)) + knob("Kada", sel("tx_when", TX_WHEN, s.tx_when));
    const cur = c.engine || engineOf(s), mo = String(c.modelOptions || ""), ce = `value="${esc(cur)}"`;
    const eng = mo.includes(ce) ? mo.replace(ce, ce + " selected") : mo + opt(cur, splitEngine(cur).join(" · "), true);
    const m3 = knob("na", sel("tr_target", TARGETS, s.tr_target)) + knob("Prevodilac", `<select data-pk="tr_engine">${eng}</select>`);
    const m4 = knob("Format", sel("vid_fmt", VID_FMT, s.vid_fmt))
      + (s.vid_fmt !== "soft" ? knob("Kvalitet", sel("vid_vq", vqTable(c.enc), s.vid_vq)) + knob("Rezolucija", sel("vid_scale", SCALES, s.vid_scale)) : "")
      + (e.tr ? "" : '<span class="small muted">titl: originalni (bez Prevoda)</span>');
    R.push(row("dl", true, m1, {lock: "Preuzimanje je uvek uključeno — ostali koraci se dodaju čekiranjem."}),
      row("tx", e.tx, m2, {why: why.tx}), row("tr", e.tr, m3), row("vid", e.vid, m4, {why: why.vid}));
  } else {
    R.push('<div class="prow pnote">Preuzimanje, transkript, prevod i video sa titlom podešavaš u <b>⚙ Sve opcije</b> ispod; Transkoduj i Izvuci audio važe i dalje.</div>');
  }
  const {id: pid, p} = tbPresetOf(s, c.presets), ps = arr(c.presets).filter(isVideoPreset);
  const plist = ps.length ? ps.map(x => [x.id, x.label || x.id]) : [[pid, pid]];
  const sub = tbSubOf(s, p);
  const m5 = knob("Preset", sel("tb_preset", plist, pid).replace("<select", `<select title="${esc((p && p.desc) || "")}"`))
    + knob("Titl", sel("tb_sub", TB_SUB, sub, v => v === "burn" && copiesVideo(p)))
    + (sub !== "none" && !e.tr ? '<span class="small muted">koristi postojeći prevod — čekiraj Prevod ako ga nema</span>' : "");
  const m6 = knob("Format", sel("au_preset", AU_PRESETS, s.au_preset));
  R.push(row("tb", e.tb, m5, {why: why.tb}), row("au", e.au, m6, {why: why.au}));
  return head + `<div class="plan" role="group" aria-labelledby="planH">${R.join("")}</div>`;
}

// ---------------------------------------------------------------- kontroler (DOM samo preko ctx.root)
// ctx: {root, load(), save(st), initial(), env(), what(), presets(), modelOptions(), changed(prev, next, why)}
const P = {s: normState(null), ctx: null, last: ""};
const envOf = () => { try { return (P.ctx && P.ctx.env && P.ctx.env()) || {}; } catch { return {}; } };
function save() { try { if (P.ctx && P.ctx.save) P.ctx.save({...P.s}); } catch { /* samo udobnost */ } }
function mount(ctx) {
  P.ctx = ctx; P.last = "";
  let raw = null;
  try { raw = ctx.load ? ctx.load() : null; } catch { raw = null; }
  const first = !raw || typeof raw !== "object";
  P.s = normState(first ? (ctx.initial ? ctx.initial() : null) : raw, envOf());
  if (first) save();
  if (ctx.root && !ctx.root._plan) {
    ctx.root._plan = true;
    ctx.root.addEventListener("click", onClick);
    ctx.root.addEventListener("change", onChange);
  }
  render();
  return {first, state: {...P.s}};
}
const state = () => ({...P.s});
function set(patch, why) {
  const prev = P.s;
  P.s = normState({...prev, ...patch}, envOf());
  save();
  if (P.ctx && P.ctx.changed) P.ctx.changed(prev, {...P.s}, why); else render();
}
function syncFrom(o) {  // Sve opcije otvorene: forma -> redovi (bez povratnog poziva)
  P.s = {...fromOpts(o, P.s, envOf()), all: P.s.all};
  save();
}
function onClick(e) {
  const t = e.target.closest ? e.target : e.target.parentElement;
  if (!t) return;
  const c = t.closest("input[data-pc]");
  if (c && c.getAttribute("aria-disabled") === "true") { e.preventDefault(); return; }  // iskljucen red: klik ne menja
  const q = t.closest("[data-q]");
  if (q) { set(applyQuick(P.s, q.dataset.q), "quick"); return; }
  if (t.closest("[data-pall]")) set({all: !P.s.all}, "all");
}
function onChange(e) {
  const t = e.target;
  if (t.dataset.pc) { if (t.getAttribute("aria-disabled") !== "true") set({[t.dataset.pc]: t.checked}, "row"); }
  else if (t.dataset.pk) set({[t.dataset.pk]: t.value}, "menu");
}
const focusKey = a => a.dataset.pc ? `[data-pc="${a.dataset.pc}"]` : a.dataset.pk ? `[data-pk="${a.dataset.pk}"]`
  : a.dataset.q ? `[data-q="${a.dataset.q}"]` : a.hasAttribute("data-pall") ? "[data-pall]" : "";
function render() {
  const c = P.ctx; if (!c || !c.root) return;
  const env = envOf(), presets = c.presets ? c.presets() : null;
  const html = rowsHtml(P.s, {what: c.what ? c.what() : P.s.what, all: P.s.all, presets, enc: env.encoder,
    modelOptions: c.modelOptions ? c.modelOptions() : "", engine: engineOf(P.s, env)});
  if (html === P.last) return;  // isti sadrzaj: ne diraj DOM (fokus, otvoren meni)
  P.last = html;
  const a = document.activeElement, key = a && c.root.contains(a) ? focusKey(a) : "";
  c.root.innerHTML = html;
  if (key) { const el = c.root.querySelector(key); if (el) el.focus({preventScroll: true}); }
}

window.Plan = {mount, render, state, set, syncFrom, toOpts, afterOf, whatOf, fromOpts, planFromRecipe, recipePatch, describe, shortLabel,
  matchQuick, applyQuick, engineOf, trInfo, trText, trBadge, QUICK, WLANGS};
window.__planTest = {WHAT, QUALITY, ACONV, WLANGS, TX_WHEN, TARGETS, VID_FMT, VQ_VT, VQ_SW, SCALES, TB_SUB, AU_PRESETS, STEPS, QUICK, DEF,
  esc, normState, whatOf, disabledWhy, effective, splitEngine, engineOf, toOpts, isVideoPreset, copiesVideo, tbPresetOf, afterOf,
  matchQuick, applyQuick, fromOpts, planFromRecipe, recipePatch, shortLabel, describe, baseLang, keyEM, trInfo, fmtDay, trText, trBadge, rowsHtml};
})();
