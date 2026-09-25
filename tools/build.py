#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 catalog.json + rulers/ + data/*.jsonl 打包成 site/data.js，给静态页面用。

  python tools/build.py

每章只取「当前尺指纹」下最新的一条记录；尺改过的旧记录不上站（不可比）。
🔴 只打包读数和章名，不打包正文——正文在 texts/，不进仓库也不上站。
"""
import json, os, sys, time, urllib.parse

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from taste import ruler_fp


def source_url(res, page):
    s = res.get("source", {})
    if s.get("kind") == "wikisource" and page:
        return "https://%s.wikisource.org/wiki/%s" % (s["lang"], urllib.parse.quote(page))
    return None


def main():
    cat = json.load(open(os.path.join(ROOT, "catalog.json"), encoding="utf-8"))
    rulers, out = {}, []
    for res in cat["resources"]:
        rid = res["ruler"]
        if rid not in rulers:
            r = json.load(open(os.path.join(ROOT, "rulers", rid + ".json"), encoding="utf-8"))
            rulers[rid] = {"版本": r["版本"], "适用": r.get("适用"), "模型": r["模型"],
                           "截断字数": r["截断字数"], "指纹": ruler_fp(r), "题": r["题"]}
        fp = rulers[rid]["指纹"]

        latest = {}
        log = os.path.join(ROOT, "data", res["id"] + ".jsonl")
        if os.path.exists(log):
            for line in open(log, encoding="utf-8"):
                rec = json.loads(line)
                if rec["尺指纹"] == fp:
                    latest[rec["n"]] = rec          # 后写的覆盖先写的

        idx_path = os.path.join(ROOT, "texts", res["id"], "index.json")
        index = json.load(open(idx_path, encoding="utf-8")) if os.path.exists(idx_path) else []
        ns = sorted(set(u["n"] for u in index) | set(latest))
        by_n = {u["n"]: u for u in index}
        units = []
        for n in ns:
            rec, u = latest.get(n), by_n.get(n, {})
            src = rec or u
            units.append({
                "n": n, "label": src.get("label"), "title": src.get("title"),
                "url": source_url(res, src.get("page")) if res["text_policy"] == "全文可放" else None,
                "字数": rec and rec["字数"], "送出字数": rec and rec["送出字数"],
                "utc": rec and rec["utc"], "answers": rec and rec["answers"],
            })
        pub = {k: v for k, v in res.items() if k != "source"}
        pub["units"] = units
        if res["text_policy"] == "全文可放":
            pub["read_url"] = source_url(res, res["source"].get("page"))
        out.append(pub)
        print("%s：%d 章，已试读 %d" % (res["title"], len(units), len(latest)))

    bundle = {"built": time.strftime("%Y-%m-%d %H:%MZ", time.gmtime()), "rulers": rulers, "resources": out}
    os.makedirs(os.path.join(ROOT, "site"), exist_ok=True)
    with open(os.path.join(ROOT, "site", "data.js"), "w", encoding="utf-8", newline="\n") as f:
        f.write("window.TASTER = " + json.dumps(bundle, ensure_ascii=False, separators=(",", ":")) + ";\n")
    print("→ site/data.js")


if __name__ == "__main__":
    main()
