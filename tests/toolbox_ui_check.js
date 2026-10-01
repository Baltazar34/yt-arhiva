#!/usr/bin/env node
// Provera cistih helpera iz static/toolbox.js bez DOM-a (window.__tbTest).
// Pokretanje: node tests/toolbox_ui_check.js
"use strict";
const fs = require("fs"), path = require("path"), vm = require("vm"), assert = require("assert");

const ROOT = path.join(__dirname, "..");
const SRC = fs.readFileSync(path.join(ROOT, "static", "toolbox.js"), "utf8");
const CONTRACT = fs.readFileSync(path.join(ROOT, "docs", "toolbox-contract.md"), "utf8");

// isti helperi kao u index.html (samo ono sto cisti helperi koriste)
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const ctx = {
  esc, console, setTimeout, clearTimeout,
  opt: (v, l, sel) => `<option value="${esc(v)}"${sel ? " selected" : ""}>${esc(l)}</option>`,
  fmtB: b => String(b), fmtD: s => String(Math.round(s || 0)),
  modelOptionsHtml: () => "", SET: {engine: "ollama", model: "gemma4:12b-it-qat"},
  api: async () => { throw new Error("api nije dostupan u testu"); }, $: () => null, $$: () => [],
};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(SRC, ctx, {filename: "static/toolbox.js"});
const T = ctx.__tbTest;
const J = x => JSON.parse(JSON.stringify(x));  // objekti iz vm konteksta imaju drugi prototip
const eq = (a, b, m) => assert.deepStrictEqual(J(a), J(b), m);

let pass = 0, fail = 0;
function t(name, fn) {
  try { fn(); pass++; console.log("  ok  " + name); }
  catch (e) { fail++; console.log("  FAIL " + name + "\n       " + String(e.message).split("\n").join("\n       ")); }
}

// kljucevi speca iz ugovora (blok ispod "### spec")
const specBlock = CONTRACT.split("### spec")[1].split("```")[1];
const CONTRACT_KEYS = [];
for (const line of specBlock.split("\n")) for (const part of line.split(";")) {
  const m = /^\s*([a-z_]+):/.exec(part); if (m) CONTRACT_KEYS.push(m[1]);
}

const A = {  // analiza kao iz toolbox.analyze()
  path: "/m/film.mkv", name: "film.mkv", size: 5e9, container: "matroska,webm", duration: 600, bitrate: 20e6,
  video: {index: 0, codec: "hevc", profile: "Main 10", width: 3840, height: 2160, fps: 23.976, pix_fmt: "yuv420p10le", bit_depth: 10,
    hdr: "hdr10", interlaced: false},
  audio: [{n: 0, index: 1, codec: "truehd", channels: 8, lang: "eng"}, {n: 1, index: 2, codec: "ac3", channels: 6, lang: "eng", title: "Commentary"},
    {n: 2, index: 3, codec: "aac", channels: 2, lang: "srp"}],
  subs: [{n: 0, index: 4, codec: "subrip", lang: "eng", text: true}, {n: 1, index: 5, codec: "hdmv_pgs_subtitle", lang: "eng", text: false},
    {n: 2, index: 6, codec: "subrip", lang: "srp", text: true}],
  chapters: 12, warnings: [],
};
const B = {
  path: "/m/drugi.mkv", name: "drugi.mkv", container: "matroska,webm", duration: 300,
  video: {codec: "h264", width: 1920, height: 1080, fps: 25, bit_depth: 8, hdr: "sdr", interlaced: true},
  audio: [{n: 0, codec: "aac", channels: 2, lang: "srp"}, {n: 1, codec: "ac3", channels: 6, lang: "eng"}, {n: 2, codec: "ac3", channels: 2, lang: "eng"}],
  subs: [{n: 0, codec: "hdmv_pgs_subtitle", lang: "eng", text: false}, {n: 1, codec: "subrip", lang: "eng", text: true}],
  chapters: 0, warnings: [],
};
const fields = over => ({container: "mkv", vcodec: "hevc_vt", vq: "65", vbitrate: "", vpreset: "medium", prores_profile: "hq", scale: "1080",
  fps: "src", depth: "auto", hdr: "keep", deint: "auto", acodec: "aac", abitrate: "192k", channels: "keep", loudnorm: "-16",
  subs_keep: "all", sub_src: "none", sub_file: "", translate: "off", model_full: "ollama|gemma4:12b-it-qat", sub_mode: "soft",
  trim_start: "", trim_end: "", chapters: true, out_dir: "", suffix: ".transkod", audio_all: true, audio_n: [], ...over});

console.log("toolbox.js — cisti helperi");

