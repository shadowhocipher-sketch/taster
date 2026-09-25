// 把 tools/og/og.html 截成 site/og.png（1200×630，社交分享预览图）。书架读数变了想更新图时跑一次：
//   node tools/og/render.mjs
// 需要本机装了 playwright（npm i -D playwright 或全局）；站点本身不依赖它。
import { chromium } from "playwright";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1 });
await page.goto(pathToFileURL(path.join(here, "og.html")).href);
await page.waitForTimeout(300);
await page.screenshot({ path: path.join(here, "..", "..", "site", "og.png") });
await browser.close();
console.log("→ site/og.png");
