# -*- coding: utf-8 -*-
"""
维基文库（zh / en）。编号 = 作品主页面标题，如 wikisource-zh:三國演義。

章节 = 主页面目录里的子页面链接（[[/第001回|第一回]] 标题……），按目录顺序。
目录里六成以上是「/第…」「/Chapter …」（含 Prologue / Epilogue）时只留这些（去掉序、凡例、附录）。
wikitext 里凑不出 3 个子页面链接（目录在转载的扫描页 <pages index=…/> 或模板里）→ 改看渲染后页面上的子页面链接。
还是没有 → 单页作品，按 6000 字切块；单页正文不到 3000 字（多半只是书名页 + 版权声明）就拒绝：拆不出章节。
维基文库只收公版和自由许可的作品，所以 license 记「公版」。
同一个实例里每页的 wikitext / HTML 只取一次；两次调用至少隔 GAP 秒。
"""
import html, re, time, urllib.parse

from core import chunk
from . import http

MIN_SINGLE = 3000      # 单页作品正文少于这么多字 = 没拆出章节，只剩书名页之类
_VERSIONS = re.compile(r"\{\{\s*(versions|disambiguation|translations)\b", re.I)
_CHAPTER_SUB = re.compile(r"(?i)/(chapter|prologue|epilogue)\b")
# 🔴 页眉、编辑链接要整块去掉（连同里面嵌套的同名标签）
_NOEXPORT = re.compile(r'<(div|span)\b[^>]*class="[^"]*(?:ws-noexport|mw-editsection)[^"]*"[^>]*>')


def _drop_blocks(h):
    """整块去掉 class 里带 ws-noexport / mw-editsection 的 div、span，按嵌套层数找到配对的结束标签。
    🔴 以前用非贪婪正则删到第一个 </div>：en 的页眉是好几层 div，只删掉一小块，
       「Pride and Prejudice, Volume I (1813)by Jane Austen…Chapter 2 → sister projects」全漏进正文（2026-09-25）。
       zh 三國演義的页眉是 table、没有这种 div，改前改后正文一字不差（第一回、第二回正文指纹核对过）"""
    out, i = [], 0
    while True:
        m = _NOEXPORT.search(h, i)
        if not m:
            out.append(h[i:])
            return "".join(out)
        out.append(h[i:m.start()])
        depth, j = 1, m.end()
        for t in re.finditer(r"<(/?)%s\b[^>]*>" % m.group(1), h[m.end():]):
            if t.group(1):
                depth -= 1
            elif not t.group().endswith("/>"):
                depth += 1
            if depth == 0:
                j = m.end() + t.end()
                break
        # 配不上结束标签（HTML 坏了）：只去掉开标签，不能把后面的正文一起吞了
        i = j


def clean_html(h):
    """渲染后的 HTML → 纯文本，一段一行。
    不用 TextExtracts：正文包在 <div class=prose> 里的页（如三国第002回）它返回空串。"""
    h = re.sub(r"<(table|style|sup)\b.*?</\1>", "", h, flags=re.S)       # 上一回/下一回导航表、注脚号
    h = _drop_blocks(h)
    h = re.sub(r"<br\s*/?>|</(p|dd|div|li|h\d)>", "\n", h)
    t = html.unescape(re.sub(r"<[^>]+>", "", h))
    t = re.sub(r"^\s*(返回頁首|返回页首|Back to top)\s*$", "", t, flags=re.M)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


