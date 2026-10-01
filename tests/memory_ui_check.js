#!/usr/bin/env node
// Provera cistih helpera iz static/memory.js bez DOM-a (window.__memTest) + zakacenost u static/index.html.
// Pokretanje: node tests/memory_ui_check.js
"use strict";
const fs = require("fs"), path = require("path"), vm = require("vm"), assert = require("assert");

const ROOT = path.join(__dirname, "..");
const SRC = fs.readFileSync(path.join(ROOT, "static", "memory.js"), "utf8");
const HTML = fs.readFileSync(path.join(ROOT, "static", "index.html"), "utf8");
const CONTRACT = fs.readFileSync(path.join(ROOT, "docs", "memory-contract.md"), "utf8");

// isti helperi kao u index.html (samo ono sto cisti helperi koriste)
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const plural = (n, a, b, c) => { const d = n % 10, h = n % 100; return d === 1 && h !== 11 ? a : d >= 2 && d <= 4 && (h < 12 || h > 14) ? b : c; };
const ctx = {esc, plural, console, setTimeout, clearTimeout};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(SRC, ctx, {filename: "static/memory.js"});
const T = ctx.__memTest;
const J = x => JSON.parse(JSON.stringify(x));  // objekti iz vm konteksta imaju drugi prototip
const eq = (a, b, m) => assert.deepStrictEqual(J(a), J(b), m);

let pass = 0, fail = 0;
function t(name, fn) {
  try { fn(); pass++; console.log("  ok  " + name); }
  catch (e) { fail++; console.log("  FAIL " + name + "\n       " + String(e.message).split("\n").join("\n       ")); }
}
// nijedan sirov tag/atribut iz podataka ne sme da prodje
const EVIL = `<script>alert("x")</script><img src=x onerror='a()'>`;
function noRaw(html, what) {
  assert(!/<script/i.test(html), what + ": sirov <script>");
  assert(!/<img/i.test(html), what + ": sirov <img>");
  assert(!/onerror='/.test(html), what + ": sirov atribut sa navodnicima");
  assert(!/="[^"]*"[^\s>=]/.test(html.replace(/="[^"]*"(?=[\s>/])/g, "")), what + ": navodnik probio atribut");
}

const RULE = {id: "r1", type: "tr", lang: "sr", wrong: "sajt", right: "veb-sajt", count: 3, channels: ["Kanal A", "Kanal B", "Kanal C"],
  class: "termin", conflict: false, state: "auto", active: true, last_ts: "2026-09-30T12:34:56+0200"};
const GLOS = [
  {id: "g1", type: "tr", lang: "sr", src: "Djokovic", tgt: "Đoković", wrong: "", scope: "global", channel: "", note: "teniser"},
  {id: "g2", type: "stt", lang: "el", src: "", tgt: "Μητσοτάκης", wrong: "Μισοτάκης", scope: "channel", channel: "ERT", note: ""},
  {id: "g3", type: "stt", lang: "sr", src: "", tgt: "Ćevapi", wrong: "", scope: "global", channel: "", note: ""},
];
const RECENT = [
  {id: "c1", ts: "2026-09-30T10:00:00+0200", type: "tr", lang: "sr-lat", channel: "Kanal A", video_id: "abc", file: "a.sr.srt",
    src: "The website is down", before: "Sajt je pao", after: "Veb-sajt je pao", disabled: false},
  {id: "c2", ts: "2026-09-29T09:00:00+0200", lang: "el", channel: null, video_id: "def", file: "b.el.srt",  // star red bez type
    src: "Hello", before: "Γεια", after: "Γειά σου", disabled: true},
  {id: "c3", ts: "2026-09-28T08:00:00+0200", type: "stt", lang: "sr", channel: "Kanal B", video_id: "ghi", file: "c.whisper.srt",
    src: null, before: "Novak Jokovic je igrao", after: "Novak Đoković je igrao", disabled: false},
];

console.log("memory.js — cisti helperi");

