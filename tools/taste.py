#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
试读：把一个资源已拉下来的每一章，按它的尺问一遍判断模型，读数追加进 data/<来源>/<编号>.jsonl。

  python tools/taste.py wikisource-zh:三國演義 --dry-run   # 只报要调几次、送多少字，不花钱
  python tools/taste.py wikisource-zh:三國演義 --limit 10  # 试读前 10 章
  python tools/taste.py <键> --max-calls 200               # 单次最多调 200 次（Actions 用来封顶）

一章 = 一次调用，一次带上尺上所有题。
跳过规则：同一章 + 同一份正文（正文指纹）+ 同一把尺（尺指纹）已有记录 → 不重跑。
读数原样存，不经人手；翻成人话是前端按固定模板做的。
实际模型 ≠ 尺上钉的模型 → 立刻停，不可比的数据不落盘。
"""
import argparse, json, os, sys, time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import data_path, load_json, load_ruler, meta_path, ruler_fp, text_dir, text_fp


def plan(key, limit=None):
    meta = load_json(meta_path(key)) or sys.exit("没有 %s 的元数据，先跑 fetch.py" % key)
    ruler = load_ruler(meta["ruler"])
    fp, cut = ruler_fp(ruler), ruler["截断字数"]
    units = load_json(os.path.join(text_dir(key), "index.json"), [])[:limit]
    done = set()
    log = data_path(key)
    if os.path.exists(log):
        for line in open(log, encoding="utf-8"):
            r = json.loads(line)
            done.add((r["n"], r["正文指纹"], r["尺指纹"]))
    todo, missing = [], 0
    for u in units:
        p = os.path.join(text_dir(key), "%03d.txt" % u["n"])
        if not os.path.exists(p):
            missing += 1
            continue
        text = open(p, encoding="utf-8").read()
        if (u["n"], text_fp(text), fp) not in done:
            todo.append((u, text))
    return meta, ruler, fp, todo, missing


def taste(key, limit=None, dry_run=False, max_calls=None, log=print):
    from judge import ask
    meta, ruler, fp, todo, missing = plan(key, limit)
    cut, model = ruler["截断字数"], ruler["模型"]
    if max_calls is not None and len(todo) > max_calls:
        log("⚠️ 待读 %d 章，超过单次上限 %d，这次只读前 %d 章" % (len(todo), max_calls, max_calls))
        todo = todo[:max_calls]
    sent = sum(min(len(t), cut) for _, t in todo)
    log("%s《%s》· 尺 %s（%s）· 待读 %d 章，送出约 %s 字（%d 章超 %d 字截断）%s"
        % (key, meta["title"], ruler["版本"], fp, len(todo), "{:,}".format(sent),
           sum(1 for _, t in todo if len(t) > cut), cut, "；%d 章还没拉正文" % missing if missing else ""))
    if dry_run or not todo:
        return {"calls": 0, "planned": len(todo), "chars": sent}

    questions = {k: m["q"] for k, m in ruler["题"].items()}
    tin = tout = n = 0
    path = data_path(key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    for u, text in todo:
        out, ms = ask(text[:cut], questions, model=model)
        if out.get("model") != model:
            raise RuntimeError("实际模型 %s ≠ 尺上钉的 %s，停。不可比的数据不落盘" % (out.get("model"), model))
        rec = {
            "utc": time.strftime("%Y-%m-%d %H:%MZ", time.gmtime()),
            "n": u["n"], "label": u["label"], "title": u["title"], "url": u.get("url"),
            "字数": len(text), "送出字数": min(len(text), cut), "正文指纹": text_fp(text),
            "尺": ruler["版本"], "尺指纹": fp, "实际模型": out.get("model"),
            "answers": out["answers"],
        }
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        tin += out["usage"]["input_tokens"]; tout += out["usage"]["output_tokens"]; n += 1
        log("  ✓ %s %s · %.1f 秒" % (u["label"], u["title"], ms / 1000))
    log("完成 %d 章 · 共 %s tok 进 / %s tok 出" % (n, "{:,}".format(tin), "{:,}".format(tout)))
    return {"calls": n, "planned": len(todo), "chars": sent, "tokens_in": tin, "tokens_out": tout}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("key")
    ap.add_argument("--limit", type=int, help="只读前 N 章")
    ap.add_argument("--max-calls", type=int, help="单次最多调几次")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    try:
        taste(a.key, a.limit, a.dry_run, a.max_calls)
    except RuntimeError as e:
        sys.exit("✗ %s" % e)


if __name__ == "__main__":
    main()
