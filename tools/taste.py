#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
试读：把一个资源已拉下来的每一章，按它的尺问一遍判断模型，读数追加进 data/<来源>/<编号>.jsonl。

  python tools/taste.py wikisource-zh:三國演義 --dry-run   # 只报要调几次、送多少字，不花钱
  python tools/taste.py wikisource-zh:三國演義 --limit 10  # 试读前 10 章
  python tools/taste.py <键> --max-calls 200               # 单次最多调 200 次（Actions 用来封顶）

一章 = 一次调用，一次带上尺上所有题。
跳过规则：这一章读过（core.is_read：当前尺下有读数、章名标签对得上）而且正文指纹没变 → 不重跑。
  本机跑会重读「读过但正文变了」的章；Issue 请求（request.py）传 only=，只读批准的那批没读过的章，从不重读。
读数原样存，不经人手；翻成人话是前端按固定模板做的。
实际模型 ≠ 尺上钉的模型 → 立刻停，不可比的数据不落盘。
"""
import argparse, json, os, sys, time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import data_path, is_read, latest_reads, load_json, load_ruler, meta_path, ruler_fp, text_dir, text_fp


class JudgeError(RuntimeError):
    """判断模型那边出的错（接口报错、连不上、模型版本不对）。
    🔴 原文可能带接口回的原始报文、供应商名、模型 id：request.py 在公开的 Actions 日志和评论里只给固定措辞（2026-09-25）"""


def plan(key, limit=None, only=None):
    """→ (meta, 尺, 尺指纹, 待读 [(章, 正文)], 缺正文的章数)。
    only：章号集合，只看这几章（request.py 传批准的那批）；limit：只看目录前 N 章。"""
    meta = load_json(meta_path(key)) or sys.exit("没有 %s 的元数据，先跑 fetch.py" % key)
    ruler = load_ruler(meta["ruler"])
    fp = ruler_fp(ruler)
    units = load_json(os.path.join(text_dir(key), "index.json"), [])[:limit]
    if only is not None:
        units = [u for u in units if u["n"] in only]
    latest = latest_reads(key, fp)
    todo, missing = [], 0
    for u in units:
        p = os.path.join(text_dir(key), "%03d.txt" % u["n"])
        if not os.path.exists(p):
            missing += 1
            continue
        with open(p, encoding="utf-8") as f:
            text = f.read()
        rec = latest.get(u["n"])
        if not (is_read(rec, u) and rec.get("正文指纹") == text_fp(text)):
            todo.append((u, text))
    return meta, ruler, fp, todo, missing


def taste(key, limit=None, dry_run=False, max_calls=None, log=print, only=None):
    from judge import ask
    meta, ruler, fp, todo, missing = plan(key, limit, only)
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
        try:
            out, ms = ask(text[:cut], questions, model=model)
        except Exception as e:
            raise JudgeError(str(e)) from e
        if out.get("model") != model:
            raise JudgeError("实际模型 %s ≠ 尺上钉的 %s，停。不可比的数据不落盘" % (out.get("model"), model))
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
