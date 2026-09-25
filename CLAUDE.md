# Je·v·Taster 试读员 · 给 Claude 的交接

流程和规矩看 `README.md`。这里只记 Sam 拍板过的事（2026-09-25）：

- **是什么**：公开放 GitHub 的静态站。搜关键词 → 资源 + 每章读数。目的是替人省下找书、找资源、判断「该看什么」的时间。以后不只书（课程、论文……），每类资源一把尺。
- **名字**：Je-v-Taster / 试读员。App 名不直接用「Jev」；「关于」页照实写第三方模型和版本号。
- **只出数据和翻译，没有观点**。翻译 = `site/index.html` 里 `reading()` 的固定模板。不许加评语、不许替模型写解读——Claude 也不许在对话里替读数下结论（同 `Dev/Ops/network/走查.md` 的规矩）。
- **版权**：公版书（维基文库、Gutenberg、Standard Ebooks）可链原文；有版权的只放读数，正文不进仓库。拉书只走官方 API，不爬盗版站。判断拿不准时用 `copyright` skill。
- **花钱**：跑 Jev 前先 `--dry-run` 报调用次数；大批量（整本以上）先问 Sam。
- 🔴 **建 GitHub 仓库、push 之前问 Sam。** 发布 Jev 输出是否合 TypeSafe 条款，Sam 还没核实。
- `.stignore` 忽略 `.git`：仓库历史只在建仓那台机上，跨机靠 GitHub。
