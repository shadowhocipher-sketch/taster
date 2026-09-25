# Je·v·Taster 试读员

搜一本书、一个题材或一种感受，看每一章「读起来怎么样」的读数：会不会想看下一章、会不会中途放下、跟不跟得上、主要让人紧张还是揪心。

**只放读数，不放观点。** 每一章原文送给一个第三方判断模型，问一组冻结的问题，模型只回概率和分数。页面上的文字是按固定模板把数字翻成人话。拿不准的读数就标「拿不准」，不进排行。

## 流程

```
catalog.json ──fetch.py──▶ texts/<id>/      正文（本地，不进仓库）
             ──taste.py──▶ data/<id>.jsonl  读数（进仓库，只追加）
             ──build.py──▶ site/data.js     打包给静态页
site/index.html                             纯静态、零依赖，可直接放 GitHub Pages
```

```bash
python tools/fetch.py sanguo --limit 10      # 拉正文（维基文库 API，慢速，遇 429 退避）
python tools/taste.py sanguo --dry-run       # 先看要调几次、送多少字
python tools/taste.py sanguo --limit 10      # 试读，已读过的跳过
python tools/build.py                        # 打包
python -m http.server 8765 --directory site  # 本地看
```

`taste.py` 需要 Jev 调用封装 `jev.py`，默认找 `../../Ops/network/`，别处用环境变量 `JEV_DIR` 指定。key 不进本仓库。

## 规矩

- **尺（`rulers/*.json`）一个字都不改。** 换措辞，同一章分数能差 0.7（噪音只有 ±0.04）。要改就另存新版本，所有资源重读。
- **每类资源一把尺。** 小说用 `novel_v1`；非虚构、课程、论文以后各出一把。
- **是非题 ≥ 0.65 记是、≤ 0.35 记否，中间拿不准。** 打分信心 < 0.5、选择最高概率 < 0.4 也记拿不准。
- **读数不经人手。** `data/` 里是模型原样返回；实际模型版本和尺上钉的不一致，脚本直接停，不落盘。
- **版权**：只放读数、章名、原文链接。公版书链到维基文库等公开书库；有版权的书只放读数，正文不进仓库、不上站。
