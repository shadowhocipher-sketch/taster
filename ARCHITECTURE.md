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
| `wikisource-en` | 作品主页面标题（空格不转 `_`；多版本页要用具体版本） | `wikisource-en:Pride and Prejudice (1813)` | ✅ |
| `gutenberg` | 电子书编号 | `gutenberg:1342` | ✅ 只在「全球稳妥公版」时（见下） |
| 其他（openlibrary、archive、openalex、ctext…） | 该来源的 id | `openlibrary:OL45883W` | ❌ 只列出、不试读 |

**Gutenberg 许可规则**（`gutenberg.py` 与 `connectors.js` 必须一致）：Gutendex `copyright` 为 true → 版权；null → 未知；
false 时每位作者须满足「死于 1955 年及以前」或「卒年不详且生于 1850 年及以前」（没有作者也算过）→ 公版、可试读；
否则 → **美国公版**：只在美国是公版，列出、不试读。

**编号字符规则**（`tools/request.py` 的 `validate_key` 是唯一裁判，`connectors.js`、`app.js` 各抄一份只做前挡）：
各种文字的字母数字 + `_ .,'’·・()（）:：!！?？&、，—–「」『』-`；gutenberg 只许 `[1-9][0-9]{0,6}`；≤ 200 字；
不许首尾空白、不许以 `.` `-` 开头、不许 Windows 设备名（CON/AUX/NUL/COM1…）。
维基文库的键会先问一次 API 换成规范标题（跟重定向、只收主命名空间），`三国演义` → `三國演義`。
过不了规则的书照样列出，只是没有「请试读」按钮（书名带《》“”的维基文库作品目前就是这样）。

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
  lang: "en",                     // ISO 639-1；中文一律 "zh"；来源不说就 null
  kind: "book",                   // book | paper | course | audio | video | other
  fiction: true,                  // true | false | null（不知道）
  license: "公版",                // 公版 | 自由许可 | 美国公版 | 开放获取 | 版权 | 未知
  access: "read",                 // read（可直接全文读）| borrow | preview | metadata
  url: "https://…",               // 去来源看这个资源的页面
  cover: "https://…" | null,
  blurb: "…" | null,              // 来源自带的简介原文，≤ 200 字截断。🔴 不许生成、不许改写
  tasteable: true                 // key 非空 && license ∈ {公版, 自由许可} && kind === "book"（美国公版不算）
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
- 拿不准的不进排行，不算平均。**判得出的章不到已读章的一半，就不给平均数**，只写「拿不准」和 是/否/拿不准 各几章（2026-09-25 定：三国「中途放下」10 章只有 2 章判得出，平均这 2 章显示 34%，像结论其实不是）。
- 🔴 只有读数和翻译，**没有观点**：不写评语、不写「推荐理由」、不生成简介。
- 🔴 站上**不点名判断模型的供应商和模型名**（客户协议 §16.4），只写「第三方判断模型」+ 版本号。

## 5. 请试读（GitHub Issue → Actions）

- Issue 表单 `.github/ISSUE_TEMPLATE/taste.yml`：字段 id `key`（资源键，必填）、`type`（下拉，目前只有「小说」）；自动打标签 `试读请求`。
- 前端「请试读」按钮 = 预填链接：
  `https://github.com/shadowhocipher-sketch/taster/issues/new?template=taste.yml&title=试读：<书名>&key=<资源键>`（都做 encodeURIComponent）
- 谁能触发真跑：仓库主人开的 Issue 直接跑；别人开的，等主人加标签 `批准` 再跑（只有维护者能加标签——这就是花钱闸门）。
- `taste.yml` 分两个 job：
  - `estimate`：外人开的 Issue → 只预估（不调模型；正文不落盘，但 Gutenberg、维基文库单页作品要把全文读进内存才切得出目录），没有密钥、只读仓库、不进排队组。
  - `taste`：主人开的或加了 `批准` → 真跑，有密钥和写权限，按 Issue 各自排队（`taste-<Issue 号>`），推送冲突靠 rebase 重试。
- 「读过」全站一个定义（`core.is_read`）：当前尺下有读数、章名对得上目录。请求只读**没读过**的章，读过的正文变了也不重读（评论里会列出来），批准多少就读多少。
- 批准的必须是预估过的那个键：机器人评论末尾埋 `<!-- taster-key: … -->`，加 `批准` 时键对不上（Issue 被改过）就只重新预估、撤掉 `批准`。
- 单次上限 200 章、100 分钟（`request.py`）；没读完留 Issue 开着，再加一次 `批准` 接着读。推不上去的读数存成 Actions 附件，不白花钱。
- 试读用 `GITHUB_TOKEN` 推的提交不触发 push，所以 `pages.yml` 另挂 `workflow_run`，读完重新发布。
- key 放在仓库 Secrets：`TASTER_API_KEY`；可选变量 `TASTER_ENDPOINT`。标签要先建好：`试读请求`、`批准`、`已试读`。

## 6. 红线

- 正文（`texts/`）永远不进仓库；站上只有读数、章名、链接。
- 只试读 `公版 / 自由许可`；有版权的只列出、给链接。
- 只走官方 API / 镜像，带 UA、慢速、遇 429 退避；不爬网页，不接盗版站。
- 不接受任意文本打分（客户协议 §2.3(a)：不得把判断服务当独立服务转供）。
- 尺一个字都不改；改就是新版本，全部重读。
