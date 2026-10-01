// ================================================================ MEMORIJA (#memorija) — glosar, naucena pravila, poslednje ispravke
// Obican skript (bez modula), ucitava se posle glavnog skripta iz index.html i koristi njegove globalne
// helpere: $, $$, esc, plural, api, toast, oldApi, firstLine, hl, LS, SET, TAB, LIB. Ugovor: docs/memory-contract.md §8
// Editor titla (index.html) zove window.memGlossaryPop / memGlossaryClose / memSaveToastText.
(() => {
"use strict";

const TYPE_LBL = {tr: "Prevod", stt: "Whisper"};
const STATES = [["auto", "Automatski"], ["on", "Uvek"], ["off", "Nikad"]];
const STATE_LBL = Object.fromEntries(STATES);
const CLASS_LBL = {ime: "Ime", termin: "Termin", pravopis: "Pravopis", gramatika: "Gramatika", glosar: "Glosar"};
const CLASS_HINT = {
  ime: "ime ili naziv (veliko slovo ili broj)",
  termin: "drugačiji izraz od više reči",
  pravopis: "ista reč, drugačije pisanje (kvačice, akcenti)",
  gramatika: "pojedinačna reč, rod, padež, vreme ili ti/vi — zavisi od rečenice, zato nije automatski aktivno (ide modelu kao savet)",
  glosar: "iz glosara (whisper: kako se termin pogrešno čuje)",
};
const LANG_NAMES = {sr: "srpski", el: "grčki", en: "engleski", de: "nemački", it: "italijanski", es: "španski", fr: "francuski",
  ru: "ruski", hr: "hrvatski", bs: "bosanski", mk: "makedonski", bg: "bugarski", sl: "slovenački", tr: "turski", pt: "portugalski",
  nl: "holandski", pl: "poljski", uk: "ukrajinski", hu: "mađarski", ro: "rumunski", sq: "albanski", cs: "češki", ja: "japanski",
  zh: "kineski", ar: "arapski"};
const ISO3 = {srp: "sr", eng: "en", ell: "el", gre: "el", deu: "de", ger: "de", ita: "it", spa: "es", fra: "fr", fre: "fr", rus: "ru",
  hrv: "hr", bos: "bs", mkd: "mk", mac: "mk", bul: "bg", slv: "sl", tur: "tr", por: "pt", nld: "nl", dut: "nl", pol: "pl", ukr: "uk",
  hun: "hu", ron: "ro", rum: "ro", sqi: "sq", alb: "sq", ces: "cs", cze: "cs", jpn: "ja", zho: "zh", chi: "zh", ara: "ar"};
const TR_LANGS = ["sr", "el"];  // ciljni jezici prevoda (ugovor §4)
const STT_LANGS = ["sr", "el", "en", "de", "it", "es", "fr", "ru", "hr", "bs", "mk", "bg", "sl", "tr", "pt", "nl", "pl", "uk", "hu",
  "ro", "sq", "cs", "ja", "zh", "ar"];
const LIM = {src: 120, tgt: 120, wrong: 120, note: 300};
const FIELD_LBL = {src: "Izvorni oblik", tgt: "Prevod / termin", wrong: "Pogrešan oblik", note: "Napomena"};

// ---------------------------------------------------------------- cisti helperi (testiraju se u tests/memory_ui_check.js)
const arr = x => Array.isArray(x) ? x : [];
const clean = v => String(v ?? "").replace(/\s+/g, " ").trim();
// pretraga bez obzira na velika/mala slova i kvačice (đ -> dj)
const fold = v => String(v ?? "").toLowerCase().replace(/đ/g, "dj").normalize("NFD").replace(/[̀-ͯ]/g, "");
const op = (v, l, sel) => `<option value="${esc(v)}"${sel ? " selected" : ""}>${esc(l)}</option>`;

function baseLang(code) {  // "sr-lat" / "en-orig" / "SRP" -> "sr" / "en" / "sr"; nepoznato -> ""
  const b = String(code ?? "").trim().toLowerCase().split(/[-_]/)[0];
  if (b === "und" || !/^[a-z]{2,3}$/.test(b)) return "";
  return ISO3[b] || b;
}
const langName = c => LANG_NAMES[c] || String(c || "?");
function langLbl(k) {  // kljuc iz stats.by_lang ("sr-lat", "el", "?") -> naziv
  const b = baseLang(k), s = /-(lat|cyr)$/i.exec(String(k || ""));
  return (LANG_NAMES[b] || String(k || "?")) + (s ? (s[1].toLowerCase() === "cyr" ? " (ćir.)" : " (lat.)") : "");
}
const typeLabel = t => TYPE_LBL[t] || String(t || "?");
const stateLabel = s => STATE_LBL[s] || STATE_LBL.auto;
const classLabel = c => CLASS_LBL[c] || String(c || "—");

function fmtTs(ts) {  // "2026-09-30T12:34:56+0200" / epoch -> "30.09.2026. 12:34"
  if (ts == null || ts === "") return "";
  const p2 = x => String(x).padStart(2, "0");
  if (typeof ts === "number" || /^\d+(\.\d+)?$/.test(String(ts))) {
    let n = +ts; if (n < 1e12) n *= 1000;
    const d = new Date(n);
    return isNaN(d) ? "" : `${p2(d.getDate())}.${p2(d.getMonth() + 1)}.${d.getFullYear()}. ${p2(d.getHours())}:${p2(d.getMinutes())}`;
  }
  const m = /^(\d{4})-(\d\d)-(\d\d)(?:[T ](\d\d):(\d\d))?/.exec(String(ts));
  return m ? `${m[3]}.${m[2]}.${m[1]}.` + (m[4] ? ` ${m[4]}:${m[5]}` : "") : String(ts);
}

// GET /api/memory -> uvek isti oblik (stariji / delimican odgovor ne rusi UI)
function normDump(d) {
  d = d && typeof d === "object" ? d : {};
  return {stats: d.stats && typeof d.stats === "object" ? {...d.stats} : {}, glossary: arr(d.glossary).filter(x => x && typeof x === "object"),
    rules: arr(d.rules).filter(x => x && typeof x === "object"), recent: arr(d.recent).filter(x => x && typeof x === "object"),
    settings: d.settings && typeof d.settings === "object" ? {...d.settings} : {}};
}

// filter po tekstu (sve reci moraju da se nadju) i tipu (tr / stt; stari redovi bez type = tr)
const HAY = {
  glossary: e => [e.src, e.tgt, e.wrong, e.note, e.channel, e.lang],
  rules: r => [r.wrong, r.right, r.lang, classLabel(r.class), ...arr(r.channels)],
  recent: c => [c.src, c.before, c.after, c.channel, c.file, c.lang, c.video_id],
};
function filterRows(kind, list, f = {}) {
  const words = fold(f.q).split(/\s+/).filter(Boolean), t = f.type || "", hay = HAY[kind];
  return arr(list).filter(x => x && (!t || (x.type || "tr") === t)
    && (!words.length || (h => words.every(w => h.includes(w)))(fold(hay(x).filter(v => v != null).join("\n")))));
}

function normGlossary(e) {
  e = e || {};
  const type = e.type === "stt" ? "stt" : e.type === "tr" ? "tr" : clean(e.type);
  const out = {type, lang: clean(e.lang).toLowerCase(), src: type === "stt" ? "" : clean(e.src), tgt: clean(e.tgt),
    wrong: type === "stt" ? clean(e.wrong) : "", scope: e.scope === "channel" ? "channel" : "global", channel: "", note: clean(e.note)};
  if (out.scope === "channel") out.channel = clean(e.channel);
  if (e.id) out.id = String(e.id);
  return out;
}
// isti kljuc duplikata kao backend (memory._dup_key): wrong ulazi, pa isti whisper termin sme imati vise pogresnih oblika
const dupKey = x => JSON.stringify([x.type || "tr", clean(x.lang).toLowerCase(), clean(x.src).toLowerCase(), clean(x.tgt),
  x.scope === "channel" ? "channel" : "global", x.scope === "channel" ? clean(x.channel) : "", clean(x.wrong).toLowerCase()]);
// provera pre slanja (backend radi iste provere -> 400); list = postojeci glosar (duplikati)
function validateGlossary(e0, list) {
  const e = normGlossary(e0), errors = [];
  if (!TYPE_LBL[e.type]) errors.push("Izaberi tip: Prevod ili Whisper.");
  if (!/^[a-z]{2,3}$/.test(e.lang)) errors.push("Jezik mora biti kod od 2–3 mala slova (npr. sr, el, en).");
  if (e.type === "tr" && !e.src) errors.push("Upiši izvorni oblik (kako piše u originalnom titlu).");
  if (!e.tgt) errors.push(e.type === "stt" ? "Upiši tačan termin." : "Upiši prevod.");
  for (const k of ["src", "tgt", "wrong", "note"])
    if (e[k].length > LIM[k]) errors.push(`${FIELD_LBL[k]}: najviše ${LIM[k]} znakova (sada ${e[k].length}).`);
  if (e.type === "stt" && e.wrong && e.wrong === e.tgt) errors.push("Pogrešan oblik je isti kao tačan termin.");
  if (e.scope === "channel" && !e.channel) errors.push("Za „Samo jedan kanal“ upiši ime kanala.");
  if (!errors.length && arr(list).some(x => x && (!e.id || x.id !== e.id) && dupKey(x) === dupKey(e)))
    errors.push("Ista stavka već postoji u glosaru.");
  return {ok: !errors.length, errors, entry: e};
}

// u koje polje ide oznaceni tekst: whisper/auto -> tgt (tacan termin); prevod: kolona originala -> src, prevod -> tgt;
// originalni titl (rucni CC i sl.) je izvor prevoda -> src
function pickField(type, kind, where) {
  if (type === "stt") return "tgt";
  if (kind === "sr" || kind === "tr") return where === "src" ? "src" : "tgt";
  return "src";
}
// nacrt stavke glosara iz oznake u editoru; ctx: {text, where: "src"|"tgt", entry: stavka titla {kind, lang}, channel}
function entryFromSelection(ctx) {
  ctx = ctx || {};
  const ent = ctx.entry || {}, kind = String(ent.kind || ""), text = clean(ctx.text).slice(0, LIM.tgt);
  const d = {type: "tr", lang: "sr", src: "", tgt: "", wrong: "", scope: "global", channel: clean(ctx.channel), note: ""};
  if (kind === "whisper" || kind === "auto") { d.type = "stt"; d.lang = baseLang(ent.lang) || "sr"; }
  else if (kind === "tr") d.lang = baseLang(ent.lang) || "sr";
  d[pickField(d.type, kind, ctx.where)] = text;
  return d;
}

function ruleStatus(r) {  // -> {cls, label, why} za znacku stanja
  r = r || {};
  const st = r.state || "auto", n = +r.count || 0;
  if (st === "off") return {cls: "plain", label: "isključeno", why: "Ručno isključeno („Nikad“) — ne primenjuje se i ne ide modelu."};
  if (r.class === "glosar") return r.scope === "channel" || !r.active
    ? {cls: "plain", label: "za kanal", why: "Pravilo iz glosara za jedan kanal — primenjuje se samo u tom kanalu."}
    : {cls: "ok", label: "aktivno", why: "Pravilo iz glosara — važi dok stavka postoji u glosaru."};
  if (r.active) return {cls: "ok", label: "aktivno", why: st === "on" ? "Ručno uključeno („Uvek“) — primenjuje se na svaki novi prevod."
    : `Primenjuje se automatski na nove prevode (ispravljeno ${n}×).`};
  if (r.conflict) return {cls: "warn", label: "konflikt", why: "Ista greška je ispravljena na različite načine — kod tačne varijante izaberi „Uvek“."};
  if (r.class === "gramatika" && n >= 2) return {cls: "plain", label: "savet modelu",
    why: "Pojedinačna reč ili gramatika (rod, padež, vreme, ti/vi) zavisi od rečenice — ne zamenjuje se automatski, ali ide modelu kao napomena. „Uvek“ je primenjuje svuda."};
  if (n < 2 && r.class === "gramatika") return {cls: "plain", label: "čeka",
    why: "Viđeno jednom — kad se ista ispravka ponovi, ide modelu kao savet (ne zamenjuje se automatski; „Uvek“ je primenjuje svuda)."};
  if (n < 2) return {cls: "plain", label: "čeka", why: "Viđeno jednom — postaje aktivno kad se ista ispravka ponovi (ili izaberi „Uvek“)."};
  return {cls: "plain", label: "neaktivno", why: ""};
}

// razlika po recima (LCS): {before, after} kao HTML (<del>/<ins>), sve kroz esc()
function diffHtml(a, b) {
  const A = String(a ?? "").match(/\s+|\S+/g) || [], B = String(b ?? "").match(/\s+|\S+/g) || [];
  const n = A.length, m = B.length;
  if (n * m > 250000) return {before: esc(a ?? ""), after: esc(b ?? "")};
  const L = Array.from({length: n + 1}, () => new Int32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
    L[i][j] = A[i] === B[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const ca = new Array(n).fill(true), cb = new Array(m).fill(true);
  for (let i = 0, j = 0; i < n && j < m;) {
    if (A[i] === B[j]) { ca[i++] = false; cb[j++] = false; }
    else if (L[i + 1][j] >= L[i][j + 1]) i++; else j++;
  }
  const mark = (T, ch, tag) => {
    // razmak izmedju dve izmenjene reci ide u isti blok; razmak na ivici ne
    const c = T.map((t, k) => /^\s+$/.test(t) ? k > 0 && k < T.length - 1 && ch[k - 1] && ch[k + 1] && !/^\s+$/.test(T[k - 1]) : ch[k]);
    let out = "", run = "";
    T.forEach((t, k) => {
      if (c[k]) { run += t; return; }
      if (run) { out += `<${tag} class="d">${esc(run)}</${tag}>`; run = ""; }
      out += esc(t);
    });
    return out + (run ? `<${tag} class="d">${esc(run)}</${tag}>` : "");
  };
  return {before: mark(A, ca, "del"), after: mark(B, cb, "ins")};
}

function pairText(e) {  // obican tekst (toast / confirm)
  e = e || {};
  if (e.type === "stt") return e.wrong ? `„${e.wrong}“ → „${e.tgt}“ (whisper)` : `„${e.tgt}“ (whisper termin)`;
  return `„${e.src}“ → „${e.tgt}“`;
}

function rulesActiveOf(...rs) {  // memory.rules_active iz /api/subs/save (ili rules_active iz /api/corrections/stats)
  for (const r of rs) {
    const v = r && (r.memory && r.memory.rules_active != null ? r.memory.rules_active : r.rules_active);
    if (v != null && v !== "" && Number.isFinite(+v)) return +v;
  }
  return null;
}
function saveToastText(n, tot, ra) {
  n = +n || 0;
  return `Sačuvano · ${n} ${plural(n, "ispravka", "ispravke", "ispravki")}` + (tot != null ? ` (u memoriji ukupno ${tot})` : "")
    + (ra != null ? ` · memorija: ${ra} ${plural(ra, "aktivno pravilo", "aktivna pravila", "aktivnih pravila")}` : "");
}

// ---------------------------------------------------------------- HTML (cisto; svaka korisnicka vrednost kroz esc)
const typeBadge = t => `<span class="b${t === "stt" ? " mstt" : ""}">${esc(typeLabel(t))}</span>`;
const langBadge = l => l ? `<span class="b plain" title="${esc(langLbl(l))}">${esc(l)}</span>` : "";
const GL_EMPTY = "Glosar je prazan. Dodaj termin dugmetom „＋ Dodaj termin“ ili u editoru titla (Biblioteka → ⚙ → ✎ Uredi titl): označi reč i klikni „＋ Glosar“. Termin iz glosara se uvek prevodi tačno kako je upisan, a whisper-u ide kao nagoveštaj.";
const RULES_EMPTY = "Još nema naučenih pravila. Nastaju sama iz ispravki u editoru titla (Biblioteka → ⚙ → ✎ Uredi titl): kad istu grešku ispraviš bar dva puta, pravilo „pogrešno → ispravno“ počinje da se primenjuje na nove prevode. Pojedinačno pravilo možeš odmah uključiti sa „Uvek“.";
const RECENT_EMPTY = "Nema sačuvanih ispravki. Otvori titl u Biblioteci (⚙ → ✎ Uredi titl), ispravi prevod ili whisper transkript i sačuvaj — svaka ispravljena linija se pamti ovde i odmah pomaže sledećim prevodima.";
const NO_HIT = "Nema poklapanja za ovu pretragu.";

function glossaryRowHtml(e) {
  const stt = e.type === "stt";
  const left = stt ? (e.wrong ? `<span class="mw" title="kako whisper čuje">${esc(e.wrong)}</span>`
    : '<span class="muted small" title="termin ide whisper-u kao nagoveštaj u promptu">(nagoveštaj)</span>') : `<span class="ms">${esc(e.src)}</span>`;
  const scope = e.scope === "channel" ? `<span title="važi samo za ovaj kanal">📺 ${esc(e.channel)}</span>` : '<span class="muted">svi kanali</span>';
  return `<div class="mrow mg" data-id="${esc(e.id)}">
    <span class="mtag">${typeBadge(e.type)}${langBadge(e.lang)}</span>
    <span class="mpair">${left}<span class="arr" aria-hidden="true">→</span><span class="mr">${esc(e.tgt)}</span></span>
    <span class="msc">${scope}${e.note ? `<span class="mnote" title="${esc(e.note)}">${esc(e.note)}</span>` : ""}</span>
    <span class="ra"><button class="sm" data-m="gedit">Izmeni</button><button class="sm danger" data-m="gdel">Obriši</button></span>
  </div>`;
}
function ruleRowHtml(r) {
  const s = ruleStatus(r), ch = arr(r.channels).filter(Boolean), state = STATE_LBL[r.state] ? r.state : "auto";
  const chTxt = ch.length ? ch.slice(0, 2).join(", ") + (ch.length > 2 ? ` +${ch.length - 2}` : "") : "";
  const meta = [chTxt ? `<span title="${esc(ch.join(", "))}">📺 ${esc(chTxt)}</span>` : "", r.last_ts ? `<span>poslednja ${esc(fmtTs(r.last_ts))}</span>` : ""]
    .filter(Boolean).join('<span aria-hidden="true">·</span>') + (!r.active && s.why && state !== "off" ? `<span class="mwhy">${esc(s.why)}</span>` : "");
  return `<div class="mrow mru${r.active ? " on" : ""}" data-id="${esc(r.id)}">
    <div class="mcell"><span class="mpair"><span class="mw">${esc(r.wrong)}</span><span class="arr" aria-hidden="true">→</span><span class="mr">${esc(r.right)}</span></span>${meta ? `<div class="mmeta">${meta}</div>` : ""}</div>
    <span class="mtag">${typeBadge(r.type || "tr")}${langBadge(r.lang)}</span>
    <span class="mcnt" title="koliko puta je ispravljeno">${esc(+r.count || 0)}×</span>
    <span class="mcls" title="${esc(CLASS_HINT[r.class] || "")}">${esc(classLabel(r.class))}</span>
    <span class="mst"><span class="b ${esc(s.cls)}" title="${esc(s.why)}">${esc(s.label)}</span><select data-m="rstate" aria-label="Stanje pravila ${esc(r.wrong)} → ${esc(r.right)}"${r.class === "glosar"
      ? ' title="Pravilo iz glosara (stavku menjaš u Glosaru); „Nikad“ ga isključuje bez brisanja stavke"' : ""}>${STATES.map(([v, l]) => op(v, l, v === state)).join("")}</select></span>
  </div>`;
}
function recentRowHtml(c) {
  const d = diffHtml(c.before, c.after), use = !c.disabled;
  const meta = [typeBadge(c.type || "tr") + langBadge(c.lang), c.channel ? `<span>📺 ${esc(c.channel)}</span>` : "",
    c.ts ? `<span>${esc(fmtTs(c.ts))}</span>` : ""].filter(Boolean).join(" ");
  return `<div class="mrow mco${use ? "" : " off"}" data-id="${esc(c.id)}">
    <label class="chk muse" title="Isključena ispravka se ne koristi ni za pravila ni kao primer modelu"><input type="checkbox" data-m="cuse"${use ? " checked" : ""}> koristi</label>
    <div class="mcx">
      ${c.src ? `<div><span class="k">izvor</span><span class="ms">${esc(c.src)}</span></div>` : ""}
      <div><span class="k">pre</span><span>${d.before}</span></div>
      <div><span class="k">posle</span><span>${d.after}</span></div>
    </div>
    <div class="mmeta"${c.file ? ` title="${esc(c.file)}"` : ""}>${meta}</div>
  </div>`;
}
const GL_HEAD = '<div class="mrow mg mh" aria-hidden="true"><span>Tip · jezik</span><span>Izvorno / čuje se → ispravno</span><span>Važi za</span><span></span></div>';
const RULES_HEAD = '<div class="mrow mru mh" aria-hidden="true"><span>Pogrešno → ispravno</span><span>Tip · jezik</span><span>Puta</span><span>Klasa</span><span>Stanje</span></div>';
const MORE = (k, n) => `<div class="mmore"><button class="sm" data-m="more" data-k="${esc(k)}">Prikaži još (${esc(n)})</button></div>`;
function glossaryListHtml(list, f) {
  const all = arr(list), rows = filterRows("glossary", all, f);
  if (!rows.length) return `<div class="empty">${esc(all.length ? NO_HIT : GL_EMPTY)}</div>`;
  return GL_HEAD + rows.map(glossaryRowHtml).join("");
}
function rulesListHtml(list, f, lim = 150) {
  const all = arr(list), rows = filterRows("rules", all, f);
  if (!rows.length) return `<div class="empty">${esc(all.length ? NO_HIT : RULES_EMPTY)}</div>`;
  return RULES_HEAD + rows.slice(0, lim).map(ruleRowHtml).join("") + (rows.length > lim ? MORE("rules", rows.length - lim) : "");
}
function recentListHtml(list, f, lim = 100) {
  const all = arr(list), rows = filterRows("recent", all, f);
  if (!rows.length) return `<div class="empty">${esc(all.length ? NO_HIT : RECENT_EMPTY)}</div>`;
  return rows.slice(0, lim).map(recentRowHtml).join("") + (rows.length > lim ? MORE("recent", rows.length - lim) : "");
}
function statsHtml(st, set, rulesN) {
  st = st || {}; set = set || {};
  const num = k => Math.max(0, +st[k] || 0), bt = st.by_type || {}, bl = st.by_lang || {};
  const gl = Array.isArray(st.glossary) ? st.glossary.length : num("glossary"), tot = num("total"), ra = num("rules_active"), dis = num("disabled");
  const tiles = [
    [tot, plural(tot, "ispravka", "ispravke", "ispravki"), [bt.tr ? `prevod ${bt.tr}` : "", bt.stt ? `whisper ${bt.stt}` : ""].filter(Boolean).join(" · ") || "iz editora titla"],
    [ra, plural(ra, "aktivno pravilo", "aktivna pravila", "aktivnih pravila"), rulesN != null ? `od ${rulesN} ${plural(+rulesN, "naučenog", "naučena", "naučenih")}` : ""],
    [gl, plural(gl, "stavka glosara", "stavke glosara", "stavki glosara"), "tvoji termini"],
    [dis, plural(dis, "isključena ispravka", "isključene ispravke", "isključenih ispravki"), "ne koriste se"],
  ];
  const langs = Object.entries(bl).filter(([, v]) => +v > 0).sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `<span class="b plain">${esc(langLbl(k))} ${esc(v)}</span>`).join("");
  const on = v => v === false ? '<b class="moff">isključena</b>' : '<b class="mon">uključena</b>';
  return `<div class="mtiles">${tiles.map(([v, l, s]) => `<div class="mtile"><b>${esc(v)}</b><span>${esc(l)}</span>${s ? `<small>${esc(s)}</small>` : ""}</div>`).join("")}</div>
    ${langs ? `<div class="chips"><span class="muted small">Ispravke po jeziku:</span>${langs}</div>` : ""}
    <div class="small muted">Memorija u prevodu i lekturi: ${on(set.mem_tr)} · u whisper-u: ${on(set.mem_stt)} — <a href="#podesavanja">Podešavanja</a></div>`;
}

// ---------------------------------------------------------------- forma glosara (kartica Memorija i editor titla)
const formHint = t => t === "stt" ? "Termin ide whisper-u kao nagoveštaj; pogrešno čut oblik se posle transkripcije zamenjuje tačnim."
  : "Prevodilac za ovaj izvorni oblik uvek koristi ovaj prevod (ima prednost nad modelom).";
function langOptions(type, cur, keep) {  // keep: nepoznat postojeci kod ostaje u listi (ne gubimo vrednost)
  const list = type === "stt" ? STT_LANGS : TR_LANGS, c = String(cur || "");
  const all = keep && /^[a-z]{2,3}$/.test(c) && !list.includes(c) ? [c, ...list] : list, sel = all.includes(c) ? c : all[0];
  return all.map(l => op(l, type !== "stt" && l === "sr" ? "srpski (latinica i ćirilica)" : `${langName(l)} (${l})`, l === sel)).join("");
}
function glFormHtml(d, o = {}) {
  const e = {type: "tr", lang: "sr", src: "", tgt: "", wrong: "", scope: "global", channel: "", note: "", ...d};
  const p = o.pfx || "gf", stt = e.type === "stt";
  const pick = k => o.pick ? `<button type="button" class="lnk" data-g-pick="${k}" title="Upiši tekst koji je označen u titlu">iz označenog</button>` : "";
  const fld = (k, label, cls, ph, hide) => `<div class="f ${cls}"${hide ? " hidden" : ""}><div class="glh"><label for="${p}_${k}"${k === "tgt" ? ' class="g-lbl-tgt"' : ""}>${esc(label)}</label>${pick(k)}</div>
      <input type="text" id="${p}_${k}" data-g="${k}" maxlength="${LIM[k]}" value="${esc(e[k])}" placeholder="${esc(ph)}" autocomplete="off" spellcheck="false"></div>`;
  return `<div class="glf">
    <div class="grid">
      <div class="f"><label for="${p}_type">Tip</label><select id="${p}_type" data-g="type">${op("tr", "Prevod — termin u prevodu", !stt)}${op("stt", "Whisper — termin u transkriptu", stt)}</select></div>
      <div class="f"><label for="${p}_lang">Jezik</label><select id="${p}_lang" data-g="lang">${langOptions(e.type, e.lang, true)}</select></div>
      ${fld("src", "Izvorni oblik (u originalu)", "g-tr", "npr. Djokovic", stt)}
      ${fld("tgt", stt ? "Tačan termin" : "Prevod — uvek ovako", "", "npr. Đoković", false)}
      ${fld("wrong", "Kako ga whisper pogrešno čuje", "g-stt", "opciono, npr. Jokovic", !stt)}
      <div class="f"><label for="${p}_scope">Važi za</label><select id="${p}_scope" data-g="scope">${op("global", "Sve kanale", e.scope !== "channel")}${op("channel", "Samo jedan kanal", e.scope === "channel")}</select></div>
      <div class="f g-ch"${e.scope === "channel" ? "" : " hidden"}><label for="${p}_channel">Kanal</label>
        <input type="text" id="${p}_channel" data-g="channel" list="memChList" maxlength="200" value="${esc(e.channel)}" placeholder="ime kanala" autocomplete="off"></div>
      <div class="f wide"><label for="${p}_note">Napomena (opciono)</label>
        <input type="text" id="${p}_note" data-g="note" maxlength="${LIM.note}" value="${esc(e.note)}" placeholder="npr. teniser — uvek sa Đ" autocomplete="off"></div>
    </div>
    ${o.tip ? `<div class="hint gtip">${esc(o.tip)}</div>` : ""}
    <div class="err gerr" hidden></div>
    <div class="actions"><span class="est ghint">${esc(formHint(e.type))}</span><button type="button" class="sm" data-g-act="cancel">Otkaži</button><button type="button" class="primary sm" data-g-act="save">${o.id ? "Sačuvaj izmenu" : "Dodaj u glosar"}</button></div>
  </div>`;
}

// ---------------------------------------------------------------- DOM
const CSS = `
#mem{display:flex;flex-direction:column;gap:12px}
#mem .mbar input[type=search]{flex:1 1 200px;width:auto}
#mem .mstats{display:flex;flex-direction:column;gap:8px}
#mem .mtiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(150px,100%),1fr));gap:8px}
#mem .mtile{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:7px 12px;min-width:0;display:flex;flex-direction:column}
#mem .mtile b{font-size:20px;line-height:1.25;font-weight:700;font-variant-numeric:tabular-nums}
#mem .mtile span{font-size:12.5px}
#mem .mtile small{font-size:11.5px;color:var(--muted)}
#mem .chips{display:flex;flex-wrap:wrap;gap:4px;align-items:center}
#mem .mon{color:var(--ok);font-weight:650}#mem .moff{color:var(--warn);font-weight:650}
#mem .mhelp{border-top:1px solid var(--line);font-size:13px;padding-top:8px;padding-bottom:8px}
#mem .mhelp summary{cursor:pointer;color:var(--acc);width:max-content;max-width:100%}
#mem .mhelp ul{margin:6px 0 2px;padding-left:18px;color:var(--muted);display:flex;flex-direction:column;gap:3px}
#mem .mn{font-weight:600;letter-spacing:0;text-transform:none;margin-left:4px}
.mrow{display:grid;gap:4px 12px;align-items:center;padding:7px 14px;border-top:1px solid var(--line);min-width:0}
.mrow:first-child{border-top:0}
.mrow.mh{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);font-weight:650;padding-top:6px;padding-bottom:6px}
.mg{grid-template-columns:130px minmax(0,1fr) minmax(0,200px) auto}
.mru{grid-template-columns:minmax(0,1fr) 110px 44px 86px 210px}
.mco{grid-template-columns:82px minmax(0,1fr) minmax(0,210px);align-items:start}
.mcell{min-width:0}
.mtag{display:flex;gap:4px;flex-wrap:wrap;align-items:center}
.mpair{display:flex;gap:3px 8px;align-items:baseline;flex-wrap:wrap;min-width:0;word-break:break-word}
.mpair .arr{color:var(--acc);font-weight:700}
.mw{color:var(--muted);text-decoration:line-through;text-decoration-color:color-mix(in srgb,var(--err) 65%,transparent)}
.mr{font-weight:650}
.msc{display:flex;flex-direction:column;gap:2px;min-width:0;font-size:12.5px}
.mnote{color:var(--muted);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mcnt{font-variant-numeric:tabular-nums;font-weight:650}
.mcls{font-size:12.5px;color:var(--muted)}
.mst{display:flex;gap:6px;align-items:center;justify-content:flex-end}
.mst select{width:auto;padding:3px 6px;font-size:12.5px}
.mmeta{font-size:12px;color:var(--muted);display:flex;flex-wrap:wrap;gap:2px 6px;align-items:center;margin-top:2px;min-width:0}
.mwhy{color:var(--muted);font-style:italic;flex-basis:100%}
.mru.busy{opacity:.6}
.mco.off .mcx{opacity:.5}
.muse{margin-top:2px}
.mcx{display:flex;flex-direction:column;gap:2px;min-width:0;font-size:13px;word-break:break-word}
.mcx .k{display:inline-block;min-width:44px;font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);font-weight:650}
.mcx .ms{color:var(--muted)}
del.d{background:color-mix(in srgb,var(--err) 14%,transparent);text-decoration:line-through;text-decoration-color:color-mix(in srgb,var(--err) 60%,transparent);border-radius:3px;padding:0 1px}
ins.d{background:color-mix(in srgb,var(--ok) 18%,transparent);text-decoration:none;border-radius:3px;padding:0 1px}
.b.mstt{background:color-mix(in srgb,var(--warn) 16%,transparent);color:var(--warn)}
.mmore{padding:8px 14px;border-top:1px solid var(--line);text-align:center}
.mform{padding:10px 14px 12px;border-bottom:1px solid var(--line);background:var(--panel2)}
.mfh{display:flex;gap:8px;align-items:center;margin-bottom:6px;min-width:0}
.glf .grid{grid-template-columns:repeat(auto-fill,minmax(min(200px,100%),1fr))}
.glf .glh{display:flex;justify-content:space-between;align-items:baseline;gap:6px}
.glf .glh .lnk{font-size:11.5px;margin-bottom:3px}
.glf .actions{margin-top:10px}
.glf .gerr{margin-top:8px}
@media (max-width:760px){
  .mrow.mh{display:none}
  .mrow{padding:8px 12px}
  .mg{grid-template-columns:minmax(0,1fr) auto;grid-template-areas:"tag ra" "pair pair" "sc sc"}
  .mg .mtag{grid-area:tag}.mg .mpair{grid-area:pair}.mg .msc{grid-area:sc}.mg .ra{grid-area:ra}
  .mru{grid-template-columns:auto auto auto minmax(0,1fr)}
  .mru .mcell{grid-column:1/-1}
  .mco{grid-template-columns:minmax(0,1fr) auto;grid-template-areas:"cx cx" "meta use"}
  .mco .mcx{grid-area:cx}.mco .mmeta{grid-area:meta}.mco .muse{grid-area:use;margin-top:0}
}`;
const LAYOUT = `
<div class="card">
  <div class="head mbar">
    <h2 class="grow">Memorija ispravki</h2>
    <input type="search" id="memQ" placeholder="Traži: reč, ispravka, kanal…" aria-label="Pretraga memorije">
    <span class="seg" id="memType" aria-label="Tip"><button data-mt="">Sve</button><button data-mt="tr">Prevod</button><button data-mt="stt">Whisper</button></span>
    <button class="sm" data-m="refresh" title="Osveži">↻</button>
  </div>
  <div class="pad mstats" id="memStats"><div class="small muted">Učitavam…</div></div>
  <div class="pad err" id="memErr" hidden></div>
  <details class="mhelp pad"><summary>Kako radi memorija?</summary><ul>
    <li>Ispravke nastaju u editoru titla: Biblioteka → ⚙ → ✎ Uredi titl. Svaka ispravljena linija prevoda ili whisper transkripta se pamti ovde.</li>
    <li>Iz ispravki se uče pravila „pogrešno → ispravno“. Ista ispravka viđena bar dva puta primenjuje se automatski na nove prevode; gramatičke (rod, padež, vreme) idu modelu samo kao savet. „Uvek“ i „Nikad“ ručno menjaju odluku.</li>
    <li>Glosar ima prednost: termin se uvek prevodi tačno kako je upisan. Whisper termini idu u prompt transkripcije, a pogrešno čut oblik se posle zamenjuje tačnim.</li>
    <li>Uz svaki blok prevoda model dobija i najsličnije ranije ispravke kao primere. Nema treninga modela — sve je lokalno i deluje od sledećeg prevoda.</li>
    <li>Ispravku koja ne valja isključi prekidačem „koristi“ — tada se ne koristi ni za pravila ni kao primer.</li>
  </ul></details>
</div>
<div class="card mcard" hidden>
  <div class="head"><h2 class="grow">Glosar<span class="mn" id="memGlN"></span></h2><button class="sm primary" data-m="gadd">＋ Dodaj termin</button></div>
  <div class="mform" id="memForm" hidden></div>
  <div id="memGlList"></div>
</div>
<div class="card mcard" hidden>
  <div class="head"><h2 class="grow">Naučena pravila</h2><span class="small muted" id="memRuleN"></span></div>
  <div id="memRuleList"></div>
</div>
<div class="card mcard" hidden>
  <div class="head"><h2 class="grow">Poslednje ispravke</h2><span class="small muted" id="memRecN"></span></div>
  <div id="memRecentList"></div>
</div>`;

let MEM = null, memErr = "", memSeq = 0, memQT = 0;
const UI = {q: "", type: "", edit: null, lim: {rules: 150, recent: 100}};
const lsGet = (k, d) => { try { return typeof LS === "object" ? LS.get(k, d) : d; } catch { return d; } };
const lsSet = (k, v) => { try { if (typeof LS === "object") LS.set(k, v); } catch { /* samo udobnost */ } };
const errText = (e, what) => oldApi(e) ? `${what} traži noviju verziju servera.` : firstLine(e.message) || "greška";

function ensureCss() {
  if (document.getElementById("memcss")) return;
  const st = document.createElement("style"); st.id = "memcss"; st.textContent = CSS; document.head.appendChild(st);
}
function fillChannels() {  // predlozi kanala za polje "Kanal" (datalist)
  let dl = document.getElementById("memChList");
  if (!dl) { dl = document.createElement("datalist"); dl.id = "memChList"; document.body.appendChild(dl); }
  const s = new Set();
  if (MEM) {
    MEM.recent.forEach(c => { if (c.channel) s.add(String(c.channel)); });
    MEM.rules.forEach(r => arr(r.channels).forEach(x => { if (x) s.add(String(x)); }));
    MEM.glossary.forEach(g => { if (g.channel) s.add(String(g.channel)); });
  }
  try { arr(LIB).forEach(i => { if (i && i.channel) s.add(String(i.channel)); }); } catch { /* biblioteka jos nije ucitana */ }
  dl.innerHTML = [...s].sort((a, b) => a.localeCompare(b, "sr")).slice(0, 500).map(c => `<option value="${esc(c)}"></option>`).join("");
}

function initMemory() {
  const root = $("#mem");
  if (!root || root._m) return root;
  root._m = true;
  ensureCss();
  const t = lsGet("mem.type", "");
  UI.type = ["", "tr", "stt"].includes(t) ? t : "";
  root.innerHTML = LAYOUT;
  paintSeg();
  root.addEventListener("input", e => {
    if (e.target.id !== "memQ") return;
    clearTimeout(memQT);
    memQT = setTimeout(() => { UI.q = e.target.value; renderLists(); }, 120);
  });
  root.addEventListener("click", e => {
    const b = e.target.closest("[data-mt],[data-m]");
    if (!b || !root.contains(b) || b.tagName === "SELECT" || b.tagName === "INPUT") return;
    if (b.dataset.mt !== undefined) { UI.type = b.dataset.mt; lsSet("mem.type", UI.type); paintSeg(); renderLists(); return; }
    const m = b.dataset.m, id = rowId(b);
    if (m === "refresh") memRefresh();
    else if (m === "gadd") openForm(null);
    else if (m === "gedit") openForm(id);
    else if (m === "gdel") delGlossary(id);
    else if (m === "more") { UI.lim[b.dataset.k] = (UI.lim[b.dataset.k] || 100) + 300; renderLists(); }
  });
  root.addEventListener("change", e => {
    const t = e.target;
    if (t.dataset.m === "rstate") setRule(rowId(t), t);
    else if (t.dataset.m === "cuse") setCorrection(rowId(t), t);
  });
  return root;
}
const rowId = el => { const r = el.closest("[data-id]"); return r ? r.dataset.id : ""; };
function paintSeg() { $$("#memType button").forEach(b => b.classList.toggle("on", b.dataset.mt === UI.type)); }

async function memRefresh() {
  if (!initMemory()) return;
  const seq = ++memSeq;
  let d = null, err = "";
  try { d = normDump(await api("/api/memory?recent=100")); }
  catch (e) { err = oldApi(e) ? "Memorija traži noviju verziju servera (ruta /api/memory)." : "Memorija nije učitana: " + firstLine(e.message); }
  if (seq !== memSeq) return;  // u medjuvremenu je krenulo novije osvezavanje
  if (d) { MEM = d; memErr = ""; } else memErr = err;
  render();
}

function render() {
  const root = $("#mem");
  if (!root || !root._m) return;
  const e = $("#memErr");
  e.textContent = memErr; e.hidden = !memErr;
  if (!MEM) { $("#memStats").innerHTML = memErr ? "" : '<div class="small muted">Učitavam…</div>'; return; }
  const set = {...MEM.settings};  // SET je novije posle "Sačuvaj" u Podešavanjima
  for (const k of ["mem_tr", "mem_stt"]) if (typeof SET === "object" && SET && SET[k] !== undefined) set[k] = SET[k];
  $("#memStats").innerHTML = statsHtml(MEM.stats, set, MEM.rules.length);
  $$(".mcard", root).forEach(c => { c.hidden = false; });
  renderLists();
}
// zadrzi fokus na kontroli istog reda posle ponovnog iscrtavanja (tastatura)
function keepFocus(fn) {
  const a = document.activeElement, m = a && a.dataset ? a.dataset.m : "", id = m && a.closest ? rowId(a) : "";
  fn();
  if (!m || !id) return;
  const el = $$(`#mem [data-m="${m}"]`).find(x => rowId(x) === id);
  if (el && el !== a) el.focus({preventScroll: true});
}
function renderLists() {
  if (!MEM || !$("#memGlList")) return;
  const f = {q: UI.q, type: UI.type}, filt = !!(clean(UI.q) || UI.type);
  keepFocus(() => {
    $("#memGlList").innerHTML = glossaryListHtml(MEM.glossary, f);
    $("#memRuleList").innerHTML = rulesListHtml(MEM.rules, f, UI.lim.rules);
    $("#memRecentList").innerHTML = recentListHtml(MEM.recent, f, UI.lim.recent);
  });
  const shown = k => filterRows(k, MEM[k], f).length, act = MEM.rules.filter(r => r.active).length;
  const n = MEM.glossary.length, nr = MEM.rules.length, nc = MEM.recent.length, off = MEM.recent.filter(c => c.disabled).length;
  $("#memGlN").textContent = n ? (filt ? ` · ${shown("glossary")} od ${n}` : ` · ${n}`) : "";
  $("#memRuleN").textContent = nr ? `${act} ${plural(act, "aktivno", "aktivna", "aktivnih")} od ${nr}` + (filt ? ` · prikazano ${shown("rules")}` : "") : "";
  $("#memRecN").textContent = nc ? `${nc} ${plural(nc, "poslednja", "poslednje", "poslednjih")}` + (off ? ` · ${off} ${plural(off, "isključena", "isključene", "isključenih")}` : "")
    + (filt ? ` · prikazano ${shown("recent")}` : "") : "";
}

// ---- forma (zajednicko vezivanje; box sadrzi .glf)
const G = (box, k) => $(`[data-g="${k}"]`, box);
function readForm(box) { const o = {}; $$("[data-g]", box).forEach(el => { o[el.dataset.g] = el.value; }); return o; }
function showErr(box, errs) { const el = $(".gerr", box); if (!el) return; el.innerHTML = arr(errs).map(esc).join("<br>"); el.hidden = !arr(errs).length; }
function syncForm(box, typeChanged) {
  const t = G(box, "type").value, stt = t === "stt";
  $$(".g-tr", box).forEach(x => { x.hidden = stt; });
  $$(".g-stt", box).forEach(x => { x.hidden = !stt; });
  const lt = $(".g-lbl-tgt", box); if (lt) lt.textContent = stt ? "Tačan termin" : "Prevod — uvek ovako";
  if (typeChanged) {
    G(box, "lang").innerHTML = langOptions(t, G(box, "lang").value, false);
    if (stt && !G(box, "tgt").value && G(box, "src").value) G(box, "tgt").value = G(box, "src").value;  // oznaka ne propada
  }
  $$(".g-ch", box).forEach(x => { x.hidden = G(box, "scope").value !== "channel"; });
  const h = $(".ghint", box); if (h) h.textContent = formHint(t);
}
function focusFirst(box) {
  const order = G(box, "type").value === "stt" ? ["tgt", "wrong"] : ["src", "tgt"], els = order.map(k => G(box, k));
  const el = els.find(x => x && !x.value) || els[els.length - 1];
  if (el) { el.focus({preventScroll: true}); if (el.value) el.select(); }
}
// h: {id, list(), pick(), onCancel(), onSaved(resp, entry)}
function bindForm(box, h) {
  let busy = false;
  const save = async () => {
    if (busy) return;
    const v = validateGlossary({...readForm(box), id: h.id || ""}, h.list ? h.list() : []);
    showErr(box, v.errors);
    if (!v.ok) return;
    busy = true;
    const btn = $('[data-g-act="save"]', box); btn.disabled = true;
    try {
      const r = await api("/api/memory/glossary", {op: h.id ? "update" : "add", entry: v.entry});
      if (MEM && r && Array.isArray(r.glossary)) { MEM.glossary = r.glossary; MEM.stats.glossary = r.glossary.length; }
      h.onSaved(r, v.entry);
    } catch (e) { showErr(box, [errText(e, "Glosar")]); }
    finally { busy = false; if (btn.isConnected) btn.disabled = false; }
  };
  box.onchange = e => { const k = e.target.dataset.g; if (k === "type" || k === "scope") syncForm(box, k === "type"); };
  box.onmousedown = e => { if (e.target.closest("[data-g-pick]")) e.preventDefault(); };  // zadrzi oznaku u titlu
  box.onclick = e => {
    const a = e.target.closest("[data-g-act]"), p = e.target.closest("[data-g-pick]");
    if (a && a.dataset.gAct === "cancel") h.onCancel();
    else if (a && a.dataset.gAct === "save") save();
    else if (p && h.pick) {
      const s = h.pick() || {}, k = p.dataset.gPick, el = G(box, k);
      if (!clean(s.text)) return showErr(box, ["Prvo označi tekst u titlu (u koloni originala ili u prevodu)."]);
      el.value = clean(s.text).slice(0, LIM[k]); showErr(box, []); el.focus();
    }
  };
  box.onkeydown = e => {
    if (e.key === "Enter" && e.target.matches("input") && !e.isComposing) { e.preventDefault(); save(); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); h.onCancel(); }  // ne zatvara i editor titla
  };
  syncForm(box, false);
}

function openForm(id) {
  const box = $("#memForm"), e = id ? MEM && MEM.glossary.find(x => x.id === id) : null;
  if (!box || (id && !e)) return;
  UI.edit = id || "new";
  const d = e ? {...e} : {type: UI.type || "tr", lang: "sr", scope: "global"};
  box.innerHTML = `<div class="mfh"><b>${e ? "Izmena stavke glosara" : "Nova stavka glosara"}</b></div>` + glFormHtml(d, {pfx: "mgf", id: e ? e.id : ""});
  box.hidden = false;
  fillChannels();
  const before = new Set(MEM ? MEM.glossary.map(x => x.id) : []);
  bindForm(box, {id: e ? e.id : "", list: () => MEM ? MEM.glossary : [], onCancel: closeForm,
    onSaved: (r, en) => {
      closeForm();
      toast(`${e ? "Izmenjeno u glosaru" : "Dodato u glosar"}: ${pairText(en)}`);
      const nid = e ? e.id : (arr(r && r.glossary).find(x => !before.has(x.id)) || {}).id;
      memRefresh().then(() => { const row = nid && $$("#memGlList [data-id]").find(x => x.dataset.id === nid); if (row && typeof hl === "function") hl(row); });
    }});
  box.scrollIntoView({block: "nearest", behavior: "smooth"});
  focusFirst(box);
}
function closeForm() { const b = $("#memForm"); if (b) { b.hidden = true; b.innerHTML = ""; } UI.edit = null; }

async function delGlossary(id) {
  const e = MEM && MEM.glossary.find(x => x.id === id);
  if (!e || !confirm(`Obrisati iz glosara ${pairText(e)}?`)) return;
  try {
    const r = await api("/api/memory/glossary", {op: "delete", entry: {id}});
    if (MEM && r && Array.isArray(r.glossary)) MEM.glossary = r.glossary;
    if (UI.edit === id) closeForm();
    toast(`Obrisano iz glosara: ${pairText(e)}`);
  } catch (x) { toast("Nije obrisano: " + errText(x, "Glosar"), {cls: "err", ms: 8000}); }
  memRefresh();
}

async function setRule(id, sel) {
  const r0 = MEM && MEM.rules.find(r => r.id === id);
  if (!r0) return;
  const prev = r0.state || "auto", row = sel.closest(".mrow");
  if (row) row.classList.add("busy");
  try {
    const res = await api("/api/memory/rule", {id, state: sel.value}), was = !!r0.active;
    Object.assign(r0, res && res.rule && typeof res.rule === "object" ? res.rule : {state: sel.value});
    // bez ponovnog ucitavanja: red ostaje na istom mestu (novi redosled pri sledecem ulasku u karticu)
    if (!!r0.active !== was) MEM.stats.rules_active = Math.max(0, (+MEM.stats.rules_active || 0) + (r0.active ? 1 : -1));
    render();
  } catch (e) {
    sel.value = prev;
    toast("Stanje pravila nije sačuvano: " + errText(e, "Memorija"), {cls: "err", ms: 8000});
    if (e.status === 404) memRefresh();
  } finally { if (row) row.classList.remove("busy"); }
}

async function setCorrection(id, cb) {
  const c = MEM && MEM.recent.find(x => x.id === id);
  if (!c) return;
  const dis = !cb.checked;
  try {
    await api("/api/memory/correction", {id, disabled: dis});
    c.disabled = dis;
    renderLists();
    memRefresh();  // pravila i brojevi se preracunavaju bez ove ispravke
  } catch (e) {
    cb.checked = !dis;
    toast("Nije sačuvano: " + errText(e, "Memorija"), {cls: "err", ms: 8000});
  }
}

// ---- editor titla: mini forma "＋ Glosar" (#edGlBox u zaglavlju editora)
// o: {text, where, entry, channel, pick(), onLink(e)}; vec otvorena forma: oznaka ide u odgovarajuce polje, bez oznake = zatvori
function memGlossaryPop(o = {}) {
  const box = $("#edGlBox");
  if (!box) return false;
  ensureCss();
  const text = clean(o.text), ent = o.entry || {};
  if (!box.hidden && $(".glf", box)) {
    if (!text) { memGlossaryClose(); return false; }
    const k = pickField(G(box, "type").value, ent.kind, o.where), el = G(box, k);
    el.value = text.slice(0, LIM[k]); showErr(box, []); el.focus(); el.select();
    return true;
  }
  const d = entryFromSelection({text, where: o.where, entry: ent, channel: o.channel});
  const tip = text ? "" : "Savet: označi reč u titlu (u originalu ili u prevodu) pa klikni „＋ Glosar“ ili „iz označenog“ — polje se popuni samo.";
  box.innerHTML = `<div class="mfh"><b class="grow">＋ Glosar memorije</b><button type="button" class="sm x" data-g-act="cancel" aria-label="Zatvori">✕</button></div>`
    + glFormHtml(d, {pfx: "egf", pick: !!o.pick, tip});
  box.hidden = false;
  fillChannels();
  bindForm(box, {id: "", list: () => MEM ? MEM.glossary : [], pick: o.pick, onCancel: memGlossaryClose,
    onSaved: (r, en) => {
      memGlossaryClose();
      toast(`Dodato u glosar: ${pairText(en)}`, {ms: 6000, link: ["#memorija", "Memorija"], onLink: o.onLink});
    }});
  focusFirst(box);
  return true;
}
function memGlossaryClose() {
  const b = $("#edGlBox");
  if (!b || b.hidden) return false;
  b.hidden = true; b.innerHTML = "";
  return true;
}

window.memRefresh = memRefresh;
window.memGlossaryPop = memGlossaryPop;
window.memGlossaryClose = memGlossaryClose;
window.memSaveToastText = (n, tot, ...responses) => saveToastText(n, tot, rulesActiveOf(...responses));
window.__memTest = {TYPE_LBL, STATES, STATE_LBL, CLASS_LBL, TR_LANGS, STT_LANGS, LIM, baseLang, langName, langLbl, typeLabel, stateLabel,
  classLabel, fmtTs, fold, normDump, filterRows, normGlossary, validateGlossary, pickField, entryFromSelection, ruleStatus, diffHtml,
  pairText, rulesActiveOf, saveToastText, glossaryRowHtml, ruleRowHtml, recentRowHtml, glossaryListHtml, rulesListHtml, recentListHtml,
  statsHtml, glFormHtml, langOptions, formHint};

// kartica je vec otvorena (npr. zapamcena poslednja kartica) pre nego sto je ovaj fajl ucitan
if (typeof TAB !== "undefined" && TAB === "memorija") memRefresh();
})();