class Wikisource:
    GAP = 3.0      # 两次 API 调用之间至少隔几秒（2026-09-25 拉到第九回撞上 429）

    def __init__(self, lang):
        self.lang = lang
        self.id = "wikisource-" + lang
        self.name = "维基文库" if lang == "zh" else "Wikisource (%s)" % lang
        self._wt_cache, self._html_cache, self._page_cache = {}, {}, {}
        self._last = 0.0

    # ---------------------------------------------------------------- API
    def _api(self, **p):
        wait = self.GAP - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        p.update(format="json", formatversion="2")
        try:
            return http.get("https://%s.wikisource.org/w/api.php?%s" % (self.lang, urllib.parse.urlencode(p)))
        finally:
            self._last = time.time()

    def _wikitext(self, page):
        # meta() 和 units() 都要主页面 wikitext：一个实例只取一次
        if page not in self._wt_cache:
            d = self._api(action="parse", page=page, prop="wikitext", redirects=1)
            if "error" in d:
                raise ValueError("维基文库没有这个页面：%s（%s）" % (page, d["error"].get("info")))
            self._wt_cache[page] = d["parse"]["wikitext"]
        return self._wt_cache[page]

    def _html(self, page):
        """渲染后的 HTML（转载进来的扫描页也在里面）。页面不存在（目录里的红链）→ 空串，fetch.py 记「取不到正文」跳过。"""
        if page not in self._html_cache:
            d = self._api(action="parse", page=page, prop="text", disablelimitreport=1, redirects=1)
            if "error" in d:
                if d["error"].get("code") != "missingtitle":
                    raise RuntimeError("维基文库取页面出错：%s（%s）" % (page, d["error"].get("code")))
                self._html_cache[page] = ""
            else:
                self._html_cache[page] = d["parse"]["text"]
        return self._html_cache[page]

    def _plain(self, page):
        if page not in self._page_cache:
            self._page_cache[page] = clean_html(self._html(page))
        return self._page_cache[page]

    # ---------------------------------------------------------------- 接口
    def meta(self, rid):
        wt = self._wikitext(rid)

        def field(name):
            # 值里可能有 [[链接|显示]]，链接内的 | 不是字段分隔（「[[A Christmas Carol (Dickens)|…]]」曾被截成半截）
            m = re.search(r"\|\s*%s\s*=\s*((?:\[\[[^\]]*\]\]|[^\n|])*)" % name, wt)
            v = m.group(1).strip() if m else ""
            v = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", v).replace("'''", "")
            return re.sub(r"^(Author|作者)\s*:\s*", "", v.strip())

        return {
            "title": field("title") or rid, "author": field("author"), "lang": self.lang,
            "license": "公版", "source_name": self.name,
            "read_url": "https://%s.wikisource.org/wiki/%s" % (self.lang, urllib.parse.quote(rid.replace(" ", "_"))),
        }

    def _toc_links(self, rid, wt):
        """目录里指向本作品子页面的链接 → [(子页面路径 "/X", 显示名, 同行标题)]，按出现顺序、去重。
        先认带 * 的列表行（能带上同行的回目）；没有就退一步，认全文任何位置的子页面链接
        （en 很多作品的目录是表格或模板，或者写绝对路径 [[作品/Chapter 1|…]]）。"""
        sub = r"(/[^|\]]+|%s/[^|\]]+)" % re.escape(rid)
        # 🔴 链接后面只许吃同一行的空白：用 \s* 会跨过换行，把下一行（如「凡例」后的第一回）当成标题吞掉
        rows = re.findall(r"^\*+[ \t　]*\[\[%s\|?([^\]]*)\]\][ \t　]*(.*)$" % sub, wt, flags=re.M)
        if len(rows) < 3:
            rows = [(s, l, "") for s, l in re.findall(r"\[\[%s\|?([^\]]*)\]\]" % sub, wt)]
        out, seen = [], set()
        for s, label, rest in rows:
            # 绝对路径转成相对；[[/Chapter 1/]] 末尾的斜杠只管显示，不是页名
            s = ("/" + s[len(rid) + 1:] if s.startswith(rid + "/") else s).rstrip("/")
            if len(s) > 1 and s not in seen:
                seen.add(s)
                out.append((s, label, rest))
        return out

    def _html_links(self, rid):
        """渲染后的主页面上指向本作品子页面的链接 → [(子页面路径, 标签, 章名)]，按页面上的先后、去重。
        🔴 扫描本转载的作品（<pages index=…/>，如 Moby-Dick (1851) US edition）目录只在渲染后才有，wikitext 里一条都没有，
           以前整本书就成了一块「书名页 + 版权声明」。prop=links 按字母排（Chapter 1, 10, 100…），顺序只能从 HTML 里取（2026-09-25）"""
        base = "/wiki/%s/" % rid.replace(" ", "_")
        out, seen = [], set()
        for href, inner in re.findall(r'<a\b[^>]*?\bhref="([^"]+)"[^>]*>(.*?)</a>', self._html(rid), flags=re.S):
            path = urllib.parse.unquote(html.unescape(href)).split("#")[0]
            if not path.startswith(base):
                continue                            # 别的作品、作者页、红链（/w/index.php?…redlink=1）
            s = "/" + path[len(base):].replace("_", " ").strip("/")
            if len(s) < 2 or s in seen:
                continue
            seen.add(s)
            label = s.strip("/").replace("/", " · ")
            title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", inner))).strip()
            out.append((s, label, "" if title.casefold() in (label.casefold(), s.strip("/").casefold()) else title))
        return out

    def units(self, rid):
        wt = self._wikitext(rid)
        links = self._toc_links(rid, wt)
        if not links and _VERSIONS.search(wt):
            # 多版本 / 消歧义页（en 的「Pride and Prejudice」就是）：正文在各版本页，不能把这页当正文切。
            # 版本列表不一定是 * 开头（A Christmas Carol (Dickens) 就不是），全页找链接
            alts = list(dict.fromkeys(a.strip() for a in re.findall(r"\[\[([^|\]#]+)", wt)
                                      if ":" not in a and a.strip() != rid and not a.startswith("/")))
            raise ValueError("「%s」是多版本页，请用具体版本的键，如 %s" % (
                rid, "、".join("%s:%s" % (self.id, a) for a in alts[:5]) or "（页上没列版本）"))
        if len(links) < 3:
            found = self._html_links(rid)
            if len(found) >= 3:
                links = found
            elif links and re.search(r"<pages\s+index=", wt, flags=re.I):
                # 只链了一两个子页面（常见是 Preface），正文却在主页面上转载：按单页切，不然整本书只剩一篇序
                links = []
        if links:
            chap = [l for l in links if l[0].startswith("/第") or _CHAPTER_SUB.search(l[0])]
            if len(chap) >= 0.6 * len(links):
                links = chap
            # en 分卷的书每卷都从「Chapter 1」数起，标签重名就用子页面路径「Volume 1 · Chapter 1」
            labels = [(l or s.lstrip("/")).strip() for s, l, _ in links]
            dup = len(set(labels)) < len(labels)
            out = []
            for (sub, _, rest), label in zip(links, labels):
                if dup:
                    label = sub.strip("/").replace("/", " · ")
                title = re.sub(r"[\s　]+", " ", re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", rest)).strip()
                out.append({"n": len(out) + 1, "label": label, "title": title, "ref": {"page": rid + sub}})
            return out
        # 单页作品：整页切块
        text = self._plain(rid)
        if len(text) < MIN_SINGLE:
            raise ValueError("拆不出章节：「%s」页上找不到章节链接，整页正文只有 %d 字（多半只是书名页、版权声明）"
                             % (rid, len(text)))
        parts = chunk(text)
        return [{"n": i + 1, "label": "第%d段" % (i + 1), "title": "", "ref": {"page": rid, "chunk": i}}
                for i in range(len(parts))]

    def text(self, rid, unit):
        ref = unit["ref"]
        t = self._plain(ref["page"])
        if "chunk" in ref:
            return chunk(t)[ref["chunk"]]
        return t

    def peek(self, rid, unit):
        """不联网就拿得到的这一章正文（页面已在内存里）；拿不到回 None。request.py 用它查已读的章正文变没变。"""
        return self.text(rid, unit) if unit["ref"]["page"] in self._html_cache else None

    def unit_url(self, unit):
        return "https://%s.wikisource.org/wiki/%s" % (self.lang, urllib.parse.quote(unit["ref"]["page"].replace(" ", "_")))
