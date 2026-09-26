# Taster 试读员 · 给 Claude 的交接

流程和规矩看 `README.md`，接口约定看 `ARCHITECTURE.md`。这里只记 Sam 拍板过的事（2026-09-25）：

- **是什么**：公开放 GitHub 的静态站。搜关键词 → 资源 + 每章读数。目的是替人省下找书、找资源、判断「该看什么」的时间。以后不只书（课程、论文……），每类资源一把尺。
- **名字**：Taster 试读员（2026-09-25 定）。名字里不带 Jev——不绑死供应商，也因为 TypeSafe 客户协议 §16.4 不授权用对方名字。站上只写「第三方判断模型 + 版本号」。
- **形态**：像 Booking.com——背后连很多书库和网络资源，统一卡片，试读员读数当点评。三国只是第一个接进来的书。
- **只出数据和翻译，没有观点**。翻译 = `site/app.js` 里 `reading()` 的固定模板。不许加评语、不许替模型写解读——Claude 也不许在对话里替读数下结论（同 `Dev/Ops/network/走查.md` 的规矩）。
- **版权**：公版书（维基文库、Gutenberg）可链原文；有版权的只放读数，正文不进仓库。拉书只走官方 API，不爬盗版站。判断拿不准时用 `copyright` skill。
- **花钱**：跑 Jev 前先 `--dry-run` 报调用次数；大批量（整本以上）先问 Sam。
- **GitHub**：`shadowhocipher-sketch/taster`（09-26 从旧号 Keepexperiencing 转来，github.com 旧地址自动跳转，但 github.io 站点地址不跳转，已全部改成新地址），公开，边建边推（Sam 09-25 授权）。TypeSafe MCA 已读：§4.2 输出归我们 → 可公开；§2.3(a) 不做任意文本打分。
- `.stignore` 忽略 `.git`：仓库历史只在建仓那台机上，跨机靠 GitHub。