t("plain skript: bez import/export, bez spoljnih URL-ova, izlozene funkcije", () => {
  assert(!/^\s*(import|export)\s/m.test(SRC), "ima import/export");
  assert(!/https?:\/\//.test(SRC), "ima spoljni URL");
  for (const f of ["memRefresh", "memGlossaryPop", "memGlossaryClose", "memSaveToastText"]) assert.strictEqual(typeof ctx[f], "function", f);
});

t("index.html: kartica, ruta, podesavanja, editor, skript", () => {
  assert(/<script src="\/static\/memory\.js"><\/script>/.test(HTML), "nema <script> za memory.js");
  assert(HTML.indexOf("/static/memory.js") > HTML.indexOf("/static/toolbox.js"), "memory.js posle toolbox.js");
  assert(/const TABS = \[[^\]]*"biblioteka", "memorija"/.test(HTML), "memorija posle biblioteke u TABS");
  assert(/href="#memorija" data-tab="memorija"/.test(HTML), "link kartice");
  assert(/<section class="tab" data-tab="memorija"[^>]*>\s*<div id="mem">/.test(HTML), "sekcija #mem");
  for (const id of ["s_mem_tr", "s_mem_stt", "edGl", "edGlBox"]) assert(HTML.includes(`id="${id}"`), "nema #" + id);
  assert(/mem_tr: \$\("#s_mem_tr"\)\.value === "true"/.test(HTML) && /mem_stt: \$\("#s_mem_stt"\)\.value === "true"/.test(HTML), "saveSettings");
  assert(/\$\("#s_mem_tr"\)\.value = String\(SET\.mem_tr !== false\)/.test(HTML), "fillSettings");
  assert(/memSaveToastText/.test(HTML) && /onLink: edLeave/.test(HTML), "toast posle cuvanja");
});

t("ugovor: stanja pravila i tipovi", () => {
  assert(/"state":"auto\|on\|off"/.test(CONTRACT.replace(/\s/g, "")), "ugovor vise ne kaze auto|on|off");
  eq(T.STATES.map(s => s[0]), ["auto", "on", "off"]);
  eq(Object.keys(T.TYPE_LBL), ["tr", "stt"]);
  eq(T.TR_LANGS, ["sr", "el"]);
  eq(T.LIM, {src: 120, tgt: 120, wrong: 120, note: 300});
});

t("nazivi: tip, stanje, klasa, jezik", () => {
  assert.strictEqual(T.typeLabel("tr"), "Prevod"); assert.strictEqual(T.typeLabel("stt"), "Whisper"); assert.strictEqual(T.typeLabel("x"), "x");
  assert.strictEqual(T.stateLabel("auto"), "Automatski"); assert.strictEqual(T.stateLabel("on"), "Uvek");
  assert.strictEqual(T.stateLabel("off"), "Nikad"); assert.strictEqual(T.stateLabel(undefined), "Automatski");
  eq(["ime", "termin", "pravopis", "gramatika", "glosar"].map(T.classLabel), ["Ime", "Termin", "Pravopis", "Gramatika", "Glosar"]);
  assert.strictEqual(T.classLabel(null), "—");
  assert.strictEqual(T.langName("el"), "grčki"); assert.strictEqual(T.langName("xx"), "xx");
  assert.strictEqual(T.langLbl("sr-cyr"), "srpski (ćir.)"); assert.strictEqual(T.langLbl("sr-lat"), "srpski (lat.)");
  assert.strictEqual(T.langLbl("?"), "?");
});

t("baseLang", () => {
  eq(["sr-lat", "en-orig", "EN", "srp", "ell", "el", "und", "", null, "x1", "pt_BR"].map(T.baseLang), ["sr", "en", "en", "sr", "el", "el", "", "", "", "", "pt"]);
});

t("fmtTs", () => {
  assert.strictEqual(T.fmtTs("2026-09-30T12:34:56+0200"), "30.09.2026. 12:34");
  assert.strictEqual(T.fmtTs("2026-09-30"), "30.09.2026.");
  assert.strictEqual(T.fmtTs(""), ""); assert.strictEqual(T.fmtTs(null), "");
  assert(/^\d\d\.\d\d\.\d{4}\. \d\d:\d\d$/.test(T.fmtTs(1790000000)), "epoch sekunde");
});

t("normDump: delimican / pogresan odgovor ne rusi UI", () => {
  eq(T.normDump(null), {stats: {}, glossary: [], rules: [], recent: [], settings: {}});
  eq(T.normDump({rules: "x", glossary: [null, 1, {id: "a"}], recent: [{id: "c"}], settings: {mem_tr: false}}),
    {stats: {}, glossary: [{id: "a"}], rules: [], recent: [{id: "c"}], settings: {mem_tr: false}});
});

t("filterRows: tekst bez kvačica, sve reci, tip, stari redovi bez type = tr", () => {
  eq(T.filterRows("glossary", GLOS, {q: "djokovic"}).map(x => x.id), ["g1"]);
  eq(T.filterRows("glossary", GLOS, {q: "đoković"}).map(x => x.id), ["g1"]);
  eq(T.filterRows("glossary", GLOS, {q: "cevapi"}).map(x => x.id), ["g3"]);
  eq(T.filterRows("glossary", GLOS, {q: "ert"}).map(x => x.id), ["g2"]);  // kanal
  eq(T.filterRows("glossary", GLOS, {q: "teniser"}).map(x => x.id), ["g1"]);  // napomena
  eq(T.filterRows("glossary", GLOS, {type: "stt"}).map(x => x.id), ["g2", "g3"]);
  eq(T.filterRows("recent", RECENT, {type: "tr"}).map(x => x.id), ["c1", "c2"]);
  eq(T.filterRows("recent", RECENT, {q: "novak igrao"}).map(x => x.id), ["c3"]);
  eq(T.filterRows("recent", RECENT, {q: "novak pao"}).map(x => x.id), []);
  eq(T.filterRows("recent", RECENT, {q: "  "}).map(x => x.id), ["c1", "c2", "c3"]);
  eq(T.filterRows("rules", [RULE], {q: "kanal c"}).map(x => x.id), ["r1"]);
  eq(T.filterRows("rules", [RULE], {q: "termin"}).map(x => x.id), ["r1"]);  // klasa po srpskom nazivu
  eq(T.filterRows("rules", null, {q: "x"}), []);
});

t("validateGlossary: ispravne stavke i ciscenje polja po tipu", () => {
  let v = T.validateGlossary({type: "tr", lang: "SR", src: "  Djokovic ", tgt: "Đoković", wrong: "x", scope: "global", channel: "K", note: ""});
  assert(v.ok, v.errors.join("; "));
  eq(v.entry, {type: "tr", lang: "sr", src: "Djokovic", tgt: "Đoković", wrong: "", scope: "global", channel: "", note: ""});
  v = T.validateGlossary({type: "stt", lang: "el", src: "ignorisano", tgt: "Μητσοτάκης", wrong: "Μισοτάκης", scope: "channel", channel: " ERT "});
  assert(v.ok, v.errors.join("; "));
  eq(v.entry, {type: "stt", lang: "el", src: "", tgt: "Μητσοτάκης", wrong: "Μισοτάκης", scope: "channel", channel: "ERT", note: ""});
  v = T.validateGlossary({type: "stt", lang: "sr", tgt: "Ćevapi", id: "g9"});
  assert(v.ok); assert.strictEqual(v.entry.id, "g9"); assert.strictEqual(v.entry.scope, "global");
});

t("validateGlossary: greske (obavezna polja, duzine, jezik, kanal)", () => {
  const errs = e => T.validateGlossary(e).errors.join(" | ");
  assert(/izvorni oblik/.test(errs({type: "tr", lang: "sr", tgt: "a"})));
  assert(/Upiši prevod/.test(errs({type: "tr", lang: "sr", src: "a"})));
  assert(/tačan termin/.test(errs({type: "stt", lang: "sr", wrong: "a"})));
  assert(/Jezik/.test(errs({type: "tr", lang: "srp1", src: "a", tgt: "b"})));
  assert(/Jezik/.test(errs({type: "tr", lang: "", src: "a", tgt: "b"})));
  assert(/tip/.test(errs({type: "x", lang: "sr", src: "a", tgt: "b"})));
  assert(/najviše 120/.test(errs({type: "tr", lang: "sr", src: "a".repeat(121), tgt: "b"})));
  assert(T.validateGlossary({type: "tr", lang: "sr", src: "a".repeat(120), tgt: "b"}).ok);
  assert(/najviše 300/.test(errs({type: "tr", lang: "sr", src: "a", tgt: "b", note: "n".repeat(301)})));
  assert(/kanala/.test(errs({type: "tr", lang: "sr", src: "a", tgt: "b", scope: "channel", channel: "  "})));
  assert(/isti kao tačan/.test(errs({type: "stt", lang: "sr", tgt: "Ana", wrong: "Ana"})));
  assert(T.validateGlossary({type: "stt", lang: "sr", tgt: "Đoković", wrong: "Djokovic"}).ok, "kvačice su prava ispravka");
});

t("validateGlossary: duplikat (src bez obzira na velika slova), izmena iste stavke nije duplikat", () => {
  const e = {type: "tr", lang: "sr", src: "DJOKOVIC", tgt: "Đoković", scope: "global"};
  assert(/već postoji/.test(T.validateGlossary(e, GLOS).errors.join()));
  assert(T.validateGlossary({...e, id: "g1"}, GLOS).ok, "update iste stavke");
  assert(T.validateGlossary({...e, tgt: "Novak Đoković"}, GLOS).ok, "drugi prevod");
  assert(T.validateGlossary({...e, scope: "channel", channel: "K"}, GLOS).ok, "drugi opseg");
  const g2 = {type: "stt", lang: "el", tgt: "Μητσοτάκης", scope: "channel", channel: "ERT"};
  assert(/već postoji/.test(T.validateGlossary({...g2, wrong: "μισοτάκης"}, GLOS).errors.join()), "wrong bez obzira na velika slova");
  assert(T.validateGlossary({...g2, wrong: "Μιτσοτάκη"}, GLOS).ok, "isti termin, drugi pogresan oblik = nova stavka (kao backend)");
  assert(T.validateGlossary(g2, GLOS).ok, "bez wrong je drugi kljuc od stavke sa wrong");
});

t("entryFromSelection / pickField: oznaka iz editora -> nacrt stavke", () => {
  const base = {wrong: "", scope: "global", note: ""};
  eq(T.entryFromSelection({text: "  Novak \n Đoković ", where: "tgt", entry: {kind: "whisper", lang: "sr"}, channel: "Kanal A"}),
    {...base, type: "stt", lang: "sr", src: "", tgt: "Novak Đoković", channel: "Kanal A"});
  eq(T.entryFromSelection({text: "Mitsotakis", where: "tgt", entry: {kind: "auto", lang: "en-orig"}}),
    {...base, type: "stt", lang: "en", src: "", tgt: "Mitsotakis", channel: ""});
  eq(T.entryFromSelection({text: "website", where: "src", entry: {kind: "sr", lang: "sr"}}),
    {...base, type: "tr", lang: "sr", src: "website", tgt: "", channel: ""});
  eq(T.entryFromSelection({text: "veb-sajt", where: "tgt", entry: {kind: "sr", lang: "sr"}}),
    {...base, type: "tr", lang: "sr", src: "", tgt: "veb-sajt", channel: ""});
  eq(T.entryFromSelection({text: "ιστότοπος", where: "tgt", entry: {kind: "tr", lang: "el"}}),
    {...base, type: "tr", lang: "el", src: "", tgt: "ιστότοπος", channel: ""});
  eq(T.entryFromSelection({text: "website", where: "tgt", entry: {kind: "manual", lang: "en"}}),
    {...base, type: "tr", lang: "sr", src: "website", tgt: "", channel: ""});
  eq(T.entryFromSelection({}), {...base, type: "tr", lang: "sr", src: "", tgt: "", channel: ""});
  assert.strictEqual(T.entryFromSelection({text: "x".repeat(500), entry: {kind: "whisper"}}).tgt.length, 120);
  assert.strictEqual(T.entryFromSelection({text: "a", entry: {kind: "whisper", lang: "und"}}).lang, "sr");
  eq([T.pickField("stt", "sr", "src"), T.pickField("tr", "tr", "src"), T.pickField("tr", "sr", "tgt"), T.pickField("tr", "manual", "tgt")],
    ["tgt", "src", "tgt", "src"]);
  // nacrt prolazi validaciju cim korisnik doda drugu stranu
  const d = T.entryFromSelection({text: "website", where: "src", entry: {kind: "sr"}});
  assert(T.validateGlossary({...d, tgt: "veb-sajt"}).ok);
});

t("ruleStatus: aktivno / konflikt / nikad / uvek / gramatika / ceka / glosar", () => {
  const s = r => T.ruleStatus({...RULE, ...r}).label;
  assert.strictEqual(s({}), "aktivno");
  assert.strictEqual(s({active: false, conflict: true}), "konflikt");
  assert.strictEqual(s({active: false, state: "off"}), "isključeno");
  assert(/Uvek/.test(T.ruleStatus({...RULE, state: "on"}).why));
  assert.strictEqual(s({active: false, class: "gramatika", count: 2}), "savet modelu");
  assert.strictEqual(s({active: false, count: 1}), "čeka");
  assert.strictEqual(s({class: "glosar"}), "aktivno");
  assert.strictEqual(s({class: "glosar", active: false}), "za kanal");
  // backend: glosar samo za kanal je active=true ali scope "channel" -> nije "aktivno" svuda
  assert.strictEqual(s({class: "glosar", active: true, scope: "channel"}), "za kanal");
  assert.strictEqual(s({class: "glosar", active: true, scope: "all"}), "aktivno");
  assert.strictEqual(s({class: "glosar", active: false, state: "off"}), "isključeno");
  assert.strictEqual(T.ruleStatus(null).label, "čeka");
  // gramatika nikad ne postaje sama aktivna: tekst "čeka" to kaže
  const g1 = T.ruleStatus({...RULE, active: false, class: "gramatika", count: 1});
  assert.strictEqual(g1.label, "čeka");
  assert(/savet/.test(g1.why) && !/postaje aktivno/.test(g1.why), g1.why);
});

t("forma glosara: zaglavlje polja nije .gl (lista plejliste ima border/scroll)", () => {
  const h = T.glFormHtml({type: "tr", lang: "sr", src: "", tgt: "", wrong: "", scope: "global", channel: "", note: ""},
    {pfx: "t", pick: true});
  assert(h.includes('class="glh"') && !/class="gl"/.test(h), h.slice(0, 300));
});

t("diffHtml: oznacene izmenjene reci, escape", () => {
  let d = T.diffHtml("Sajt je pao", "Veb-sajt je pao");
  assert.strictEqual(d.before, '<del class="d">Sajt</del> je pao');
  assert.strictEqual(d.after, '<ins class="d">Veb-sajt</ins> je pao');
  d = T.diffHtml("isti tekst", "isti tekst");
  assert(!/<(del|ins)/.test(d.before + d.after));
  d = T.diffHtml("a b c", "a x y c");
  assert.strictEqual(d.after, 'a <ins class="d">x y</ins> c');  // razmak izmedju dve izmene u istom bloku
  d = T.diffHtml("", "novo");
  assert.strictEqual(d.after, '<ins class="d">novo</ins>'); assert.strictEqual(d.before, "");
  d = T.diffHtml(`x "<b>"`, `x '<i>'`);
  assert.strictEqual(d.before, 'x <del class="d">&quot;&lt;b&gt;&quot;</del>');
  assert.strictEqual(d.after, 'x <ins class="d">&#39;&lt;i&gt;&#39;</ins>');
  const big = "reč ".repeat(600);
  assert.strictEqual(T.diffHtml(big, big + "x").after, esc(big + "x"));  // predugacko -> bez diffa, i dalje escape
});

t("pairText / saveToastText / rulesActiveOf", () => {
  assert.strictEqual(T.pairText(GLOS[0]), "„Djokovic“ → „Đoković“");
  assert.strictEqual(T.pairText(GLOS[1]), "„Μισοτάκης“ → „Μητσοτάκης“ (whisper)");
  assert.strictEqual(T.pairText(GLOS[2]), "„Ćevapi“ (whisper termin)");
  assert.strictEqual(T.rulesActiveOf({ok: true, memory: {rules_active: 5}}), 5);
  assert.strictEqual(T.rulesActiveOf({ok: true}, {total: 3, rules_active: 2}), 2);
  assert.strictEqual(T.rulesActiveOf({memory: {rules_active: 0}}, {rules_active: 9}), 0);
  assert.strictEqual(T.rulesActiveOf({ok: true}, null, undefined), null);
  assert.strictEqual(T.saveToastText(2, 40, 5), "Sačuvano · 2 ispravke (u memoriji ukupno 40) · memorija: 5 aktivnih pravila");
  assert.strictEqual(T.saveToastText(1, null, 1), "Sačuvano · 1 ispravka · memorija: 1 aktivno pravilo");
  assert.strictEqual(T.saveToastText(5, 12, null), "Sačuvano · 5 ispravki (u memoriji ukupno 12)");
  assert.strictEqual(ctx.memSaveToastText(3, 7, {memory: {rules_active: 22}}), "Sačuvano · 3 ispravke (u memoriji ukupno 7) · memorija: 22 aktivna pravila");
  assert.strictEqual(ctx.memSaveToastText(3, 7, {changed: 3}, {total: 7}), "Sačuvano · 3 ispravke (u memoriji ukupno 7)");
});

t("redovi: sadrzaj i data-id", () => {
  const g = T.glossaryRowHtml(GLOS[0]);
  assert(g.includes('data-id="g1"') && g.includes("Djokovic") && g.includes("Đoković") && g.includes("svi kanali") && g.includes("teniser"));
  assert(/data-m="gedit"/.test(g) && /data-m="gdel"/.test(g));
  assert(T.glossaryRowHtml(GLOS[1]).includes("📺 ERT"));
  assert(T.glossaryRowHtml(GLOS[2]).includes("(nagoveštaj)"));
  const r = T.ruleRowHtml(RULE);
  assert(r.includes("sajt") && r.includes("veb-sajt") && r.includes("3×") && r.includes("Termin") && r.includes("Kanal A, Kanal B +1"));
  assert(/<option value="auto" selected>Automatski<\/option>/.test(r) && !/disabled/.test(r));
  assert(/<option value="off" selected>Nikad<\/option>/.test(T.ruleRowHtml({...RULE, state: "off", active: false})));
  assert(/<option value="auto" selected>/.test(T.ruleRowHtml({...RULE, state: "čudno"})), "nepoznato stanje -> Automatski");
  const gr = T.ruleRowHtml({...RULE, class: "glosar", state: "off", active: false});
  assert(!/<select[^>]*disabled/.test(gr) && /value="off" selected/.test(gr), "i glosar pravilo moze Uvek/Nikad (backend postuje stanje)");
  const c = T.recentRowHtml(RECENT[0]);
  assert(c.includes('data-id="c1"') && /data-m="cuse" checked/.test(c) && c.includes("The website is down") && c.includes("<ins"));
  const off = T.recentRowHtml(RECENT[1]);
  assert(/class="mrow mco off"/.test(off) && !/data-m="cuse" checked/.test(off) && off.includes(">Prevod<"), "star red bez type = Prevod");
  assert(!/izvor<\/span>/.test(T.recentRowHtml(RECENT[2])), "stt bez izvora");
});

t("XSS: <script>, navodnici i apostrofi iz podataka su escapovani svuda", () => {
  const e = {id: `g"1'><x`, type: "tr", lang: "sr", src: EVIL, tgt: EVIL, wrong: EVIL, scope: "channel", channel: EVIL, note: EVIL};
  const r = {...RULE, id: `r"'<>`, wrong: EVIL, right: EVIL, channels: [EVIL, EVIL, EVIL], lang: EVIL, type: EVIL, class: EVIL,
    last_ts: EVIL, count: EVIL, state: EVIL};
  const c = {id: `c"'`, ts: EVIL, type: "stt", lang: EVIL, channel: EVIL, file: EVIL, src: EVIL, before: EVIL, after: EVIL + " x"};
  const outs = {
    glossaryRow: T.glossaryRowHtml(e), glossaryStt: T.glossaryRowHtml({...e, type: "stt"}), ruleRow: T.ruleRowHtml(r),
    ruleConflict: T.ruleRowHtml({...r, active: false, conflict: true}), recentRow: T.recentRowHtml(c),
    lists: T.glossaryListHtml([e], {}) + T.rulesListHtml([r], {}) + T.recentListHtml([c], {}),
    stats: T.statsHtml({total: EVIL, rules_active: 2, glossary: 1, disabled: 0, by_lang: {[EVIL]: 3}, by_type: {tr: EVIL}}, {}, 4),
    form: T.glFormHtml(e, {pfx: "t", pick: true, tip: EVIL, id: "x"}),
    formStt: T.glFormHtml({...e, type: "stt", lang: EVIL}),
    langOpts: T.langOptions("stt", EVIL, true),
  };
  for (const [k, h] of Object.entries(outs)) noRaw(h, k);
  assert(outs.glossaryRow.includes('data-id="g&quot;1&#39;&gt;&lt;x"'));
  assert(outs.recentRow.includes("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;"));
  assert(outs.form.includes('value="&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;&lt;img src=x onerror=&#39;a()&#39;&gt;"'));
  assert(outs.lists.includes("&lt;script&gt;"));
});

t("prazna stanja objasnjavaju kako se memorija puni; filter bez pogodaka", () => {
  const g = T.glossaryListHtml([], {}), r = T.rulesListHtml([], {}), c = T.recentListHtml(null, {});
  assert(/editor/.test(g) && /＋ Glosar/.test(g), g);
  assert(/editor/.test(r) && /dva puta/.test(r) && /Uvek/.test(r), r);
  assert(/Uredi titl/.test(c), c);
  for (const h of [T.glossaryListHtml(GLOS, {q: "nema-toga"}), T.rulesListHtml([RULE], {type: "stt"}), T.recentListHtml(RECENT, {q: "zzz"})])
    assert(/Nema poklapanja/.test(h), h);
  assert(/mh/.test(T.rulesListHtml([RULE], {})), "zaglavlje kolona");
  const many = Array.from({length: 5}, (_, i) => ({...RULE, id: "r" + i}));
  const h = T.rulesListHtml(many, {}, 3);
  assert.strictEqual((h.match(/class="mrow mru on"/g) || []).length, 3);
  assert(/Prikaži još \(2\)/.test(h) && /data-k="rules"/.test(h));
});

t("statsHtml: brojevi, jezici, stanje podesavanja", () => {
  const h = T.statsHtml({total: 12, by_lang: {"sr-lat": 7, el: 3, "sr-cyr": 2, en: 0}, by_type: {tr: 10, stt: 2}, disabled: 1, rules_active: 4, glossary: 3},
    {mem_tr: true, mem_stt: false}, 9);
  assert(h.includes("<b>12</b><span>ispravki</span>") && h.includes("prevod 10 · whisper 2"));
  assert(h.includes("<b>4</b><span>aktivna pravila</span>") && h.includes("od 9 naučenih"));
  assert(h.includes("<b>3</b><span>stavke glosara</span>") && h.includes("<b>1</b><span>isključena ispravka</span>"));
  assert(h.includes("srpski (lat.) 7") && h.includes("grčki 3") && !h.includes("engleski"), "jezici bez nula");
  assert(/u prevodu i lekturi: <b class="mon">uključena<\/b> · u whisper-u: <b class="moff">isključena<\/b>/.test(h));
  assert(h.includes('href="#podesavanja"'));
  const z = T.statsHtml(null, null);
  assert(z.includes("<b>0</b>") && z.includes("iz editora titla") && /mon/.test(z), "prazna memorija, podrazumevano uključena");
  assert(T.statsHtml({glossary: [{}, {}]}, {}).includes("<b>2</b><span>stavke glosara</span>"), "glossary kao lista");
});

t("forma glosara: polja po tipu, jezici, pick dugmad, zadrzan nepoznat jezik", () => {
  const f = T.glFormHtml({type: "tr", lang: "sr", src: "a", tgt: "b"}, {pfx: "x"});
  const keys = [...f.matchAll(/data-g="([a-z]+)"/g)].map(m => m[1]);
  eq(keys, ["type", "lang", "src", "tgt", "wrong", "scope", "channel", "note"]);
  assert(/<div class="f g-stt" hidden>/.test(f) && /<div class="f g-tr">/.test(f) && /<div class="f g-ch" hidden>/.test(f));
  assert(!/data-g-pick/.test(f) && /Dodaj u glosar/.test(f));
  const s = T.glFormHtml({type: "stt", lang: "en", scope: "channel", channel: "K"}, {pick: true, id: "g1"});
  assert(/<div class="f g-tr" hidden>/.test(s) && /<div class="f g-stt">/.test(s) && /<div class="f g-ch">/.test(s));
  assert(/data-g-pick="tgt"/.test(s) && /Sačuvaj izmenu/.test(s) && /Tačan termin/.test(s));
  assert(/<option value="en" selected>engleski \(en\)<\/option>/.test(s));
  const tr = T.langOptions("tr", "el", false);
  eq([...tr.matchAll(/value="([a-z]+)"/g)].map(m => m[1]), ["sr", "el"]);
  assert(/value="el" selected/.test(tr) && /latinica i ćirilica/.test(tr));
  assert(/<option value="de" selected>/.test(T.langOptions("tr", "de", true)), "postojeca vrednost se ne gubi");
  assert(/<option value="sr" selected>/.test(T.langOptions("tr", "de", false)), "promena tipa -> prvi jezik");
  assert(T.formHint("stt") !== T.formHint("tr"));
});

console.log(`\n${pass} ok, ${fail} palo`);
process.exit(fail ? 1 : 0);
