// 连接器测试：site/zh.js + site/connectors.js 装进 Node 跑。
//   离线：假数据验规则（许可、可试读、消歧义、限流冷却……），不联网
//   在线：每家真查一两次（量很小），验 Resource 形状、记下 CORS 头；连接器带了自定义头的站，另发一次 OPTIONS 看预检放不放行
//   对拍：可试读来源的编号规则和 tools/request.py 的 validate_key 逐条比（要 python；没有就跳过）
// 用法：node tools/test_connectors.mjs [--offline | --live] [--only id,id] [--strict]
// 形状不对、浏览器会被 CORS 拦、离线规则不符、和 request.py 对不上 → 退出码 1。
// 网络抽风（超时、502）默认只警告；--strict 时也算失败。
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const HERE = fileURLToPath(import.meta.url);
const ROOT = path.dirname(path.dirname(HERE));
const argv = process.argv.slice(2);
const flag = f => argv.includes(f);
const only = (argv[argv.indexOf("--only") + 1] || "").split(",").filter(s => argv.includes("--only") && s);
const STRICT = flag("--strict");

const realFetch = globalThis.fetch;
for (const f of ["site/zh.js", "site/connectors.js"]) vm.runInThisContext(readFileSync(path.join(ROOT, f), "utf8"), { filename: f });
const CONNECTORS = globalThis.CONNECTORS;
const byId = Object.fromEntries(CONNECTORS.map(c => [c.id, c]));

// ---------------------------------------------------------------- 形状（ARCHITECTURE.md §1 §2）
const FIELDS = ["key", "source", "sourceName", "id", "title", "authors", "year", "lang", "kind", "fiction", "license", "access", "url", "cover", "blurb", "tasteable"];
const KINDS = ["book", "paper", "course", "audio", "video", "other"];
const LICENSES = ["公版", "自由许可", "美国公版", "开放获取", "版权", "未知"];
const TASTE_LICENSES = ["公版", "自由许可"];                     // 同 Python TASTEABLE_LICENSES：美国公版不在里面
const ACCESS = ["read", "borrow", "preview", "metadata"];
const TASTE_SOURCES = new Set(["wikisource-zh", "wikisource-en", "gutenberg"]);
const KEY_RE = /^([a-z][a-z0-9-]{1,30}):(.{1,200})$/;          // 同 tools/core.py
const HTML = /<\/?[a-z][^>]*>/i;
// 编号规则：照 tools/request.py validate_key 独立再写一遍（不用 connectors.js 里那份），对拍见 pyCheck()
const ID_RE = /^[\p{L}\p{N}_ .,'’·・()（）:：!！?？&、，—–「」『』-]+$/u;
const ID_RULES = { gutenberg: /^[1-9][0-9]{0,6}$/ };
const WIN_RESERVED = /^(con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³]) *(\..*)?$/i;
const idOk = (src, id) => [...(src + ":" + id)].length <= 200 && (ID_RULES[src] || ID_RE).test(id) && id === id.trim()
  && !/^[.-]/.test(id) && !WIN_RESERVED.test(id);

