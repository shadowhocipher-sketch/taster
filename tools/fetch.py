#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把一个资源的元数据和正文拉到本地。正文进 texts/（不进仓库），元数据进 data/<来源>/<编号>.meta.json（进仓库）。

  python tools/fetch.py wikisource-zh:三國演義 --limit 10
  python tools/fetch.py gutenberg:1342 --type 小说
  python tools/fetch.py <键> --meta-only          # 只拉元数据和目录，正文不落盘

--meta-only / 预估：正文不写进 texts/。但目录要从全文切的书源（Gutenberg、维基文库单页作品）会把全文读进内存，
  只是不存盘；维基文库分章的作品只读目录页。
only=（request.py 用）：只拉这几章的正文——Issue 请求只拉批准的那批，别的章一个字不拉。
许可不是「公版 / 自由许可」的一律拒绝——有版权的书、只在美国公版的书不拉正文。
已经拉过的章节不重拉。meta.json 里人工改过的 type / ruler / tags 不会被覆盖。
"""
import argparse, os, sys, time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sources
from core import data_path, load_json, meta_path, parse_key, save_json, text_dir

DEFAULT_RULER = {"小说": "novel_v1"}


def fetch(key, limit=None, rtype=None, ruler=None, meta_only=False, log=print, only=None):
    """→ (meta, 目录)。limit：只拉目录前 N 章的正文；only：章号集合，只拉这几章的正文。"""
    src_id, rid = parse_key(key)
    src = sources.get(src_id)
    m = src.meta(rid)
    if m["license"] not in sources.TASTEABLE_LICENSES:
        note = "（%s）" % m["license_note"] if m.get("license_note") else ""
        raise ValueError("%s 的许可是「%s」%s，不拉正文、不试读" % (key, m["license"], note))

    old = load_json(meta_path(key), {})
    rtype = rtype or old.get("type") or m.get("type") or "小说"
    meta = dict(old)
    meta.update(m)
    meta.update({"key": key, "source": src_id, "id": rid, "type": rtype,
                 "ruler": ruler or old.get("ruler") or DEFAULT_RULER.get(rtype),
                 "tags": old.get("tags") or m.get("tags") or [],
                 "added": old.get("added") or time.strftime("%Y-%m-%d", time.gmtime())})
    if not meta["ruler"]:
        raise ValueError("「%s」这类资源还没有尺，不能试读" % rtype)

    units = src.units(rid)
    if not units:
        raise ValueError("%s 拆不出章节" % key)
    for u in units:
        if hasattr(src, "unit_url"):
            u["url"] = src.unit_url(u)
    meta["units_total"] = len(units)
    save_json(meta_path(key), meta)
    # 目录（章名 + 原文链接）进仓库，站点打包不依赖 texts/；ref 是书源内部用的，留在 texts/
    save_json(data_path(key, "units.json"), [{k: u.get(k) for k in ("n", "label", "title", "url")} for u in units])
    d = text_dir(key)
    save_json(os.path.join(d, "index.json"), units)
    log("%s《%s》：目录 %d 章" % (key, meta["title"], len(units)))
    if meta_only:
        return meta, units

    for u in units[:limit]:
        p = os.path.join(d, "%03d.txt" % u["n"])
        if (only is not None and u["n"] not in only) or os.path.exists(p):
            continue
        t = src.text(rid, u).strip()
        if not t:
            log("  ⚠️ %s 取不到正文，跳过" % u["label"])
            continue
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(t)
        log("  %s %s · %s 字" % (u["label"], u["title"], "{:,}".format(len(t))))
    return meta, units


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("key")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--type", dest="rtype")
    ap.add_argument("--ruler")
    ap.add_argument("--meta-only", action="store_true", help="只存元数据和目录，正文不落盘（有的书源切目录要把全文读进内存）")
    a = ap.parse_args()
    try:
        fetch(a.key, a.limit, a.rtype, a.ruler, a.meta_only)
    except ValueError as e:
        sys.exit("✗ %s" % e)


if __name__ == "__main__":
    main()
