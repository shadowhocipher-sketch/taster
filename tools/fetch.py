#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把一个资源的全文拉到本地 texts/<id>/（texts/ 不进仓库）。

  python tools/fetch.py sanguo              # 拉目录 + 全部章节
  python tools/fetch.py sanguo --limit 10   # 只拉前 10 章

只走官方渠道：维基文库用 MediaWiki API（action=parse），每章间隔 3 秒，429 就退避，不爬网页。
已经拉过的章节不重拉。
"""
import argparse, html, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = {"User-Agent": "Je-v-Taster/0.1 (reading-data research; slow, backs off on 429)"}


def load_resource(rid):
    cat = json.load(open(os.path.join(ROOT, "catalog.json"), encoding="utf-8"))
    for r in cat["resources"]:
        if r["id"] == rid:
            return r
    sys.exit("catalog.json 里没有 %r" % rid)


def wiki(lang, **p):
    p.update(format="json", formatversion="2")
    url = "https://%s.wikisource.org/w/api.php?%s" % (lang, urllib.parse.urlencode(p))
    for i in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.load(r)
        except Exception as e:
            if i == 4:
                raise
            # 429 = 嫌我们快了（2026-09-25 拉到第九回就撞上）。照 Retry-After 等，没给就指数退避
            wait = 5 * 2 ** i
            if isinstance(e, urllib.error.HTTPError) and e.code == 429:
                wait = max(wait, int(e.headers.get("Retry-After") or 0), 30)
            print("  重试（%s），等 %d 秒" % (e, wait))
            time.sleep(wait)


def wikisource_units(src):
    """从主页面目录读出章节：*[[/第001回|第一回]]　　宴桃園……　　斬黃巾……"""
    wt = wiki(src["lang"], action="parse", page=src["page"], prop="wikitext")["parse"]["wikitext"]
    pat = re.compile(r"^\*\s*\[\[(%s[^|\]]*)\|([^\]]+)\]\]\s*(.*)$" % re.escape(src["unit_prefix"]), re.M)
    units = []
    for sub, label, rest in pat.findall(wt):
        title = re.sub(r"[\s　]+", " ", rest).strip()
        units.append({"n": len(units) + 1, "page": src["page"] + sub, "label": label.strip(), "title": title})
    return units


def wikisource_text(src, page):
    """用渲染后的 HTML 抽正文。不用 TextExtracts：正文包在 <div class=prose> 里的页（如第002回）它返回空串。"""
    h = wiki(src["lang"], action="parse", page=page, prop="text", disablelimitreport=1)["parse"]["text"]
    h = re.sub(r"<(table|style|sup)\b.*?</\1>", "", h, flags=re.S)      # 上一回/下一回导航表、注脚号
    h = re.sub(r"<br\s*/?>|</(p|dd|div|li|h\d)>", "\n", h)
    t = html.unescape(re.sub(r"<[^>]+>", "", h))
    t = re.sub(r"^\s*返回頁首\s*$", "", t, flags=re.M)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("id")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()

    res = load_resource(a.id)
    src = res["source"]
    if src["kind"] != "wikisource":
        sys.exit("还不支持的来源：%s" % src["kind"])
    out = os.path.join(ROOT, "texts", a.id)
    os.makedirs(out, exist_ok=True)

    units = wikisource_units(src)
    if not units:
        sys.exit("目录解析不出章节，检查 unit_prefix")
    json.dump(units, open(os.path.join(out, "index.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("%s：目录 %d 章" % (res["title"], len(units)))

    for u in units[: a.limit]:
        path = os.path.join(out, "%03d.txt" % u["n"])
        if os.path.exists(path):
            continue
        time.sleep(3)
        text = wikisource_text(src, u["page"]).strip()
        if not text:
            print("  ⚠️ %s 取不到正文，跳过" % u["label"])
            continue
        open(path, "w", encoding="utf-8", newline="\n").write(text)
        print("  %s %s · %s 字" % (u["label"], u["title"], "{:,}".format(len(text))))


if __name__ == "__main__":
    main()