function checkResource(r, c) {
  const bad = [], t = (ok, msg) => { if (!ok) bad.push(msg); };
  if (!r || typeof r !== "object") return ["不是对象"];
  for (const f of FIELDS) t(f in r, "缺字段 " + f);
  for (const k of Object.keys(r)) t(FIELDS.includes(k), "多余字段 " + k);
  t(r.source === c.id, "source ≠ 连接器 id");
  t(r.sourceName === c.name, "sourceName ≠ 连接器 name");
  t(typeof r.id === "string" && r.id.length > 0, "id 空");
  t(typeof r.title === "string" && r.title.trim().length > 0, "title 空");
  t(!HTML.test(r.title), "title 带 HTML");
  t(Array.isArray(r.authors) && r.authors.every(a => typeof a === "string" && a.length > 0), "authors 不是字符串数组");
  t(r.year === null || Number.isInteger(r.year), "year 不是整数/null");
  t(r.lang === null || /^[a-z]{2}$/.test(r.lang), "lang 不是 ISO 639-1：" + r.lang);
  t(KINDS.includes(r.kind), "kind 不认识：" + r.kind);
  t([true, false, null].includes(r.fiction), "fiction 不是 true/false/null");
  t(LICENSES.includes(r.license), "license 不认识：" + r.license);
  t(ACCESS.includes(r.access), "access 不认识：" + r.access);
  t(typeof r.url === "string" && /^https?:\/\/\S+$/.test(r.url), "url 不对：" + r.url);
  t(r.cover === null || (typeof r.cover === "string" && /^https:\/\/\S+$/.test(r.cover)), "cover 不是 https/null");
  t(r.blurb === null || (typeof r.blurb === "string" && r.blurb.length > 0 && [...r.blurb].length <= 200 && !HTML.test(r.blurb)), "blurb 超 200 字或带 HTML");
  if (r.key !== null) {
    t(typeof r.key === "string" && KEY_RE.test(r.key), "key 格式不对：" + r.key);
    t(r.key === r.source + ":" + r.id, "key ≠ source:id");
    t(TASTE_SOURCES.has(r.source), "不可试读的来源不该有 key");
    t(!TASTE_SOURCES.has(r.source) || idOk(r.source, r.id), "request.py 不收的编号不该有 key");
  } else t(!TASTE_SOURCES.has(r.source) || !idOk(r.source, r.id), "可试读来源缺 key");
  const want = r.key !== null && TASTE_LICENSES.includes(r.license) && r.kind === "book";
  t(r.tasteable === want, "tasteable 与规则不符（应为 " + want + "）");
  t(r.license !== "美国公版" || !r.tasteable, "美国公版不该可试读");
  // 许可不明的不说「可直接读」（Internet Archive 上打得开不等于能合法读）
  t(r.source !== "archive" || r.license !== "未知" || r.access === "metadata", "archive 许可未知却不是 metadata：" + r.access);
  return bad;
}
function checkConnector(c) {
  const bad = [], t = (ok, msg) => { if (!ok) bad.push(msg); };
  t(/^[a-z][a-z0-9-]{1,30}$/.test(c.id), "id 格式");
  t(typeof c.name === "string" && c.name, "name");
  t(/^https:\/\//.test(c.homepage), "homepage");
  t(Array.isArray(c.kinds) && c.kinds.length && c.kinds.every(k => KINDS.includes(k)), "kinds");
  t(Array.isArray(c.langs) && c.langs.length && c.langs.every(l => /^[a-z]{2}$/.test(l)), "langs");
  t(typeof c.search === "function", "search");
  return bad;
}
const w = s => [...String(s)].reduce((n, ch) => n + (/[ᄀ-￿]/.test(ch) ? 2 : 1), 0);
const pad = (s, n) => String(s) + " ".repeat(Math.max(0, n - w(s)));
const cut = (s, n) => { let o = ""; for (const ch of String(s)) { if (w(o + ch) > n - 1) return o + "…"; o += ch; } return o; };

// ---------------------------------------------------------------- 对拍 tools/request.py
// 键交给 validate_key：原样收下才算过（它会先去掉首尾空白，改了键也算不收）。没有 python / 导入失败 → 跳过
function pyValidate(keys) {
  const code = [
    "import sys, json",
    "sys.path.insert(0, 'tools')",
    "import request",
    "out = []",
    "for k in json.loads(sys.stdin.buffer.read().decode('utf-8')):",
    "    try: out.append(request.validate_key(k) == k)",
    "    except request.Rejected: out.append(False)",
    "print(json.dumps(out))",
  ].join("\n");
  for (const py of ["python", "python3"]) {
    const r = spawnSync(py, ["-c", code], { cwd: ROOT, input: JSON.stringify(keys), encoding: "utf8", env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
    if (r.status === 0) try { return JSON.parse(r.stdout.trim().split("\n").pop()); } catch (e) { /* 换下一个 */ }
  }
  return null;
}
async function pyCheck(extra = []) {
  const ids = {
    "wikisource-zh": ["三國演義", "《三國志演義》序", "关于古籍“标点”等著作权问题的答复", "三國演義 (消歧義)", "水滸傳（百回本）", "紅樓夢/第一回",
      "「測試」", "〈序〉", "史記·卷一", "Ⅷ", "x²", "😀", "a#b", "a:b", "100% 純"],
    "wikisource-en": ["Pride and Prejudice (1813)", "AUX", "aux.txt", "COM1", "Lpt² .x", "Café", "Café", " x", "-x", ".x", "A/B",
      "Tom's Adventures", "Who? What! 'Now'", "A — B – C", "a".repeat(186), "a".repeat(187)],
    gutenberg: ["1342", "01342", "12345678", "1234567", "abc"],
  };
  const keys = [...Object.entries(ids).flatMap(([s, xs]) => xs.map(x => s + ":" + x)), ...extra];
  const py = pyValidate(keys);
  if (!py) { console.log("  ⚠ 没法运行 python tools/request.py，对拍跳过"); return true; }
  const bad = keys.filter((k, i) => { const j = k.indexOf(":"); return idOk(k.slice(0, j), k.slice(j + 1)) !== py[i]; });
  if (bad.length) console.log("    对不上（JS 规则 ≠ request.py）：" + bad.map(k => JSON.stringify(k) + (py[keys.indexOf(k)] ? " py 收" : " py 拒")).join("、"));
  return !bad.length;
}

// ---------------------------------------------------------------- 离线
async function offline() {
  let fails = 0, n = 0;
  const ok = (cond, label) => { n++; if (!cond) { fails++; console.log("  ✗ " + label); } };
  const shape = (rs, c) => { for (const r of rs) for (const b of checkResource(r, c)) ok(false, c.id + " " + r.id + "：" + b); };
  const seen = [];
  const J = (body, status = 200) => new Response(typeof body === "string" ? body : JSON.stringify(body),
    { status, headers: { "content-type": "application/json", "access-control-allow-origin": "*" } });
  // 假装服务器不回。AbortSignal.timeout 的定时器不拖住 Node 进程，这里自己挂一个，不然进程直接退出
  const hang = signal => new Promise((_, no) => {
    const keep = setInterval(() => {}, 1000);
    signal.addEventListener("abort", () => { clearInterval(keep); no(signal.reason); }, { once: true });
  });
  const P = (u, k) => u ? new URL(u).searchParams.get(k) : undefined;
  const TPL = t => [{ ns: 10, title: "Template:" + t }];
  // Gutendex：许可规则（copyright + 作者生卒年）的各种情况，id 90xx 的书名就是它该得的许可
  const A = (name, birth_year, death_year) => ({ name, birth_year, death_year });
  const T = (id, title, authors, extra) => Object.assign({ id, title, authors, subjects: [], bookshelves: [], languages: ["en"], copyright: false,
    media_type: "Text", formats: {} }, extra);
  const DEX = [
    { id: 1342, title: "Pride and Prejudice", authors: [A("Austen, Jane", 1775, 1817)], subjects: ["Courtship -- Fiction"], bookshelves: ["Category: Novels"],
      languages: ["en"], copyright: false, media_type: "Text", formats: { "image/jpeg": "https://www.gutenberg.org/cache/epub/1342/pg1342.cover.medium.jpg" },
      summaries: ["这段是自动生成的摘要，不该出现"] },
    { id: 9001, title: "Some Modern Book", authors: [A("Wells, H. G. (Herbert George)", 1866, 1946)], subjects: [], bookshelves: ["Category: Biographies"],
      languages: ["en"], copyright: true, media_type: "Text", formats: {} },
    { id: 9002, title: "Statut inconnu", authors: [], subjects: [], bookshelves: [], languages: ["fr"], copyright: null, media_type: "Text", formats: {} },
    { id: 26301, title: "Pride and Prejudice", authors: [A("Austen, Jane", 1775, 1817)], subjects: ["Love stories"], bookshelves: [],
      languages: ["en"], copyright: false, media_type: "Sound", formats: {} },
    T(9003, "公版 卒于 1955", [A("Edge, Death", 1880, 1955)]),
    T(9004, "美国公版 卒于 1956", [A("Late, Death", 1880, 1956)]),
    T(9005, "公版 无卒年 生于 1850", [A("Early, Birth", 1850, null)]),
    T(9006, "美国公版 无卒年 生于 1851", [A("Later, Birth", 1851, null)]),
    T(9007, "公版 没有作者", []),
    T(9008, "美国公版 生卒年都没有", [{ name: "Nobody, Known" }]),
    T(9009, "美国公版 两位作者一位太晚", [A("Old, One", 1800, 1870), A("Young, Two", 1890, 1970)]),
    T(9010, "公版 公元前", [A("Plato", -428, -348)]),
    T(9011, "未知 copyright 缺字段", [A("Austen, Jane", 1775, 1817)], { copyright: undefined }),   // JSON 里没有这个键
  ];
  const ROUTES = [
    [/zh\.wikisource\.org.*generator=search/, () => J({ query: { pages: [
      { pageid: 5, ns: 0, title: "三國演義/附錄4", index: 5 },       // 故意乱序：要按 index 排
      { pageid: 1, ns: 0, title: "三國演義", index: 1 },
      { pageid: 2, ns: 0, title: "三國演義 (消歧義)", index: 2 },      // 没有页面属性，靠标题认
      { pageid: 3, ns: 0, title: "反三國演義/第01回", index: 3 },
      { pageid: 4, ns: 0, title: "张三、李四民间借贷纠纷民事一审民事判决书", index: 4 },
      { pageid: 6, ns: 0, title: "三國志", index: 6, pageprops: { disambiguation: "" } },
      { pageid: 7, ns: 0, title: "《三國志演義》序", index: 7 },       // request.py 不收《》→ 列出，但没有 key
      { pageid: 10, ns: 0, title: "新青年/第四卷/三國演義雜談", index: 8 },    // 主页面「新青年」对不上查询 → 不归
      { pageid: 11, ns: 0, title: "三國演義研究月刊/第1期", index: 9 },  // 对得上，但是期刊 → 不归
      { pageid: 12, ns: 0, title: "除非", index: 0 },                    // 只是正文提到：排到标题对得上的后面
    ] } })],
    [/zh\.wikisource\.org.*generator=links/, () => J({ query: { pages: [
      { pageid: 1, ns: 0, title: "三國演義" },
      { pageid: 9, ns: 0, title: "三國志演義" },
      { ns: 0, title: "不存在的版本", missing: true },
      { pageid: 8, ns: 0, title: "三國演義 (另一消歧義)", pageprops: { disambiguation: "" } },
    ] } })],
    [/en\.wikisource\.org.*generator=search/, (u, init) => P(u, "gsrsearch") === "neterr" ? Promise.reject(new TypeError("Failed to fetch")) : J({ query: { pages: [
      { pageid: 1, ns: 0, title: "Pride and Prejudice", index: 1, templates: TPL("Versions") },   // 多版本页：没有页面属性，靠模板认
      { pageid: 2, ns: 0, title: "Pride and Prejudice (1817)/Chapter 5", index: 2 },
      { pageid: 3, ns: 0, title: "1911 Encyclopædia Britannica/Austen, Jane", index: 3 },       // 词条 → 不归到整部百科
      { pageid: 4, ns: 0, title: "The Pride and Prejudice Review/Issue 1", index: 4 },          // 对得上，但是期刊
      { pageid: 5, ns: 0, title: "AUX", index: 5 },                                              // Windows 设备名：没有 key
    ] } })],
    [/en\.wikisource\.org.*generator=links/, () => J({ query: { pages: [
      { pageid: 11, ns: 0, title: "Pride and Prejudice (1813)" },
      { pageid: 12, ns: 0, title: "Pride and Prejudice (1817)" },
      { pageid: 13, ns: 0, title: "Pride and Prejudice (translations)", templates: TPL("Translations") },   // 二级多版本页
      { pageid: 14, ns: 0, title: "A Jane Austen Companion" },                                  // 出处：同时链了它的子页面
      { pageid: 15, ns: 0, title: "A Jane Austen Companion/Pride and Prejudice" },
      { pageid: 16, ns: 0, title: "Lady Susan" },                                               // 对不上查询 → 排在后面
    ] } })],
    [/gutendex\.com/, (u, init) => P(u, "search") === "hangdex" ? hang(init.signal) : P(u, "search") === "坏书" ? J({}, 500) : J({ count: DEX.length, results: DEX })],
    [/api\.ctext\.org/, u => P(u, "title") === "限额" ? J({ error: { code: "ERR_REQUEST_LIMIT", description: "limit" } })
      : P(u, "title") === "限流" ? new Response("{}", { status: 429, headers: { "retry-after": "99999" } }) : J({ books: [
      { title: "三國演義", urn: "ctp:sanguo-yanyi" }, { title: "毛宗崗批評本三國演義", urn: "ctp:wb310689" },
      { title: "三國演義", urn: "" }, { title: "三國演義", urn: "ctp:sanguo-yanyi" },
    ] })],
    [/openlibrary\.org/, u => (P(u, "q") !== null && [...P(u, "q")].length < 3) || P(u, "title") === "坏" || P(u, "author") === "坏"
      ? J({ detail: [{ msg: "Value error, Query too short, must be at least 3 characters" }] }, 422) : J({ docs: [
      { key: "/works/OL1W", title: "Pride and Prejudice", author_name: ["Jane Austen"], first_publish_year: 1813, language: ["eng", "ger", "fre"],
        cover_i: 14348537, ebook_access: "public", public_scan_b: true, subject: ["Fiction"],
        first_sentence: ["It is a truth universally acknowledged, that a single man in possession of a good fortune, must be in want of a wife."] },
      { key: "/works/OL2W", title: "Pride and Prejudice and Zombies", author_name: ["Seth Grahame-Smith"], first_publish_year: 2009,
        language: ["fre", "spa", "eng"], ebook_access: "borrowable", public_scan_b: false, subject: ["Parodies"], first_sentence: ["A copyrighted first line."] },
      { key: "/works/OL3W", title: "三国演义", author_name: ["罗贯中"], ebook_access: "no_ebook", public_scan_b: false },
    ] })],
    [/archive\.org\/advancedsearch/, () => J({ response: { docs: [
      { identifier: "pride_and_prejudice_librivox", title: "Pride and Prejudice", creator: "Jane Austen", date: "2006-03-13T00:00:00Z", language: "eng",
        licenseurl: "http://creativecommons.org/licenses/publicdomain/", mediatype: "audio", collection: ["librivoxaudio"],
        description: "<p>LibriVox recording of <b>Pride and Prejudice</b> &amp; more.</p>" },
      { identifier: "prideprejudice00aust", title: "Pride and prejudice", creator: ["Austen, Jane, 1775-1817"], date: "1894-01-01T00:00:00Z",
        language: "eng", mediatype: "texts", collection: ["americana"], description: "长".repeat(300) },
      // 借阅集合里的现代书：查询里已排除，万一漏回来也不列（Hachette v. IA）
      { identifier: "prideprejudice0000aust", title: "Pride and prejudice", date: "1981", mediatype: "texts", collection: ["internetarchivebooks", "inlibrary", "printdisabled"],
        description: "xvi, 759 pages : 20 cm" },
      { identifier: "lccn_1999", title: "Pride", date: "1999", mediatype: "texts", collection: ["americana", "printdisabled"] },
      { identifier: "nodate_lend", title: "Pride", mediatype: "texts", collection: ["inlibrary"] },
      // 借阅集合里 1930 年以前的：留着
      { identifier: "oldlend", title: "Pride and prejudice", date: "1905", mediatype: "texts", collection: ["internetarchivebooks", "inlibrary"], description: "26" },
      { identifier: "scan1", title: "Pride and prejudice", date: "1907", mediatype: "texts", collection: ["americana"],
        description: "Book digitized by Google from the library of Harvard University and uploaded to the Internet Archive by user tpb." },
      { identifier: "scan2", title: "Pride and prejudice", date: "1910", mediatype: "texts", collection: ["toronto"], description: ["4 v. in 1. 23 cm"] },
      { identifier: "scan3", title: "Orgullo y prejuicio", date: "1920", mediatype: "texts", collection: ["toronto"], description: "253 páginas" },
      { identifier: "film1", title: "Pride and Prejudice (1940)", year: "1940", mediatype: "movies", collection: ["feature_films"],
        licenseurl: "http://creativecommons.org/publicdomain/mark/1.0/" },
      // 百万图书计划：1930 年以后的、没年份的不列
      { identifier: "ul_modern", title: "Pride", year: "1965", mediatype: "texts", collection: ["universallibrary"] },
      { identifier: "ul_nodate", title: "水滸傳", mediatype: "texts", collection: ["universallibrary"] },
      { identifier: "ul_old", title: "水滸傳", date: "1900", mediatype: "texts", collection: ["universallibrary"], language: "chi",
        description: "明代章回小說，演梁山泊一百零八人故事。" },
      // 许可不明：只有书目；太短的描述不当简介
      { identifier: "lv_nolic", title: "Pride and Prejudice (version 2)", mediatype: "audio", collection: ["librivoxaudio"], description: "Includes index." },
      { identifier: "text_1950", title: "Pride and prejudice", date: "1950", mediatype: "texts", collection: ["americana"], description: "線裝四冊" },
      { identifier: "film_nolic", title: "Pride (1950)", year: "1950", mediatype: "movies", collection: ["feature_films"], description: "Pride" },
    ] } })],
    [/api\.openalex\.org/, u => /toomany/.test(P(u, "filter") || "")
      ? new Response("{}", { status: 429, headers: { "retry-after": "50000", "x-ratelimit-remaining": "0", "x-ratelimit-reset": "50000" } })
      : J({ results: [
      { id: "https://openalex.org/W1", display_name: "On <i>Pride and Prejudice</i>", publication_year: 2001, language: "en", type: "article",
        doi: "https://doi.org/10.1/x", open_access: { is_oa: true }, best_oa_location: { landing_page_url: "https://example.org/oa", pdf_url: null },
        authorships: [{ author: { display_name: "A. Scholar" } }] },
      { id: "https://openalex.org/W2", display_name: "《三国演义》“演义”考", publication_year: 2009, language: "zh", type: "article", doi: null,
        open_access: { is_oa: false }, best_oa_location: null, primary_location: { landing_page_url: "http://www.cqvip.com/x.html" }, authorships: [] },
      { id: "https://openalex.org/W3", display_name: null, type: "article" },
      { id: "https://openalex.org/W4", display_name: "A Book", type: "book", open_access: { is_oa: false }, doi: "https://doi.org/10.1/b", authorships: [] },
    ] })],
  ];
  const heads = new Map();                     // 地址 → 带的请求头
  globalThis.fetch = async (url, init = {}) => {
    const u = String(url);
    seen.push(u);
    heads.set(u, [...new Headers(init.headers)]);
    if (init.signal && init.signal.aborted) throw init.signal.reason;
    for (const [re, fn] of ROUTES) if (re.test(u)) return fn(u, init);
    throw new TypeError("离线测试没配这个地址：" + u);
  };
  globalThis.TASTER_WS_GAP_MS = 0;
  const run = async (id, q, o) => { const rs = await byId[id].search(q, o); shape(rs, byId[id]); return rs; };
  const get = (rs, id) => rs.find(r => r.id === id) || {};
  const ids = rs => JSON.stringify(rs.map(r => r.id));
  const fails_ = (p, re) => p.then(() => false, e => re.test(e.message) || (console.log("    实际报错：" + e.message), false));

  console.log("— 离线：简繁");
  const V = globalThis.zhVariants;
  ok(JSON.stringify(V("三国演义")) === '["三国演义","三國演義"]', "zhVariants 三国演义");
  ok(JSON.stringify(V("三國演義")) === '["三國演義","三国演义"]', "zhVariants 三國演義");
  ok(JSON.stringify(V("Pride")) === '["Pride"]', "zhVariants 纯英文只有一个");
  ok(V.toSimp("頭髮乾燥") === "头发干燥", "繁→简 多对一（髮→发、乾→干）");
  const tr = { 皇后: "皇后", 王后: "王后", 头发: "頭髮", 钟表: "鐘錶", 干将莫邪: "干將莫邪", 范进中举: "范進中舉", 于谦: "于謙", 千里: "千里", 说岳全传: "說岳全傳", 后来: "後來" };
  const bad = Object.entries(tr).filter(([s, t]) => V.toTrad(s) !== t).map(([s]) => s + "→" + V.toTrad(s));
  ok(!bad.length, "简→繁 常见词整词换（皇后不变後、范进不变範）" + (bad.length ? "，错：" + bad.join(" ") : ""));
  ok(V.toSimp("乾隆下江南") === "乾隆下江南", "繁→简 乾隆不变干隆");
  ok(JSON.stringify(V("吾輩ハ猫デアル")) === '["吾輩ハ猫デアル"]', "带假名的日文不转");

  console.log("— 离线：连接器");
  for (const c of CONNECTORS) for (const b of checkConnector(c)) ok(false, c.id + " 连接器元数据：" + b);
  ok(new Set(CONNECTORS.map(c => c.id)).size === CONNECTORS.length, "连接器 id 不重复");

  let rs = await run("wikisource-zh", "三国演义");
  ok(ids(rs) === '["三國演義","三國志演義","反三國演義","除非","《三國志演義》序"]', "维基文库：子页面归主页面（对得上查询、不是期刊的）、消歧义换成它链到的作品、去红链和判决书、全文命中排后，实际 " + ids(rs));
  ok(rs.every(r => r.license === "公版" && r.access === "read"), "维基文库全部公版、可读");
  ok(rs.filter(r => r.id !== "《三國志演義》序").every(r => r.tasteable), "维基文库作品可试读");
  ok(get(rs, "《三國志演義》序").key === null && get(rs, "《三國志演義》序").tasteable === false, "request.py 不收的标题（《》）：key=null、不可试读");
  ok(get(rs, "三國演義").url === "https://zh.wikisource.org/wiki/%E4%B8%89%E5%9C%8B%E6%BC%94%E7%BE%A9", "维基文库 url");
  ok(seen.some(u => /gsrsearch=%E4%B8%89%E5%9C%8B/.test(u)), "维基文库查了繁体写法");
  const sr = seen.find(u => u.includes("generator=search"));
  ok(P(sr, "prop") === "pageprops|templates" && P(sr, "tltemplates") === "Template:Versions|Template:Translations|Template:Disambiguation",
    "维基文库搜索带上多版本/译本/消歧义模板");
  const lk = seen.filter(u => u.includes("generator=links"));
  ok(lk.length === 1 && P(lk[0], "titles") === "三國演義 (消歧義)|三國志" && P(lk[0], "tltemplates"), "两种消歧义页（标题认的、页面属性认的）一次请求展开");
  const wsH = seen.filter(u => u.includes("wikisource.org")).map(u => JSON.stringify(heads.get(u)));
  ok(wsH.length && wsH.every(h => h === JSON.stringify([["api-user-agent", "Taster/0.2 (https://github.com/Keepexperiencing/taster)"]])),
    "维基文库每个请求都带 Api-User-Agent，别的头不带，实际 " + [...new Set(wsH)].join(" / "));

  seen.length = 0;
  rs = await run("wikisource-en", "Pride and Prejudice");
  ok(ids(rs) === '["Pride and Prejudice (1813)","Pride and Prejudice (1817)","Lady Susan","AUX"]',
    "en 多版本页（{{versions}}）换成各版本、百科词条和期刊不归、出处和二级多版本页去掉，实际 " + ids(rs));
  ok(!rs.some(r => r.id === "Pride and Prejudice"), "多版本页本身不出现（fetch.py 不收）");
  ok(get(rs, "Pride and Prejudice (1813)").tasteable && get(rs, "AUX").key === null && !get(rs, "AUX").tasteable, "版本页可试读；Windows 设备名没有 key");
  ok(P(seen.find(u => u.includes("generator=links")), "titles") === "Pride and Prejudice", "多版本页走消歧义展开");

  seen.length = 0;
  rs = await run("gutenberg", "Pride and Prejudice", { limit: 50 });
  ok(seen.every(u => !heads.get(u).length), "Gutendex 请求不带自定义头（带了就要 CORS 预检）");
  const g = get(rs, "1342");
  const lic = DEX.filter(b => b.id > 9002 && b.id < 9100).map(b => [String(b.id), b.title.split(" ")[0]]);
  const wrong = lic.filter(([id, want]) => get(rs, id).license !== want).map(([id, want]) => id + " 应为 " + want + " 实为 " + get(rs, id).license);
  ok(!wrong.length, "Gutenberg 许可：copyright + 作者生卒年（卒 ≤ 1955，或无卒年且生 ≤ 1850）" + (wrong.length ? "，错：" + wrong.join("；") : ""));
  const us = rs.filter(r => r.license === "美国公版");
  ok(us.length === 4 && us.every(r => r.key && !r.tasteable), "Gutenberg 美国公版：有 key、不可试读");
  ok(rs.filter(r => r.license === "公版" && r.kind === "book").every(r => r.tasteable), "Gutenberg 公版的书可试读");
  ok(g.license === "公版" && g.tasteable && g.fiction === true && g.cover && g.blurb === null && g.authors[0] === "Jane Austen", "Gutenberg 1342：公版、可试读、小说、有封面、不放自动摘要");
  ok(get(rs, "9001").license === "版权" && !get(rs, "9001").tasteable && get(rs, "9001").fiction === false && get(rs, "9001").authors[0] === "H. G. Wells", "Gutenberg copyright:true → 版权");
  ok(get(rs, "9002").license === "未知" && !get(rs, "9002").tasteable && get(rs, "9002").lang === "fr", "Gutenberg copyright:null → 未知");
  ok(get(rs, "26301").kind === "audio" && !get(rs, "26301").tasteable, "Gutenberg 有声书不可试读");
  seen.length = 0;
  const t0 = Date.now();
  ok(await fails_(byId.gutenberg.search("hangdex", { timeout: 300 }), /超时.*过一两分钟再搜/), "Gutendex 卡住 → 按时限报错、说明过会儿再搜");
  ok(Date.now() - t0 < 2000, "Gutendex 卡住时按时限返回");
  ok(seen.length === 1 && seen.every(u => u.startsWith("https://gutendex.com/")), "Gutenberg 只查 Gutendex，不碰 www.gutenberg.org（robots.txt 禁 /ebooks/search）");
  seen.length = 0;
  await run("gutenberg", "水浒");
  ok(seen.length === 2 && seen.some(u => P(u, "search") === "水滸") && seen.some(u => P(u, "search") === "水浒"), "Gutenberg 中文查询原文、繁体各查一次");
  seen.length = 0;
  await run("gutenberg", "吾輩ハ猫デアル");
  ok(seen.length === 1 && P(seen[0], "search") === "吾輩ハ猫デアル", "Gutenberg 带假名的不转繁体");
  rs = await run("gutenberg", "坏书", { limit: 50 });
  ok(rs.length === DEX.length, "Gutenberg 两路一路失败，用另一路的结果");

  rs = await run("ctext", "三国演义");
  ok(rs.length === 2 && get(rs, "sanguo-yanyi").url === "https://ctext.org/sanguo-yanyi" && get(rs, "wb310689").url === "https://ctext.org/wiki.pl?if=gb&res=310689", "ctext：去空 urn、去重、两种链接");
  ok(rs.every(r => r.key === null && r.license === "未知"), "ctext 只列出");
  ok(await byId.ctext.search("限额").then(() => false, () => true), "ctext 报错 JSON → throw");

  seen.length = 0;
  rs = await run("openlibrary", "Pride and Prejudice");
  ok(get(rs, "OL1W").license === "公版" && get(rs, "OL1W").access === "read" && get(rs, "OL1W").lang === "en" && get(rs, "OL1W").blurb === null
    && get(rs, "OL1W").cover === "https://covers.openlibrary.org/b/id/14348537-M.jpg", "Open Library 公版：read、封面、不放首句（是正文不是简介）");
  ok(!/first_sentence/.test(P(seen[0], "fields")), "Open Library 不再要 first_sentence");
  ok(get(rs, "OL2W").license === "版权" && get(rs, "OL2W").access === "borrow" && get(rs, "OL2W").blurb === null, "Open Library 可借阅：版权");
  ok(get(rs, "OL3W").license === "未知" && get(rs, "OL3W").access === "metadata" && get(rs, "OL3W").lang === null, "Open Library 无电子书：未知、metadata");
  ok(rs.every(r => r.key === null && !r.tasteable), "Open Library 不可试读");
  seen.length = 0;
  rs = await run("openlibrary", "水浒").catch(e => (console.log("    实际报错：" + e.message), []));
  ok(rs.length === 3 && seen.length === 2 && P(seen[0], "title") === "水浒" && P(seen[1], "author") === "水浒" && seen.every(u => P(u, "q") === null),
    "Open Library 少于 3 个字：q= 会 422，改查 title= 和 author=");
  ok(await fails_(byId.openlibrary.search("坏"), /HTTP 422/), "Open Library 两路都失败 → throw");

  seen.length = 0;
  rs = await run("archive", 'Pride: "and" (Prejudice)', { limit: 50 });
  const q = P(seen[0], "q"), tq = (q.match(/^\(title:\(([^)]*)\)/) || [])[1];
  ok(/collection:\(americana OR /.test(q) && tq && !/["():]/.test(tq), "Archive：只查策展集合、用户输入里的 Lucene 符号去掉，实际 q=" + q);
  ok(q.includes("-collection:(inlibrary OR printdisabled OR lendinglibrary OR universallibrary)") && q.includes("year:[1 TO 1929]"),
    "Archive 查询：借阅集合、百万图书计划只要 1930 年以前的");
  const lv = get(rs, "pride_and_prejudice_librivox");
  ok(lv.license === "公版" && lv.kind === "audio" && lv.access === "read" && lv.blurb === "LibriVox recording of Pride and Prejudice & more.", "Archive LibriVox：公版标记、音频、简介去标签");
  const pa = get(rs, "prideprejudice00aust");
  ok(pa.license === "美国公版" && pa.access === "read" && !pa.tasteable && pa.year === 1894 && [...pa.blurb].length === 200, "Archive 1894 年文字 → 美国公版（不是公版）；简介截到 200");
  ok(!["prideprejudice0000aust", "lccn_1999", "nodate_lend"].some(id => get(rs, id).id), "Archive 借阅集合里的现代书 / 没年份的不列");
  ok(get(rs, "oldlend").access === "borrow" && get(rs, "oldlend").license === "美国公版" && get(rs, "oldlend").blurb === null, "Archive 借阅集合里 1905 年的：留、borrow、美国公版、纯数字不当简介");
  ok(["scan1", "scan2", "scan3"].every(id => get(rs, id).id && get(rs, id).blurb === null && get(rs, id).license === "美国公版"), "Archive 扫描说明、载体形态（23 cm、253 páginas）不当简介");
  ok(get(rs, "film1").kind === "video" && get(rs, "film1").license === "公版", "Archive 电影公版标记 → video 公版");
  ok(!get(rs, "ul_modern").id && !get(rs, "ul_nodate").id, "Archive 百万图书计划 1930 年以后的、没年份的不列");
  ok(get(rs, "ul_old").license === "美国公版" && get(rs, "ul_old").blurb === "明代章回小說，演梁山泊一百零八人故事。", "Archive 百万图书计划 1900 年的留；中文简介够 10 字就留");
  ok(["lv_nolic", "text_1950", "film_nolic"].every(id => get(rs, id).license === "未知" && get(rs, id).access === "metadata"), "Archive 许可未知 → 只有书目，不说可直接读");
  ok(["lv_nolic", "text_1950", "film_nolic"].every(id => get(rs, id).blurb === null), "Archive 太短的描述（西文 < 20 字、中文 < 10 字）不当简介");
  seen.length = 0;
  await run("archive", "水浒");
  const hq = P(seen[0], "q");
  ok(hq.includes("title:(水浒)") && hq.includes("title:(水滸)"), "Archive 中文查询简繁一起查，实际 q=" + hq);

  seen.length = 0;
  rs = await run("openalex", "Pride and Prejudice");
  ok(P(seen[0], "filter") === "title_and_abstract.search:Pride and Prejudice" && P(seen[0], "search") === null, "OpenAlex 只查标题+摘要（search= 是全文）");
  ok(rs.length === 3 && get(rs, "W1").title === "On Pride and Prejudice" && get(rs, "W1").license === "开放获取" && get(rs, "W1").url === "https://example.org/oa"
    && get(rs, "W1").kind === "paper" && get(rs, "W1").fiction === false, "OpenAlex OA：去标签、开放获取、OA 链接、没标题的丢掉");
  ok(get(rs, "W2").license === "未知" && get(rs, "W2").access === "metadata" && get(rs, "W2").url === "http://www.cqvip.com/x.html", "OpenAlex 非 OA");
  ok(get(rs, "W4").kind === "book" && get(rs, "W4").fiction === null, "OpenAlex book 类型");
  seen.length = 0;
  await run("openalex", "a,b|c !d:e");
  ok(P(seen[0], "filter") === "title_and_abstract.search:a b c d e", "OpenAlex filter 里的 , | ! : 换成空格，实际 " + P(seen[0], "filter"));

  ok(await byId.openlibrary.search("x", { signal: AbortSignal.abort() }).then(() => false, e => e.name === "AbortError"), "调用方取消 → AbortError");
  ok(JSON.stringify(await byId.openalex.search("   ")) === "[]", "空查询 → []");
  ok(await fails_(byId.openalex.search("toomany"), /429.*额度用完.*小时/), "OpenAlex 额度用完的 429 → 报「额度用完、约几小时后」");
  let before = seen.length;
  ok(await fails_(byId.openalex.search("again"), /额度用完.*约 1[34] 小时后再试/) && seen.length === before, "额度用完后按 Reset 冷却（不封顶 600 秒），不再发请求");
  ok(await fails_(byId.ctext.search("限流"), /429/), "429 → throw");
  before = seen.length;
  ok(await fails_(byId.ctext.search("again"), /限流中，约 10 分钟后再试/) && seen.length === before, "普通 429：Retry-After 封顶 600 秒，冷却中不再发请求");
  // 放最后：en 维基文库从这里开始冷却
  ok(await fails_(byId["wikisource-en"].search("neterr"), /没连上（可能被限流）/), "维基媒体 TypeError（报错不带 CORS 头）→ 当限流");
  before = seen.length;
  ok(await fails_(byId["wikisource-en"].search("again"), /可能被限流.*后再试/) && seen.length === before, "维基媒体 TypeError 之后冷却一分钟，不再发请求");
  ok(await byId["wikisource-zh"].search("三国演义").then(rs => rs.length > 0, () => false), "冷却只管那一个站");

  ok(await pyCheck(), "编号规则和 tools/request.py validate_key 对拍");

  console.log(`  ${n - fails}/${n} 通过`);
  return fails;
}

// ---------------------------------------------------------------- 在线
const ORIGIN = "https://keepexperiencing.github.io";
const UA = "Taster/0.2 (https://github.com/Keepexperiencing/taster; connector test; few requests)";
const LIVE = {
  "wikisource-zh": ["三国演义", "水浒"], "wikisource-en": ["Pride and Prejudice", "Jane Austen"],
  gutenberg: ["Pride and Prejudice", "水浒"], ctext: ["三国演义", "水浒"],
  openlibrary: ["三国演义", "Pride and Prejudice"], archive: ["Pride and Prejudice", "水浒"],
  openalex: ["三国演义", "Pride and Prejudice"],
};
// 查到东西时必须包含的（查不到算网络问题，只警告）
// 🔴 en「Pride and Prejudice」是 {{versions}} 多版本页，fetch.py 不收，得是具体版本（2026-09-25）
const MUST = { "wikisource-zh|三国演义": "三國演義", "wikisource-en|Pride and Prejudice": "Pride and Prejudice (1813)", "ctext|三国演义": "sanguo-yanyi" };
// 不许出现的编号
const MUST_NOT = { "wikisource-en|Pride and Prejudice": /^Pride and Prejudice$/, "wikisource-en|Jane Austen": /Encyclop|Dictionary of|Britannica|Reference Work/ };

async function live() {
  let fails = 0, errs = 0;
  let log = [];
  const tasteKeys = new Set();
  // 连接器自己带了请求头 → 浏览器会先发 OPTIONS 预检；Node 的 fetch 不发，这里每个站替它发一次、看放不放行
  const preflight = new Map();
  async function checkPreflight(url, names) {
    const host = new URL(url).host;
    if (preflight.has(host)) return;
    const p = { names, ok: false, note: "" };
    preflight.set(host, p);
    try {
      const r = await realFetch(url, { method: "OPTIONS", headers: { Origin: ORIGIN, "User-Agent": UA,
        "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": names.join(",") } });
      const acao = r.headers.get("access-control-allow-origin"), acah = (r.headers.get("access-control-allow-headers") || "").toLowerCase();
      const allowed = acah.split(",").map(s => s.trim());
      p.ok = r.ok && (acao === "*" || acao === ORIGIN) && names.every(n => allowed.includes(n) || allowed.includes("*"));
      p.note = `${r.status} allow-origin=${acao} allow-headers=${acah || "—"}`;
    } catch (err) { p.ok = null; p.note = "预检没连上：" + (err && err.message || err); }   // 网络问题：只警告
  }
  globalThis.fetch = async (url, init = {}) => {
    const own = [...new Headers(init.headers).keys()];
    if (own.length) await checkPreflight(url, own);
    const h = new Headers(init.headers); h.set("Origin", ORIGIN); h.set("User-Agent", UA);
    const e = { host: new URL(url).host, status: "ERR", acao: null };
    log.push(e);
    try {
      const r = await realFetch(url, { ...init, headers: h });
      e.status = r.status; e.acao = r.headers.get("access-control-allow-origin");
      return r;
    } catch (err) {
      // 超时、调用方取消都记成「取消」
      if (init.signal && init.signal.aborted) e.status = "取消";
      throw err;
    }
  };
  // 🔴 zh.wikisource 限流很凶：测试里两次请求隔 3 秒以上（浏览器里默认 0.5 秒）
  globalThis.TASTER_WS_GAP_MS = 3100;
  for (const c of CONNECTORS) {
    if (only.length && !only.includes(c.id)) continue;
    console.log(`\n${c.id}  ${c.name}`);
    console.log("  " + pad("查询", 22) + pad("条数", 6) + pad("可试读", 8) + pad("耗时", 8) + "样例");
    log = [];
    for (const q of LIVE[c.id] || ["Pride and Prejudice"]) {
      const t = Date.now();
      try {
        const rs = await c.search(q, { limit: 10, timeout: c.id.startsWith("wikisource") ? 20000 : undefined });
        const ms = ((Date.now() - t) / 1000).toFixed(1) + "s";
        const s = rs[0];
        console.log("  " + pad(cut(q, 20), 22) + pad(rs.length, 6) + pad(rs.filter(r => r.tasteable).length, 8) + pad(ms, 8)
          + (s ? cut(`${s.title}｜${s.license}｜${s.access}｜${s.lang ?? "-"}`, 60) : "（无）"));
        for (const r of rs) for (const b of checkResource(r, c)) { fails++; console.log("    ✗ 形状 " + r.id + "：" + b); }
        const must = MUST[c.id + "|" + q];
        if (must && rs.length && !rs.some(r => r.id === must)) { fails++; console.log(`    ✗ 应该查到 ${must}，实际：${rs.map(r => r.id).join("、")}`); }
        const not = MUST_NOT[c.id + "|" + q], hit = not ? rs.filter(r => not.test(r.id)) : [];
        if (hit.length) { fails++; console.log(`    ✗ 不该出现：${hit.map(r => r.id).join("、")}`); }
        for (const r of rs) if (r.key && TASTE_SOURCES.has(r.source)) tasteKeys.add(r.key);
      } catch (e) {
        errs++;
        console.log("  " + pad(cut(q, 20), 22) + "⚠ 报错：" + (e && e.message || e));
      }
    }
    const hosts = {};
    for (const e of log) (hosts[e.host] ||= []).push(e);
    for (const [host, es] of Object.entries(hosts)) {
      const good = es.filter(e => e.status >= 200 && e.status < 300);
      const acao = [...new Set(good.map(e => e.acao))];
      const cors = good.length ? good.every(e => e.acao === "*" || e.acao === ORIGIN) : null;
      console.log(`  CORS ${host}：${acao.join(",") || "—"}（${es.map(e => e.status).join(" ")}）${cors === false ? "  ✗ 浏览器会被拦" : cors ? "  ✓" : ""}`);
      if (cors === false) fails++;
      const pf = preflight.get(host);
      if (pf) {
        console.log(`  预检 ${host}（${pf.names.join(",")}）：${pf.note}${pf.ok ? "  ✓" : pf.ok === false ? "  ✗ 浏览器会被拦" : "  ⚠"}`);
        if (pf.ok === false) fails++; else if (pf.ok === null) errs++;
      }
    }
  }
  // 真查回来的键也和 request.py 对一遍（给了 key 的都得被它原样收下）
  if (tasteKeys.size) {
    const ks = [...tasteKeys], py = pyValidate(ks);
    if (!py) console.log("\n  ⚠ 没法运行 python tools/request.py，键对拍跳过");
    else {
      const bad = ks.filter((k, i) => !py[i]);
      console.log(`\n键对拍：${ks.length} 个可试读来源的键，request.py 不收的 ${bad.length} 个${bad.length ? "：" + bad.join("、") : ""}`);
      fails += bad.length;
    }
  }
  console.log(`\n在线：形状/CORS 问题 ${fails} 个，连接器报错 ${errs} 次${errs && !STRICT ? "（网络问题只警告，--strict 才算失败）" : ""}`);
  return fails + (STRICT ? errs : 0);
}

// ---------------------------------------------------------------- 主程序
let bad = 0;
if (flag("--offline")) bad = await offline();
else {
  if (!flag("--live")) {
    // 离线另起进程：它会故意撞 429 让连接器冷却，别污染在线那一轮
    const r = spawnSync(process.execPath, [HERE, "--offline"], { stdio: "inherit" });
    bad += r.status ? 1 : 0;
  }
  bad += await live();
}
process.exit(bad ? 1 : 0);
