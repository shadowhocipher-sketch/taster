/* Taster 试读员 · 前端逻辑（零依赖、无构建）
 * 读数 → 人话只在 reading() 一处（ARCHITECTURE.md §4）。站上只有读数和固定模板翻译，没有观点。
 * 来源数据一律不可信：字符串都过 esc()，链接只放 http(s)，提示框用 textContent。 */
(function (W) {
"use strict";

// ---------------------------------------------------------------- 常量
const PASS = 0.65, FAIL = 0.35;            // 与走查同一把尺：中间 = 拿不准
const EMOS = ["紧张", "好笑", "揪心", "好奇", "痛快", "平淡", "困惑"];   // 🔴 颜色槽按尺上的固定顺序给，不按章数多少排，否则一筛选就换色（2026-09-25）
const HOME_EMOS = ["紧张", "揪心", "好笑", "好奇", "痛快"];
const HOME_DIMS = ["想看下一章", "中途放下", "跟得上"];
// 搜索里的口语 → 尺上的题。只做同义词映射，不做任何判断。
// 🔴 「最抓人」「最容易弃」这类评价词只能在这里当搜索别名，页面上的名字一律用尺上的说法拼（dimLabel，2026-09-25）
const ALIAS = {
  "想看下一章": ["抓人", "最抓人", "上头", "停不下来", "追更", "一口气"],
  "继续读": ["开头好", "入坑", "开篇"],
  "中途放下": ["弃书", "最容易弃", "劝退", "读不下去", "难啃"],
  "跟得上": ["好懂", "最好懂", "易读", "不迷路", "入门"],
  "喜欢主角": ["主角", "人设"],
};
const REPO = "https://github.com/shadowhocipher-sketch/taster";
const KEY_RE = /^[a-z][a-z0-9-]{1,30}:.{1,200}$/;      // 同 tools/core.py
// 能试读的来源和编号规则，同 tools/request.py（ARCHITECTURE.md §1）。前端只是先挡一道，真正把关在 request.py
const TASTE_SRC = ["wikisource-zh", "wikisource-en", "gutenberg"];
const ID_OK = { gutenberg: /^[1-9][0-9]{0,6}$/ };
// 不收 \p{M}：Python 的 \w 不含附加符号，分解形式的「Café」前端放行、request.py 会拒
const ID_RE = /^[\p{L}\p{N}_ .,'’·・()（）:：!！?？&、，—–「」『』-]+$/u;
function keyTasteable(key) {
  const i = key ? key.indexOf(":") : -1;
  if (i < 0) return false;
  const src = key.slice(0, i), id = key.slice(i + 1);
  return TASTE_SRC.includes(src) && (ID_OK[src] || ID_RE).test(id) && id === id.trim() && !/^[.-]/.test(id);
}
const TASTE_LIC = ["公版", "自由许可"];                 // 「美国公版」不在里面：只在美国是公版，不试读
const LICENSES = ["公版", "自由许可", "美国公版", "开放获取", "版权", "未知"];
const KINDS = { book: "书", paper: "论文", course: "课程", audio: "音频", video: "视频", other: "其他" };
const ACCESS = { read: "可直接读", borrow: "可借阅", preview: "可预览", metadata: "只有书目" };
const LANGS = { zh: "中文", en: "英文", ja: "日文", ko: "韩文", fr: "法文", de: "德文", es: "西班牙文", it: "意大利文",
  ru: "俄文", la: "拉丁文", pt: "葡萄牙文", nl: "荷兰文", el: "希腊文", ar: "阿拉伯文", sa: "梵文" };
const TIMEOUT = 20000, LIMIT = 20;

// ---------------------------------------------------------------- 小工具
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = p => Math.round(p * 100) + "%";
const enc = encodeURIComponent;
const avg = xs => xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
const fmt = n => typeof n === "number" ? n.toLocaleString("zh-CN") : "?";
const langName = l => LANGS[l] || (l ? l.toUpperCase() : "未知");
const lg = l => l && l !== "zh" && /^[a-z]{2,3}$/.test(l) ? ` lang="${l}"` : "";   // 外文书名、作者、简介让读屏换语音

function safeUrl(u) {                 // 只放 http(s)；javascript:、data: 之类一律丢掉
  if (typeof u !== "string") return null;
  u = u.trim();
  if (!/^https?:\/\//i.test(u)) return null;
  try { const x = new URL(u); return x.protocol === "https:" || x.protocol === "http:" ? x.href : null; }
  catch (e) { return null; }
}
const ext = (u, text, cls, attrs) => u ? `<a${cls ? ` class="${cls}"` : ""} href="${esc(u)}" target="_blank" rel="noopener noreferrer"${attrs || ""}>${text}</a>` : "";
const rHref = (key, n) => "#/r/" + enc(key) + (n != null ? "/" + n : "");
function issueUrl(key, title) {       // ARCHITECTURE.md §5 预填链接
  return REPO + "/issues/new?template=taste.yml&title=" + enc("试读：" + title) + "&key=" + enc(key);
}

// 繁简：zh.js 若在就用（另一个文件提供），不在就只认原文
const zhMemo = new Map();
function zhForms(s) {
  s = String(s || "");
  if (zhMemo.has(s)) return zhMemo.get(s);
  const out = new Set([s]), Z = W.ZH || W.zh || {};
  const add = v => { if (typeof v === "string" && v) out.add(v); else if (Array.isArray(v)) v.forEach(add); };
  try {
    for (const f of [W.zhVariants, Z.variants]) if (typeof f === "function") add(f(s));
    for (const k of ["toSimp", "toTrad", "toSimplified", "toTraditional", "t2s", "s2t"])
      if (typeof Z[k] === "function") add(Z[k](s));
  } catch (e) { /* zh.js 出错就当没有 */ }
  const r = [...out];
  if (zhMemo.size > 5000) zhMemo.clear();
  zhMemo.set(s, r);
  return r;
}
function zhCanon(s) {                 // 分组用的唯一写法：转简体（zh.js 的 zhVariants.toSimp）；没有就取变体里码位最小的
  const Z = W.ZH || W.zh || {};
  for (const f of [W.zhVariants && W.zhVariants.toSimp, Z.toSimp, Z.toSimplified, Z.t2s, Z.toS]) {
    if (typeof f === "function") { try { const v = f(s); if (typeof v === "string") return v; } catch (e) { /* 忽略 */ } }
  }
  return zhForms(s).slice().sort()[0];
}

// ---------------------------------------------------------------- 数据
const DATA = W.TASTER && Array.isArray(W.TASTER.shelf) ? W.TASTER : null;
const D = DATA || { built: null, rulers: {}, shelf: [] };
const SHELF = new Map(D.shelf.map(r => [r.key, r]));
const rulerOf = r => (r && D.rulers[r.ruler]) || null;
const readUnits = r => (r.units || []).filter(u => u.answers);
const totalUnits = r => r.units_total || (r.units || []).length;
const allUnits = () => D.shelf.flatMap(r => readUnits(r).map(u => ({ r, u })));
// 排行维度在页面上的名字：用尺上的「说法」拼成中性的描述
function dimLabel(d) {
  if (d.startsWith("感受:")) return "「主要感受：" + d.slice(3) + "」的章";
  const ru = Object.values(D.rulers).find(x => x["题"][d]), m = ru && ru["题"][d];
  if (m && m.q.type === "score") return "「" + m["说法"] + "」分数在满分 65% 以上的章";
  return "「" + (m ? m["说法"] : d) + "」判为是的章";
}
// 每章的原文地址。🔴 有的来源没有逐章地址：Gutenberg 的章 url 是 null，旧数据每章都是同一个书页，
// 会列出几十个一模一样的链接（2026-09-25）→ 全书只有一个地址、或等于全书地址的，不算逐章链接
function chapUrlOf(r) {
  const whole = safeUrl(r.read_url), urls = (r.units || []).map(u => safeUrl(u.url));
  const distinct = new Set(urls.filter(Boolean)).size;
  return u => { const x = safeUrl(u && u.url); return x && x !== whole && distinct > 1 ? x : null; };
}

// ---------------------------------------------------------------- 读数 → 人话（固定模板，不加观点；语义与旧版 index.html 一字不差）
function reading(ruler, k, a) {
  const m = ruler["题"][k] || {};
  if (!a) return { text: "未试读", cls: "unsure" };
  if (a.type === "noul") {
    const p = a.noul, yes = p >= PASS, no = p <= FAIL;
    const good = m["坏"] === "高" ? no : yes, bad = m["坏"] === "高" ? yes : no;
    return { text: pct(p) + (yes ? " · 是" : no ? " · 否" : " · 拿不准"),
             cls: good ? "good" : bad ? "bad" : "unsure", sure: yes || no, val: p };
  }
  if (a.type === "score") {
    const max = Object.keys(a.legend).length - 1, lab = a.legend[String(Math.round(a.score))];
    if (a.confidence < 0.5) return { text: "拿不准（信心 " + a.confidence.toFixed(2) + "）", cls: "unsure", sure: false };
    return { text: "≈ " + lab + "（" + a.score.toFixed(1) + "/" + max + "）", cls: a.score / max >= PASS ? "good" : a.score / max <= FAIL ? "bad" : "unsure",
             sure: true, val: a.score / max };
  }
  const top = Object.entries(a.probabilities || {}).sort((x, y) => y[1] - x[1]);
  if (!top.length) return { text: "未试读", cls: "unsure" };
  if (top[0][1] < 0.4) return { text: "拿不准（" + top.slice(0, 2).map(t => t[0] + " " + pct(t[1])).join(" / ") + "）", cls: "unsure", sure: false };
  return { html: emo(top[0][0]) + " " + pct(top[0][1]), text: top[0][0] + " " + pct(top[0][1]), sure: true, choice: top[0][0], val: top[0][1] };
}
function emoCls(e) { if (e === "拿不准") return "eu"; const i = EMOS.indexOf(e); return i < 0 ? "eo" : "e" + (i + 1); }
function emo(e) { return `<span class="emo"><i class="sw ${emoCls(e)}" aria-hidden="true"></i>${esc(e)}</span>`; }
function cell(r) { return r.html ? r.html : `<span class="${r.cls}">${esc(r.text)}</span>`; }
function sentence(ruler, k, a) { return esc(ruler["题"][k]["说法"]) + "：" + cell(reading(ruler, k, a)); }
function emoList(ru) {                // 感受选项按尺上的顺序
  const c = ru && ru["题"]["主要感受"] && ru["题"]["主要感受"].q.criteria;
  return c && !Array.isArray(c) ? Object.keys(c) : EMOS;
}

// 一题在已试读章上的汇总。拿不准的不算平均；
// 🔴 判得出的不到一半就不给平均数（2026-09-25 定）：三国「中途放下」10 章只有 2 章判得出，
// 只平均这 2 章会大字显示 34%，看着像结论，其实 8 章都拿不准
function stat(r, k) {
  const ru = rulerOf(r), out = { avg: null, sure: 0, yes: 0, no: 0, unsure: 0, read: 0 };
  if (!ru || !ru["题"][k]) return out;
  const vals = [];
  for (const u of readUnits(r)) {
    const a = u.answers[k];
    if (!a) continue;
    out.read++;
    const x = reading(ru, k, a);
    if (!x.sure) { out.unsure++; continue; }
    vals.push(x.val);
    if (a.type === "noul") { if (a.noul >= PASS) out.yes++; else out.no++; }
  }
  out.sure = vals.length;
  out.avg = out.sure * 2 >= out.read ? avg(vals) : null;
  return out;
}
function topEmotion(r) {
  const ru = rulerOf(r), c = {};
  let read = 0, sure = 0;
  if (!ru || !ru["题"]["主要感受"]) return null;
  for (const u of readUnits(r)) {
    const a = u.answers["主要感受"];
    if (!a) continue;
    read++;
    const x = reading(ru, "主要感受", a);
    if (x.choice) { sure++; c[x.choice] = (c[x.choice] || 0) + 1; }
  }
  const best = Object.entries(c).sort((a, b) => b[1] - a[1] || EMOS.indexOf(a[0]) - EMOS.indexOf(b[0]))[0];
  return { emo: best ? best[0] : null, n: best ? best[1] : 0, read, sure };
}
function unitEmo(ru, u) {             // null = 未试读；"拿不准"；或者感受名
  const a = u.answers && u.answers["主要感受"];
  if (!a) return null;
  const x = reading(ru, "主要感受", a);
  return x.choice || "拿不准";
}

// ---------------------------------------------------------------- 搜索结果：统一形状、分组、排序、筛选
function shelfRes(r, i) {
  return { key: r.key, source: r.source, sourceName: r.source_name || r.source, id: r.id, title: r.title,
    authors: r.author ? [r.author] : [], year: null, lang: r.lang || null, kind: "book",
    fiction: r.type === "小说" ? true : null, license: r.license || "未知", access: r.read_url ? "read" : "metadata",
    url: safeUrl(r.read_url), cover: null, blurb: null, tasteable: true, _shelf: r, _src: -1, _ord: i || 0 };
}
function normRes(x, c, srcIdx, ord) {
  if (!x || typeof x !== "object") return null;
  const s = v => (typeof v === "string" ? v : typeof v === "number" ? String(v) : "").trim();
  const title = s(x.title).slice(0, 300);
  if (!title) return null;
  const key = typeof x.key === "string" && KEY_RE.test(x.key.trim()) ? x.key.trim() : null;
  const kind = Object.prototype.hasOwnProperty.call(KINDS, x.kind) ? x.kind : "other";
  const license = LICENSES.includes(x.license) ? x.license : "未知";
  const access = Object.prototype.hasOwnProperty.call(ACCESS, x.access) ? x.access : "metadata";
  let year = typeof x.year === "number" ? x.year : /^-?\d{1,4}$/.test(s(x.year)) ? +s(x.year) : null;
  if (!Number.isInteger(year) || year < -3000 || year > 2100) year = null;
  const src = /^[a-z][a-z0-9-]{1,30}$/.test(s(x.source)) ? s(x.source) : c.id;
  let blurb = s(x.blurb) || null;
  if (blurb && blurb.length > 200) blurb = blurb.slice(0, 200) + "…";   // 约定 ≤ 200 字；超了只截断，不改写
  const authors = (Array.isArray(x.authors) ? x.authors : x.authors ? [x.authors] : []).map(s).filter(Boolean).slice(0, 8);
  const lang = s(x.lang).toLowerCase().split(/[-_]/)[0].slice(0, 8) || null;
  return { key, source: src, sourceName: s(x.sourceName).slice(0, 80) || c.name, id: s(x.id).slice(0, 200), title, authors, year,
    lang, kind, fiction: x.fiction === true ? true : x.fiction === false ? false : null, license, access,
    url: safeUrl(x.url), cover: safeUrl(x.cover), blurb,
    tasteable: keyTasteable(key) && TASTE_LIC.includes(license) && kind === "book",      // 按约定自己再算一遍，不信来源给的
    _shelf: key ? SHELF.get(key) || null : null, _src: srcIdx, _ord: ord };
}

function normTitle(t) {               // 小写、去副标题、去标点空格、繁简归一
  let s = String(t || "").trim().toLowerCase();
  const cut = s.search(/[:：(（[【;；]| — | - /);
  if (cut > 0) s = s.slice(0, cut);
  const out = zhCanon(s).replace(/[\p{P}\p{S}\s]/gu, "");
  return out || s;
}
function surname(a) {                 // 第一作者的姓：中文取首字（去朝代标记、取「·」后一段），西文取姓
  a = String(a || "").trim().replace(/^[[（(【〔][^\]）)】〕]{1,6}[\]）)】〕]\s*/, "").replace(/\s*[(（].*$/, "");
  if (!a) return "";
  if (/[㐀-鿿]/.test(a)) {
    const parts = a.split(/[·•・]/);
    return zhCanon(parts[parts.length - 1].trim()).charAt(0);
  }
  a = a.includes(",") ? a.split(",")[0] : a.split(/\s+/).pop();
  return a.toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
}
const groupKey = x => normTitle(x.title) + "|" + surname(x.authors[0]);

const canRequest = x => x.tasteable && x.fiction !== false;       // 目前只有小说尺
function reasonOf(x) {                // 不能试读的原因——只陈述事实
  if (x.license === "版权") return "有版权：只列出、给链接，不试读";
  if (x.license === "美国公版") return "仅在美国是公版：只列出、给链接，不试读";
  if (!x.key || !keyTasteable(x.key)) return "来源不支持试读";
  if (x.kind !== "book") return "这类资源还没有尺";
  if (x.fiction === false) return "这类资源还没有尺（目前只有小说尺）";
  if (!TASTE_LIC.includes(x.license)) return "许可不明：只试读公版和自由许可的";
  return "来源不支持试读";
}
function groupResults(items) {
  // 🔴 先按（来源序, 名次）排好再分组，两遍连：先按资源键、再按「书名|姓」，连到一起的并成一组。
  //    原来按到达顺序逐条挂，同一个搜索跑两次能分出不同的卡片（2026-09-25）
  const xs = items.slice().sort((a, b) => a._src - b._src || a._ord - b._ord);
  const up = xs.map((_, i) => i), shelfOf = xs.map(x => x._shelf || null);
  const find = i => { while (up[i] !== i) i = up[i] = up[up[i]]; return i; };
  const join = (a, b) => {
    a = find(a); b = find(b);
    if (a === b) return;
    // 🔴 两本不同的书架书永远不并：同一部作品从两个来源试读，各有各的读数，并了就有一本点不进去（2026-09-25）
    if (shelfOf[a] && shelfOf[b] && shelfOf[a] !== shelfOf[b]) return;
    if (b < a) [a, b] = [b, a];
    up[b] = a;
    shelfOf[a] = shelfOf[a] || shelfOf[b];
  };
  const first = new Map();
  const link = (k, i) => { if (first.has(k)) join(first.get(k), i); else first.set(k, i); };
  xs.forEach((x, i) => { if (x.key) link("k\n" + x.key, i); });
  xs.forEach((x, i) => link("t\n" + groupKey(x), i));
  const byRoot = new Map(), groups = [];
  xs.forEach((x, i) => {
    const root = find(i);
    let g = byRoot.get(root);
    if (!g) {
      const sh = shelfOf[root];
      g = { id: sh ? "k:" + sh.key : groupKey(x), items: [] };      // 书名键里没有「:」，两种 id 撞不上
      byRoot.set(root, g);
      groups.push(g);
    }
    g.items.push(x);
  });
  const rank = x => [x._src < 0 ? -1 : x._ord, x._src];
  for (const g of groups) {
    g.shelf = (g.items.find(x => x._shelf) || {})._shelf || null;
    g.onShelf = !!g.shelf;
    g.items.sort((a, b) => (!!b._shelf - !!a._shelf) || (b.tasteable - a.tasteable) || ((b.access === "read") - (a.access === "read"))
      || (a._src - b._src) || (a._ord - b._ord));
    g.primary = g.items[0];
    g.rank = g.items.map(rank).sort((a, b) => a[0] - b[0] || a[1] - b[1])[0];
    g.shelfOrd = g.onShelf ? D.shelf.indexOf(g.shelf) : 1e9;
    const ys = g.items.map(x => x.year).filter(y => y != null);
    g.year = ys.length ? Math.min(...ys) : null;
    g.score = g.onShelf ? stat(g.shelf, "想看下一章").avg : null;
    g.req = !g.onShelf ? g.items.find(canRequest) || null : null;
  }
  return groups;
}
function sortGroups(gs, how) {
  // 相关度：书架在前，其余按各家自己的名次轮流插（第 1 名们、第 2 名们……）
  const rel = (a, b) => (b.onShelf - a.onShelf) || (a.shelfOrd - b.shelfOrd) || (a.rank[0] - b.rank[0]) || (a.rank[1] - b.rank[1]);
  const out = gs.slice();
  if (how === "score") return out.sort((a, b) => ((b.score != null) - (a.score != null)) || ((b.score || 0) - (a.score || 0)) || rel(a, b));
  if (how === "year") return out.sort((a, b) => ((a.year == null) - (b.year == null)) || ((a.year || 0) - (b.year || 0)) || rel(a, b));
  return out.sort(rel);
}
const newFilters = () => ({ src: new Set(), kind: new Set(), lang: new Set(), read: false, tasted: false, req: false });
function passes(g, f) {
  const any = (set, get) => !set.size || g.items.some(x => set.has(get(x)));
  return any(f.src, x => x.source) && any(f.kind, x => x.kind) && any(f.lang, x => x.lang || "?")
    && (!f.read || g.items.some(x => x.access === "read")) && (!f.tasted || g.onShelf) && (!f.req || !!g.req);
}

// 书架本地查：每个词（含繁简变体）都要在书名/作者/键/标签里出现
function matchAll(hay, toks) {
  const hs = zhForms(hay).map(h => h.toLowerCase());
  return toks.every(t => zhForms(t).some(f => hs.some(h => h.includes(f.toLowerCase()))));
}
function searchShelf(q) {
  const toks = String(q).toLowerCase().split(/\s+/).filter(Boolean), res = [], units = [];
  if (!toks.length) return { res, units };
  D.shelf.forEach(r => {
    const hay = [r.title, r.author, r.key, r.id, r.source_name, r.type, ...(r.tags || [])].join(" ");
    if (matchAll(hay, toks)) res.push(r);
    for (const u of readUnits(r)) if (matchAll((u.label || "") + " " + (u.title || ""), toks)) units.push({ r, u });
  });
  return { res, units };
}
function readingHint(q) {             // 搜的是一种感受或一个维度 → 给排行入口
  const s = String(q).trim().toLowerCase();
  // 「最紧张」这种说法只当搜索别名认，入口的名字用中性的
  for (const e of EMOS) if (s === e || s === "最" + e) return { href: "#/rank/" + enc("感受:" + e), label: dimLabel("感受:" + e) };
  for (const [k, al] of Object.entries(ALIAS)) if (s === k || al.includes(s)) return { href: "#/rank/" + enc(k), label: dimLabel(k) };
  return null;
}

// ---------------------------------------------------------------- 连接器（connectors.js 并行写；?mock=1 用假书库）
let CONNS = [];
function listConnectors(mock) {
  const arr = mock ? W.MOCK_CONNECTORS : W.CONNECTORS;
  if (!Array.isArray(arr)) return [];
  return arr.filter(c => c && typeof c.search === "function").map(c => ({
    id: String(c.id || "?"), name: String(c.name || c.id || "?"), homepage: safeUrl(c.homepage), search: c.search.bind(c) }));
}

const CACHE = new Map();              // q → 搜索状态；回到同一个搜索不重查
function startSearch(q) {
  let S = CACHE.get(q);
  if (S) return S;
  for (const [k, old] of CACHE) if (old.pending > 0) { old.ctl.abort(); CACHE.delete(k); }   // 新搜索开始：没查完的旧搜索停掉
  const sh = searchShelf(q);
  S = { q, ctl: new AbortController(), conns: [], pending: 0, filters: newFilters(), sort: "rel", open: new Set(),
    items: sh.res.map((r, i) => shelfRes(r, i)), unitHits: sh.units, groups: [] };
  CACHE.set(q, S);
  if (CACHE.size > 12) CACHE.delete(CACHE.keys().next().value);
  CONNS.forEach((c, i) => runConn(S, c, i));
  return S;
}
function runConn(S, c, i) {
  const st = S.conns[i] = { id: c.id, name: c.name, state: "loading", n: 0, err: "" };
  S.pending++;
  const ctl = new AbortController();
  let timer;
  const stop = new Promise((_, rej) => {
    timer = setTimeout(() => { ctl.abort(); rej(new Error("超时")); }, TIMEOUT);
    S.ctl.signal.addEventListener("abort", () => { ctl.abort(); rej(new Error("已停止")); }, { once: true });
  });
  let p;
  try { p = Promise.resolve(c.search(S.q, { signal: ctl.signal, limit: LIMIT })); } catch (e) { p = Promise.reject(e); }
  Promise.race([p, stop]).then(list => {
    if (!Array.isArray(list)) throw new Error("返回的不是列表");
    const items = list.slice(0, 60).map((x, j) => normRes(x, c, i, j)).filter(Boolean);
    S.items = S.items.filter(x => x._src !== i).concat(items);
    st.state = "ok"; st.n = items.length;
  }).catch(e => {
    st.state = "err"; st.err = String((e && e.message) || e || "出错").slice(0, 120);
  }).finally(() => {
    clearTimeout(timer);
    S.pending--;
    if (curSearch === S) {
      paintSearch(S);
      announce(st.name + "：" + (st.state === "ok" ? st.n + " 条" : "没连上") + (S.pending ? "" : "。全部来源查完，共 " + S.shown + " 个资源"));
    }
  });
}

// ---------------------------------------------------------------- 页面片段
function badges(x, extra) {
  const lic = TASTE_LIC.includes(x.license) ? "lic-ok" : x.license === "版权" ? "lic-no" : x.license === "美国公版" ? "lic-us" : "";
  const licTip = x.license === "美国公版" ? ` title="仅在美国是公版，别的国家可能还有版权"` : "";
  return `<p class="badges"><span class="badge">${esc(x.sourceName)}</span><span class="badge ${lic}"${licTip}>${esc(x.license)}</span>`
    + `<span class="badge">${esc(ACCESS[x.access] || x.access)}</span><span class="badge">${esc(KINDS[x.kind] || x.kind)}${x.fiction === true ? " · 小说" : ""}</span>`
    + (x.lang ? `<span class="badge">${esc(langName(x.lang))}</span>` : "") + (extra || "") + `</p>`;
}
function coverHtml(g, title) {
  const src = g.items.map(x => x.cover).find(Boolean);
  return `<div class="cover" aria-hidden="true">${src ? `<img src="${esc(src)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">` : esc(Array.from(title)[0] || "?")}</div>`;
}
function authorsText(a) { return a.length > 3 ? a.slice(0, 3).join("、") + " 等" : a.join("、"); }

// 🔴 搜索结果每来一家就整片重画：凡是会被重画的链接、按钮都要带 data-fid，否则焦点掉回 <body>（2026-09-25）
const fid = (...xs) => ` data-fid="${esc(xs.join(":"))}"`;
function tasterBox(g) {
  // 🔴 用 div role=group，不用 <aside>：几十张卡就是几十个 complementary 地标，把真地标淹了（2026-09-25）
  if (g.onShelf) {
    const r = g.shelf, s = stat(r, "想看下一章"), te = topEmotion(r);
    // 没读数 = 未试读；读了但都判不出 = 拿不准。两者别混（同 reading() 的措辞）
    const big = s.avg != null ? pct(s.avg) : s.read ? "拿不准" : "未试读";
    return `<div class="tbox" role="group" aria-label="试读读数">
      <div class="tscore"><span class="tnum${s.avg == null ? " na" : ""}">${big}</span>
        <span class="tlab">读完想看下一章<small>${s.avg != null ? `判得出的 ${s.sure} 章平均` : s.read ? `${s.unsure}/${s.read} 章拿不准` : "还没有读数"}</small></span></div>
      <div class="tmeta">已试读 ${r.units_read || readUnits(r).length}/${totalUnits(r)} 章</div>
      ${te && te.emo ? `<div class="tmeta">章数最多的主要感受：${emo(te.emo)} ${te.n} 章</div>` : ""}
      <a class="btn ghost" href="${rHref(r.key)}"${fid("r", g.id)}>看试读报告</a></div>`;
  }
  if (g.req) {
    return `<div class="tbox" role="group" aria-label="试读">
      <p class="tnone">还没试读</p>
      <a class="btn" href="${esc(issueUrl(g.req.key, g.req.title))}" target="_blank" rel="noopener noreferrer"${fid("q", g.id)}>请试读</a>
      <p class="tnote">在 GitHub 提一个请求，维护者批准后试读</p></div>`;
  }
  return `<div class="tbox" role="group" aria-label="试读"><p class="tnone">不试读</p><p class="tnote">${esc(reasonOf(g.primary))}</p></div>`;
}
function card(g, S) {
  const p = g.primary, r = g.shelf, title = r ? r.title : p.title, tl = lg(r ? r.lang : p.lang);
  const href = r ? rHref(r.key) : p.key ? rHref(p.key) : p.url;
  const titleHtml = !href ? `<span${tl}>${esc(title)}</span>`
    : href.startsWith("#") ? `<a href="${esc(href)}"${fid("t", g.id)}><span${tl}>${esc(title)}</span></a>`
    : `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer"${fid("t", g.id)}><span${tl}>${esc(title)}</span> <span aria-hidden="true">↗</span><span class="sr-only">（在来源网站打开）</span></a>`;
  // 年份是各书库书目里记的（常是某个现代版本），不是成书年代：明代的三国显示「1960 年」会误导，写明出处
  const yr = g.year != null ? "书目记 " + (g.year < 0 ? "公元前 " + -g.year : g.year) + " 年" : "";
  const by = [p.authors.length ? `<span${lg(p.lang)}>${esc(authorsText(p.authors))}</span>` : "", esc(yr)].filter(Boolean).join(" · ");
  const bx = g.items.find(x => x.blurb);
  const others = g.items.slice(1);
  const open = S && S.open.has(g.id) ? " open" : "";
  return `<article class="card" data-gid="${esc(g.id)}">
    ${coverHtml(g, title)}
    <div class="body">
      <h3>${titleHtml}</h3>
      ${by ? `<p class="by">${by}</p>` : ""}
      ${badges(p, r ? `<span class="badge">书架 · ${esc(r.type || "")}</span>` : "")}
      ${bx ? blurbHtml(bx) : ""}
      ${r ? miniLanes(r) : ""}
      ${!r && p.url && href !== p.url ? `<p class="sub">${ext(p.url, "去 " + esc(p.sourceName) + " 看 ↗", "", fid("go", g.id))}</p>` : ""}
      ${others.length ? `<details class="offers" data-gid="${esc(g.id)}"${open}><summary${fid("o", g.id)}>另有 ${others.length} 个来源</summary><ul>
        ${others.map(x => offerLi(x, g.id, g.shelf)).join("")}</ul></details>` : ""}
    </div>
    ${tasterBox(g)}
  </article>`;
}
// 🔴 标签不写「简介」：Open Library 放的是正文首句，Internet Archive 放的是条目描述——只说是来源自带的（2026-09-25）
function blurbHtml(x) {
  return `<p class="blurb"><span class="bl">${esc(x.sourceName)} 自带文字</span><span${lg(x.lang)}>${esc(x.blurb)}</span></p>`;
}
function offerLi(x, gid, here) {         // here = 当前卡/页自己的书架书，不重复给它的报告链接
  const f = (t) => fid(t, gid || "", x.source, x.id);
  return `<li><span class="src">${esc(x.sourceName)}</span>
    <span class="t"><span${lg(x.lang)}>${esc(x.title)}${x.authors.length ? " · " + esc(authorsText(x.authors)) : ""}</span>${x.year != null ? " · " + esc(x.year) : ""}</span>
    <span class="sub">${esc(x.license)} · ${esc(ACCESS[x.access] || x.access)}</span>
    ${x._shelf && x._shelf !== here ? `<a href="${rHref(x.key)}"${f("or")}>看试读报告</a>` : ""}
    ${ext(x.url, "去看 ↗", "", f("ol"))}</li>`;
}

// 迷你感受行：只画出现过的感受，一行一种，位置 = 章序（全书，没读的地方留空）
function miniLanes(r) {
  const ru = rulerOf(r);
  if (!ru || !ru["题"]["主要感受"]) return "";
  const us = r.units || [], n = us.length;
  const pick = us.map(u => unitEmo(ru, u));
  const rows = [...emoList(ru), "拿不准"].map(e => [e, pick.reduce((c, x) => c + (x === e), 0)]).filter(x => x[1] > 0);
  if (!rows.length || !n) return "";
  const label = "每章主要感受：" + rows.map(([e, c]) => e + " " + c + " 章").join("，") + "（已试读 " + pick.filter(Boolean).length + "/" + n + " 章）";
  // 小图不做键盘导航：数字都在旁边写着，逐章明细一步就到「看试读报告」；鼠标悬停给原生提示，列出是哪几章
  const which = e => { const ns = us.filter((u, i) => pick[i] === e).map(u => u.n); return ns.length > 40 ? ns.slice(0, 40).join("、") + " 等" : ns.join("、"); };
  return `<div class="mini" role="img" aria-label="${esc(label)}">${rows.map(([e, c]) =>
    `<span class="ml">${esc(e)}</span><svg viewBox="0 0 ${n * 10} 10" preserveAspectRatio="none" aria-hidden="true" focusable="false">`
    + `<title>${esc(e + " " + c + " 章：第 " + which(e) + " 章")}</title><rect class="track" width="${n * 10}" height="10"/>`
    + pick.map((x, i) => x === e ? `<rect class="f-${emoCls(e)}" x="${i * 10}" width="8" height="10"/>` : "").join("")
    + `</svg><span class="mc">${c} 章</span>`).join("")}
    <span class="cap">一格一章，共 ${n} 章；空白是还没试读的章</span></div>`;
}

function unitTable(rows, keys, withBook) {
  if (!rows.length) return `<p class="sub">暂时没有。</p>`;
  const ru0 = rulerOf(rows[0].r);
  keys = keys || ["想看下一章", "中途放下", "跟得上", "主要感受"];
  return `<div class="tscroll" tabindex="0" role="region" aria-label="逐章读数表"><table>
    <thead><tr><th scope="col">章</th>${keys.map(k => `<th scope="col"${ru0 && ru0["题"][k] ? ` title="${esc(ru0["题"][k]["说法"])}"` : ""}>${esc(k)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map(({ r, u }) => {
      const ru = rulerOf(r);
      return `<tr><td><a href="${rHref(r.key, u.n)}"${lg(r.lang)}>${withBook ? esc(r.title) + " · " : ""}${esc(u.label)}</a><span class="t2"${lg(r.lang)}>${esc(u.title)}</span></td>
        ${keys.map(k => `<td class="v">${ru && ru["题"][k] ? cell(reading(ru, k, u.answers[k])) : "—"}</td>`).join("")}</tr>`;
    }).join("")}</tbody></table></div>`;
}

// ---------------------------------------------------------------- 图表（按容器实际宽度画，窄屏不缩字）
const CH = { L: 58, R: 58 };
function xTicks(us, cx, pw) {
  const n = us.length;
  if (!n) return [];
  const span = us[n - 1].n - us[0].n + 1, maxT = Math.max(2, Math.floor(pw / 46));
  const st = [1, 2, 5, 10, 20, 25, 50, 100, 200, 500, 1000].find(s => span / s <= maxT) || 2000;
  const out = [];
  us.forEach((u, i) => {
    if (i === 0 || i === n - 1 || u.n % st === 0) {
      if (out.length && cx(i) - cx(out[out.length - 1]) < 30) { if (i === n - 1) out.pop(); else return; }
      out.push(i);
    }
  });
  return out;
}
function curveChart(r, k, rng, width) {
  const ru = rulerOf(r), m = ru["题"][k];
  const us = r.units.filter(u => u.n >= rng[0] && u.n <= rng[1]), n = us.length;
  // 🔴 不设 300 的下限：320 宽的手机上图区只有 ~250，下限会把整页撑出横向滚动（2026-09-25）
  const Wd = Math.max(160, Math.floor(width)), H = 232, L = CH.L, R = CH.R, T = 12, B = 30;
  const pw = Wd - L - R, ph = H - T - B, cw = pw / Math.max(1, n);
  const X = i => L + cw * (i + 0.5), Y = v => T + ph * (1 - v);
  const vs = us.map(u => { const a = u.answers && u.answers[k]; return a && a.type === "noul" && typeof a.noul === "number" ? a.noul : null; });
  const showDots = cw >= 10;
  let path = "", pen = "M", dots = "";
  vs.forEach((v, i) => {
    if (v == null) { pen = "M"; return; }
    path += pen + X(i).toFixed(1) + " " + Y(v).toFixed(1) + " ";
    pen = "L";
    const iso = (i === 0 || vs[i - 1] == null) && (i === n - 1 || vs[i + 1] == null);
    if (showDots || iso) dots += `<circle class="dot" cx="${X(i).toFixed(1)}" cy="${Y(v).toFixed(1)}" r="4"/>`;
  });
  const hl = v => `<line class="${v === PASS || v === FAIL ? "thr" : v === 0 ? "basel" : "gridl"}" x1="${L}" x2="${L + pw}" y1="${Y(v)}" y2="${Y(v)}"/>`
    + `<text class="tick" x="${L - 8}" y="${Y(v) + 4}" text-anchor="end">${Math.round(v * 100)}%</text>`;
  const zl = (t, v) => `<text class="zone" x="${L + pw + 8}" y="${(Y(v) + 4).toFixed(1)}">${t}</text>`;
  const ticks = xTicks(us, X, pw).map(i => `<text class="tick" x="${X(i).toFixed(1)}" y="${H - 8}" text-anchor="middle">${us[i].n}</text>`).join("");
  const got = vs.filter(v => v != null).length;
  const aria = `${m["说法"]}：第 ${rng[0]}–${rng[1]} 章里 ${got} 章有读数。用左右方向键逐章查看，回车打开这一章。数值也在下方逐章读数表里。`;
  const svg = `<svg class="live" width="${Wd}" height="${H}" viewBox="0 0 ${Wd} ${H}" tabindex="0" role="group" aria-label="${esc(aria)}">
    <rect class="band" x="${L}" y="${Y(PASS)}" width="${pw}" height="${Y(FAIL) - Y(PASS)}"/>
    ${hl(1)}${hl(PASS)}${hl(FAIL)}${hl(0)}
    ${zl("是", (1 + PASS) / 2)}${zl("拿不准", 0.5)}${zl("否", FAIL / 2)}
    ${ticks}
    <line class="xh" y1="${T}" y2="${T + ph}" x1="0" x2="0" visibility="hidden"/>
    <path class="ln" d="${path}"/>${dots}
    <circle class="hdot" r="5" cx="0" cy="0" visibility="hidden"/>
  </svg>`;
  return { svg, geo: { W: Wd, H, L, T, ph, cw, n, X, Y }, us, vs,
    tip: i => { const a = us[i].answers && us[i].answers[k];
      return { lead: a ? reading(ru, k, a).text : "未试读", rows: [us[i].label + "　" + (us[i].title || "")] }; },
    has: i => vs[i] != null };
}
function lanesChart(r, rng, width) {
  const ru = rulerOf(r);
  const us = r.units.filter(u => u.n >= rng[0] && u.n <= rng[1]), n = us.length;
  const rows = [...emoList(ru), "拿不准"];
  // 🔴 一格至少 4px（2px 格 + 2px 缝）：缝没了，相邻两章同一种感受就连成一条数不清（2026-09-25）。
  //    放不下就让图比容器宽，容器自己横向滚动，不撑整页
  const L = CH.L, R = CH.R, rowH = 12, gy = 6, T = 2, B = 24, gap = 2;
  const Wd = Math.max(160, Math.floor(width), L + R + 4 * n);
  const H = T + rows.length * (rowH + gy) - gy + B, pw = Wd - L - R, cw = pw / Math.max(1, n);
  const X = i => L + cw * (i + 0.5);
  const pick = us.map(u => unitEmo(ru, u));
  let body = "";
  rows.forEach((e, j) => {
    const y = T + j * (rowH + gy), c = pick.reduce((s, x) => s + (x === e), 0);
    body += `<text class="lane-l" x="${L - 8}" y="${y + rowH - 2}" text-anchor="end">${esc(e)}</text>`
      + `<rect class="track" x="${L}" y="${y}" width="${pw}" height="${rowH}"/>`
      + pick.map((x, i) => x === e ? `<rect class="f-${emoCls(e)}" x="${(L + cw * i + gap / 2).toFixed(1)}" y="${y}" width="${(cw - gap).toFixed(1)}" height="${rowH}"/>` : "").join("")
      + `<text class="lane-c" x="${L + pw + 8}" y="${y + rowH - 2}">${c} 章</text>`;
  });
  const ticks = xTicks(us, X, pw).map(i => `<text class="tick" x="${X(i).toFixed(1)}" y="${H - 6}" text-anchor="middle">${us[i].n}</text>`).join("");
  const counts = rows.map(e => e + " " + pick.reduce((s, x) => s + (x === e), 0) + " 章").join("，");
  const aria = `每章主要感受，第 ${rng[0]}–${rng[1]} 章：${counts}。用左右方向键逐章查看，回车打开这一章。`;
  const svg = `<svg class="live" width="${Wd}" height="${H}" viewBox="0 0 ${Wd} ${H}" tabindex="0" role="group" aria-label="${esc(aria)}">
    ${body}${ticks}<rect class="colhi" x="0" y="${T - 2}" width="${cw}" height="${H - B - T + 4}" visibility="hidden"/></svg>`;
  return { svg, geo: { W: Wd, H, L, T, ph: H - B - T, cw, n, X }, us,
    tip: i => { const a = us[i].answers && us[i].answers["主要感受"];
      return { lead: a ? reading(ru, "主要感受", a).text : "未试读", rows: [us[i].label + "　" + (us[i].title || "")] }; },
    has: i => pick[i] != null };
}
function bindColumns(svg, c, r) {
  const g = c.geo, xh = svg.querySelector(".xh"), hd = svg.querySelector(".hdot"), hi = svg.querySelector(".colhi");
  let cur = -1;
  const idxAt = clientX => { const b = svg.getBoundingClientRect(); const i = Math.floor(((clientX - b.left) * g.W / b.width - g.L) / g.cw); return i >= 0 && i < g.n ? i : -1; };
  const href = i => c.us[i] && c.us[i].answers ? rHref(r.key, c.us[i].n) : null;
  function off() { cur = -1; hideTip(); for (const el of [xh, hd, hi]) if (el) el.setAttribute("visibility", "hidden"); }
  function mark(i, px, py) {
    if (i < 0) return off();
    cur = i;
    const x = c.geo.X(i);
    if (xh) { xh.setAttribute("x1", x); xh.setAttribute("x2", x); xh.setAttribute("visibility", "visible"); }
    if (hi) { hi.setAttribute("x", g.L + g.cw * i); hi.setAttribute("visibility", "visible"); }
    if (hd) {
      if (c.vs[i] != null) { hd.setAttribute("cx", x); hd.setAttribute("cy", g.Y(c.vs[i])); hd.setAttribute("visibility", "visible"); }
      else hd.setAttribute("visibility", "hidden");
    }
    const t = c.tip(i);
    showTip(px, py, t.lead, t.rows);
    return t;
  }
  function keyMark(i) {
    if (i < 0) return;
    const box = svg.parentElement;          // 图比容器宽时，键盘走到哪就把哪一章滚进来
    if (box && box.scrollWidth > box.clientWidth + 1) {
      const px = c.geo.X(i) * svg.getBoundingClientRect().width / g.W - box.scrollLeft;
      if (px < g.L || px > box.clientWidth - 24) box.scrollLeft += px - box.clientWidth / 2;
    }
    const b = svg.getBoundingClientRect();
    const t = mark(i, b.left + c.geo.X(i) * b.width / g.W, b.top + g.T * b.height / g.H);
    if (t) announce(t.lead + "，" + t.rows.join("，"));
  }
  const stops = [];
  for (let i = 0; i < g.n; i++) if (c.has(i)) stops.push(i);
  svg.addEventListener("pointermove", e => { const i = idxAt(e.clientX); if (i !== cur || e.pointerType === "mouse") mark(i, e.clientX, e.clientY); });
  svg.addEventListener("pointerleave", off);
  svg.addEventListener("click", e => { const h = href(idxAt(e.clientX)); if (h) location.hash = h; });
  svg.addEventListener("focus", () => keyMark(cur >= 0 ? cur : stops[0] ?? -1));
  svg.addEventListener("blur", off);
  svg.addEventListener("keydown", e => {
    if (!stops.length) return;
    const at = stops.indexOf(cur);
    let i;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") i = stops[Math.min(stops.length - 1, at + 1)];
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") i = stops[Math.max(0, at < 0 ? 0 : at - 1)];
    else if (e.key === "Home") i = stops[0];
    else if (e.key === "End") i = stops[stops.length - 1];
    else if (e.key === "Enter" && href(cur)) { location.hash = href(cur); return; }
    else if (e.key === "Escape") { off(); return; }
    else return;
    e.preventDefault();
    keyMark(i);
  });
}
function showTip(x, y, lead, rows) {
  const t = document.getElementById("tip");
  if (!t) return;
  t.textContent = "";
  const b = document.createElement("strong");
  b.textContent = lead;
  t.appendChild(b);
  for (const s of rows) { const d = document.createElement("div"); d.textContent = s; t.appendChild(d); }
  t.hidden = false;
  const w = t.offsetWidth, h = t.offsetHeight, vw = document.documentElement.clientWidth;
  const left = Math.max(8, Math.min(x + 14, vw - w - 8));
  let top = y - h - 14;
  if (top < 8) top = y + 18;
  t.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
}
function hideTip() { const t = document.getElementById("tip"); if (t) t.hidden = true; }

// 资源页的视图状态（范围、曲线看哪一题），按资源记住
const VIEW = new Map();
function viewOf(r) {
  if (VIEW.has(r.key)) return VIEW.get(r.key);
  const ru = rulerOf(r), done = readUnits(r).map(u => u.n);
  const noul = Object.keys(ru["题"]).filter(k => ru["题"][k].q.type === "noul");
  const all = [r.units[0] ? r.units[0].n : 1, r.units.length ? r.units[r.units.length - 1].n : 1];
  const read = done.length ? [Math.min(...done), Math.max(...done)] : all;
  const partial = read[0] !== all[0] || read[1] !== all[1];
  const v = { q: noul.includes("想看下一章") ? "想看下一章" : noul[0], noul, all, read, partial,
    range: partial && done.length / Math.max(1, r.units.length) < 0.5 ? "read" : "all" };
  VIEW.set(r.key, v);
  return v;
}
function drawCharts(force) {
  document.querySelectorAll("[data-chart]").forEach(el => {
    const r = SHELF.get(el.dataset.key);
    if (!r) return;
    // 🔴 用 getBoundingClientRect 向下取整，不用 clientWidth：后者四舍五入，262.67 → 263，图宽出 0.33px 就冒一条滚动条（2026-09-25）
    const w = Math.floor(el.getBoundingClientRect().width) || 600;
    if (!force && el.dataset.w === String(w)) return;
    el.dataset.w = String(w);
    const v = viewOf(r), rng = v.range === "read" ? v.read : v.all;
    const c = el.dataset.chart === "curve" ? curveChart(r, v.q, rng, w) : lanesChart(r, rng, w);
    el.innerHTML = c.svg;
    bindColumns(el.firstElementChild, c, r);
  });
}

// ---------------------------------------------------------------- 页面
function searchForm(q, slim) {
  return `<form class="search${slim ? " slim" : ""}" role="search" data-search>
    <label class="sr-only" for="q">搜书名、作者或关键词</label>
    <input id="q" name="q" type="search" value="${esc(q || "")}" placeholder="书名、作者、关键词……如 三国、Austen" autocomplete="off" enterkeyhint="search">
    <button type="submit">搜索</button></form>`;
}
function homePage() {
  const shelfGroups = groupResults(D.shelf.map((r, i) => shelfRes(r, i)));
  const names = ["书架", ...CONNS.map(c => c.name)];
  return { title: "", nav: "home", html: `
  <section class="hero"><div class="wrap">
    <h1>搜一本书，先看每一章的读数</h1>
    <p class="lead">一次查多家公开书库。书架上的书按章送给第三方判断模型读，每章得到一组读数——这里只放读数和固定模板的翻译，不放观点。</p>
    ${searchForm("")}
    <p class="srcs">同时查：${names.map(esc).join(" · ")}${CONNS.length ? "" : "（外部书库这次没加载上，只查书架）"}</p>
  </div></section>
  <div class="wrap">
    <h2 id="h-chips">按读数找章</h2>
    <ul class="chips" aria-labelledby="h-chips">
      ${HOME_EMOS.map(e => `<li><a class="chip" href="#/rank/${enc("感受:" + e)}"><i class="sw ${emoCls(e)}" aria-hidden="true"></i>${esc(dimLabel("感受:" + e))}</a></li>`).join("")}
      ${HOME_DIMS.map(k => `<li><a class="chip" href="#/rank/${enc(k)}">${esc(dimLabel(k))}</a></li>`).join("")}
    </ul>
    <h2>书架 · 已试读</h2>
    ${DATA ? "" : `<p class="empty">缺 data.js——先跑 <code>python tools/build.py</code>。</p>`}
    ${shelfGroups.map(g => card(g)).join("") || (DATA ? `<p class="sub">书架还是空的。</p>` : "")}
  </div>` };
}

let curSearch = null;
function searchPage(q) {
  const S = startSearch(q);
  curSearch = S;
  const hint = readingHint(q);
  return { title: "「" + q + "」", nav: "home", S, html: `
  <div class="searchbar"><div class="wrap">${searchForm(q, true)}</div></div>
  <div class="wrap">
    <div class="srch">
      <aside class="filters" id="filters" aria-label="筛选"></aside>
      <div class="results-col">
        <div class="res-head">
          <div class="res-bar">
            <h1>「${esc(q)}」<span class="sub" id="resCount"></span></h1>
            <button type="button" class="filter-toggle" aria-expanded="false" aria-controls="filters" data-act="filters">筛选</button>
            <label>排序 <select id="sort">
              <option value="rel">相关度</option><option value="score">读完想看下一章（平均）</option><option value="year">年代（早→晚）</option>
            </select></label>
          </div>
          <ul class="sst" id="sst" aria-label="各来源查询状态"></ul>
        </div>
        ${hint ? `<p class="hint">「${esc(q)}」对应读数里的一个维度：<a href="${hint.href}">看${esc(hint.label)} →</a></p>` : ""}
        <div id="results"></div>
        <div id="unitHits"></div>
      </div>
    </div>
  </div>`, after: () => { document.getElementById("sort").value = S.sort; paintSearch(S); } };
}
function keepFocus(el, fn) {          // 结果流式刷新时，别把焦点丢了
  const a = document.activeElement, id = a && el.contains(a) ? a.getAttribute("data-fid") : null;
  fn();
  if (id) { const b = el.querySelector(`[data-fid="${CSS.escape(id)}"]`); if (b) b.focus({ preventScroll: true }); }
}
function paintSearch(S) {
  const sst = document.getElementById("sst"), fl = document.getElementById("filters"), res = document.getElementById("results");
  if (!sst || !fl || !res) return;
  // 各家的状态小片带 tabindex=-1：点「重试」后按钮会消失，焦点落到这一片上，不掉回 <body>
  const chip = (cls, name, text, retry, i) => `<li class="st ${cls}"${i != null ? ` tabindex="-1"${fid("st", i)}` : ""}><span class="ic" aria-hidden="true"></span>${esc(name)} · ${esc(text)}${retry}</li>`;
  keepFocus(sst, () => {
    sst.innerHTML = chip("ok", "书架", S.items.filter(x => x._src < 0).length + " 条", "")
      + (CONNS.length ? S.conns.map((st, i) => chip(st.state, st.name, st.state === "loading" ? "查询中…" : st.state === "ok" ? st.n + " 条" : "没连上",
          st.state === "err" ? `<span class="sr-only">（${esc(st.err)}）</span> <button type="button" class="linkbtn" data-retry="${i}"${fid("retry", i)} title="${esc(st.err)}">重试</button>` : "", i)).join("")
        : chip("err", "外部书库", "没加载", ""));
  });
  const groups = S.groups = groupResults(S.items);
  keepFocus(fl, () => { fl.innerHTML = filterPanel(S, groups); });
  const shown = sortGroups(groups.filter(g => passes(g, S.filters)), S.sort);
  S.shown = shown.length;
  const rc = document.getElementById("resCount");
  if (rc) rc.textContent = ` · ${shown.length} 个资源${shown.length !== groups.length ? "（共 " + groups.length + "）" : ""}${S.pending ? " · 还在查 " + S.pending + " 家" : ""}`;
  keepFocus(res, () => {
    res.innerHTML = shown.length ? shown.map(g => card(g, S)).join("")
      : groups.length ? `<p class="empty">筛选后没有结果。<button type="button" class="linkbtn" data-act="clear"${fid("clear", "r")}>清除筛选</button></p>`
      : S.pending ? `<p class="empty">还在查……</p>` : `<p class="empty">没找到。换个关键词，或者试试繁体/简体、英文书名。</p>`;
  });
  const uh = document.getElementById("unitHits");
  if (uh && !uh.dataset.done) {
    uh.dataset.done = "1";
    uh.innerHTML = S.unitHits.length ? `<h2>书架上章名里有「${esc(S.q)}」的章</h2>` + unitTable(S.unitHits.slice(0, 50), null, true) : "";
  }
}
function filterPanel(S, groups) {
  const f = S.filters;
  const facet = (name, legend, opts, sel) => {
    const list = [...opts.entries()];
    return `<fieldset><legend>${legend}</legend>${list.length ? list.map(([v, o]) =>
      `<label><input type="checkbox" data-f="${name}" value="${esc(v)}" data-fid="${name}:${esc(v)}"${sel.has(v) ? " checked" : ""}>${esc(o.label)}<span class="c">${o.n}</span></label>`).join("")
      : `<p class="none">—</p>`}</fieldset>`;
  };
  const count = (get, label) => {
    const m = new Map();
    for (const g of groups) {
      const seen = new Set(g.items.map(get));
      for (const v of seen) { const o = m.get(v) || { label: label(v, g), n: 0 }; o.n++; m.set(v, o); }
    }
    return m;
  };
  const srcName = new Map();
  for (const g of groups) for (const x of g.items) if (!srcName.has(x.source)) srcName.set(x.source, x.sourceName);
  const sw = (name, label, on, n) => `<label><input type="checkbox" data-f="${name}" data-fid="${name}"${on ? " checked" : ""}>${label}<span class="c">${n}</span></label>`;
  const nf = f.src.size + f.kind.size + f.lang.size + f.read + f.tasted + f.req;
  return `<h2>筛选</h2>${nf ? `<button type="button" class="linkbtn" data-act="clear" data-fid="clear">清除筛选（${nf}）</button>` : ""}
    <fieldset><legend>试读</legend>
      ${sw("tasted", "已试读", f.tasted, groups.filter(g => g.onShelf).length)}
      ${sw("req", "可申请试读", f.req, groups.filter(g => g.req).length)}
      ${sw("read", "可直接读", f.read, groups.filter(g => g.items.some(x => x.access === "read")).length)}
    </fieldset>
    ${facet("src", "来源", count(x => x.source, v => srcName.get(v) || v), f.src)}
    ${facet("kind", "类型", count(x => x.kind, v => KINDS[v] || v), f.kind)}
    ${facet("lang", "语言", count(x => x.lang || "?", v => v === "?" ? "未知" : langName(v)), f.lang)}`;
}

function resourcePage(key, n) {
  const r = SHELF.get(key);
  if (!r) return offShelfPage(key);
  if (n != null) return chapterPage(r, n);
  const ru = rulerOf(r);
  if (!ru) return { title: r.title, html: `<div class="wrap"><p class="empty">这个资源的尺（${esc(r.ruler)}）不在数据里。</p></div>` };
  const done = readUnits(r), total = totalUnits(r), x = shelfRes(r, 0), v = viewOf(r);
  const g = findCached(key), others = g ? g.items.filter(i => !i._shelf) : [];
  const chapUrl = chapUrlOf(r), perChap = r.units.some(chapUrl);
  return { title: r.title, nav: "home", html: `<div class="wrap">
    <p class="crumb"><a href="#/">首页</a> › ${esc(r.source_name)} › ${esc(r.title)}</p>
    <header class="rhead">${coverHtml({ items: [x] }, r.title)}
      <div><h1${lg(r.lang)}>${esc(r.title)}</h1>
        <p class="by">${r.author ? `<span${lg(r.lang)}>${esc(r.author)}</span>${r.type ? " · " : ""}` : ""}${esc(r.type || "")}</p>
        ${badges(x)}</div></header>
    <section class="panel" aria-labelledby="h-where"><h2 id="h-where">去哪读</h2>
      <div class="where">${ext(safeUrl(r.read_url), `在${esc(r.source_name)}读全文 ↗`, "btn")}
        ${others.map(o => ext(o.url, esc(o.sourceName) + (o.title !== r.title ? "：" + esc(o.title) : "") + " ↗", "btn ghost")).join("")}</div>
      <details><summary>${perChap ? "每章原文链接" : "章目录"}（${r.units.length} 章）</summary>
        ${perChap ? "" : `<p class="sub">这个来源没有逐章的原文地址，原文在上面的全书链接里。</p>`}<ol class="chap-links">
        ${r.units.map(u => `<li${lg(r.lang)}>${ext(chapUrl(u), esc(u.label) + " " + esc(u.title)) || esc(u.label + " " + (u.title || ""))}${u.answers ? ` <a class="done" href="${rHref(r.key, u.n)}">读数</a>` : ""}</li>`).join("")}
      </ol></details>
    </section>
    <section class="panel" aria-labelledby="h-rep"><h2 id="h-rep">试读报告</h2>
      <p class="meta-line">第三方判断模型 · 模型版本 ${esc(ru["模型版本"])} · 尺 ${esc(ru["版本"])}（指纹 ${esc(ru["指纹"])}）· 已试读 ${done.length}/${total} 章${D.built ? " · 数据 " + esc(D.built) : ""}</p>
      ${done.length ? `${tiles(r)}
      <div class="controls">
        ${v.partial ? `<div><span class="seg-l" id="l-range">范围</span><span class="seg" role="group" aria-labelledby="l-range">
          <button type="button" data-act="range" data-v="read" aria-pressed="${v.range === "read"}">已试读的第 ${v.read[0]}–${v.read[1]} 章</button>
          <button type="button" data-act="range" data-v="all" aria-pressed="${v.range === "all"}">全书 ${v.all[0]}–${v.all[1]} 章</button></span></div>` : ""}
        ${v.noul.length > 1 ? `<div><span class="seg-l" id="l-q">曲线</span><span class="seg" role="group" aria-labelledby="l-q">
          ${v.noul.map(k => `<button type="button" data-act="q" data-v="${esc(k)}" aria-pressed="${v.q === k}">${esc(k)}</button>`).join("")}</span></div>` : ""}
      </div>
      ${v.q ? `<h3 class="chart-title" id="ct-curve">每章「${esc(ru["题"][v.q]["说法"])}」</h3>
      <p class="sub">每个点是一章的原始概率。虚线以上记「是」，以下记「否」，两线之间是「拿不准」。断开的地方是还没试读的章。</p>
      <div class="chart" data-chart="curve" data-key="${esc(r.key)}"></div>` : ""}
      ${ru["题"]["主要感受"] ? `<h3 class="chart-title">每章的主要感受</h3>
      <p class="sub">一行一种感受，一格一章，右边是章数。「拿不准」= 最高的选项不到 40%。</p>
      <div class="chart" data-chart="lanes" data-key="${esc(r.key)}"></div>` : ""}
      <h3>逐章读数</h3>
      ${unitTable(done.map(u => ({ r, u })), Object.keys(ru["题"]))}` : `<p class="empty">还没有试读过的章。</p>`}
    </section></div>` };
}
function tiles(r) {
  const ru = rulerOf(r), q = ru["题"], out = [];
  const noulTile = (k, hero) => {
    const s = stat(r, k);
    return `<div class="tile${hero ? " tile-hero" : ""}"><div class="tl">${esc(q[k]["说法"])}</div>
      <div class="tv">${s.avg != null ? pct(s.avg) : s.read ? "拿不准" : "—"}</div>
      <div class="ts">${s.avg != null ? `判得出的 ${s.sure} 章平均 · ` : s.read ? "判得出的不到一半，不给平均 · " : ""}是 ${s.yes} · 否 ${s.no} · 拿不准 ${s.unsure}</div></div>`;
  };
  if (q["想看下一章"]) out.push(noulTile("想看下一章", true));
  for (const k of ["中途放下", "跟得上"]) if (q[k]) out.push(noulTile(k));
  const read = readUnits(r).length, total = totalUnits(r), w = Math.min(100, 100 * read / Math.max(1, total));
  out.push(`<div class="tile"><div class="tl">已试读</div><div class="tv">${read}<span class="of">/${total} 章</span></div>
    <svg viewBox="0 0 100 8" preserveAspectRatio="none" aria-hidden="true" focusable="false"><rect class="m-track" width="100" height="8"/><rect class="m-fill" width="${w.toFixed(2)}" height="8"/></svg></div>`);
  const te = topEmotion(r);
  if (te) out.push(`<div class="tile"><div class="tl">章数最多的主要感受</div><div class="tv">${te.emo ? emo(te.emo) : "—"}</div>
    <div class="ts">${te.emo ? `判得出的 ${te.sure} 章里 ${te.n} 章` : `${te.read} 章都拿不准`}</div></div>`);
  return `<div class="tiles">${out.join("")}</div>`;
}
function chapterPage(r, n) {
  const ru = rulerOf(r), i = r.units.findIndex(u => String(u.n) === String(n)), u = r.units[i];
  if (!u || !ru) return { title: r.title, html: `<div class="wrap"><p class="crumb"><a href="${rHref(r.key)}">← ${esc(r.title)}</a></p><p class="empty">没有这一章。</p></div>` };
  const prev = r.units[i - 1], next = r.units[i + 1];
  const crit = m => {
    const c = m.q.criteria;
    if (!c) return "";
    return "　选项：" + (Array.isArray(c) ? c.map(esc).join(" / ") : Object.entries(c).map(([k, d]) => esc(k) + "（" + esc(d) + "）").join(" / "));
  };
  const extra = (k, a) => {
    if (!a) return "";
    if (a.type === "noul") return `<span class="raw">原始读数 ${Number(a.noul).toFixed(2)}</span>`;
    if (a.type === "score") {
      const leg = Object.entries(a.legend || {}).sort((x, y) => +x[0] - +y[0]);
      return `<span class="raw">分数 ${Number(a.score).toFixed(2)} · 信心 ${Number(a.confidence).toFixed(2)}</span>`
        + bars(leg.map(([s, lab]) => [lab, (a.probabilities || {})[s] || 0, "f-s1", null]));
    }
    const order = Object.keys(ru["题"][k].q.criteria || a.probabilities || {});
    return bars(order.map(e => [e, (a.probabilities || {})[e] || 0, "f-" + emoCls(e), e]));
  };
  return { title: u.label + " · " + r.title, nav: "home", html: `<div class="wrap">
    <p class="crumb"><a href="#/">首页</a> › <a href="${rHref(r.key)}">${esc(r.title)}</a> › ${esc(u.label)}</p>
    <h1${lg(r.lang)}>${esc(u.label)}　${esc(u.title)}</h1>
    ${chapUrlOf(r)(u) ? `<p>${ext(chapUrlOf(r)(u), "读这一章原文 ↗", "btn ghost")}</p>`
      : safeUrl(r.read_url) ? `<p>${ext(safeUrl(r.read_url), "在" + esc(r.source_name) + "读全书 ↗", "btn ghost")} <span class="sub">这个来源没有逐章的原文地址</span></p>` : ""}
    <section class="panel" aria-label="这一章的读数">
    ${u.answers ? Object.keys(ru["题"]).map(k => `<div class="qa">
        <p class="qs">${sentence(ru, k, u.answers[k])}${extra(k, u.answers[k])}</p>
        <p class="qq">问的是：「${esc(ru["题"][k].q.instructions)}」${crit(ru["题"][k])}</p></div>`).join("")
      : `<p class="empty">这一章还没试读。</p>`}
    </section>
    ${u.answers ? `<p class="note">送出 ${fmt(u["送出字数"])} 字 / 全章 ${fmt(u["字数"])} 字 · ${esc(u.utc)} · 尺 ${esc(ru["版本"])} · 指纹 ${esc(ru["指纹"])} · 第三方判断模型 · 模型版本 ${esc(ru["模型版本"])}</p>` : ""}
    <nav class="pager" aria-label="上一章、下一章">
      <span>${prev ? `<a href="${rHref(r.key, prev.n)}">← ${esc(prev.label)}</a>` : ""}</span>
      <span>${next ? `<a href="${rHref(r.key, next.n)}">${esc(next.label)} →</a>` : ""}</span></nav></div>` };
}
function bars(rows) {                 // 选项概率：固定顺序（尺上的顺序），数值写在条外
  // 🔴 不用 viewBox 拉伸：手机上条宽 100%，4px 圆角会被横向拉扁（2026-09-25）。
  //    按百分比画：整条 rx=4，再用半截方块盖住左端 → 数据端圆、基线端方（rx 会被夹到条长一半，半截正好盖住）
  return `<div class="bars">${rows.map(([lab, p, cls, e]) => {
    const w = Math.max(0, Math.min(1, p)) * 100;
    const fill = w <= 0 ? "" : `<rect class="${cls}" width="${w.toFixed(2)}%" height="10" rx="4"/><rect class="${cls}" width="${(w / 2).toFixed(2)}%" height="10"/>`;
    return `<span class="bl">${e ? emo(e) : esc(lab)}</span><svg width="100%" height="10" aria-hidden="true" focusable="false">
      <rect class="track" width="100%" height="10"/>${fill}</svg><span class="bv">${pct(p)}</span>`;
  }).join("")}</div>`;
}
function findCached(key) {
  for (const S of CACHE.values()) {
    const g = (S.groups.length ? S.groups : groupResults(S.items)).find(g => g.items.some(x => x.key === key));
    if (g) return g;
  }
  return null;
}
function offShelfPage(key) {
  const g = findCached(key);
  if (!g) {
    const valid = KEY_RE.test(key);
    return { title: "没有这个资源", html: `<div class="wrap"><p class="crumb"><a href="#/">首页</a></p>
      <h1>书架上没有这个资源</h1><p>资源键：<code>${esc(key)}</code></p>
      ${valid ? `<p><a href="#/s/${enc(key.slice(key.indexOf(":") + 1))}">去搜一下</a></p>` : ""}</div>` };
  }
  const x = g.items.find(i => i.key === key) || g.primary;
  const by = [x.authors.length ? `<span${lg(x.lang)}>${esc(authorsText(x.authors))}</span>` : "", x.year != null ? esc(x.year + " 年") : ""].filter(Boolean).join(" · ");
  return { title: x.title, nav: "home", html: `<div class="wrap">
    <p class="crumb"><a href="#/">首页</a> › ${esc(x.sourceName)} › ${esc(x.title)}</p>
    <header class="rhead">${coverHtml(g, x.title)}<div><h1${lg(x.lang)}>${esc(x.title)}</h1>${by ? `<p class="by">${by}</p>` : ""}${badges(x)}
      ${x.blurb ? blurbHtml(x) : ""}</div></header>
    <section class="panel" aria-labelledby="h-where"><h2 id="h-where">去哪读</h2>
      <ul class="offers-list">${g.items.map(i => offerLi(i, "p")).join("")}</ul></section>
    <section class="panel" aria-labelledby="h-rep"><h2 id="h-rep">试读</h2>
      ${canRequest(x) ? `<p>还没试读。</p><p><a class="btn" href="${esc(issueUrl(x.key, x.title))}" target="_blank" rel="noopener noreferrer">请试读</a></p>
        <p class="sub">在 GitHub 提一个请求（需要 GitHub 账号），维护者批准后试读，读完这里会出现每章读数。</p>`
      : `<p class="sub">${esc(reasonOf(x))}</p>`}</section></div>` };
}

function rankPage(dim) {
  const dims = [...Object.keys(ALIAS), ...EMOS.map(e => "感受:" + e)];
  const lab = dimLabel;
  let h = `<div class="wrap"><h1>排行</h1><ul class="chips" aria-label="选一个维度">${dims.map(d =>
    `<li><a class="chip" href="#/rank/${enc(d)}"${d === dim ? ' aria-current="page"' : ""}>${d.startsWith("感受:") ? `<i class="sw ${emoCls(d.slice(3))}" aria-hidden="true"></i>` : ""}${esc(lab(d))}</a></li>`).join("")}</ul>`;
  if (!dim) return { title: "排行", nav: "rank", html: h + `<p class="sub">选一个维度。只列判得出的章；拿不准的不上榜。</p></div>` };
  if (!dims.includes(dim)) return { title: "排行", nav: "rank", html: h + `<p class="empty">没有这个维度。</p></div>` };
  let rows;
  if (dim.startsWith("感受:")) {
    const e = dim.slice(3);
    rows = allUnits().filter(x => rulerOf(x.r) && rulerOf(x.r)["题"]["主要感受"])
      .map(x => ({ ...x, rd: reading(rulerOf(x.r), "主要感受", x.u.answers["主要感受"]) }))
      .filter(x => x.rd.choice === e).sort((a, b) => b.rd.val - a.rd.val);
    h += `<h2>主要感受是「${esc(e)}」的章</h2><p class="sub">只列判得出的（最高概率 ≥ 40%），按这个感受的概率排。</p>`;
  } else {
    rows = allUnits().filter(x => rulerOf(x.r) && rulerOf(x.r)["题"][dim]).map(x => ({ ...x, rd: reading(rulerOf(x.r), dim, x.u.answers[dim]) }))
      .filter(x => x.rd.sure && (x.rd.val >= PASS)).sort((a, b) => b.rd.val - a.rd.val);
    const ru = Object.values(D.rulers).find(ru => ru["题"][dim]), m = ru && ru["题"][dim];
    const score = m && m.q.type === "score";
    h += `<h2>${esc(m ? m["说法"] : dim)}：${score ? "分数在满分 65% 以上的章" : "判为「是」的章"}</h2>
      <p class="sub">${score ? "只列信心 ≥ 0.5 的，按分数从高到低。" : "只列 ≥ " + PASS + " 的，按概率从高到低。"}拿不准的不上榜。</p>`;
  }
  const keys = ["想看下一章", "中途放下", "跟得上", "主要感受"];
  if (!keys.includes(dim) && !dim.startsWith("感受:")) keys.unshift(dim);
  return { title: "排行 · " + lab(dim), nav: "rank", html: h + unitTable(rows.slice(0, 50), keys, true) + `</div>` };
}
function aboutPage() {
  const rulers = Object.values(D.rulers);
  const crit = c => !c ? "" : Array.isArray(c) ? `<br><span class="sub">选项：${c.map(esc).join(" / ")}</span>`
    : `<br><span class="sub">选项：${Object.entries(c).map(([k, d]) => esc(k) + "（" + esc(d) + "）").join(" / ")}</span>`;
  const TYPE = { noul: "是非题", score: "打分题", choice: "选择题" };
  return { title: "怎么读的", nav: "about", html: `<div class="wrap prose">
    <h1>这里的数是怎么来的</h1>
    <p>书架上每一章的原文，送给一个<b>第三方判断模型</b>，问一组固定的问题。模型只回数字：是非题回 0~1 的概率，打分题回分数和信心，选择题回每个选项的概率。
    页面上的文字是把这些数字<b>按固定模板</b>翻成人话，没有任何人或模型另写评语、简介或理由。</p>
    <h2>数字怎么翻成人话</h2>
    <ul><li>是非题：≥ ${PASS} 记「是」，≤ ${FAIL} 记「否」，中间记「拿不准」。颜色按「坏」的方向：比如「读到一半会放下」，「是」标红。</li>
      <li>打分题：信心 &lt; 0.5 记「拿不准」，否则记「≈ 最接近的选项（分数/满分）」。</li>
      <li>选择题：最高的选项概率 &lt; 40% 记「拿不准」，并列出前两名；否则记「选项 概率」。</li>
      <li>拿不准的不进排行，也不算进平均。卡片上的「读完想看下一章」是判得出的那些章的平均；判得出的不到一半时不给平均，只写「拿不准」和各有几章。</li>
      <li>同一题重跑，噪音约 ±0.04；两章相差不到 0.1，当作一样。</li>
      <li>问题的措辞一个字都不改——换措辞，同一章的分数能差 0.7。要改就换一把新尺，全部重读。</li>
      <li>每章最多送前若干字（见下面的「截断字数」）；章页上写了这一章送出多少字、全章多少字。</li></ul>
    ${rulers.map(ru => `<h2>尺 ${esc(ru["版本"])}</h2>
      <p class="sub">适用：${esc(ru["适用"])} · 第三方判断模型，模型版本 ${esc(ru["模型版本"])} · 每章最多送 ${fmt(ru["截断字数"])} 字 · 指纹 <code>${esc(ru["指纹"])}</code></p>
      <div class="tscroll"><table class="qlist"><thead><tr><th scope="col">题</th><th scope="col">类型</th><th scope="col">原文（一字不改）</th><th scope="col">页面上的说法</th></tr></thead><tbody>
      ${Object.entries(ru["题"]).map(([k, m]) => `<tr><td>${esc(k)}</td><td>${esc(TYPE[m.q.type] || m.q.type)}${m["坏"] ? `<br><span class="sub">坏的方向：${esc(m["坏"])}</span>` : ""}</td>
        <td>${esc(m.q.instructions)}${crit(m.q.criteria)}</td><td>${esc(m["说法"])}</td></tr>`).join("")}
      </tbody></table></div>`).join("") || `<p class="sub">（数据里没有尺。）</p>`}
    <h2>版权</h2>
    <p>本站只放读数、章名和指向原文的链接，正文不进仓库、不上站。只试读标「公版」和「自由许可」的作品；「美国公版」「版权」「未知」的只列出、给链接。拉正文只走各书库的官方接口，慢速、带标识，不接盗版站。</p>
    <h2>搜索从哪里查</h2>
    <p>搜索框在你的浏览器里直接查各家书库的公开接口：</p>
    <ul>${CONNS.map(c => `<li>${c.homepage ? ext(c.homepage, esc(c.name)) : esc(c.name)}</li>`).join("") || "<li>外部书库这次没加载上。</li>"}</ul>
    <h2>卡片上的字段从哪来</h2>
    <p><b>原样照搬来源的</b>：书名、作者、「××自带文字」（来源自带的简介或条目描述，只截断到 200 字、不改写；太短的编目碎片不放）、年份（书目里记的年份，常是某个现代版本的出版年，不是成书年代）。</p>
    <p><b>本站按固定规则推出来的</b>：许可、能不能直接读、是不是小说、类型。规则是机械的，不是法律意见：</p>
    <ul><li>许可 · 维基文库：维基文库只收公版或自由许可的作品，本站不逐本细分，一律标「公版」。</li>
      <li>许可 · Project Gutenberg：Gutendex 说有版权 → 「版权」；说不清 → 「未知」；说在美国是公版时，再看作者生卒年——每位作者都卒于 1955 年或更早（没有卒年的，生于 1850 年或更早；没有作者也算）→ 「公版」，否则 → 「美国公版」。1955 年是到 2026 年已满「作者死后 70 年」的线。</li>
      <li>许可 · Internet Archive：带公版标记或 CC0 → 「公版」；1930 年以前出版的文字 → 「美国公版」；其他 → 「未知」。借阅集合和百万图书计划只列 1930 年以前的文字。</li>
      <li>许可 · 其他：Open Library 有公版扫描本 → 「公版」，只能借阅或只对阅读障碍读者开放 → 「版权」；OpenAlex 开放获取的 → 「开放获取」；其余一律「未知」。</li>
      <li>「美国公版」= 只在美国是公版：只列出、给链接，不试读。</li>
      <li>是不是公版要看在哪个国家：同一本书在美国是公版，在按「作者死后 70 年」算的国家可能还有版权，反过来也有。卡片上的许可是按上面的规则算出来的，不代表在你所在的地方一定能用。</li>
      <li>能不能直接读：维基文库、Gutenberg、中国哲学书电子化计划记「可直接读」；Open Library 按它的电子书字段（公开 / 可借阅 / 没有）；Internet Archive 许可「未知」的一律「只有书目」，借阅集合里的记「可借阅」；OpenAlex 开放获取的记「可直接读」。</li>
      <li>是不是小说：只看来源自己的分类词（fiction、novels、小说……），没有明确的词就不标。</li>
      <li>类型（书、音频、视频、论文……）：按来源给的媒体类型或文献类型。</li></ul>
    <h2>怎么申请试读</h2>
    <ol><li>搜到一本公版或自由许可的小说，卡片右边会有「请试读」。</li>
      <li>点它会打开 GitHub 的请求表单（需要 GitHub 账号），资源键已经填好，提交即可。</li>
      <li>维护者加上「批准」标签后，自动试读，一次最多 200 章；没读完的下次接着读。</li>
      <li>读完后，这里的书架会出现这本书和每章读数。</li></ol>
    <p>${ext(REPO + "/issues/new?template=taste.yml", "直接打开请求表单 ↗")} · ${ext(REPO, "项目源码 ↗")}</p>
  </div>` };
}
function notFound() { return { title: "没有这个页面", html: `<div class="wrap"><h1>没有这个页面</h1><p><a href="#/">回首页</a></p></div>` }; }

// ---------------------------------------------------------------- 路由
function parseHash() {
  const h = location.hash;
  if (h && h !== "#" && !h.startsWith("#/")) return null;       // 页内锚点，不是路由
  return h.slice(2).split("/").map(s => { try { return decodeURIComponent(s); } catch (e) { return s; } });
}
let booted = false;
function route() {
  const p = parseHash();
  if (!p) return;
  hideTip();
  const [a, b, c] = p;
  let v;
  if (!a) v = homePage();
  else if (a === "s") {
    const q = p.slice(1).join("/").trim();
    if (!q) { location.replace("#/"); return; }
    v = searchPage(q);
  } else if (a === "r" && b) v = resourcePage(b, c || null);
  else if (a === "rank") v = rankPage(b || "");
  else if (a === "about") v = aboutPage();
  else v = notFound();
  if (a !== "s") curSearch = null;
  const app = document.getElementById("app");
  app.innerHTML = v.html;
  document.title = (v.title ? v.title + " · " : "") + "Taster 试读员";
  document.querySelectorAll("[data-nav]").forEach(el => {
    if (el.dataset.nav === v.nav) el.setAttribute("aria-current", "page"); else el.removeAttribute("aria-current");
  });
  if (v.after) v.after();
  drawCharts(true);
  window.scrollTo(0, 0);
  if (booted) { const h = app.querySelector("h1") || app; h.setAttribute("tabindex", "-1"); h.focus({ preventScroll: true }); }
  booted = true;
}
function announce(msg) {
  const el = document.getElementById("live");
  if (!el) return;
  el.textContent = "";
  setTimeout(() => { el.textContent = msg; }, 60);
}

function onClick(e) {
  const t = e.target.closest("[data-act],[data-retry],#skip");
  if (!t) return;
  if (t.id === "skip") { e.preventDefault(); const m = document.getElementById("main"); m.focus(); m.scrollIntoView(); return; }
  if (t.hasAttribute("data-retry")) {
    const S = curSearch, i = +t.getAttribute("data-retry");
    if (S && CONNS[i] && S.conns[i].state === "err") {
      runConn(S, CONNS[i], i);
      paintSearch(S);
      const c = document.querySelector(`#sst [data-fid="st:${i}"]`);
      if (c) c.focus();
      announce(S.conns[i].name + "：重新查询中");
    }
    return;
  }
  const act = t.dataset.act;
  if (act === "filters") {
    const f = document.getElementById("filters"), open = !f.classList.contains("open");
    f.classList.toggle("open", open);
    t.setAttribute("aria-expanded", String(open));
  } else if (act === "clear" && curSearch) {
    curSearch.filters = newFilters();
    paintSearch(curSearch);
    const fl = document.getElementById("filters");
    const first = fl && fl.querySelector("input");
    if (first) first.focus();
  } else if (act === "range" || act === "q") {
    const el = document.querySelector("[data-chart]"), key = el && el.dataset.key, r = key && SHELF.get(key);
    if (!r) return;
    const v = viewOf(r);
    if (act === "range") v.range = t.dataset.v; else v.q = t.dataset.v;
    t.parentElement.querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", String(b === t)));
    const ct = document.getElementById("ct-curve");
    if (ct) ct.textContent = "每章「" + rulerOf(r)["题"][v.q]["说法"] + "」";
    drawCharts(true);
  }
}
function onChange(e) {
  const t = e.target, S = curSearch;
  if (!S) return;
  if (t.id === "sort") { S.sort = t.value; paintSearch(S); return; }
  const f = t.dataset && t.dataset.f;
  if (!f) return;
  if (f === "read" || f === "tasted" || f === "req") S.filters[f] = t.checked;
  else if (S.filters[f]) { if (t.checked) S.filters[f].add(t.value); else S.filters[f].delete(t.value); }
  paintSearch(S);
  announce("筛选后 " + S.shown + " 个资源");
}
function onSubmit(e) {
  const f = e.target.closest("[data-search]");
  if (!f) return;
  e.preventDefault();
  const q = f.querySelector("input").value.trim();
  location.hash = q ? "#/s/" + enc(q) : "#/";
}
function start(mock) {
  CONNS = listConnectors(mock);
  const foot = document.getElementById("foot");
  foot.innerHTML = `<div class="wrap">${D.built ? `数据 ${esc(D.built)} 生成 · ` : ""}只有读数，没有观点 · 判断来自第三方判断模型 · <a href="#/about">方法与题目</a> · ${ext(REPO, "GitHub")}${mock ? ` · <b>开发模式：假书库</b>` : ""}</div>`;
  document.addEventListener("click", onClick);
  document.addEventListener("change", onChange);
  document.addEventListener("submit", onSubmit);
  document.addEventListener("toggle", e => {
    const d = e.target;
    if (curSearch && d.matches && d.matches("details.offers")) { const id = d.dataset.gid; if (d.open) curSearch.open.add(id); else curSearch.open.delete(id); }
  }, true);
  document.addEventListener("error", e => {             // 封面图挂了就换回占位
    const t = e.target;
    if (t && t.tagName === "IMG" && t.parentElement && t.parentElement.classList.contains("cover")) t.remove();
  }, true);
  let rt;
  addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => drawCharts(false), 150); });
  addEventListener("scroll", hideTip, { passive: true });
  addEventListener("hashchange", route);
  route();
}
function boot() {
  // 🔴 假书库只在本机认：发布出去的站上谁加 ?mock=1 都能让假书目带着真「请试读」按钮出现（2026-09-25）
  const local = ["localhost", "127.0.0.1", "[::1]", "::1"].includes(location.hostname);
  const mock = local && new URLSearchParams(location.search).get("mock") === "1";
  if (!mock) return start(false);
  const s = document.createElement("script");         // 假书库只在 ?mock=1 时加载，正式页面不碰
  s.src = "mock-connectors.js";
  s.onload = s.onerror = () => start(true);
  document.head.appendChild(s);
}

// 纯函数给 node 测试用；浏览器里照常启动
W.TasterApp = { reading, stat, topEmotion, normRes, shelfRes, groupResults, sortGroups, passes, newFilters, normTitle, surname,
  groupKey, searchShelf, readingHint, reasonOf, canRequest, issueUrl, safeUrl, esc, curveChart, lanesChart, miniLanes, card,
  tiles, unitTable, homePage, rankPage, aboutPage, resourcePage, chapterPage, xTicks, zhForms, listConnectors, PASS, FAIL };
if (typeof document !== "undefined" && document.getElementById) {
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
}
})(typeof window !== "undefined" ? window : globalThis);
