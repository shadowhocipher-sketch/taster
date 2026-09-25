#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
试读：把一个资源的每一章按它的尺（rulers/*.json）问一遍 Jev，读数追加进 data/<id>.jsonl。

  python tools/taste.py sanguo --dry-run      # 只报要调几次、送多少字，不花钱
  python tools/taste.py sanguo --limit 10     # 试读前 10 章
  python tools/taste.py sanguo                # 全部（已读过的自动跳过）

一章 = 一次调用，一次带上尺上所有题。
跳过规则：同一章、同一份正文（正文指纹）、同一把尺（尺指纹）已经有记录 → 不重跑。
读数原样存，不经 Claude 之手；翻成人话是 build.py 按固定模板做的。

Jev 的调用封装用 Dev/Ops/network/jev.py（key 在那边的 .env，不进本仓库）。
别的机器 / 别的人：设环境变量 JEV_DIR 指向放 jev.py 的目录。
"""
import argparse, hashlib, json, os, sys, time

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JEV_DIR = os.environ.get("JEV_DIR") or os.path.normpath(os.path.join(ROOT, "..", "..", "Ops", "network"))


def ruler_fp(r):
    core = {k: r[k] for k in ("模型", "截断字数", "题")}
    return hashlib.sha256(json.dumps(core, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:8]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("id")
    ap.add_argument("--limit", type=int, help="只读前 N 章")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    cat = json.load(open(os.path.join(ROOT, "catalog.json"), encoding="utf-8"))
    res = next((r for r in cat["resources"] if r["id"] == a.id), None) or sys.exit("catalog 里没有 %r" % a.id)
    ruler = json.load(open(os.path.join(ROOT, "rulers", res["ruler"] + ".json"), encoding="utf-8"))
    fp, cut, model = ruler_fp(ruler), ruler["截断字数"], ruler["模型"]
    questions = {k: m["q"] for k, m in ruler["题"].items()}

    tdir = os.path.join(ROOT, "texts", a.id)
    units = json.load(open(os.path.join(tdir, "index.json"), encoding="utf-8"))[: a.limit]
    log = os.path.join(ROOT, "data", a.id + ".jsonl")
    done = set()
    if os.path.exists(log):
        for line in open(log, encoding="utf-8"):
            r = json.loads(line)
            done.add((r["n"], r["正文指纹"], r["尺指纹"]))

    todo = []
    for u in units:
        p = os.path.join(tdir, "%03d.txt" % u["n"])
        if not os.path.exists(p):
            print("  ⚠️ %s 还没拉正文（先跑 fetch.py），跳过" % u["label"])
            continue
        text = open(p, encoding="utf-8").read()
        tfp = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
        if (u["n"], tfp, fp) not in done:
            todo.append((u, text, tfp))

    sent = sum(min(len(t), cut) for _, t, _ in todo)
    over = sum(1 for _, t, _ in todo if len(t) > cut)
    print("%s · 尺 %s（%s）· %s · 待读 %d 章，送出约 %s 字（%d 章超 %d 字会截断）"
          % (res["title"], ruler["版本"], fp, model, len(todo), "{:,}".format(sent), over, cut))
    if a.dry_run or not todo:
        return

    sys.path.insert(0, JEV_DIR)
    try:
        from jev import ask
    except ImportError:
        sys.exit("找不到 jev.py（查过 %s）。设 JEV_DIR 指过去。" % JEV_DIR)

    os.makedirs(os.path.dirname(log), exist_ok=True)
    tin = tout = 0
    for u, text, tfp in todo:
        try:
            out, ms = ask(text[:cut], questions, model=model)
        except RuntimeError as e:
            sys.exit("Jev 报错，停在 %s：%s" % (u["label"], e))
        if out.get("model") != model:
            sys.exit("🔴 实际模型 %s ≠ 尺上钉的 %s，停。不可比的数据不落盘。" % (out.get("model"), model))
        rec = {
            "utc": time.strftime("%Y-%m-%d %H:%MZ", time.gmtime()),
            "n": u["n"], "label": u["label"], "title": u["title"], "page": u.get("page"),
            "字数": len(text), "送出字数": min(len(text), cut), "正文指纹": tfp,
            "尺": ruler["版本"], "尺指纹": fp, "实际模型": out.get("model"),
            "answers": out["answers"],
        }
        with open(log, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        tin += out["usage"]["input_tokens"]; tout += out["usage"]["output_tokens"]
        print("  ✓ %s %s · %.1f 秒" % (u["label"], u["title"], ms / 1000))
    print("完成 %d 章 · 共 %s tok 进 / %s tok 出" % (len(todo), "{:,}".format(tin), "{:,}".format(tout)))


if __name__ == "__main__":
    main()
