#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 data/<来源>/*.meta.json + *.units.json + *.jsonl + rulers/ 打包成 site/data.js。

  python tools/build.py

每章只取「当前尺指纹」下最新的一条；尺改过的旧记录不上站（不可比）。「读过」的定义见 core.is_read。
一章都没读过的资源不上书架（只拉过目录的，站上没东西可看）。
章的 url 可以是 null（Gutenberg 各章没有单独的页面），前端就只显示章名。
🔴 只打包读数、章名、链接，不打包正文。
🔴 站上不点名判断模型的供应商（客户协议 §16.4）：尺的模型 id 只露版本号。
"""
import glob, json, os, re, sys, time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import ROOT, data_path, is_read, latest_reads, load_json, load_ruler, ruler_fp

PUBLIC_META = ("key", "source", "source_name", "id", "title", "author", "lang", "type", "license",
               "read_url", "tags", "ruler", "units_total", "added")


def model_version(model_id):
    m = re.search(r"(\d+(?:\.\d+)+)$", model_id)
    return m.group(1) if m else "?"


def bundle():
    """→ site/data.js 里的那个对象（不写文件）。check.py 拿它核对 data.js 是不是最新的。"""
    rulers, shelf, skipped = {}, [], 0
    for mp in sorted(glob.glob(os.path.join(ROOT, "data", "*", "*.meta.json"))):
        meta = load_json(mp)
        key, rname = meta["key"], meta["ruler"]
        if rname not in rulers:
            r = load_ruler(rname)
            rulers[rname] = {"版本": r["版本"], "适用": r.get("适用"), "模型版本": model_version(r["模型"]),
                             "截断字数": r["截断字数"], "指纹": ruler_fp(r), "题": r["题"]}
        fp = rulers[rname]["指纹"]

        latest = latest_reads(key, fp)
        units = {u["n"]: dict(u) for u in load_json(data_path(key, "units.json"), [])}
        for n, rec in list(latest.items()):
            if n in units and not is_read(rec, units[n]):
                # 目录变过序号——读数挂错章比没有更糟
                print("  ⚠️ 第 %d 章目录是「%s」，读数记的是「%s」，不上站" % (n, units[n]["label"], rec["label"]))
                del latest[n]
                continue
            u = units.setdefault(n, {"n": n, "label": rec["label"], "title": rec["title"], "url": rec.get("url")})
            u.update({"字数": rec["字数"], "送出字数": rec["送出字数"], "utc": rec["utc"], "answers": rec["answers"]})
        if not latest:
            print("%s《%s》：%d 章，一章都没读过，不上书架" % (key, meta["title"], len(units)))
            skipped += 1
            continue
        pub = {k: meta.get(k) for k in PUBLIC_META}
        pub["units"] = [units[n] for n in sorted(units)]
        pub["units_read"] = len(latest)
        shelf.append(pub)
        print("%s《%s》：%d 章，已试读 %d" % (key, meta["title"], len(units), len(latest)))

    print("→ site/data.js（%d 个资源%s）" % (len(shelf), "；%d 个没读过的没上架" % skipped if skipped else ""))
    return {"built": time.strftime("%Y-%m-%d %H:%MZ", time.gmtime()), "rulers": rulers, "shelf": shelf}


def build():
    b = bundle()
    with open(os.path.join(ROOT, "site", "data.js"), "w", encoding="utf-8", newline="\n") as f:
        f.write("window.TASTER = " + json.dumps(b, ensure_ascii=False, separators=(",", ":")) + ";\n")
    return b


if __name__ == "__main__":
    build()