t("plain skript: bez import/export i bez spoljnih URL-ova", () => {
  assert(!/^\s*(import|export)\s/m.test(SRC), "ima import/export");
  assert(!/https?:\/\//.test(SRC), "ima spoljni URL");
  assert.strictEqual(typeof ctx.initToolbox, "function");
  assert.strictEqual(typeof ctx.tbAddPath, "function");
});

t("SPEC_KEYS == kljucevi iz ugovora", () => {
  assert(CONTRACT_KEYS.length >= 28, "parsiranje ugovora: " + CONTRACT_KEYS.join(","));
  eq([...T.SPEC_KEYS].sort(), [...CONTRACT_KEYS].sort());
  eq(Object.keys(T.FALLBACK).sort(), [...CONTRACT_KEYS].sort());
});

t("parsePaths: novi red, navodnici, razmaci, Terminal escape, file://, dupli", () => {
  eq(T.parsePaths("/a/b.mkv\n/c/d e.mkv\n"), ["/a/b.mkv", "/c/d e.mkv"]);
  eq(T.parsePaths('"/a/x y.mkv" "/b/z.mp4"'), ["/a/x y.mkv", "/b/z.mp4"]);
  eq(T.parsePaths("'/a/x y.mkv' '/b/z.mp4'"), ["/a/x y.mkv", "/b/z.mp4"]);
  eq(T.parsePaths("/a/x.mkv /b/y z.mp4"), ["/a/x.mkv", "/b/y z.mp4"]);
  eq(T.parsePaths("/Volumes/Spoljni Disk/Film (2019).mkv"), ["/Volumes/Spoljni Disk/Film (2019).mkv"]);
  eq(T.parsePaths("/Users/test/Novi\\ folder/a\\ b\\(1\\).mkv /tmp/c.mkv"), ["/Users/test/Novi folder/a b(1).mkv", "/tmp/c.mkv"]);
  eq(T.parsePaths("file:///Users/test/My%20Movie.mkv"), ["/Users/test/My Movie.mkv"]);
  eq(T.parsePaths("/Movies/Rock 'n' Roll.mkv"), ["/Movies/Rock 'n' Roll.mkv"]);
  eq(T.parsePaths("~/Movies/a.mkv ~/Movies/b c.mkv"), ["~/Movies/a.mkv", "~/Movies/b c.mkv"]);
  eq(T.parsePaths("/a.mkv\r\n/a.mkv\n\n  \t/b.mkv  "), ["/a.mkv", "/b.mkv"]);
  eq(T.parsePaths('"/a/nezatvoren navodnik.mkv'), ["/a/nezatvoren navodnik.mkv"]);
  eq(T.parsePaths("  \n\r\n "), []);
  eq(T.parsePaths(""), []);
});

t("normTime / toSec", () => {
  assert.strictEqual(T.normTime("90"), "00:01:30");
  assert.strictEqual(T.normTime("1:30"), "00:01:30");
  assert.strictEqual(T.normTime("1:02:03.5"), "01:02:03.500");
  assert.strictEqual(T.normTime("0:00:01,25"), "00:00:01.250");
  assert.strictEqual(T.normTime(" 01:00:00 "), "01:00:00");
  assert.strictEqual(T.normTime(""), "");
  assert.strictEqual(T.normTime("abc"), null);
  assert.strictEqual(T.normTime("1:2:3:4"), null);
  assert.strictEqual(T.normTime("1.5:30"), null);
  assert.strictEqual(T.toSec(""), null);
  assert(Number.isNaN(T.toSec("x")));
  assert.strictEqual(T.toSec("1:00:00.5"), 3600.5);
});

t("specFromFields: kompletan spec, tacni tipovi", () => {
  const s = T.specFromFields(fields({trim_start: "90", trim_end: "2:00"}));
  eq(Object.keys(s).sort(), [...CONTRACT_KEYS].sort());
  assert.strictEqual(s.vq, 65); assert.strictEqual(typeof s.vq, "number");
  assert.strictEqual(s.chapters, true);
  assert.strictEqual(s.audio_tracks, "all");
  assert.strictEqual(s.engine, "ollama"); assert.strictEqual(s.model, "gemma4:12b-it-qat");
  assert.strictEqual(s.trim_start, "00:01:30"); assert.strictEqual(s.trim_end, "00:02:00");
  for (const k of T.SPEC_KEYS) if (!["vq", "chapters", "audio_tracks"].includes(k)) assert.strictEqual(typeof s[k], "string", k);
});

t("specFromFields: audio trake, model sa '|', neispravno vreme ostaje sirovo", () => {
  const s = T.specFromFields(fields({audio_all: false, audio_n: [2, "0", 2, -1, "x"], model_full: "claude|opus", vq: "x",
    chapters: false, trim_start: "abc"}));
  eq(s.audio_tracks, [0, 2]);
  assert.strictEqual(s.engine, "claude"); assert.strictEqual(s.model, "opus");
  assert.strictEqual(s.vq, T.FALLBACK.vq);
  assert.strictEqual(s.chapters, false);
  assert.strictEqual(s.trim_start, "abc");
  eq(T.specFromFields(fields({audio_all: false, audio_n: []})).audio_tracks, []);
  assert.strictEqual(T.specFromFields(fields({suffix: " (kopija)  "})).suffix, " (kopija)");
  assert.strictEqual(T.specFromFields(fields({suffix: ""})).suffix, "");
  const m = T.specFromFields(fields({model_full: "ollama|hf.co/x|y:q4"}));
  assert.strictEqual(m.engine, "ollama"); assert.strictEqual(m.model, "hf.co/x|y:q4");
});

t("effectiveSpec: podrazumevano nista se ne prevodi/burnuje; neutralne vrednosti za onemogucena polja", () => {
  let s = T.effectiveSpec(T.specFromFields(fields({translate: "lat", sub_mode: "burn", sub_src: "none", sub_file: "/x.srt"})), A);
  assert.strictEqual(s.translate, "off"); assert.strictEqual(s.sub_mode, "soft"); assert.strictEqual(s.sub_file, "");
  s = T.effectiveSpec(T.specFromFields(fields({vcodec: "copy", scale: "720", fps: "25", deint: "bwdif", vbitrate: "8M"})), A);
  eq([s.scale, s.fps, s.deint, s.vbitrate], ["src", "src", "off", ""]);
  s = T.effectiveSpec(T.specFromFields(fields({acodec: "copy", loudnorm: "-23", channels: "2"})), A);
  eq([s.loudnorm, s.channels], ["off", "keep"]);
  s = T.effectiveSpec(T.specFromFields(fields({container: "m4a", sub_src: "whisper", translate: "lat"})), A);
  eq([s.vcodec, s.sub_src, s.translate, s.subs_keep], ["none", "none", "off", "none"]);
  s = T.effectiveSpec(T.specFromFields(fields({sub_src: "track:1", translate: "lat", sub_mode: "burn"})), A);  // PGS
  eq([s.translate, s.sub_mode], ["off", "burn"]);
  s = T.effectiveSpec(T.specFromFields(fields({sub_src: "track:0", translate: "cyr", sub_mode: "burn"})), A);
  eq([s.translate, s.sub_mode], ["cyr", "burn"]);
  s = T.effectiveSpec(T.specFromFields(fields({hdr: "sdr"})), B);  // SDR izvor -> nema tonemapa
  assert.strictEqual(s.hdr, "keep");
  s = T.effectiveSpec(T.specFromFields(fields({hdr: "sdr", depth: "10"})), A);
  eq([s.hdr, s.depth], ["sdr", "10"]);
  s = T.effectiveSpec(T.specFromFields(fields({vcodec: "h264_vt", depth: "10"})), A);
  assert.strictEqual(s.depth, "8");
  s = T.effectiveSpec(T.specFromFields(fields({vcodec: "x264", depth: "10"})), A);  // backend dozvoljava High10 (uz upozorenje)
  assert.strictEqual(s.depth, "10");
  s = T.effectiveSpec(T.specFromFields(fields({vcodec: "prores_vt", vbitrate: "50M", depth: "10"})), A);
  eq([s.vbitrate, s.depth], ["", "auto"]);
  s = T.effectiveSpec(T.specFromFields(fields({sub_src: "file", sub_file: "/s/a.srt"})), A);
  assert.strictEqual(s.sub_file, "/s/a.srt");
  s = T.effectiveSpec(T.specFromFields(fields({})), {...A, video: null});
  assert.strictEqual(s.vcodec, "none");
  // izvor bez videa: titl polja su onemogucena -> "Primeni prvu na sve" ne sme da prosledi whisper/burn
  s = T.effectiveSpec(T.specFromFields(fields({sub_src: "whisper", sub_mode: "burn", translate: "lat"})), {...A, video: null});
  eq([s.vcodec, s.sub_src, s.sub_mode, s.translate, s.subs_keep], ["none", "none", "soft", "off", "none"]);
  eq(Object.keys(s).sort(), [...CONTRACT_KEYS].sort());
});

t("mapTracks: uparivanje po jeziku (i redosledu unutar jezika)", () => {
  const s = T.specFromFields(fields({audio_all: false, audio_n: [0, 2], sub_src: "track:2"}));  // eng#1 + srp#1, sub srp
  const r = T.mapTracks(A, B, s);
  eq(r.audio_tracks, [0, 1]);       // srp -> 0, eng#1 -> 1
  assert.strictEqual(r.sub_src, null);  // B nema srp titl -> ostaje kako je
  eq(T.mapTracks(A, B, {...s, audio_tracks: [1]}).audio_tracks, [2]);  // eng#2 (komentar) -> eng#2
  eq(T.mapTracks(A, B, {...s, sub_src: "track:0"}).sub_src, "track:1");  // eng tekst -> eng tekst (ne PGS)
  eq(T.mapTracks(A, B, {...s, sub_src: "track:1"}).sub_src, "track:0");  // eng PGS -> eng PGS
  eq(T.mapTracks(A, B, {...s, sub_src: "whisper"}).sub_src, "whisper");
  eq(T.mapTracks(A, B, {...s, sub_src: "file"}).sub_src, null);
  eq(T.mapTracks(A, B, {...s, audio_tracks: "all"}).audio_tracks, "all");
  eq(T.mapTracks(A, {audio: [{n: 0, lang: "jpn"}], subs: []}, s).audio_tracks, [0]);  // nista se ne poklapa -> prva
  eq(T.mapTracks(A, {audio: [], subs: []}, s).audio_tracks, []);
  eq(T.mapTracks(A, B, {...s, audio_tracks: []}).audio_tracks, []);  // namerno bez zvuka ostaje bez zvuka
});

t("clientCheck: isečak, spoljni titl, prazan izlaz", () => {
  const ok = s => T.clientCheck(T.specFromFields(fields(s)), A);
  eq(ok({}).errors, []);
  assert(ok({trim_start: "2:00", trim_end: "1:00"}).errors.some(e => /posle početka/.test(e)));
  assert(ok({trim_start: "x"}).errors.some(e => /Početak isečka nije/.test(e)));
  assert(ok({trim_start: "11:00"}).errors.some(e => /posle kraja fajla/.test(e)));
  assert(ok({trim_end: "20:00"}).warnings.length === 1);
  assert(ok({sub_src: "file", sub_file: ""}).errors.some(e => /fajl titla/.test(e)));
  assert(ok({container: "mp3", audio_all: false, audio_n: []}).errors.length === 1);
  assert(ok({vcodec: "none", audio_all: false, audio_n: []}).errors.some(e => /prazan/.test(e)));
});

t("summarize: kratak opis bez izuzetaka", () => {
  const s1 = T.summarize(T.effectiveSpec(T.specFromFields(fields({sub_src: "track:0", translate: "lat", sub_mode: "burn", hdr: "sdr"})), A), A);
  assert(/HEVC VT q65 1080p HDR→SDR/.test(s1), s1);
  assert(/titl: S1 eng → srpski \(latinica\) · burn/.test(s1), s1);
  assert(/→ MKV$/.test(s1), s1);
  const s2 = T.summarize(T.effectiveSpec(T.specFromFields(fields({container: "mp3", acodec: "mp3", abitrate: "320k"})), A), A);
  assert(/bez videa · 1 audio → MP3 320k/.test(s2), s2);
  const s3 = T.summarize(T.effectiveSpec(T.specFromFields(fields({vcodec: "x265", vq: "20", vbitrate: "8M", trim_end: "60"})), A), A);
  assert(/x265 8 Mb\/s/.test(s3) && /isečak početak–00:01:00/.test(s3), s3);
  assert.strictEqual(typeof T.summarize(T.specFromFields(fields({})), null), "string");
});

t("cardHtml: data-t polja pokrivaju ceo spec (osim engine/model/audio_tracks)", () => {
  const keys = [...T.cardHtml().matchAll(/data-t="([a-z_]+)"/g)].map(m => m[1]);
  assert.strictEqual(new Set(keys).size, keys.length, "duplikat data-t");
  const need = T.SPEC_KEYS.filter(k => !["engine", "model", "audio_tracks"].includes(k)).concat(["model_full", "preset"]);
  eq([...keys].sort(), need.sort());
});

t("labeli traka i kontejnera (escape je u pozivaocu)", () => {
  assert.strictEqual(T.audioLabel(A.audio[1]), "A2 · eng · AC-3 · 5.1 · „Commentary“");
  assert.strictEqual(T.subLabel(A.subs[1]), "S2 · eng · PGS · slika");
  assert.strictEqual(T.contName(A), "MKV");
  assert.strictEqual(T.contName({name: "a.mov", container: "mov,mp4,m4a,3gp,3g2,mj2"}), "MOV");
  assert.strictEqual(T.contName({name: "a.ts", container: "mpegts"}), "MPEG-TS");
});

console.log(`\n${pass} ok, ${fail} palo`);
process.exit(fail ? 1 : 0);
