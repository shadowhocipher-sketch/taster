# Taster 试读员 · 架构与接口约定

像 Booking.com：**供应**是各家书库和网络资源（连接器），**比价**是统一的资源卡片，**点评**是试读员读数。

```
浏览器（GitHub Pages，纯静态）
  site/index.html ── 搜索框 ──▶ site/connectors.js ──▶ 各书库公开 API（CORS，直接从浏览器查）
        │                                              维基文库 / Gutenberg / Open Library / Internet Archive / OpenAlex …
        └── site/data.js（已试读的书架 + 读数）
                 ▲
GitHub Actions   │  build.py
  「请试读」Issue ─▶ tools/request.py ─▶ fetch.py（sources/*.py 拉正文）─▶ taste.py（judge.py 调判断模型）─▶ data/
```

## 1. 资源键

`<来源>:<编号>`，全站唯一写法。Python 书源、JS 连接器、数据文件名、Issue 请求都用它。

| 来源 id | 编号 | 例 | 能试读 |
|---|---|---|---|
| `wikisource-zh` | 作品主页面标题（原样，繁体就繁体） | `wikisource-zh:三國演義` | ✅ |
| `wikisource-en` | 作品主页面标题（空格不转 `_`） | `wikisource-en:Pride and Prejudice` | ✅ |
| `gutenberg` | 电子书编号 | `gutenberg:1342` | ✅（Gutendex `copyright === false` 才行） |
| 其他（openlibrary、archive、openalex…） | 该来源的 id | `openlibrary:OL45883W` | ❌ 只列出、不试读 |

## 2. 搜索结果（JS `Resource`，所有连接器统一返回这个形状）

```js
{
  key: "gutenberg:1342",          // 可试读的来源才有；否则 null
  source: "gutenberg",            // 连接器 id
  sourceName: "Project Gutenberg",
  id: "1342",
  title: "Pride and Prejudice",
  authors: ["Jane Austen"],
  year: 1813,                     // 首次出版年，不知道就 null
  lang: "en",                     // ISO 639-1；中文一律 "zh"
  kind: "book",                   // book | paper | course | audio | video | other
  fiction: true,                  // true | false | null（不知道）
  license: "公版",                // 公版 | 自由许可 | 开放获取 | 版权 | 未知
  access: "read",                 // read（可直接全文读）| borrow | preview | metadata
  url: "https://…",               // 去来源看这个资源的页面
  cover: "https://…" | null,
  blurb: "…" | null,              // 来源自带的简介原文，≤ 200 字截断。🔴 不许生成、不许改写
  tasteable: true                 // key 非空 && license ∈ {公版, 自由许可} && kind === "book"
}
```

连接器（`site/connectors.js`）：

```js
window.CONNECTORS = [{
  id: "gutenberg", name: "Project Gutenberg", homepage: "https://www.gutenberg.org",
  kinds: ["book"], langs: ["en", "zh", …],
  search(q, { signal, limit }) → Promise<Resource[]>   // 失败就 throw，前端显示「这家没连上」
}, …]
```

## 3. 书架数据（`site/data.js`，由 `tools/build.py` 生成）

```js
window.TASTER = {
  built: "2026-09-25 06:10Z",
  rulers: { novel_v1: { 版本, 适用, 模型版本: "1.13.0", 截断字数, 指纹, 题: { 继续读: { 坏, 说法, q: {type, instructions, criteria?} }, … } } },
  shelf: [{
    key, source, source_name, id, title, author, lang, type: "小说", license, read_url, tags: [], ruler,
    units_total: 120, units_read: 10, added,
    units: [{ n, label, title, url, 字数?, 送出字数?, utc?, answers?: { 题: 原始读数 } }]
  }]
}
```

原始读数三种形状见 `tools/judge.py` 顶部注释。

## 4. 读数 → 人话（固定模板，全站唯一实现在前端 `reading()`）

- 是非题（noul）：`p ≥ 0.65` 记「是」，`p ≤ 0.35` 记「否」，中间「拿不准」。颜色按「坏」的方向：坏=高 的题，「是」是红。
- 打分题（score）：`confidence < 0.5` 记「拿不准」，否则「≈ 标签（分/满分）」。
- 选择题（choice）：最高概率 `< 0.4` 记「拿不准（前两名）」，否则「选项 概率」。
- 拿不准的不进排行，不算平均。
- 🔴 只有读数和翻译，**没有观点**：不写评语、不写「推荐理由」、不生成简介。
- 🔴 站上**不点名判断模型的供应商和模型名**（客户协议 §16.4），只写「第三方判断模型」+ 版本号。

## 5. 请试读（GitHub Issue → Actions）

- Issue 表单 `.github/ISSUE_TEMPLATE/taste.yml`：字段 id `key`（资源键，必填）、`type`（下拉，目前只有「小说」）；自动打标签 `试读请求`。
- 前端「请试读」按钮 = 预填链接：
  `https://github.com/Keepexperiencing/taster/issues/new?template=taste.yml&title=试读：<书名>&key=<资源键>`（都做 encodeURIComponent）
- 谁能触发真跑：仓库主人开的 Issue 直接跑；别人开的，等主人加标签 `批准` 再跑（只有维护者能加标签——这就是花钱闸门）。
- 单次上限 200 章（`taste.py --max-calls`）；没读完就留 Issue 开着，再加一次 `批准` 接着读。
- key 放在仓库 Secrets：`TASTER_API_KEY`；可选变量 `TASTER_ENDPOINT`。

## 6. 红线

- 正文（`texts/`）永远不进仓库；站上只有读数、章名、链接。
- 只试读 `公版 / 自由许可`；有版权的只列出、给链接。
- 只走官方 API / 镜像，带 UA、慢速、遇 429 退避；不爬网页，不接盗版站。
- 不接受任意文本打分（客户协议 §2.3(a)：不得把判断服务当独立服务转供）。
- 尺一个字都不改；改就是新版本，全部重读。
