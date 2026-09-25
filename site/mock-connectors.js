/* 开发用假书库：只在本机（localhost / 127.0.0.1）加 ?mock=1 时由 app.js 加载，发布出去的站上 ?mock=1 不生效。
 * 形状照 ARCHITECTURE.md §2；故意带延迟、一个会抛错、一个很慢，还混了恶意字符串和坏链接，用来测转义和流式显示。 */
(function (W) {
"use strict";
const wait = (ms, signal) => new Promise((ok, no) => {
  const t = setTimeout(ok, ms);
  if (signal) signal.addEventListener("abort", () => { clearTimeout(t); no(new DOMException("aborted", "AbortError")); }, { once: true });
});
// 只给假书库用的一点点繁简，真正的繁简在 zh.js
const ST = { 国: "國", 义: "義", 罗: "羅", 贯: "貫", 传: "傳", 红: "紅", 梦: "夢", 楼: "樓" };
const fold = s => String(s).toLowerCase().replace(/./g, c => ST[c] || c);
const hit = (q, x) => fold(q).split(/\s+/).filter(Boolean).every(t => fold([x.title, ...(x.authors || [])].join(" ")).includes(t)) || q === "mock";

const R = (o) => Object.assign({ key: null, year: null, lang: "en", kind: "book", fiction: null, license: "未知", access: "metadata",
  url: null, cover: null, blurb: null, tasteable: false }, o);

const GUTEN = [
  R({ key: "gutenberg:1342", source: "mock-gutenberg", sourceName: "假古登堡", id: "1342", title: "Pride and Prejudice", authors: ["Austen, Jane"],
    year: 1813, fiction: true, license: "公版", access: "read", url: "https://www.gutenberg.org/ebooks/1342", tasteable: true,
    blurb: "It is a truth universally acknowledged…（来源自带简介，原样显示）" }),
  R({ key: "gutenberg:23950", source: "mock-gutenberg", sourceName: "假古登堡", id: "23950", title: "Romance of the Three Kingdoms, Vol. 1",
    authors: ["Luo, Guanzhong"], year: 1925, fiction: true, license: "公版", access: "read", url: "https://www.gutenberg.org/ebooks/23950", tasteable: true }),
  R({ key: "gutenberg:666", source: "mock-gutenberg", sourceName: "假古登堡", id: "666", title: "<img src=x onerror=alert(1)>三国 XSS 测试",
    authors: ["\"><script>alert(2)</script>"], license: "公版", access: "read", fiction: false,
    url: "javascript:alert(3)", cover: "javascript:alert(4)", blurb: "<b>不该变粗</b> & 不该执行", tasteable: true }),
];
const OL = [
  R({ source: "mock-openlibrary", sourceName: "假 Open Library", id: "OL1W", title: "三国演义", authors: ["罗贯中"], year: 1522, lang: "zh",
    fiction: true, license: "未知", access: "borrow", url: "https://openlibrary.org/works/OL1W", cover: "https://covers.openlibrary.org/b/id/0-M.jpg" }),
  R({ source: "mock-openlibrary", sourceName: "假 Open Library", id: "OL2W", title: "Pride and prejudice", authors: ["Jane Austen"], year: 1813,
    fiction: true, license: "版权", access: "borrow", url: "https://openlibrary.org/works/OL2W", blurb: "An annotated modern edition." }),
  R({ source: "mock-openlibrary", sourceName: "假 Open Library", id: "OL3W", title: "三國演義", authors: ["（明）羅貫中"], year: 1591, lang: "zh",
    fiction: true, license: "版权", access: "preview", url: "https://openlibrary.org/works/OL3W" }),
  R({ source: "mock-openlibrary", sourceName: "假 Open Library", id: "OL4W", title: "Three Kingdoms and Chinese Culture", authors: ["Kimberly Besio"],
    year: 2007, kind: "paper", license: "开放获取", access: "read", url: "https://example.org/paper" }),
];
const WS = [
  R({ key: "wikisource-zh:三國演義", source: "mock-wikisource", sourceName: "假维基文库", id: "三國演義", title: "三國演義", authors: ["羅貫中"],
    lang: "zh", fiction: true, license: "公版", access: "read", url: "https://zh.wikisource.org/wiki/%E4%B8%89%E5%9C%8B%E6%BC%94%E7%BE%A9", tasteable: true }),
  R({ key: "wikisource-zh:紅樓夢", source: "mock-wikisource", sourceName: "假维基文库", id: "紅樓夢", title: "紅樓夢", authors: ["曹雪芹"],
    lang: "zh", fiction: true, license: "公版", access: "read", url: "https://zh.wikisource.org/wiki/%E7%B4%85%E6%A8%93%E5%A4%A2", tasteable: true }),
];

const make = (id, name, list, ms) => ({
  id, name, homepage: "https://example.org/" + id, kinds: ["book"], langs: ["zh", "en"],
  async search(q, { signal, limit } = {}) { await wait(ms, signal); return list.filter(x => hit(q, x)).slice(0, limit || 20); },
});
W.MOCK_CONNECTORS = [
  make("mock-gutenberg", "假古登堡", GUTEN, 400),
  make("mock-openlibrary", "假 Open Library", OL, 1500),
  make("mock-wikisource", "假维基文库", WS, 900),
  { id: "mock-broken", name: "假坏书库", homepage: "https://example.org/broken", kinds: ["book"], langs: ["en"],
    async search(q, { signal } = {}) { await wait(700, signal); throw new Error("HTTP 503（假的）"); } },
  { id: "mock-junk", name: "假乱回书库", homepage: "notaurl", kinds: ["book"], langs: ["en"],
    async search(q, { signal } = {}) { await wait(300, signal); return [null, 42, { title: "" }, { title: "只有书名", key: "BAD KEY", year: "abc", kind: "zzz" }]; } },
];
})(window);
