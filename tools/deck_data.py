#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 data/gutenberg/1342.jsonl 生成 site/decks/pride-and-prejudice/data.js（读数部分）。

  python tools/deck_data.py                     # 填充 deck 数据
  python tools/deck_data.py --check             # 只看进度：几章有读数，还差几章

只存原始读数，翻人话在前端按 ARCHITECTURE.md §4 的固定模板做（判定线同 app.js reading()）：
  noul   ≥ 0.65 记「是」、≤ 0.35 记「否」、中间「拿不准」
  choice 最高概率 < 0.4 → 「拿不准」（不用 confidence 字段——站规看的是最高概率）
这里不写任何观点。
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import ROOT, data_path, load_ruler, ruler_fp

DECK_DATA = os.path.join(ROOT, "site", "decks", "pride-and-prejudice", "data.js")
KEY = "gutenberg:1342"
UNITS_TOTAL = 61


def top_prob(fe):
    """选择题最高概率；没有 probabilities 就 None。"""
    p = fe.get("probabilities") or {}
    return round(max(p.values()), 4) if p else None


def collect():
    """→ (readings, 最后一条记录)。readings 按章号排好，未读的章不在列表里。"""
    meta = {}
    if os.path.exists(data_path(KEY)):
        with open(data_path(KEY), encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                meta[rec["n"]] = rec             # 同章多次读：留最后一次（taste.py 追加写，新的在末尾）
    readings = []
    for n, rec in sorted(meta.items()):
        a = rec.get("answers", {})
        wn = a.get("想看下一章", {})
        fe = a.get("主要感受", {})
        readings.append({
            "n": n,
            "want_next": round(wn.get("noul", 0), 2) if wn.get("type") == "noul" else None,
            "feel": fe.get("choice") if fe.get("type") == "choice" else None,
            "feel_top": top_prob(fe) if fe.get("type") == "choice" else None,
        })
    return readings, (rec if meta else None)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只报进度不写文件")
    a = ap.parse_args(argv)

    readings, last = collect()
    total_units = UNITS_TOTAL
    print("有读数 %d / %d 章" % (len(readings), total_units))
    if a.check:
        return
    if not readings:
        print("还没有任何读数，data.js 不动")
        return

    ruler = load_ruler("novel_v1")
    model_v = (last or {}).get("实际模型", "")
    model_version = model_v.split("-", 1)[1] if "-" in model_v else model_v

    import time
    js = """/* Taster deck data · Pride and Prejudice
   由 tools/deck_data.py 从 data/gutenberg/1342.jsonl 生成，别手改。
   want_next = 「想看下一章」原始概率；feel/feel_top = 「主要感受」选项和它的最高概率。
   站上只有读数和固定模板翻译，没有观点。 */
window.DECK = {
  key: %s,
  title: "Pride and Prejudice",
  author: "Jane Austen",
  year: 1813,
  ruler: "novel_v1",
  ruler_fp: %s,
  model_version: %s,
  built: %s,
  units_total: %d,
  readings: %s
};
""" % (json.dumps(KEY), json.dumps(ruler_fp(ruler)), json.dumps(model_version),
       json.dumps(time.strftime("%Y-%m-%d %H:%MZ", time.gmtime())),
       total_units,
       json.dumps(readings, ensure_ascii=False))
    with open(DECK_DATA, "w", encoding="utf-8", newline="\n") as f:
        f.write(js)
    print("写入 %s（%d 章读数）" % (DECK_DATA, len(readings)))


if __name__ == "__main__":
    main()
