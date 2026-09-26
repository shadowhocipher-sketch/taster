// 供应：各书库公开 API 的连接器 → 统一的 Resource（接口见 ARCHITECTURE.md §2）。
// 浏览器直接查（每家都得带 CORS 头），也能在 Node 里跑（tools/test_connectors.mjs）。
// 🔴 只走官方 API。不加自定义请求头——一加就触发 CORS 预检，好几家不接。
//    唯一例外是维基媒体的 Api-User-Agent：它的预检放行这个头（2026-09-25 实测），见 wikisource()。
// 🔴 blurb 只放来源自带的原文，截到 200 字；不许生成、不许改写。
(function () {
  "use strict";
  const W = typeof window !== "undefined" ? window : globalThis;
  // 「美国公版」只列出、不试读，所以不在 TASTE_OK 里（同 Python 的 TASTEABLE_LICENSES）
  const TIMEOUT = 8000, LIMIT = 10, TASTE_OK = ["公版", "自由许可"];

  // ---------------------------------------------------------------- 通用
  function deadline(signal, ms) {
    let t;
    if (AbortSignal.timeout) t = AbortSignal.timeout(ms);
    else { const c = new AbortController(); setTimeout(() => c.abort(new DOMException("timeout", "TimeoutError")), ms); t = c.signal; }
    if (!signal) return t;
    if (AbortSignal.any) return AbortSignal.any([signal, t]);
    const c = new AbortController(), f = s => () => c.abort(s.reason);
    signal.addEventListener("abort", f(signal)); t.addEventListener("abort", f(t));
    return c.signal;
  }
  function sleep(ms, signal) {
    return new Promise((ok, no) => {
      if (signal && signal.aborted) return no(signal.reason);
      const id = setTimeout(ok, ms);
      if (signal) signal.addEventListener("abort", () => { clearTimeout(id); no(signal.reason); }, { once: true });
    });
  }
  // 一次搜索的上下文：限额、总时限、调用方的取消
  function ctx(opts, ms0) {
    opts = opts || {};
    const ms = opts.timeout || ms0 || TIMEOUT;
    return { limit: Math.max(1, Math.min(opts.limit || LIMIT, 50)), caller: opts.signal, ms, signal: deadline(opts.signal, ms) };
  }
  const cool = {};           // host → { until: 冷却到几点, why }
  const later = ms => "约 " + (ms < 90e3 ? Math.ceil(ms / 1e3) + " 秒" : ms < 90 * 60e3 ? Math.round(ms / 60e3) + " 分钟" : Math.round(ms / 3600e3) + " 小时") + "后再试";
  // 🔴 维基媒体的报错响应（429、414……）不带 CORS 头，浏览器里只看到 TypeError、读不到状态码（2026-09-25）
  const WIKIMEDIA = /(^|\.)(wikisource|wikipedia|wikimedia|wikidata)\.org$/;
  async function get(url, cx, headers) {
    const host = new URL(url).host, now = Date.now();
    if (cool[host] && cool[host].until > now) throw new Error(host + " " + cool[host].why + "，" + later(cool[host].until - now));
    let r;
    try { r = await fetch(url, headers ? { signal: cx.signal, headers } : { signal: cx.signal }); }
    catch (e) {
      if (cx.caller && cx.caller.aborted) throw e;
      if (cx.signal.aborted) throw new Error(host + " 超时（" + cx.ms / 1000 + " 秒）");
      // 被限流也只是个 TypeError：当它限流了，冷却一分钟，别接着撞
      if (e && e.name === "TypeError" && WIKIMEDIA.test(host)) {
        cool[host] = { until: now + 60e3, why: "刚才没连上（可能被限流）" };
        throw new Error(host + " 没连上（可能被限流），" + later(60e3));
      }
      throw e;
    }
    if (r.status === 429) {
      // Retry-After 跨域时多半读不到（OpenAlex 专门放行了），读不到就冷却 60 秒。
      // 🔴 OpenAlex 匿名额度按天算（每 IP 每天 1000 点、一次搜索 10 点）：用完了 Remaining=0，要等到 Reset，最长一天（2026-09-25）
      const sec = k => { const v = r.headers.get(k); if (!v) return 0; const n = +v; return n >= 0 ? n : Math.max(0, (Date.parse(v) - now) / 1e3) || 0; };
      const out = r.headers.get("X-RateLimit-Remaining") === "0";
      let s = out ? Math.max(sec("Retry-After"), sec("X-RateLimit-Reset")) : sec("Retry-After");
      s = s > 0 ? Math.min(s, out ? 86400 : 600) : 60;
      cool[host] = { until: now + s * 1e3, why: out ? "今天的免费额度用完了" : "限流中" };
      throw new Error(host + " 限流（429）" + (out ? "：今天的免费额度用完了，" + later(s * 1e3) : ""));
    }
    if (!r.ok) throw new Error(host + " HTTP " + r.status);
    return r.json();
  }
  const enc = encodeURIComponent;
  const qs = o => Object.entries(o).map(([k, v]) => enc(k) + "=" + enc(v)).join("&");

  const ENT = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " };
  const unent = s => s.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, e) => {
    if (e[0] !== "#") return ENT[e.toLowerCase()] ?? m;
    const cp = e[1] === "x" || e[1] === "X" ? parseInt(e.slice(2), 16) : +e.slice(1);
    return cp > 0 && cp <= 0x10ffff ? String.fromCodePoint(cp) : m;
  });
  // 行内标签直接去（中文里补空格会把「三国<i>演义</i>」拆开），块级标签换空格；解完实体再去一遍，防 &lt;b&gt; 变回标签
  const untag = s => s.replace(/<\/?(i|b|em|strong|sup|sub|span|a|u|small)\b[^>]*>/gi, "").replace(/<[^>]*>/g, " ");
  const plain = s => untag(unent(untag(String(s)))).replace(/\s+/g, " ").trim();
  function clip(s, n = 200) {
    if (Array.isArray(s)) s = s[0];
    if (s == null) return null;
    const a = Array.from(plain(s));
    if (!a.length) return null;
    return a.length > n ? a.slice(0, n - 1).join("") + "…" : a.join("");
  }
  const first = v => (Array.isArray(v) ? v[0] : v);
  const list = v => (v == null ? [] : Array.isArray(v) ? v : [v]).map(x => plain(x)).filter(Boolean);
  function yearOf(v) {
    const m = String(first(v) ?? "").match(/-?\d{4}/);
    const y = m ? +m[0] : NaN;
    return Number.isInteger(y) && y > 0 && y <= new Date().getFullYear() + 1 ? y : null;
  }
  const LANG = {
    eng: "en", english: "en", chi: "zh", zho: "zh", chinese: "zh", fre: "fr", fra: "fr", french: "fr",
    ger: "de", deu: "de", german: "de", spa: "es", spanish: "es", ita: "it", italian: "it", jpn: "ja", japanese: "ja",
    rus: "ru", russian: "ru", por: "pt", portuguese: "pt", dut: "nl", nld: "nl", dutch: "nl", lat: "la", latin: "la",
    kor: "ko", korean: "ko", ara: "ar", arabic: "ar", gre: "el", ell: "el", greek: "el", swe: "sv", swedish: "sv",
    pol: "pl", polish: "pl", dan: "da", danish: "da", fin: "fi", finnish: "fi", nor: "no", norwegian: "no",
    cze: "cs", ces: "cs", czech: "cs", hun: "hu", hungarian: "hu", tur: "tr", turkish: "tr", heb: "he", hebrew: "he",
    hin: "hi", hindi: "hi", vie: "vi", vietnamese: "vi", tha: "th", thai: "th", ukr: "uk", ukrainian: "uk",
    per: "fa", fas: "fa", persian: "fa", san: "sa", sanskrit: "sa", epo: "eo", esperanto: "eo", wel: "cy", cym: "cy",
    welsh: "cy", cat: "ca", catalan: "ca", tgl: "tl", tagalog: "tl", ice: "is", isl: "is", icelandic: "is",
  };
  function langOf(v) {
    v = String(first(v) ?? "").trim().toLowerCase();
    if (/^[a-z]{2}$/.test(v)) return v;
    if (/^[a-z]{2}[-_]/.test(v)) return v.slice(0, 2);          // zh-cn / zh-tw → zh
    return LANG[v] || null;
  }
  const HAN = /\p{Script=Han}/u;
  // 一部作品多种语言时：中文查询优先 zh，否则优先 en
  function pickLang(v, q) {
    const ls = [...new Set(list(v).map(langOf).filter(Boolean))];
    if (ls.length <= 1) return ls[0] || null;
    if (HAN.test(q) && ls.includes("zh")) return "zh";
    return ls.includes("en") ? "en" : ls[0];
  }
  // 虚构与否只看来源自己的分类词；没有明确的词就 null，不猜
  const NONFIC = /\bnon-?fiction\b/i;
  const FIC = /\b(fiction|novels?|short stories|stories|romances?)\b|小说|小說/i;
  const NONFIC_SHELF = /category: (biographies|history|essays|philosophy|science|cooking|travel|reference|religion|politics|economics|law|health|mathematics|encyclopedias|psychology|education|business|technology|engineering|sociology)/i;
  function fictionOf(v) {
    const s = list(v).join(" | ");
    if (!s) return null;
    if (NONFIC.test(s)) return false;
    if (FIC.test(s)) return true;
    if (NONFIC_SHELF.test(s)) return false;
    return null;
  }
  // "Austen, Jane" → "Jane Austen"；"Wells, H. G. (Herbert George)" → "H. G. Wells"。多于一个逗号的原样留
  function flipName(n) {
    const s = String(n).replace(/\s*\([^)]*\)/g, "").trim(), m = s.match(/^([^,]+),\s*([^,]+)$/);
    return m ? m[2] + " " + m[1] : s;
  }

  // 资源键：编号规则抄 tools/request.py 的 validate_key（ID_RE、ID_RULES、WIN_RESERVED、KEY_MAX）。
  // 🔴 那边不收的键这边就不给（key=null → 不可试读），不然「请试读」开出 Issue 就被拒：
  //    维基文库标题常带《》“”，request.py 不收（2026-09-25）。两边改一边，另一边一起改
  const ID_RE = /^[\p{L}\p{N}_ .,'’·・()（）:：!！?？&、，—–「」『』-]+$/u;   // Python 的 \w = 字母+数字+_，不含附加符号
  const ID_RULES = { gutenberg: /^[1-9][0-9]{0,6}$/ };
  const WIN_RESERVED = /^(con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³]) *(\..*)?$/i;
  function keyOf(src, id) {
    const key = src + ":" + id;
    return Array.from(key).length <= 200 && (ID_RULES[src] || ID_RE).test(id) && id === id.trim() && !/^[.-]/.test(id)
      && !WIN_RESERVED.test(id) ? key : null;
  }

  function res(c, o) {
    const r = Object.assign({
      key: null, source: c.id, sourceName: c.name, id: "", title: "", authors: [], year: null, lang: null,
      kind: "book", fiction: null, license: "未知", access: "metadata", url: "", cover: null, blurb: null,
    }, o);
    r.tasteable = !!r.key && TASTE_OK.includes(r.license) && r.kind === "book";
    return r;
  }
  function uniq(items, n) {
    const seen = new Set(), out = [];
    for (const r of items) if (r && r.id && r.title && !seen.has(r.id)) { seen.add(r.id); out.push(r); }
    return out.slice(0, n);
  }

  // ---------------------------------------------------------------- 维基文库
  // 🔴 zh.wikisource 限流很凶（2026-09-25 连拉十来页就 429）：同一站两次请求之间至少隔 gap 毫秒
  const nextAt = {};
  async function politely(host, cx) {
    const gap = W.TASTER_WS_GAP_MS ?? 500, now = Date.now(), at = Math.max(now, nextAt[host] || 0);
    nextAt[host] = at + gap;
    if (at > now) await sleep(at - now, cx.signal);
  }
  // 🔴 zh 站收了海量裁判文书，搜「水浒」前二十条一半是「××水浒××公司…判决书」（2026-09-25）
  const WS_JUNK = /(判决书|判決書|裁定书|裁定書|调解书|調解書|决定书|決定書)$|^(Author|Portal|Index|Page|Translation|Wikisource|作者|主題|主题|索引|页面|頁面|维基文库|維基文庫):/i;
  // 🔴 消歧义页不一定带 disambiguation 页面属性：「三國演義 (消歧義)」就没有，标题也得认；
  //    en 的多版本页（{{versions}}，如「Pride and Prejudice」）也没有，得看模板——fetch.py 不收这种页（2026-09-25）。
  //    模板名同 tools/sources/wikisource.py 拒收的那三个；用重定向名引用的也会记在目标模板名下
  const WS_MULTI = "Template:Versions|Template:Translations|Template:Disambiguation";
  const isDis = p => (p.pageprops && "disambiguation" in p.pageprops) || (p.templates && p.templates.length > 0)
    || /\((消歧義|消歧义|disambiguation)\)$/i.test(p.title);
  // 🔴 章节子页面归主页面，只在主页面标题本身对得上查询时才归：en 搜「Emma」「Jane Austen」会命中
  //    「1911 Encyclopædia Britannica/Austen, Jane」这种词条，归上去就成了整部百科、还给「请试读」（2026-09-25）
  const WS_REF = /encyclop|cyclopæ?a?edia|dictionary|gazetteer|magazine|monthly|weekly|quarterly|review|journal|gazette|百科|[辭辞詞词字]典|[類类]書|[類类]书|雜誌|杂志|期刊|月刊|[週周]刊|日[報报]/i;
  // 维基媒体的 User-Agent 政策要求标明身份；浏览器里改不了 User-Agent，官方给的替代就是 Api-User-Agent。
  // 代价：每个不同的请求先多一次 OPTIONS 预检（回应不带 Max-Age，浏览器只缓存几秒）
  const WS_UA = { "Api-User-Agent": "Taster/0.2 (https://github.com/shadowhocipher-sketch/taster)" };
  const fold = s => (W.zhVariants ? W.zhVariants.toSimp(s) : String(s)).normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();
  const STOP = new Set(["the", "and", "of", "an", "in", "on", "to", "at", "by", "for", "with", "from", "or", "de", "la", "le", "el", "der", "die", "das", "und"]);
  function terms(q) {
    const ts = fold(q).split(/[\s\p{P}\p{S}]+/u).filter(t => t && (HAN.test(t) || t.length >= 2) && !STOP.has(t));
    return ts.length ? ts : [fold(q).trim()];
  }
  const hits = (title, ts) => { const f = fold(title); return ts.some(t => t && f.includes(t)); };

  function wikisource(lang) {
    const host = lang + ".wikisource.org";
    const c = {
      id: "wikisource-" + lang, name: lang === "zh" ? "维基文库" : "Wikisource (" + lang + ")",
      homepage: "https://" + host, kinds: ["book"], langs: [lang],
    };
    async function api(p, cx) {
      await politely(host, cx);
      const d = await get("https://" + host + "/w/api.php?" + qs(Object.assign(p, { format: "json", formatversion: 2, origin: "*" })), cx, WS_UA);
      if (d.error) throw new Error(host + " " + (d.error.info || d.error.code));
      return (d.query && d.query.pages) || [];
    }
    const url = t => "https://" + host + "/wiki/" + enc(t.replace(/ /g, "_")).replace(/%2F/g, "/");
    c.search = async function (q, opts) {
      q = String(q ?? "").trim();
      if (!q) return [];
      const cx = ctx(opts);
      const variants = lang === "zh" && W.zhVariants ? W.zhVariants(q) : [q];
      const ts = terms(q);
      const props = { prop: "pageprops|templates", ppprop: "disambiguation", tltemplates: WS_MULTI, tllimit: "max" };
      // 排名表：作品标题，或 {dis: 消歧义/多版本页标题}（占位，稍后换成它链到的作品）
      const rank = [], seen = new Set();
      const add = t => { if (!seen.has(t) && !WS_JUNK.test(t)) { seen.add(t); rank.push(t); } };
      for (let i = 0; i < variants.length && rank.length < cx.limit; i++) {
        let pages;
        try {
          pages = await api(Object.assign({ action: "query", generator: "search", gsrsearch: variants[i], gsrnamespace: 0,
            gsrlimit: Math.min(50, cx.limit * 2 + 5) }, props), cx);
        } catch (e) {
          if (i === 0 || (cx.caller && cx.caller.aborted)) throw e;
          break;                               // 后面的写法没查成就算了，先交已有的
        }
        for (const p of pages.filter(p => p.title).sort((a, b) => (a.index ?? 1e9) - (b.index ?? 1e9))) {
          const root = p.title.split("/")[0];
          if (root !== p.title) {              // 章节子页面 → 归到作品主页面（主页面得对得上查询，且不是百科、期刊）
            if (hits(root, ts) && !WS_REF.test(root)) add(root);
          } else if (isDis(p)) {
            if (!seen.has(p.title)) { seen.add(p.title); rank.push({ dis: p.title }); }
          } else add(p.title);
        }
      }
      // 消歧义 / 多版本页（如「水滸傳」「Pride and Prejudice」）：把它链到的作品页换进来，顺带去掉红链、子页面和二级消歧义
      const dis = rank.slice(0, cx.limit).filter(x => typeof x === "object").map(x => x.dis).slice(0, 5);
      let links = [];
      if (dis.length) {
        try {
          const ps = (await api(Object.assign({ action: "query", generator: "links", titles: dis.join("|"), gplnamespace: 0,
            gpllimit: "max", redirects: 1 }, props), cx)).filter(p => p.title && !p.missing);
          // 🔴 消歧义页的词条行是「[[1911 Encyclopædia Britannica/Abbott, Emma|…]] in [[1911 Encyclopædia Britannica]]」：
          //    链了 X/… 的那个 X 是出处，不是作品（en「Emma」展开后头一条就是整部百科，2026-09-25）
          const box = new Set(ps.filter(p => p.title.includes("/")).map(p => p.title.split("/")[0]));
          links = ps.filter(p => !isDis(p) && !p.title.includes("/") && !box.has(p.title) && !WS_JUNK.test(p.title)).map(p => p.title)
            .sort((a, b) => (hits(b, ts) - hits(a, ts)) || (a < b ? -1 : a > b ? 1 : 0));   // 对得上查询的在前
        } catch (e) { if (cx.caller && cx.caller.aborted) throw e; }
      }
      // 展开的作品排在消歧义页原来的名次上；后面搜索结果里再出现就不重复
      const titles = [], out = new Set(), put = t => { if (!out.has(t)) { out.add(t); titles.push(t); } };
      const strong = new Set(links);
      for (const x of rank) {
        if (typeof x === "string") put(x);
        else { links.forEach(put); links = []; }
      }
      // 标题对得上查询的、消歧义页展开的排前面；只是正文里提到的全文命中排后面（zh 搜「水浒」会搜出胡适书信、「除非」，2026-09-25）
      titles.forEach(t => { if (hits(t, ts)) strong.add(t); });
      titles.sort((a, b) => strong.has(b) - strong.has(a));
      return uniq(titles.map(t => res(c, {
        key: keyOf(c.id, t), id: t, title: t, lang, kind: "book", license: "公版", access: "read", url: url(t),
      })), cx.limit);
    };
    return c;
  }

  // ---------------------------------------------------------------- Project Gutenberg
  // 🔴 只查 Gutendex。www.gutenberg.org 的 robots.txt 禁 /ebooks/search（OPDS 搜索也在里面），机器人政策写明网站只给人用，
  //    程序访问会封 IP；tools/sources/gutenberg.py 也不碰它（2026-09-25 撤掉了 OPDS 后备）。
  // 🔴 Gutendex 冷查询要 35~70 秒（2026-09-25 实测）；查过的有缓存，再查 0.2~1 秒——超时掐掉的请求服务器也照样算完缓存。
  //    缓存在 Cloudflare，存 4 小时、按 Origin 分开存（Vary: origin）：线上用户共用一个 Origin，谁先查过后面的人就快；
  //    localhost 预览和线上各算各的，本机慢不代表线上慢。时限放宽到 18 秒（前端给每家 20 秒），超时就明说「过一会儿再搜」。
  const GUT_MS = 18000;
  // 许可：和 Python 书源同一条规则，两边一起改。copyright true → 版权，null → 未知；
  // false 只说明在美国是公版 → 再看每位作者：卒年 ≤ 1955，或没有卒年但生年 ≤ 1850（没有作者也算过）→ 公版；
  // 有一位不满足 → 美国公版（列出、不试读）。
  // 🔴 Gutendex 的 copyright:false 是美国口径，「作者死后 70 年」的国家里可能还有版权（2026-09-25）
  const num = v => typeof v === "number" && Number.isFinite(v);
  const gutSafe = a => (num(a.death_year) && a.death_year <= 1955) || (a.death_year == null && num(a.birth_year) && a.birth_year <= 1850);
  function gutLicense(b) {
    if (b.copyright === true) return "版权";
    if (b.copyright !== false) return "未知";
    return (b.authors || []).every(a => gutSafe(a || {})) ? "公版" : "美国公版";
  }
  const KANA = /[\u3040-\u30ff]/;
  const gutenberg = {
    id: "gutenberg", name: "Project Gutenberg", homepage: "https://www.gutenberg.org",
    kinds: ["book", "audio"], langs: ["en", "fr", "de", "fi", "nl", "it", "es", "pt", "zh", "la", "sv", "el"],
    async search(q, opts) {
      q = String(q ?? "").trim();
      if (!q) return [];
      const cx = ctx(opts, GUT_MS), c = gutenberg;
      // Gutenberg 的中文书几乎都是繁体，Gutendex 又不做简繁转换（「水浒」查不到「水滸傳」）→ 原文、繁体各查一次。
      // 🔴 单字转繁会错（皇后→皇後、范进→範進），原文也得查；带假名的是日文，不转（2026-09-25）
      const qs1 = HAN.test(q) && !KANA.test(q) && W.zhVariants ? [...new Set([q, W.zhVariants.toTrad(q)])] : [q];
      const got = await Promise.allSettled(qs1.map(s => get("https://gutendex.com/books/?" + qs({ search: s }), cx)));
      if (cx.caller && cx.caller.aborted) throw cx.caller.reason;
      const ok = got.filter(g => g.status === "fulfilled" && g.value && Array.isArray(g.value.results));
      if (!ok.length) {
        const e = (got.find(g => g.status === "rejected") || {}).reason || new Error("gutendex.com 返回的不是书目");
        if (/超时/.test(e.message)) throw new Error(e.message + "：Gutendex 头一回查这个词要半分钟以上，过一两分钟再搜就快了");
        throw e;
      }
      return uniq(ok.flatMap(g => g.value.results).map(fromDex), cx.limit);

      function fromDex(b) {
        const id = String(b.id);
        return res(c, {
          key: keyOf("gutenberg", id), id, title: plain(b.title || ""), authors: (b.authors || []).map(a => flipName(a.name)),
          year: null, lang: langOf(b.languages), kind: b.media_type === "Text" ? "book" : b.media_type === "Sound" ? "audio" : "other",
          fiction: fictionOf([...(b.subjects || []), ...(b.bookshelves || [])]),
          license: gutLicense(b),
          access: "read", url: "https://www.gutenberg.org/ebooks/" + id, cover: (b.formats || {})["image/jpeg"] || null,
          blurb: null,     // 🔴 Gutendex 的 summaries 是 Gutenberg 自动生成的摘要，不算来源原文，不放
        });
      }
    },
  };

  // ---------------------------------------------------------------- 中国哲学书电子化计划（ctext）
  // 🔴 只用 api.ctext.org；网页本身有反爬，别去抓（2026-09-25 取文档页就被拦）
  const ctext = {
    id: "ctext", name: "中国哲学书电子化计划", homepage: "https://ctext.org",
    kinds: ["book"], langs: ["zh"],
    async search(q, opts) {
      q = String(q ?? "").trim();
      if (!q) return [];
      const cx = ctx(opts);
      const d = await get("https://api.ctext.org/searchtexts?" + qs({ title: q }), cx);
      if (d.error) throw new Error("ctext " + (d.error.code || "") + " " + (d.error.description || ""));
      return uniq((d.books || []).filter(b => b.urn && b.title).map(b => {
        const id = String(b.urn).replace(/^ctp:/, ""), wb = id.match(/^wb(\d+)$/);
        return res(ctext, {
          id, title: plain(b.title), lang: "zh", kind: "book", access: "read",
          url: wb ? "https://ctext.org/wiki.pl?if=gb&res=" + wb[1] : "https://ctext.org/" + id,
        });
      }), cx.limit);
    },
  };

  // ---------------------------------------------------------------- Open Library
  const openlibrary = {
    id: "openlibrary", name: "Open Library", homepage: "https://openlibrary.org",
    kinds: ["book"], langs: ["en", "zh", "fr", "de", "es", "it", "ja", "ru", "pt"],
    async search(q, opts) {
      q = String(q ?? "").trim();
      if (!q) return [];
      const cx = ctx(opts);
      const fields = "key,title,author_name,first_publish_year,language,cover_i,ebook_access,public_scan_b,subject";
      // 🔴 q= 少于 3 个字直接 422（「水浒」「鲁迅」都查不了，2026-09-25）；title= / author= 不限，短查询就这两样各查一次
      const ps = Array.from(q).length < 3 ? [{ title: q }, { author: q }] : [{ q }];
      const got = await Promise.allSettled(ps.map(p => get("https://openlibrary.org/search.json?" + qs(Object.assign(p, { limit: cx.limit, fields })), cx)));
      if (cx.caller && cx.caller.aborted) throw cx.caller.reason;
      const ok = got.filter(g => g.status === "fulfilled");
      if (!ok.length) throw got[0].reason;
      return uniq(ok.flatMap(g => g.value.docs || []).map(b => {
        const id = String(b.key || "").replace(/^\/works\//, ""), pd = b.public_scan_b === true, ea = b.ebook_access;
        return res(openlibrary, {
          id, title: plain(b.title || ""), authors: list(b.author_name), year: yearOf(b.first_publish_year),
          lang: pickLang(b.language, q), kind: "book", fiction: fictionOf(b.subject),
          license: pd ? "公版" : ea === "borrowable" || ea === "printdisabled" ? "版权" : "未知",
          access: ea === "public" ? "read" : ea === "borrowable" ? "borrow" : "metadata",
          url: "https://openlibrary.org/works/" + id,
          cover: b.cover_i ? "https://covers.openlibrary.org/b/id/" + b.cover_i + "-M.jpg" : null,
          blurb: null,     // 🔴 search.json 没有简介；first_sentence 是正文第一句，不是来源写的简介，不放（2026-09-25）
        });
      }), cx.limit);
    },
  };

  // ---------------------------------------------------------------- Internet Archive
  // 🔴 只查图书馆扫描、LibriVox、公版电影这些策展集合。社区上传区（opensource、booksbylanguage…）
  // 有大量盗版转存，2026-09-25 搜「水浒」前几条就是从 Anna's Archive 搬来的 1975/1986 年版。
  // 中文古籍扫描（明清刻本）几乎都在 universallibrary（百万图书计划），留着。
  // 🔴 借阅集合（inlibrary、printdisabled、lendinglibrary）是在版权书的受控数字借阅，Hachette v. Internet Archive
  //    （2d Cir. 2024）判这种出借不算合理使用；这些扫描本同时挂在 internetarchivebooks、americana 下，光从名单删掉不够。
  //    只留 1930 年以前出版的（美国公版线）——默认取保守的一边，放不放宽由维护者决定（2026-09-25）。
  // 🔴 universallibrary（百万图书计划）也扫了大量还在版权期的现代书，同样只留 1930 年以前的文字（2026-09-25）
  const IA_OK = ["americana", "toronto", "europeanlibraries", "cdl", "library_of_congress", "gutenberg", "biodiversity",
    "fedlink", "universallibrary", "internetarchivebooks", "librivoxaudio", "feature_films", "prelinger"];
  const IA_LEND = ["inlibrary", "printdisabled", "lendinglibrary"];
  const IA_OLD = IA_LEND.concat(["universallibrary"]);      // 这些集合只要 1930 年以前的文字
  const IA_PD = /creativecommons\.org\/(publicdomain\/(mark|zero)|licenses\/publicdomain)/i;
  // 🔴 description 常是编目的载体形态（「240 pages ; 20 cm」「253 páginas」「26」）或扫描说明，不是简介（2026-09-25）
  const IA_NOTE = /^(book digitized by google|the metadata below describe|digitized by|scanned (by|in|from))/i;
  // 太短的是编目碎片（「Includes index.」「線裝四冊」），不是简介：西文不到 20 字、中日韩文不到 10 字就丢
  const CJK = /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]/u;
  function iaBlurb(v) {
    const s = clip(v);
    if (!s || IA_NOTE.test(s)) return null;
    if (Array.from(s).length < (CJK.test(s) ? 10 : 20)) return null;
    if (s.length <= 80 && (/^[\d\s.,;:()[\]-]+$/.test(s) || /\d\s*(cm|pages?|páginas|p\.|pp\.|leaves)(?![a-z])/i.test(s))) return null;
    return s;
  }
  const luc = s => s.replace(/[+\-&|!(){}[\]^"~*?:\\/]/g, " ").replace(/\b(AND|OR|NOT|TO)\b/g, m => m.toLowerCase()).trim();
  const archive = {
    id: "archive", name: "Internet Archive", homepage: "https://archive.org",
    kinds: ["book", "audio", "video"], langs: ["en", "zh", "fr", "de", "es", "it", "la", "ru"],
    async search(q, opts) {
      q = String(q ?? "").trim();
      // 🔴 IA 不做简繁转换，古籍扫描的书名都是繁体：「水浒」0 条、「水滸」几十条（2026-09-25）→ 两种写法一起查
      const ts = [...new Set((HAN.test(q) && W.zhVariants ? W.zhVariants(q) : [q]).map(luc))].filter(Boolean);
      if (!ts.length) return [];
      const cx = ctx(opts);
      const query = "(" + ts.map(t => "title:(" + t + ") OR creator:(" + t + ")").join(" OR ") + ") AND collection:(" + IA_OK.concat(IA_LEND).join(" OR ")
        + ") AND ((mediatype:(texts OR audio OR movies) -collection:(" + IA_OLD.join(" OR ") + ")) OR (mediatype:texts AND year:[1 TO 1929]))";
      const fl = ["identifier", "title", "creator", "date", "year", "language", "licenseurl", "mediatype", "collection", "description", "subject"];
      const d = await get("https://archive.org/advancedsearch.php?" + qs({ q: query, rows: cx.limit, output: "json", "sort[]": "downloads desc" })
        + fl.map(f => "&fl[]=" + f).join(""), cx);
      return uniq(((d.response || {}).docs || []).map(b => {
        const id = String(b.identifier || ""), col = list(b.collection), kind = { texts: "book", audio: "audio", movies: "video" }[b.mediatype] || "other";
        const y = yearOf(b.year ?? b.date), old = kind === "book" && y != null && y < 1930;
        if (col.some(x => IA_OLD.includes(x)) && !old) return null;   // 查询里已排除，这里再挡一道
        // 明确的公版标记 / CC0 → 公版；1930 年以前出版的文字只过了美国的线（保守取 1930）→ 美国公版；其余 → 未知
        const license = IA_PD.test(String(b.licenseurl || "")) ? "公版" : old ? "美国公版" : "未知";
        const lend = col.includes("inlibrary") || col.includes("lendinglibrary");
        return res(archive, {
          id, title: plain(first(b.title) || ""), authors: list(b.creator), year: y, lang: langOf(b.language), kind,
          fiction: fictionOf(b.subject), license,
          // 🔴 许可不明的一律「只有书目」：IA 上打得开不等于能合法读（2026-09-25）
          access: license === "未知" ? "metadata" : lend ? "borrow" : col.includes("printdisabled") ? "metadata" : "read",
          url: "https://archive.org/details/" + id, cover: "https://archive.org/services/img/" + id, blurb: iaBlurb(b.description),
        });
      }), cx.limit);
    },
  };

  // ---------------------------------------------------------------- OpenAlex（论文）
  const openalex = {
    id: "openalex", name: "OpenAlex", homepage: "https://openalex.org",
    kinds: ["paper", "book"], langs: ["en", "zh", "fr", "de", "es", "ja", "ru", "pt"],
    async search(q, opts) {
      q = String(q ?? "").trim();
      if (!q) return [];
      // 🔴 search= 现在是全文检索（meta 里写着 full text has），「水浒」头一条是 NLP 论文 ERNIE；只查标题+摘要（2026-09-25）。
      //    filter 语法里 , 分隔条件、| 是或、! 是非、: 分键值 → 用户输入里的都换成空格
      const f = q.replace(/[,|!:]/g, " ").replace(/\s+/g, " ").trim();
      if (!f) return [];
      const cx = ctx(opts);
      const d = await get("https://api.openalex.org/works?" + qs({ filter: "title_and_abstract.search:" + f, per_page: cx.limit,
        select: "id,display_name,publication_year,language,type,doi,open_access,best_oa_location,primary_location,authorships" }), cx);
      return uniq((d.results || []).map(w => {
        const id = String(w.id || "").split("/").pop(), oa = !!(w.open_access && w.open_access.is_oa);
        const best = w.best_oa_location || {}, prim = w.primary_location || {};
        const kind = w.type === "book" ? "book" : w.type === "dataset" ? "other" : "paper";
        return res(openalex, {
          id, title: plain(w.display_name || ""), authors: (w.authorships || []).map(a => a.author && a.author.display_name).filter(Boolean).slice(0, 10),
          year: yearOf(w.publication_year), lang: langOf(w.language), kind, fiction: kind === "paper" ? false : null,
          license: oa ? "开放获取" : "未知", access: oa ? "read" : "metadata",
          url: best.landing_page_url || best.pdf_url || w.doi || prim.landing_page_url || "https://openalex.org/" + id,
          blurb: null,     // 摘要是倒排索引，拼回去也算改写，不放
        });
      }), cx.limit);
    },
  };

  W.CONNECTORS = [wikisource("zh"), wikisource("en"), gutenberg, ctext, openlibrary, archive, openalex];
})();
