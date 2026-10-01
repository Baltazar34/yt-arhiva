#!/usr/bin/env node
// Provera cistih helpera iz static/plan.js bez DOM-a (window.__planTest) + zakacenost u static/index.html.
// Pokretanje: node tests/plan_ui_check.js
"use strict";
const fs = require("fs"), path = require("path"), vm = require("vm"), assert = require("assert");

const ROOT = path.join(__dirname, "..");
const SRC = fs.readFileSync(path.join(ROOT, "static", "plan.js"), "utf8");
const HTML = fs.readFileSync(path.join(ROOT, "static", "index.html"), "utf8");

const ctx = {console, setTimeout, clearTimeout};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(SRC, ctx, {filename: "static/plan.js"});
const T = ctx.__planTest;
const J = x => JSON.parse(JSON.stringify(x));  // objekti iz vm konteksta imaju drugi prototip
const eq = (a, b, m) => assert.deepStrictEqual(J(a), J(b), m);

let pass = 0, fail = 0;
function t(name, fn) {
  try { fn(); pass++; console.log("  ok  " + name); }
  catch (e) { fail++; console.log("  FAIL " + name + "\n       " + String(e.message).split("\n").join("\n       ")); }
}
// nijedan sirov tag/atribut iz podataka ne sme da prodje
const EVIL = `"><script>alert(1)</script><img src=x onerror='a()'>`;
function noRaw(html, what) {
  assert(!/<script/i.test(html), what + ": sirov <script>");
  assert(!/<img/i.test(html), what + ": sirov <img>");
  assert(!/onerror='/.test(html), what + ": sirov atribut sa navodnicima");
  assert(!/="[^"]*"[^\s>=/]/.test(html.replace(/="[^"]*"(?=[\s>/])/g, "")), what + ": navodnik probio atribut");
}
const ENV = {engine: "synergy", model: "gemma4:12b-it-qat", encoder: "hevc", quality: 65, script: "lat"};
const S = o => T.normState(o, ENV);
const PRESETS = [
  {id: "arhiva", label: "Arhiva", spec: {container: "mkv", vcodec: "hevc_vt"}},
  {id: "apple", label: "Apple", spec: {container: "mp4", vcodec: "hevc_vt"}},
  {id: "remux", label: "Samo remux", spec: {container: "mkv", vcodec: "copy"}},
  {id: "audio_mp3", label: "Samo audio — MP3", spec: {container: "mp3", vcodec: "none"}},
];
const optVals = (src, name) => {  // [["v","l"],...] iz const NAME = [...] u index.html
  const m = new RegExp("const " + name + " = (\\[[\\s\\S]*?\\]\\]);").exec(src);
  assert(m, "nema " + name + " u index.html");
  return [...m[1].matchAll(/\["([^"]*)",/g)].map(x => x[1]);
};

console.log("plan.js — cisti helperi");

t("plain skript: bez import/export, bez spoljnih URL-ova, izlozen Plan", () => {
  assert(!/^\s*(import|export)\s/m.test(SRC), "ima import/export");
  assert(!/https?:\/\//.test(SRC), "ima spoljni URL");
  for (const f of ["mount", "render", "state", "set", "syncFrom", "toOpts", "afterOf", "describe", "trInfo", "trBadge"])
    assert.strictEqual(typeof ctx.Plan[f], "function", f);
});

t("index.html: plan.js pre glavnog skripta, kartica, Biblioteka, Podesavanja", () => {
  const ip = HTML.indexOf('<script src="/static/plan.js"></script>'), main = HTML.indexOf("<script>\n");
  assert(ip > 0 && main > ip, "plan.js mora pre glavnog <script>");
  assert(/<div class="card pad" id="planCard">\s*<div id="planBox">/.test(HTML), "#planBox");
  for (const id of ["rsum", "knobs", "custom", "s_embed_subs", "s_recipe", "libSr", "bTarget"]) assert(HTML.includes(`id="${id}"`), "nema #" + id);
  assert(!/id="recipes"/.test(HTML), "stari #recipes treba da nestane");
  const sr = /<select class="itm" id="libSr"[\s\S]*?<\/select>/.exec(HTML)[0];
  eq([...sr.matchAll(/value="([a-z]*)"/g)].map(m => m[1]), ["", "translated", "transcript", "none", "yes", "no"], "filter Stanje + stari izbori");
  assert(/<button data-b="transcribe"/.test(HTML), "batch Transkribuj");
  assert(/libBatch\("transcribe", \{\}\)/.test(HTML), "Transkribuj -> libBatch");
  assert(/embed_subs: \$\("#s_embed_subs"\)\.value === "true"/.test(HTML), "saveSettings embed_subs");
  assert(/\$\("#s_embed_subs"\)\.value = String\(SET\.embed_subs !== false\)/.test(HTML), "fillSettings embed_subs");
  assert(/Podrazumevani izbor/.test(HTML) && /\{id: "samo", icon: "⬇"/.test(HTML), "recept samo u Podesavanjima");
  assert(/opt\("all\+tr", /.test(HTML), "Sve opcije: svi + YT automatski prevodi");
  assert(/data-p="whisper_lang"/.test(HTML), "jezik govora u formi obrade");
  assert(/LS\.get\("plan", null\)/.test(HTML) && /LS\.set\("plan", st\)/.test(HTML), "pamcenje plan");
  assert(/data-jump=/.test(HTML) && /after_jobs/.test(HTML), "Red: linkovi na after poslove");
  assert(/\[data-trb\]/.test(HTML) && /\/api\/library\/batch", \{folders: \[f\], action: act/.test(HTML), "bedz: jedan klik -> /api/library/batch");
  assert(/trBadgeFor\(a, a\.folder/.test(HTML), "bedz u 'U biblioteci već imaš'");
  assert(/after_skipped/.test(HTML), "Red: preskoceni koraci posle preuzimanja");
  // Sve opcije: jezik govora bez vrednosti = automatski (ne stari izbor); ostala polja forme se pamte (plan_custom)
  assert(/setProcForm\(\$\("#custom \.pform"\), \{whisper_lang: "", \.\.\.o\.proc\}\)/.test(HTML), "seed whisper_lang");
  assert(/LS\.set\("plan_custom", r\)/.test(HTML) && /seedCustom\(planSeed\(true\)\)/.test(HTML), "pamcenje Sve opcije");
});

t("isti izbori kao index.html (ACONV_FULL, VQ)", () => {
  eq(T.ACONV.map(x => x[0]), optVals(HTML, "ACONV_FULL"));
  const vq = /const VQ = \{\s*vt: (\[[\s\S]*?\]\]),\s*sw: (\[[\s\S]*?\]\]),/.exec(HTML);
  assert(vq, "nema VQ");
  eq(T.VQ_VT.map(x => x[0]), [...vq[1].matchAll(/\["(\d+)",/g)].map(m => m[1]));
  eq(T.VQ_SW.map(x => x[0]), [...vq[2].matchAll(/\["(\d+)",/g)].map(m => m[1]));
  eq(T.AU_PRESETS.map(x => x[0]), ["audio_m4a", "audio_mp3", "audio_flac"]);
  eq(T.TARGETS.map(x => x[0]), ["lat", "cyr", "el"]);
  assert(T.WLANGS[0][0] === "" && T.WLANGS.slice(1).every(([k]) => /^[a-z]{2,3}$/.test(k)), "whisper jezici: '' + ISO kodovi");
});

t("normState: podrazumevano samo red 1; los zapis -> podrazumevano", () => {
  const d = S(null);
  eq([d.what, d.quality, d.tx, d.tr, d.vid, d.tb, d.au, d.all], ["full", "best", false, false, false, false, false, false]);
  assert.strictEqual(d.tr_target, "lat"); assert.strictEqual(d.vid_vq, "65");
  const x = S({what: "x", quality: 999, tx: "yes", tr: 1, aconv: "wav", tx_lang: "<b>", tr_target: "de", vid_vq: "20",
    tb_preset: "audio_mp3", tr_engine: 'x|"><script>', au_preset: "audio_ogg", all: true});
  eq([x.what, x.quality, x.tx, x.tr, x.aconv, x.tx_lang, x.tr_target, x.vid_vq, x.tb_preset, x.tr_engine, x.au_preset, x.all],
    ["full", "best", false, false, "mp3", "", "lat", "65", "arhiva", "", "audio_m4a", true]);
  assert.strictEqual(T.normState({}, {script: "cyr", encoder: "x265", quality: 65}).tr_target, "cyr");
  assert.strictEqual(T.normState({}, {encoder: "x265"}).vid_vq, "20");
  assert.strictEqual(T.normState({vid_vq: "23"}, {encoder: "x265"}).vid_vq, "23");
  assert.strictEqual(S({tr_engine: "ollama|hf.co/x|y:q4"}).tr_engine, "ollama|hf.co/x|y:q4");
  assert.strictEqual(S({quality: "1080"}).quality, "1080");
});

t("toOpts: ceo sadrzaj / samo audio / samo titlovi bez obrade (ugovor §C)", () => {
  eq(T.toOpts(S(null), ENV), {dl: {video_fmt: "auto:best", audio_fmt: "all", subs: "all"}, proc: null});
  eq(T.toOpts(S({quality: "1080"}), ENV).dl.video_fmt, "auto:1080");
  eq(T.toOpts(S({what: "audio", aconv: "flac"}), ENV), {dl: {video_fmt: "audio", audio_fmt: "auto", audio_conv: "flac", subs: "none"}, proc: null});
  eq(T.toOpts(S({what: "subs"}), ENV), {dl: {video_fmt: "none", subs: "all"}, proc: null});
});

t("toOpts: transkript bez prevoda (kad nema / uvek, jezik)", () => {
  const p = T.toOpts(S({tx: true}), ENV).proc;
  eq([p.sub_source, p.target, p.output, p.keep_source, "whisper_lang" in p], ["auto-best", "none", "none", true, false]);
  const q = T.toOpts(S({tx: true, tx_when: "always", tx_lang: "el"}), ENV).proc;
  eq([q.sub_source, q.whisper_lang, q.target, q.output], ["whisper:run", "el", "none", "none"]);
  assert.strictEqual(T.toOpts(S({tx: false, tx_lang: "el", tr: true}), ENV).proc.whisper_lang, undefined, "jezik samo uz cekiran Transkript");
});

t("toOpts: prevod, prevodilac, video sa titlom", () => {
  const p = T.toOpts(S({tr: true, tr_target: "cyr"}), ENV).proc;
  eq([p.sub_source, p.target, p.output, p.engine, p.model], ["auto-best", "cyr", "none", "synergy", "gemma4:12b-it-qat"]);
  const g = T.toOpts(S({tr: true, tr_engine: "ollama|hf.co/x|y:q4"}), ENV).proc;
  eq([g.engine, g.model], ["ollama", "hf.co/x|y:q4"]);
  const v = T.toOpts(S({tr: true, vid: true, vid_fmt: "both", vid_vq: "55", vid_scale: "1080"}), ENV).proc;
  eq([v.target, v.output, v.encoder, v.vq, v.scale, v.audio_out, v.audio_enc, v.subs_out, v.keep_source],
    ["lat", "both", "hevc", 55, "1080", "orig", "aac192", "sr+orig", true]);
  const o = T.toOpts(S({vid: true}), ENV).proc;  // bez Prevoda: originalni titl
  eq([o.target, o.output], ["none", "burn"]);
  const st = T.toOpts(S({what: "subs", tr: true}), ENV).proc;
  eq([st.target, st.output], ["lat", "srt"]);
});

t("disabled pravila: video/transkod/audio uz samo audio i samo titlove", () => {
  const a = T.disabledWhy("audio"), s = T.disabledWhy("subs"), f = T.disabledWhy("full");
  eq(Object.keys(a).sort(), ["au", "tb", "vid"]);
  eq(Object.keys(s).sort(), ["au", "tb", "tx", "vid"]);
  eq(f, {});
  assert(T.disabledWhy("full", {noToolbox: true}).tb, "bez preseta");
  assert.strictEqual(T.toOpts(S({what: "audio", vid: true}), ENV).proc, null, "video uz samo audio se ne salje");
  assert.strictEqual(T.toOpts(S({what: "subs", tx: true}), ENV).proc, null, "whisper uz samo titlove nema zvuk");
  eq(T.effective(S({what: "audio", tx: true, tr: true, vid: true, tb: true, au: true}), "audio"), {tx: true, tr: true, vid: false, tb: false, au: false});
});

t("afterOf: samo cekirani koraci, preset, titl, target (ugovor §C)", () => {
  const env = {...ENV, presets: PRESETS};
  assert.strictEqual(T.afterOf(S(null), "full", env), null);
  eq(T.afterOf(S({tb: true, tb_preset: "apple"}), "full", env), {toolbox: {preset: "apple", sub: "none", target: null}});
  eq(T.afterOf(S({tb: true, tb_preset: "apple", tb_sub: "burn", tr_target: "el"}), "full", env), {toolbox: {preset: "apple", sub: "burn", target: "el"}});
  eq(T.afterOf(S({tb: true, tb_preset: "remux", tb_sub: "burn"}), "full", env).toolbox, {preset: "remux", sub: "soft", target: "lat"}, "upečen + remux -> meki");
  eq(T.afterOf(S({tb: true, tb_preset: "nestalo"}), "full", env).toolbox.preset, "arhiva", "nepoznat preset -> arhiva");
  eq(T.afterOf(S({au: true, au_preset: "audio_flac"}), "full", env), {audio: {preset: "audio_flac"}});
  eq(T.afterOf(S({tb: true, au: true}), "full", env), {toolbox: {preset: "arhiva", sub: "none", target: null}, audio: {preset: "audio_m4a"}});
  assert.strictEqual(T.afterOf(S({tb: true, au: true}), "audio", env), null, "samo audio: bez transkoda/izvlacenja");
  assert.strictEqual(T.afterOf(S({tb: true, au: true}), "subs", env), null);
  assert.strictEqual(T.afterOf(S({tb: true}), "full", {...ENV, presets: []}), null, "server bez preseta");
  eq(T.afterOf(S({tb: true, tb_preset: "apple"}), "full", ENV).toolbox.preset, "apple", "preseti jos nisu ucitani");
  eq(T.tbPresetOf(S({tb_preset: "audio_mp3"}), PRESETS).id, "arhiva");
  assert(T.isVideoPreset(PRESETS[0]) && !T.isVideoPreset(PRESETS[3]) && T.copiesVideo(PRESETS[2]) && !T.copiesVideo(PRESETS[1]));
});

t("brzi izbor: jedan klik postavlja redove; oznacen onaj koji odgovara stanju", () => {
  eq(T.QUICK.map(q => q.id), ["samo", "tx", "tr", "vid", "muzika", "titlovi"]);
  assert.strictEqual(T.matchQuick(S(null)), "samo", "podrazumevano = Samo preuzmi");
  for (const q of T.QUICK) {
    const s = S(T.applyQuick(S({tb: true, au: true, tx: true, what: "audio"}), q.id));
    assert.strictEqual(T.matchQuick(s), q.id, q.id);
    assert(!s.tb && !s.au, q.id + ": redovi 5–6 ugaseni");
  }
  const m = S(T.applyQuick(S({aconv: "flac"}), "muzika"));
  eq([m.what, m.aconv], ["audio", "mp3"]);
  const v = S(T.applyQuick(S(null), "vid"));
  eq([v.tr, v.vid], [true, true]);
  // 📝 + Transkript: whisper uvek (sa "samo kad nema titl" YouTube auto titl bi ga preskocio)
  const x = S(T.applyQuick(S(null), "tx"));
  eq([x.tx, x.tx_when, T.toOpts(x, ENV).proc.sub_source], [true, "always", "whisper:run"]);
  assert.strictEqual(T.matchQuick(S({tr: true, tb: true})), "", "dodat korak -> nijedan");
  assert.strictEqual(T.matchQuick(S({what: "subs", tr: true, tx: true})), "titlovi", "iskljucen red se ne racuna");
  assert.strictEqual(T.matchQuick(S({what: "audio", aconv: "flac"})), "muzika");
});

t("fromOpts: Sve opcije -> redovi (koliko moze), povratak bez gubitka", () => {
  for (const q of T.QUICK) {
    const s = S(T.applyQuick(S({tx_lang: "sr", vid_fmt: "soft", quality: "720"}), q.id));
    const back = T.fromOpts(T.toOpts(s, ENV), s, ENV);
    eq(T.effective(back, back.what), T.effective(s, s.what), q.id);
    assert.strictEqual(back.what, s.what, q.id);
  }
  const tx = S({tx: true, tx_when: "always", tx_lang: "el", tr: true, tr_target: "cyr", vid: true, vid_fmt: "soft", tb: true});
  const b = T.fromOpts(T.toOpts(tx, ENV), S(null), ENV);
  eq([b.tx, b.tx_when, b.tx_lang, b.tr, b.tr_target, b.vid, b.vid_fmt, b.tb], [true, "always", "el", true, "cyr", true, "soft", false]);
  assert.strictEqual(T.fromOpts(T.toOpts(S({tx_lang: "el"}), ENV), S({tx_lang: "el"}), ENV).tx_lang, "el", "zapamcen jezik ostaje dok Transkript nije cekiran");
  assert.strictEqual(T.fromOpts(T.toOpts(S({tr: true}), ENV), S(null), ENV).tr_engine, "", "podrazumevani prevodilac prati Podesavanja");
  const keep = S({tx: true, tr: true});  // "samo kad nema" + prevod = isto auto-best -> red ostaje cekiran
  assert.strictEqual(T.fromOpts(T.toOpts(keep, ENV), keep, ENV).tx, true);
  const c = T.fromOpts({dl: {video_fmt: "auto:1440", audio_fmt: "auto", subs: "all+tr"}, proc: {sub_source: "none", target: "none", output: "burn",
    engine: "claude", model: "opus", vq: "75", scale: "720"}}, S({tx: true, tb: true}), ENV);
  eq([c.what, c.quality, c.tx, c.tr, c.vid, c.vid_fmt, c.tr_engine, c.vid_vq, c.vid_scale, c.tb], ["full", "1440", false, false, true, "burn", "claude|opus", "75", "720", true]);
  const n = T.fromOpts({dl: {video_fmt: "audio", audio_conv: "opus"}, proc: null}, S({tr: true}), ENV);
  eq([n.what, n.aconv, n.tr], ["audio", "opus", false]);
});

t("planFromRecipe: podrazumevani izbor (stari arhiva -> samo)", () => {
  const f = (rid, e = {}) => { const s = T.planFromRecipe(rid, {...ENV, plan_migrated: true, ...e}); return [s.what, s.tx, s.tr, s.vid, s.all].join(","); };
  assert.strictEqual(f("samo"), "full,false,false,false,false");
  assert.strictEqual(f(undefined), "full,false,false,false,false");
  assert.strictEqual(f("arhiva"), "full,false,true,false,false");
  assert.strictEqual(f("arhiva", {plan_migrated: false}), "full,false,false,false,false", "pre migracije: samo preuzimanje");
  assert.strictEqual(f("arhiva_video"), "full,false,true,true,false");
  assert.strictEqual(f("muzika"), "audio,false,false,false,false");
  assert.strictEqual(f("titlovi"), "subs,false,true,false,false");
  assert.strictEqual(f("titlovi", {auto_translate: false}), "subs,false,false,false,false");
  assert.strictEqual(f("custom"), "full,false,false,false,true");
  eq(T.recipePatch("muzika", ENV), {what: "audio", all: false, tx: false, tr: false, vid: false, tb: false, au: false, aconv: "mp3"});
});

t("describe: transkript, prevod, izlaz, koraci posle", () => {
  const D = (s, e = {}) => T.describe({...T.toOpts(S(s), ENV), after: T.afterOf(S(s), S(s).what, {...ENV, presets: PRESETS}) || undefined},
    {modelName: () => "Kvalitetno", presetLabel: id => (PRESETS.find(p => p.id === id) || {}).label || id, ...e});
  const d0 = D(null, {embed: true});
  assert(/^Najbolji video · sve audio trake · svi titlovi \(i u MKV\) · bez prevoda \(kasnije: Biblioteka → Prevedi\) · bez rekodiranja$/.test(d0), d0);
  const d1 = D({tx: true});
  assert(/transkript: whisper samo ako YouTube nema titl \(jezik automatski\) · bez prevoda · bez rekodiranja/.test(d1), d1);
  const d2 = D({tx: true, tx_when: "always", tx_lang: "sr", tr: true});
  assert(/transkript: whisper \(srpski\) · prevod: srpski latinica \(Kvalitetno\) → SRT u arhivi/.test(d2), d2);
  assert(/transkript: whisper samo ako YouTube nema titl \(jezik automatski\) · prevod: srpski latinica/.test(D({tx: true, tr: true}, {tx: true})), "cekiran Transkript uz Prevod");
  assert(!/transkript/.test(D({tr: true})), "bez Transkripta: nema pomena");
  const d3 = D({vid: true});
  assert(/bez prevoda \(originalni titl\) → MP4 za gledanje \(HEVC VideoToolbox, upečen originalni titl, originalni zvuk\)/.test(d3) && !/bez rekodiranja/.test(d3), d3);
  const d4 = D({tb: true, tb_preset: "apple", tb_sub: "soft", au: true, au_preset: "audio_mp3"});
  assert(/posle: Toolbox „Apple“ \+ meki titl \(srpski latinica\) · posle: audio MP3 320$/.test(d4) && !/bez rekodiranja/.test(d4), d4);
  assert(/^Samo audio · MP3 320 kb\/s · omot i tagovi · bez titlova · bez prevoda$/.test(D({what: "audio"})), D({what: "audio"}));
  assert(/svi titlovi \+ YouTube automatski prevodi/.test(T.describe({dl: {video_fmt: "auto:best", audio_fmt: "all", subs: "all+tr"}, proc: null})));
  assert(/^Bez videa · svi titlovi · prevod: grčki → SRT fajl$/.test(T.describe({dl: {video_fmt: "none", subs: "all"}, proc: {sub_source: "auto-best", target: "el", output: "srt"}})));
  assert.strictEqual(typeof T.describe(null), "string");
  // Sve opcije: ne zadrzi izvor + video izlaz + koraci posle -> server zadrzava izvor (kaze i sazetak)
  const ks = o => T.describe({dl: {video_fmt: "auto:best", audio_fmt: "all", subs: "all"}, after: {audio: {preset: "audio_mp3"}},
    proc: {sub_source: "auto-best", target: "none", output: "soft", keep_source: false, ...o}});
  assert(/izvor se zadržava/.test(ks({})) && !/izvor se zadržava/.test(ks({keep_source: true})) && !/izvor se zadržava/.test(ks({output: "none"})), ks({}));
});

t("shortLabel", () => {
  assert.strictEqual(T.shortLabel(S(null)), "Ceo sadržaj");
  assert.strictEqual(T.shortLabel(S({tx: true, tr: true, vid: true, tb: true, au: true})), "Ceo sadržaj + Transkript + Prevod + Video sa titlom + Toolbox + Audio");
  assert.strictEqual(T.shortLabel(S({what: "audio", vid: true, au: true})), "Samo audio");
  assert.strictEqual(T.shortLabel(S({tr: true, tb: true}), "full", true), "⚙ Sve opcije + Toolbox");
});

// manifest subs stavke (kao panel.py) i nova polja stavke
const SUBS = [
  {key: "manual:en", kind: "manual", lang: "en", file: "subs/en.srt", name: "English"},
  {key: "auto:en", kind: "auto", lang: "en", file: "subs/en.auto.srt"},
  {key: "sr:lat:synergy:gemma4:12b-it-qat:manual:en", kind: "sr", lang: "sr", file: "subs/sr-lat.a.srt", src: "manual:en", engine: "synergy", model: "gemma4:12b-it-qat"},
  {key: "sr:cyr:claude:sonnet:lektura:manual:en", kind: "sr", lang: "sr", file: "subs/sr-cyr.b.srt", src: "manual:en"},
  {key: "tr:el:gemini:gemini-3.8-flash:manual:en", kind: "tr", lang: "el", file: "subs/el.c.srt", src: "manual:en"},
];

t("trInfo: nova polja servera imaju prednost; stari odgovor -> iz subs", () => {
  const n = T.trInfo({tr_state: "transcript", translated: [], transcripts: [{kind: "whisper", lang: "sr", file: "subs/whisper.srt"}], subs: SUBS, source: "a.mkv"});
  eq([n.state, n.translated.length, n.transcripts[0].kind, n.src], ["transcript", 0, "whisper", true]);
  const o = T.trInfo({subs: SUBS});
  assert.strictEqual(o.state, "translated");
  eq(o.translated.map(x => [x.target, x.engine, x.model, x.lektura]),
    [["el", "gemini", "gemini-3.8-flash", false], ["cyr", "claude", "sonnet", true], ["lat", "synergy", "gemma4:12b-it-qat", false]]);
  eq(o.transcripts.map(x => x.kind), ["manual", "auto"]);
  eq(T.keyEM({key: "sr:lat:ollama:gemma4:12b-it-qat:rev:auto:en-orig", src: "auto:en-orig"}), ["ollama", "gemma4:12b-it-qat"]);
  eq(T.keyEM({key: "sr:lat:rebuilt:abc"}), ["", ""]);
  assert.strictEqual(T.trInfo({subs: SUBS.slice(0, 2)}).state, "transcript");
  assert.strictEqual(T.trInfo({subs: [{key: "manual:sr", kind: "manual", lang: "sr", file: "x"}]}).state, "translated", "srpski rucni CC = prevedeno");
  assert.strictEqual(T.trInfo({subs: [], source: "a.mkv"}).state, "none");
  assert.strictEqual(T.trInfo(null).state, "none");
  assert.strictEqual(T.trInfo({tr_state: "bogus", subs: SUBS.slice(0, 1)}).state, "transcript", "nepoznat tr_state -> izracunaj");
  // cat_short (U arhivi vec imas) stariji: samo brojevi
  eq([T.trInfo({media: true, subs: 3, tr: 0, sr: []}).state, T.trInfo({media: true, subs: 3, tr: 0, sr: []}).src], ["transcript", true]);
  eq(T.trInfo({media: false, subs: 0, tr: 2, sr: ["lat", "cyr"]}).translated.map(x => x.target), ["lat", "cyr"]);
  assert.strictEqual(T.trInfo({media: true, subs: 0, tr: 0, sr: []}).state, "none");
});

t("trText / bedz: prevedeno, transkript bez prevoda, nema titla", () => {
  const one = T.trText(T.trInfo({tr_state: "translated", translated: [{target: "lat", engine: "synergy", model: "gemma4", date: "2026-09-30", lektura: false, file: "a.srt"}]}));
  eq([one.cls, one.icon, one.text], ["ok", "🟢", "Prevedeno: SR lat ✓ · Kvalitetno · 30.09.2026."]);
  const many = T.trText(T.trInfo({subs: SUBS}));
  assert.strictEqual(many.text, "Prevedeno: EL ✓ · SR ćir ✓ · SR lat ✓ · ✓ lektura");
  assert(/SR ćir · .*lektura/.test(many.title), many.title);
  assert.strictEqual(T.trText(T.trInfo({tr_state: "translated", translated: [], sr_cc: true})).text, "Prevedeno: SR CC ✓ (YouTube)");
  const tw = T.trText(T.trInfo({tr_state: "transcript", translated: [], transcripts: [{kind: "auto", lang: "en"}, {kind: "whisper", lang: "sr"}]}));
  eq([tw.cls, tw.icon, tw.text], ["warn", "🟠", "Transkript bez prevoda: sr whisper +1"]);
  assert.strictEqual(T.trText(T.trInfo({subs: SUBS.slice(0, 2)})).text, "Transkript bez prevoda: en CC +1");
  assert.strictEqual(T.trText(T.trInfo({subs: [SUBS[1]]})).text, "Transkript bez prevoda: en auto");
  assert.strictEqual(T.trText(T.trInfo({media: true, subs: 4, tr: 0})).text, "Transkript bez prevoda (4)");
  const no = T.trText(T.trInfo({}));
  eq([no.cls, no.icon, no.text], ["plain", "⚪", "Nema titla"]);
  eq(["2026-09-30", "20260930", "", null, "x"].map(T.fmtDay), ["30.09.2026.", "30.09.2026.", "", "", ""]);
  assert(/^\d\d\.\d\d\.\d{4}\.$/.test(T.fmtDay(1790000000)));
});

t("trBadge: dugmad Prevedi / Transkribuj (jedan klik), u redu, bez foldera", () => {
  const tr = T.trBadge(T.trInfo({subs: SUBS.slice(0, 1), source: "a.mkv"}), {folder: "/m/a", tgt: "srpski latinica"});
  assert(/class="b trb warn"/.test(tr) && /data-trb="translate" data-f="\/m\/a"/.test(tr) && /Prevedi na srpski latinica/.test(tr), tr);
  const no = T.trBadge(T.trInfo({subs: [], source: "a.mkv"}), {folder: "/m/b"});
  assert(/data-trb="transcribe" data-f="\/m\/b"/.test(no) && />Transkribuj</.test(no), no);
  assert(!/data-trb/.test(T.trBadge(T.trInfo({subs: []}), {folder: "/m/c"})), "bez izvora zvuka: nema Transkribuj");
  assert(!/data-trb/.test(T.trBadge(T.trInfo({subs: []}), {folder: "/m/c", src: false})));
  assert(/data-trb="transcribe"/.test(T.trBadge(T.trInfo({subs: []}), {folder: "/m/c", src: true})));
  assert(!/data-trb/.test(T.trBadge(T.trInfo({subs: SUBS}), {folder: "/m/d", src: true})), "prevedeno: bez dugmeta");
  // izvor bez zvuka (server has_audio false): nema Transkribuj (backend bi ga preskocio), objasnjenje u title
  const mute = T.trBadge(T.trInfo({subs: [], source: "a.mkv", has_audio: false}), {folder: "/m/f", src: true});
  assert(!/data-trb/.test(mute) && /izvor nema zvuk/.test(mute), mute);
  assert(!/data-trb/.test(T.trBadge(T.trInfo({media: true, audio: 0, has_audio: false, tr_state: "none"}), {folder: "/m/g", src: true})), "cat_short");
  assert(/data-trb="transcribe"/.test(T.trBadge(T.trInfo({subs: [], source: "a.mkv", has_audio: true}), {folder: "/m/h"})));
  const pend = T.trBadge(T.trInfo({subs: SUBS.slice(0, 1)}), {folder: "/m/e", pending: true});
  assert(!/data-trb/.test(pend) && /u redu…/.test(pend), pend);
  assert(!/<button/.test(T.trBadge(T.trInfo({subs: SUBS.slice(0, 1)}), {})), "bez foldera: samo bedz");
  assert(/aria-hidden="true">🟠<\/span>/.test(tr), "ikonica skrivena za citac ekrana");
});

t("keep_source prati Podesavanja (keep_original)", () => {
  const st = {what: "full", tr: true};
  eq(T.toOpts(st, {}).proc.keep_source, true);
  eq(T.toOpts(st, {keep_original: true}).proc.keep_source, true);
  eq(T.toOpts(st, {keep_original: false}).proc.keep_source, false);
});
t("XSS: sve korisnicke vrednosti u bedzu i redovima idu kroz esc", () => {
  const evilIt = {subs: [{key: "manual:" + EVIL, kind: "manual", lang: EVIL, file: EVIL, name: EVIL}], source: EVIL};
  noRaw(T.trBadge(T.trInfo(evilIt), {folder: EVIL, tgt: EVIL}), "transkript");
  noRaw(T.trBadge(T.trInfo({subs: [], source: "x"}), {folder: EVIL}), "nema titla");
  noRaw(T.trBadge(T.trInfo({tr_state: "translated", translated: [{target: EVIL, engine: EVIL, model: EVIL, date: EVIL, file: EVIL, name: EVIL, lektura: true}]}),
    {folder: EVIL}), "prevedeno");
  noRaw(T.trBadge(T.trInfo({tr_state: EVIL, translated: EVIL, transcripts: [{kind: EVIL, lang: EVIL, file: EVIL}], subs: EVIL}), {folder: EVIL}), "los odgovor");
  noRaw(T.rowsHtml(S({tb: true, tr: true}), {presets: [{id: "x1", label: EVIL, desc: EVIL, spec: {vcodec: "hevc_vt"}}],
    modelOptions: `<option value="a|b">${T.esc(EVIL)}</option>`, engine: 'z|"><img src=x>'}), "redovi");
  assert(T.trBadge(T.trInfo(evilIt), {folder: EVIL}).includes("&lt;script&gt;"), "escape umesto brisanja");
});

t("rowsHtml: redovi, zakljucan red 1, iskljuceni redovi (aria), meniji samo kad je cekirano", () => {
  const h = T.rowsHtml(S(null), {presets: PRESETS, modelOptions: '<option value="synergy|gemma4:12b-it-qat">Kvalitetno</option>', engine: "synergy|gemma4:12b-it-qat"});
  eq([...h.matchAll(/data-row="([a-z]+)"/g)].map(m => m[1]), ["dl", "tx", "tr", "vid", "tb", "au"]);
  eq([...h.matchAll(/<input type="checkbox" data-pc="([a-z]+)"( checked)?/g)].map(m => m[1] + (m[2] ? "+" : "")), ["dl+", "tx", "tr", "vid", "tb", "au"]);
  assert(/data-pc="dl" checked aria-disabled="true" aria-describedby="plw_dl"/.test(h), "red 1 zakljucan");
  assert(/<label class="pchk"><input type="checkbox" data-pc="tx">/.test(h), "checkbox u <label>");
  eq([...h.matchAll(/data-pk="([a-z_]+)"/g)].map(m => m[1]), ["what", "quality"], "meniji samo za cekirane");
  assert(/data-q="samo" aria-pressed="true"/.test(h) && /role="group" aria-label="Brzi izbor"/.test(h));
  assert(/data-pall aria-expanded="false" aria-controls="custom"/.test(h));
  const a = T.rowsHtml(S({what: "audio", vid: true, tb: true, au: true, tx: true}), {presets: PRESETS});
  assert(/data-row="vid" title="Samo audio nema video/.test(a), "title objasnjava");
  assert(/data-pc="vid" aria-disabled="true" aria-describedby="plw_vid"/.test(a) && /id="plw_vid">Samo audio nema video/.test(a));
  assert(/nije moguće uz Samo audio/.test(a));
  assert(!/data-pc="vid" checked/.test(a), "iskljucen red nije cekiran");
  assert(/data-pk="aconv"/.test(a) && /data-pk="tx_lang"/.test(a) && /data-pk="tx_when"/.test(a));
  const t1 = T.rowsHtml(S({tr: true, tb: true, tb_preset: "remux", tb_sub: "burn"}), {presets: PRESETS, modelOptions: '<option value="synergy|g">K</option><option value="claude|opus">C</option>', engine: "claude|opus"});
  assert(/<option value="claude\|opus" selected>/.test(t1), "izabran prevodilac");
  assert(/<option value="burn" disabled>/.test(t1) && /<option value="soft" selected>/.test(t1), "remux: upečen iskljucen -> meki");
  assert(!/audio_mp3/.test(t1.split('data-pk="tb_preset"')[1].split("</select>")[0]), "bez audio preseta u Transkoduj");
  assert(/<option value="x\|y" selected>x · y<\/option>/.test(T.rowsHtml(S({tr: true}), {modelOptions: "", engine: "x|y"})), "nepoznat prevodilac se ne gubi");
  const all = T.rowsHtml(S({all: true, tb: true}), {all: true, presets: PRESETS});
  eq([...all.matchAll(/data-row="([a-z]+)"/g)].map(m => m[1]), ["tb", "au"], "Sve opcije: redovi 5–6 vaze");
  assert(/aria-expanded="true"/.test(all) && /class="prow pnote"/.test(all));
  const sb = T.rowsHtml(S({what: "subs", tx: true}), {});
  assert(/data-pc="tx" aria-disabled="true"/.test(sb) && /nije moguće uz Samo titlove/.test(sb));
  assert(/data-pc="tb" aria-disabled="true"/.test(T.rowsHtml(S({tb: true}), {presets: []})), "bez preseta: Transkoduj iskljucen");
});

console.log(`\n${pass} ok, ${fail} palo`);
process.exit(fail ? 1 : 0);
