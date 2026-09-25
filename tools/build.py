#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 data/<来源>/*.meta.json + *.units.json + *.jsonl + rulers/ 打包成 site/data.js。

  python tools/build.py

每章只取「当前尺指纹」下最新的一条；尺改过的旧记录不上站（不可比）。
🔴 只打包读数、章名、链接，不打包正文。
🔴 站上不点名判断模型的供应商（客户协议 §16.4）：尺的模型 id 只露版本号。
"""
import glob, json, os, re, sys, time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import ROOT, data_path, load_json, load_ruler, ruler_fp

PUBLIC_META = ("key", "source", "source_name", "id", "title", "author", "lang", "type", "license",
               "read_url", "tags", "ruler", "units_total", "added")


def model_version(model_id):
    m = re.search(r"(\d+(?:\.\d+)+)$", model_id)
    return m.group(1) if m else "?"


def build():
    rulers, shelf = {}, []
    for mp in sorted(glob.glob(os.path.join(ROOT, "data", "*", "*.meta.json"))):
        meta = load_json(mp)
        key, rname = meta["key"], meta["ruler"]
        if rname not in rulers:
            r = load_ruler(rname)
            rulers[rname] = {"版本": r["版本"], "适用": r.get("适用"), "模型版本": model_version(r["模型"]),
                             "截断字数": r["截断字数"], "指纹": ruler_fp(r), "题": r["题"]}
        fp = rulers[rname]["指纹"]

        latest = {}
        log = data_path(key)
        if os.path.exists(log):
            for line in open(log, encoding="utf-8"):
                rec = json.loads(line)
                if rec["尺指纹"] == fp:
                    latest[rec["n"]] = rec          # 后写的覆盖先写的

        units = {u["n"]: dict(u) for u in load_json(data_path(key, "units.json"), [])}
        for n, rec in list(latest.items()):
            if n in units and units[n]["label"] != rec["label"]:
                # 目录变过序号（2026-09-25 解析 bug 让第一回丢失、全书错位一格）——读数挂错章比没有更糟
                print("  ⚠️ 第 %d 章目录是「%s」，读数记的是「%s」，不上站" % (n, units[n]["label"], rec["label"]))
                del latest[n]
                continue
            u = units.setdefault(n, {"n": n, "label": rec["label"], "title": rec["title"], "url": rec.get("url")})
            u.update({"字数": rec["字数"], "送出字数": rec["送出字数"], "utc": rec["utc"], "answers": rec["answers"]})
        pub = {k: meta.get(k) for k in PUBLIC_META}
        pub["units"] = [units[n] for n in sorted(units)]
        pub["units_read"] = len(latest)
        shelf.append(pub)
        print("%s《%s》：%d 章，已试读 %d" % (key, meta["title"], len(units), len(latest)))

    bundle = {"built": time.strftime("%Y-%m-%d %H:%MZ", time.gmtime()), "rulers": rulers, "shelf": shelf}
    out = os.path.join(ROOT, "site", "data.js")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("window.TASTER = " + json.dumps(bundle, ensure_ascii=False, separators=(",", ":")) + ";\n")
    print("→ site/data.js（%d 个资源）" % len(shelf))


if __name__ == "__main__":
    build()
