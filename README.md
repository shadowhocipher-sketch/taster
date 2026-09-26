# Taster 试读员

**搜一本书，先看每一章读起来怎么样。只有读数，没有观点。**

→ **在线使用：https://shadowhocipher-sketch.github.io/taster/**

像订酒店先看评分：你搜一个书名、题材，或者一种感受（「揪心」「好笑」「停不下来」），Taster 同时去查各家公开书库，把结果排成统一的卡片。已经试读过的书，每一章都有一组读数——

| 读数 | 问的是 |
|---|---|
| 想看下一章 | 普通读者读完这一章，会不会想立刻看下一章？ |
| 中途放下 | 会不会读到一半就放下？ |
| 跟得上 | 能不能跟上这一章发生了什么，不迷路？ |
| 主要感受 | 紧张 / 好笑 / 揪心 / 好奇 / 痛快 / 平淡 / 困惑 |

每个数都是第三方判断模型对原文给出的概率。页面上的文字只是按固定模板把数字翻成人话；模型拿不准的就写「拿不准」，不进排行。**没有人写评语，也没有模型写推荐语。**

> 杰文斯悖论说：一样东西变便宜，人反而会用得更多。
> 判断「这本书值不值得读」变便宜了，希望人会读得更多。

## 它背后连着哪些书库

| 来源 | 能搜 | 能试读 |
|---|---|---|
| 维基文库（中文 / 英文） | ✅ | ✅ 公版或自由许可 |
| Project Gutenberg（经 Gutendex） | ✅ | ✅ 作者去世满 70 年的；只在美国是公版的标「美国公版」、不试读 |
| 中国哲学书电子化计划（ctext） | ✅ | — 只列出、给链接 |
| Open Library | ✅ | — |
| Internet Archive | ✅ | — |
| OpenAlex（论文） | ✅ | — |

搜索在你的浏览器里直接查这些书库的公开 API（Gutendex 是社区维护的 Gutenberg 目录 API），本站没有服务器、不记录你搜了什么。
卡片上的书名、作者、简介、年份是来源自带的；许可、能不能直接读、是不是小说是本站按固定规则推断的，规则写在站内「怎么读的」页。

## 想让试读员读一本书？

在搜索结果里点「请试读」，会打开一个预填好的 Issue。维护者批准后，GitHub Actions 会把这本书一章一章读完，读数提交回仓库，网站自动更新。只接受公版和自由许可的书。

## 规矩

- **尺不改。** 问题的措辞一个字都不动——换措辞，同一章的分数能差 0.7，而重跑噪音只有 ±0.04。要改就是一把新尺，全部重读。
- **判定线**：是非题 ≥ 0.65 记「是」、≤ 0.35 记「否」，中间「拿不准」；打分信心 < 0.5、选择题最高概率 < 40% 也记「拿不准」。
- **版权**：正文从不进仓库、不上网站。公版书链回原书库；有版权的书只列出、给链接，不试读。
- **不接受任意文字打分**，只读目录里的书。

## 本地跑

```bash
python tools/fetch.py wikisource-zh:三國演義 --limit 10   # 拉正文到 texts/（不进仓库）
python tools/taste.py wikisource-zh:三國演義 --dry-run    # 先看要调几次
python tools/taste.py wikisource-zh:三國演義 --limit 10   # 试读（需要 TASTER_API_KEY）
python tools/build.py                                     # 打包 site/data.js
python -m http.server 8765 --directory site               # 打开 http://localhost:8765
```

接口约定见 [ARCHITECTURE.md](ARCHITECTURE.md)。想接一家新书库：浏览器端加一个连接器（`site/connectors.js`）；能试读的再加一个 Python 书源（`tools/sources/`），并把来源 id 加进 `site/app.js` 的 `TASTE_SRC` 和 `tools/test_connectors.mjs` 的可试读列表。

---

**English.** Taster searches public libraries (Wikisource, Project Gutenberg via Gutendex, ctext, Open Library, Internet Archive, OpenAlex) from your browser and shows, for books it has "tasted", per-chapter reader-response readings — *would a typical reader want the next chapter, put it down halfway, keep up, and what do they mostly feel* — as raw probabilities from a third-party judge model, translated by fixed templates. Data, not opinions. Public-domain texts only; no text is stored in this repo.
