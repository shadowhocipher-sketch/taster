# -*- coding: utf-8 -*-
"""
维基文库（zh / en）。编号 = 作品主页面标题，如 wikisource-zh:三國演義。

章节 = 主页面目录里的子页面链接（[[/第001回|第一回]] 标题……），按目录顺序。
目录里六成以上是「/第…」时只留这些（去掉序、凡例、附录）。没有子页面的单页作品按 6000 字切块。
维基文库只收公版和自由许可的作品，所以 license 记「公版」。
"""
import html, re, time, urllib.parse

from core import chunk
from . import http


class Wikisource:
    def __init__(self, lang):
        self.lang = lang
        self.id = "wikisource-" + lang
        self.name = "维基文库" if lang == "zh" else "Wikisource (%s)" % lang
        self._page_cache = {}

    # ---------------------------------------------------------------- API
    def _api(self, **p):
        p.update(format="json", formatversion="2")
        return http.get("https://%s.wikisource.org/w/api.php?%s" % (self.lang, urllib.parse.urlencode(p)))

    def _wikitext(self, page):
        d = self._api(action="parse", page=page, prop="wikitext", redirects=1)
        if "error" in d:
            raise ValueError("维基文库没有这个页面：%s（%s）" % (page, d["error"].get("info")))
        return d["parse"]["wikitext"]

    def _plain(self, page):
        """用渲染后的 HTML 抽正文。不用 TextExtracts：正文包在 <div class=prose> 里的页（如三国第002回）它返回空串。"""
        if page in self._page_cache:
            return self._page_cache[page]
        time.sleep(3)
        h = self._api(action="parse", page=page, prop="text", disablelimitreport=1, redirects=1)["parse"]["text"]
        h = re.sub(r"<(table|style|sup)\b.*?</\1>", "", h, flags=re.S)       # 上一回/下一回导航表、注脚号
        h = re.sub(r'<(div|span)[^>]*class="[^"]*(ws-noexport|mw-editsection)[^"]*"[^>]*>.*?</\1>', "", h, flags=re.S)
        h = re.sub(r"<br\s*/?>|</(p|dd|div|li|h\d)>", "\n", h)
        t = html.unescape(re.sub(r"<[^>]+>", "", h))
        t = re.sub(r"^\s*(返回頁首|返回页首|Back to top)\s*$", "", t, flags=re.M)
        t = re.sub(r"\n\s*\n+", "\n", t).strip()
        self._page_cache[page] = t
        return t

    # ---------------------------------------------------------------- 接口
    def meta(self, rid):
        wt = self._wikitext(rid)

        def field(name):
            m = re.search(r"\|\s*%s\s*=\s*([^\n|]*)" % name, wt)
            v = m.group(1).strip() if m else ""
            return re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", v).replace("'''", "").strip()

        return {
            "title": field("title") or rid, "author": field("author"), "lang": self.lang,
            "license": "公版", "source_name": self.name,
            "read_url": "https://%s.wikisource.org/wiki/%s" % (self.lang, urllib.parse.quote(rid.replace(" ", "_"))),
        }

    def units(self, rid):
        wt = self._wikitext(rid)
        # 🔴 链接后面只许吃同一行的空白：用 \s* 会跨过换行，把下一行（如「凡例」后的第一回）当成标题吞掉
        links = re.findall(r"^\*+[ \t　]*\[\[(/[^|\]]+)\|?([^\]]*)\]\][ \t　]*(.*)$", wt, flags=re.M)
        if links:
            chap = [l for l in links if l[0].startswith("/第")]
            if len(chap) >= 0.6 * len(links):
                links = chap
            out = []
            for sub, label, rest in links:
                label = (label or sub.lstrip("/")).strip()
                title = re.sub(r"[\s　]+", " ", re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", rest)).strip()
                out.append({"n": len(out) + 1, "label": label, "title": title, "ref": {"page": rid + sub}})
            return out
        # 单页作品：整页切块
        parts = chunk(self._plain(rid))
        return [{"n": i + 1, "label": "第%d段" % (i + 1), "title": "", "ref": {"page": rid, "chunk": i}}
                for i in range(len(parts))]

    def text(self, rid, unit):
        ref = unit["ref"]
        t = self._plain(ref["page"])
        if "chunk" in ref:
            return chunk(t)[ref["chunk"]]
        return t

    def unit_url(self, unit):
        return "https://%s.wikisource.org/wiki/%s" % (self.lang, urllib.parse.quote(unit["ref"]["page"].replace(" ", "_")))
