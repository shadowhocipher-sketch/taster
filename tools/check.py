#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
红线检查。每个 PR 上自动跑（.github/workflows/check.yml），本机也能跑；不联网、不花钱。

  python tools/check.py

查四样，哪样不过就列出来、退出码 1：
  1. 正文不进仓库：texts/ 下没有被 git 跟踪的文件；读数记录只有 taste.py 写的那几个字段。
  2. 网站（site/）不点名判断模型和它的供应商（客户协议 §16.4）。要找的名字从尺上钉的模型 id 和
     judge.py 的默认接口地址里取，这里不另写一份。
  3. 读数对得上（读数进仓库的验收标准，2026-10-03 定）：尺指纹 = 现在这把尺、实际模型 = 尺上钉的模型、
     章号在目录里、章名标签对得上、题跟尺一致；目录章数 = meta 的 units_total。
  4. site/data.js 是最新的：跟 build.py 用 data/ 重新算的一致（打包时间除外）。
编辑导读有没有跟读数分开、有没有借读数下结论——机器判断不了，归人审（.github/pull_request_template.md 的检查表）。
"""
import glob, io, json, os, re, subprocess, sys, urllib.parse
from contextlib import redirect_stdout

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build, core, judge  # noqa: E402

REC_FIELDS = {"utc", "n", "label", "title", "url", "字数", "送出字数", "正文指纹", "尺", "尺指纹", "实际模型", "answers"}
SITE_EXT = (".html", ".js", ".mjs", ".css", ".json", ".md", ".txt", ".svg")


def rel(p):
    return os.path.relpath(p, core.ROOT).replace(os.sep, "/")


def check_texts():
    try:
        out = subprocess.run(["git", "ls-files", "-z", "--", "texts"], cwd=core.ROOT,
                             capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return ["不是 git 仓库，查不了 texts/ 有没有被跟踪"]
    return ["%s 被 git 跟踪了" % f for f in out.decode("utf-8").split("\0") if f]


def banned_names():
    """站上不许出现的名字：尺上钉的模型 id 去掉版本号（xxx-1.13.0 → xxx），加上默认接口域名和路径里的词。"""
    names = set()
    for p in glob.glob(os.path.join(core.ROOT, "rulers", "*.json")):
        m = re.match(r"[a-z]+", core.load_json(p)["模型"].lower())
        if m:
            names.add(m.group())
    u = urllib.parse.urlsplit(judge.DEFAULT_ENDPOINT)
    generic = {"api", "www", "com", "ai", "io", "net", "org"}
    for w in (u.hostname or "").split(".") + u.path.split("/"):
        w = w.lower()
        if re.fullmatch(r"[a-z]{3,}", w) and w not in generic:
            names.add(w)
    return names


def check_site():
    """短名字按整词找（避开 Jevons 这类）；长名字再在去掉空格、连字符后找一遍（抓「Type Safe」「System-One」）。"""
    names, bad = sorted(banned_names()), []
    for dp, _, files in os.walk(os.path.join(core.ROOT, "site")):
        for fn in sorted(files):
            if not fn.endswith(SITE_EXT):
                continue
            p = os.path.join(dp, fn)
            with open(p, encoding="utf-8", errors="replace") as f:
                text = f.read().lower()
            squeezed = re.sub(r"[\s\-_.]+", "", text)
            for n in names:
                if re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(n), text) or (len(n) >= 6 and n in squeezed):
                    bad.append("%s：出现了判断模型或供应商的名字（%d 个字母，以 %s 开头）" % (rel(p), len(n), n[0]))
    return bad


def check_data():
    bad, rulers = [], {}
    for mp in sorted(glob.glob(os.path.join(core.ROOT, "data", "*", "*.meta.json"))):
        meta = core.load_json(mp)
        key = meta.get("key")
        try:
            ok = os.path.normcase(core.meta_path(key)) == os.path.normcase(mp)
        except (ValueError, AttributeError, TypeError):
            ok = False
        if not ok:
            bad.append("%s：key %r 跟文件名对不上" % (rel(mp), key))
            continue
        rname = meta.get("ruler")
        if rname not in rulers:
            try:
                rulers[rname] = core.load_ruler(rname)
            except (ValueError, TypeError):
                bad.append("%s：没有这把尺 %r" % (rel(mp), rname))
                continue
        r = rulers[rname]
        fp = core.ruler_fp(r)

        units = core.load_json(core.data_path(key, "units.json"))
        if units is None:
            bad.append("%s：没有 units.json" % rel(mp))
            continue
        by_n = {u["n"]: u for u in units}
        if len(by_n) != len(units):
            bad.append("%s：目录章号有重复" % rel(core.data_path(key, "units.json")))
        if meta.get("units_total") != len(units):
            bad.append("%s：units_total=%s，目录有 %d 章" % (rel(mp), meta.get("units_total"), len(units)))

        p = core.data_path(key)
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            lines = f.readlines()
        for i, line in enumerate(lines, 1):
            if not line.strip():
                continue
            where = "%s:%d" % (rel(p), i)
            try:
                rec = json.loads(line)
            except ValueError:
                bad.append("%s：不是 JSON" % where)
                continue
            extra, missing = set(rec) - REC_FIELDS, REC_FIELDS - set(rec)
            if extra:
                bad.append("%s：多了字段 %s" % (where, "、".join(sorted(extra))))
            if missing:
                bad.append("%s：少了字段 %s" % (where, "、".join(sorted(missing))))
                continue
            if rec["尺指纹"] != fp:
                bad.append("%s：尺指纹 %s，现在这把尺是 %s" % (where, rec["尺指纹"], fp))
            if rec["实际模型"] != r["模型"] or rec["尺"] != r["版本"]:
                bad.append("%s：模型或尺的版本跟尺上钉的不一样" % where)
            u = by_n.get(rec["n"])
            if u is None:
                bad.append("%s：第 %s 章不在目录里" % (where, rec["n"]))
            elif not core.is_read(rec, u):
                bad.append("%s：章名「%s」，目录是「%s」" % (where, rec["label"], u["label"]))
            if not isinstance(rec["answers"], dict) or set(rec["answers"]) != set(r["题"]):
                bad.append("%s：题跟尺不一致" % where)

    for p in sorted(glob.glob(os.path.join(core.ROOT, "data", "*", "*.jsonl"))
                    + glob.glob(os.path.join(core.ROOT, "data", "*", "*.units.json"))):
        base = re.sub(r"\.(jsonl|units\.json)$", "", p)
        if not os.path.exists(base + ".meta.json"):
            bad.append("%s：没有对应的 meta.json" % rel(p))
    return bad


def check_datajs():
    p = os.path.join(core.ROOT, "site", "data.js")
    if not os.path.exists(p):
        return ["没有 site/data.js：跑 python tools/build.py"]
    with open(p, encoding="utf-8") as f:
        m = re.fullmatch(r"window\.TASTER = (.*);\s*", f.read(), re.S)
    try:
        cur = json.loads(m.group(1)) if m else None
    except ValueError:
        cur = None
    if cur is None:
        return ["site/data.js 格式不对：跑 python tools/build.py"]
    with redirect_stdout(io.StringIO()):
        fresh = build.bundle()
    cur.pop("built", None)
    fresh.pop("built", None)
    return [] if cur == fresh else ["site/data.js 跟 data/ 对不上：跑 python tools/build.py 再提交"]


CHECKS = (("正文不进仓库", check_texts), ("站上不点名判断模型", check_site),
          ("读数对得上", check_data), ("site/data.js 是最新的", check_datajs))


def main():
    failed = 0
    for name, fn in CHECKS:
        bad = fn()
        print("%s %s" % ("✗" if bad else "✓", name))
        for b in bad:
            print("    " + b)
        failed += bool(bad)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
