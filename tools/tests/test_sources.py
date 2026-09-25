# -*- coding: utf-8 -*-
"""
书源（tools/sources/*）的单元测试。全离线：网络调用都换成 fixtures/ 里的小样本。

  python -m unittest discover -s tools/tests
"""
import email.utils, io, json, os, re, shutil, sys, tempfile, time, unittest, urllib.error, urllib.parse
from contextlib import redirect_stdout
from unittest import mock

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
import core, sources  # noqa: E402
from sources import gutenberg as gb, http, wikisource  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def author(name="Tester, A.", born=1800, died=1870):
    return {"name": name, "birth_year": born, "death_year": died}


def gutendex(rid="99999", title="The Test Novel", authors=("Tester, A.",), lang="en", copyright=False, media="Text"):
    # 名字给字符串 = 一位 1870 年去世的作者（全球公版）；要试许可规则就直接给 author(...) 字典
    authors = [a if isinstance(a, dict) else author(a) for a in authors]
    return {"id": int(rid), "title": title, "authors": authors, "languages": [lang],
            "copyright": copyright, "media_type": media,
            "formats": {"text/plain; charset=utf-8": "https://www.gutenberg.org/ebooks/%s.txt.utf-8" % rid}}


class FakeNet:
    """顶替 sources.http.get：按 URL 回 fixture，记下请求过哪些 URL。
    book / rdf 给 None = 404，给异常就抛这个异常。html：{页名: 渲染后的 HTML}，没有的页回 missingtitle。"""

    def __init__(self, book=None, text=None, wikitext=None, rdf=None, html=None):
        self.book, self.text, self.wikitext, self.rdf, self.html, self.urls = book, text, wikitext, rdf, html or {}, []

    @staticmethod
    def _answer(url, v):
        if v is None:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        if isinstance(v, Exception):
            raise v
        return v

    def __call__(self, url, as_json=True, **kw):
        self.urls.append(url)
        if "gutendex.com" in url:
            return self._answer(url, self.book)
        if "gutenberg.pglaf.org" in url and url.endswith(".rdf"):
            return self._answer(url, self.rdf)
        if "gutenberg.pglaf.org" in url and url.endswith(".txt"):
            return self.text
        if "wikisource.org/w/api.php" in url:
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
            if q.get("prop") == "text":
                if q["page"] not in self.html:
                    return {"error": {"code": "missingtitle", "info": "The page you specified doesn't exist."}}
                return {"parse": {"title": q["page"], "text": self.html[q["page"]]}}
            return {"parse": {"title": "x", "wikitext": self.wikitext}}
        raise AssertionError("测试里不该请求 %s" % url)


def patched(net):
    return mock.patch.object(http, "get", net)


# ---------------------------------------------------------------- Gutenberg：纯函数
class TestNumbers(unittest.TestCase):
    def test_roman(self):
        self.assertEqual([gb.roman(s) for s in ("I", "iv", "XIV", "LXI", "MCMXC")], [1, 4, 14, 61, 1990])
        self.assertIsNone(gb.roman("IIII"))
        self.assertIsNone(gb.roman("MIXED"))

    def test_words(self):
        self.assertEqual(gb.words("ONE"), 1)
        self.assertEqual(gb.words("Twenty-First"), 21)
        self.assertEqual(gb.words("the first"), 1)
        self.assertEqual(gb.words("THE TWELFTH"), 12)
        self.assertIsNone(gb.words("of"))

    def test_zh(self):
        cases = {"一": 1, "十": 10, "十一": 11, "二十": 20, "廿一": 21, "一百二十": 120, "一百零五": 105,
                 "一二○": 120, "一一八": 118, "一○○": 100, "１２": 12, "12": 12}
        for s, n in cases.items():
            self.assertEqual(gb.zh_num(s), n, s)
        self.assertIsNone(gb.zh_num("甲"))


class TestHeading(unittest.TestCase):
    def h(self, line):
        x = gb.heading(line)
        return x and (x.level, x.style, x.num, x.label, x.rest)

    def test_english(self):
        self.assertEqual(self.h("CHAPTER I."), (1, "chapter", 1, "CHAPTER I", ""))
        self.assertEqual(self.h("Chapter 12: The Boy"), (1, "chapter", 12, "Chapter 12", "The Boy"))
        self.assertEqual(self.h("CHAPTER THE FIRST"), (1, "chapter", 1, "CHAPTER THE FIRST", ""))
        self.assertEqual(self.h("Chapter I.]"), (1, "chapter", 1, "Chapter I", ""))   # P&P 1342：章名是插图说明的最后一行
        self.assertEqual(self.h("BOOK TWO: 1805"), (0, "book", 2, "BOOK TWO", "1805"))
        self.assertEqual(self.h("   XIV.  "), (1, "roman", 14, "XIV", ""))
        self.assertEqual(self.h("I. A SCANDAL IN BOHEMIA"), (1, "roman-title", 1, "I", "A SCANDAL IN BOHEMIA"))
        self.assertEqual(self.h("EPILOGUE"), (1, "extra", None, "EPILOGUE", ""))
        self.assertEqual(self.h("C H A P.   I"), (1, "chapter", 1, "C H A P. I", ""))      # Tristram Shandy 1079
        self.assertEqual(self.h("CHAP. XIV."), (1, "chapter", 14, "CHAP. XIV", ""))

    def test_book_the_second_with_lowercase_title(self):
        """A Tale of Two Cities 98：隔着「--」的小写是卷名，不是正文。"""
        self.assertEqual(self.h("Book the First--Recalled to Life"), (0, "book", 1, "Book the First", "Recalled to Life"))
        self.assertEqual(self.h("Book the Second--the Golden Thread"), (0, "book", 2, "Book the Second", "the Golden Thread"))
        self.assertEqual(self.h("Book the Third—the Track of a Storm"), (0, "book", 3, "Book the Third", "the Track of a Storm"))
        self.assertEqual(self.h("Book II. In which the scene shifts to a new country"),
                         (0, "book", 2, "Book II", "In which the scene shifts to a new country"))   # 句子式卷名照认

    def test_english_prose_is_not_heading(self):
        for line in ("Book the first coach you can find, her mother had said",
                     "Part of the reason was the weather.",
                     "Part two—the hardest part—was getting there.",
                     "Part II. The next morning they rose early and went down to the",     # 折到下一行接着说
                     "Chapter and verse, he said.",
                     "It was a cold morning.",
                     # Moby-Dick 2701 Cetology 章正文里的交叉引用
                     "BOOK I. (_Folio_), CHAPTER I. (_Sperm Whale_).—This whale, among the",
                     "BOOK I. (_Folio_) CHAPTER IV. (_Hump Back_).—This whale is often seen",
                     "BOOK II. (_Octavo_), CHAPTER III. (_Narwhale_), that is, _Nostril"):
            self.assertIsNone(gb.heading(line), line)

    def test_chinese(self):
        self.assertEqual(self.h("第一回：宴桃園豪傑三結義，斬黃巾英雄首立功"),
                         (1, "回", 1, "第一回", "宴桃園豪傑三結義，斬黃巾英雄首立功"))
        self.assertEqual(self.h("第一二○回　薦杜預老將獻新謀"), (1, "回", 120, "第一二○回", "薦杜預老將獻新謀"))
        self.assertEqual(self.h("第三章 出發"), (1, "章", 3, "第三章", "出發"))
        self.assertEqual(self.h("卷之二"), (0, "卷", 2, "卷之二", ""))

    def test_chinese_prose_is_not_heading(self):
        self.assertIsNone(gb.heading("第四回中既將薛家母子在榮府內寄居等事略已表明，此回則暫不能寫矣．"))
        self.assertIsNone(gb.heading("第二回合，兩人又戰了三十餘合"))


class TestBoilerplate(unittest.TestCase):
    def test_strip_en(self):
        body = gb.strip_boilerplate(fixture("pg_en_novel.txt").replace("\n", "\r\n"))
        self.assertNotIn("\r", body)
        self.assertNotIn("Project Gutenberg", body)
        self.assertNotIn("START OF", body)
        self.assertNotIn("ENOUGH TO WRAP", body)          # 折行的 START 行也要去干净
        self.assertNotIn("Produced by", body)
        self.assertNotIn("LICENSE", body)
        self.assertTrue(body.lstrip().startswith("THE TEST NOVEL"))
        self.assertIn("where she had been.", body)

    def test_strip_zh(self):
        body = gb.strip_boilerplate(fixture("pg_zh_novel.txt"))
        self.assertTrue(body.startswith("目錄"))
        self.assertNotIn("End of Project Gutenberg", body)
        self.assertNotIn("Updated editions", body)

    def test_strip_bom_and_old_marker(self):
        body = gb.strip_boilerplate("﻿header\n*** START OF THIS PROJECT GUTENBERG EBOOK X ***\nHello.\n"
                                    "*** END OF THIS PROJECT GUTENBERG EBOOK X ***\nlicense")
        self.assertEqual(body, "Hello.")

    def test_strip_etext_small_print_and_foreign_footers(self):
        end = "\n*** END OF THIS PROJECT GUTENBERG EBOOK X ***\nlicense"
        cases = {
            "etext": "header\n*** START OF THE PROJECT GUTENBERG ETEXT X ***\nHello.\n"
                     "*** END OF THE PROJECT GUTENBERG ETEXT X ***\nlicense",
            "this_etext": "header\n*** START OF THIS PROJECT GUTENBERG ETEXT X ***\nHello." + end,
            "small_print_nostar": "header\n*END THE SMALL PRINT! FOR PUBLIC DOMAIN ETEXTS*Ver.04.29.93*END*\nHello.\n"
                                  "End of the Project Gutenberg Etext of X\nlicense",
            "fin_de": "*** START OF THIS PROJECT GUTENBERG EBOOK X ***\nHello.\n\n"
                      "Fin de Project Gutenberg's Les misérables, by Victor Hugo\n" + end,
            "ende_dieses": "*** START OF THIS PROJECT GUTENBERG EBOOK X ***\nHello.\n\n"
                           "Ende dieses Project Gutenberg Etextes \"Faust\"." + end,
        }
        for name, raw in cases.items():
            self.assertEqual(gb.strip_boilerplate(raw), "Hello.", name)


class TestClean(unittest.TestCase):
    def test_pgdp_markers(self):
        """P&P 1342 信件抬头「/* NIND “My dear friend, */」：记号去掉，字留下。"""
        t = gb.clean("He read it aloud.\n\n     /* NIND “My dear friend, */\n\n“If you are not so compassionate.”\n\n"
                     "     /* RIGHT “Hunsford, near Westerham, Kent, _15th October_. */\n\n/*\nRoses are red,\nViolets are blue.\n*/")
        self.assertNotIn("/*", t)
        self.assertNotIn("*/", t)
        self.assertIn("“My dear friend,\n“If you are not so compassionate.”", t)
        self.assertIn("“Hunsford, near Westerham, Kent, _15th October_.", t)
        self.assertIn("Roses are red,\nViolets are blue.", t)


class TestSplit(unittest.TestCase):
    def test_english_novel(self):
        body = gb.strip_boilerplate(fixture("pg_en_novel.txt"))
        parts = gb.split_units(body)
        self.assertEqual([p[0] for p in parts], ["CHAPTER I", "CHAPTER II", "CHAPTER III", "CHAPTER IV"])
        self.assertEqual([p[1] for p in parts], ["The Arrival", "", "Departure", ""])
        lines = body.split("\n")
        texts = [gb.clean("\n".join(lines[a:b])) for _, _, a, b in parts]
        self.assertTrue(texts[0].startswith("It was a cold morning"))
        self.assertIn("down the long hill into the village", texts[0])      # 硬换行拼回去，中间一个空格
        self.assertNotIn("Illustration", texts[0])
        self.assertNotIn("Copyright 1900", texts[0])
        self.assertEqual(texts[0].count("\n"), 2)                             # 一段一行
        self.assertIn("Roses are red,\nViolets are blue,", texts[1])         # 诗保留换行
        self.assertIn("Book the first coach", texts[2])                      # 正文里的「Book the first」不是卷标题
        self.assertTrue(texts[3].endswith("where she had been."))            # THE END 以后不算
        for t in texts:
            self.assertNotIn("PREFACE", t)
            self.assertNotIn("CONTENTS", t)

    def test_chinese_novel(self):
        body = gb.strip_boilerplate(fixture("pg_zh_novel.txt"))
        parts = gb.split_units(body)
        self.assertEqual([p[0] for p in parts], ["第一回", "第二回", "第三回", "第四回"])
        self.assertEqual(parts[0][1], "甲乙丙丁三結義，戊己庚辛首立功")
        self.assertEqual(parts[1][1], "張三怒鞭李四 王五謀誅趙六")
        self.assertEqual(parts[2][1], "風雨夕悶制風雨詞 金蘭契互剖金蘭語")   # 回目在下一行、前面没空行
        lines = body.split("\n")
        texts = [gb.clean("\n".join(lines[a:b])) for _, _, a, b in parts]
        self.assertIn("折行的地方", texts[0])                                # 中文拼行不加空格
        self.assertNotIn("目錄", texts[0])
        self.assertIn("第四回中既將前事表明", texts[2])                      # 正文行不是回目
        self.assertTrue(texts[3].endswith("各自回家。"))
        self.assertNotIn("全書完", texts[3])

    def test_no_headings_falls_back(self):
        self.assertIsNone(gb.split_units(gb.strip_boilerplate(fixture("pg_no_headings.txt"))))

    def test_toc_with_blank_lines_and_books(self):
        para = "Some real text for this chapter, long enough to count as a chapter body. " * 4
        toc = "\n\n".join("CHAPTER %s. Title %d" % (r, i) for i, r in enumerate(["I", "II", "III"], 1))
        book = lambda n, r: "BOOK %s\n\n\nCHAPTER I.\n\n%s\n\n\nCHAPTER II.\n\n%s\n\n\n" % (n, para, para)
        body = "CONTENTS\n\n%s\n\n\nPREFACE\n\n%s\n\n\n%s%s" % (toc, para, book("ONE", "I"), book("TWO", "II"))
        parts = gb.split_units(body)
        self.assertEqual([p[0] for p in parts],
                         ["BOOK ONE · CHAPTER I", "BOOK ONE · CHAPTER II", "BOOK TWO · CHAPTER I", "BOOK TWO · CHAPTER II"])


class TestSplitShapes(unittest.TestCase):
    """真书的形状（2026-09-25 对 55 本 PG 书回归时抓到的），fixture 是照着形状编的字。"""
    P = "Some real text for this chapter, long enough to count as a chapter body and then a few more words. " * 12

    def split(self, body, strip=False):
        body = gb.strip_boilerplate(body) if strip else body
        log = []
        parts = gb.split_units(body, log=log.append)
        lines = body.split("\n")
        texts = [gb.clean("\n".join(lines[a:b])) for _, _, a, b in parts or []]
        return parts, texts, log

    def assertCovers(self, body, parts):
        """第一章起，除了标题行，每一行正文都落在某一章里。"""
        lines = body.split("\n")
        inside = {k for _, _, a, b in parts for k in range(a, b)}
        first = min(a for _, _, a, _ in parts)
        lost = [ln for k, ln in enumerate(lines[first:parts[-1][3]], first)
                if ln.strip() and k not in inside and not gb.heading(ln)]
        self.assertLessEqual(len(lost), len(parts), lost[:5])        # 最多每章一行章名

    def test_moby_dick_shape(self):
        """2701：目录最后一行「Epilogue」不是第一章；Cetology 里的「BOOK I. …, CHAPTER I.」不是卷标题、不切断这一章。"""
        parts, texts, log = self.split(fixture("pg_moby_shape.txt"), strip=True)
        self.assertEqual([p[0] for p in parts], ["CHAPTER %d" % i for i in range(1, 6)] + ["Epilogue"])
        self.assertEqual([p[1] for p in parts][:5], ["Loomings", "The Carpet-Bag", "The Spouter-Inn", "Cetology", "The Specksnyder"])
        self.assertTrue(texts[0].startswith("FIRST-CHAPTER-OPENING"))
        self.assertTrue(texts[3].startswith("CETOLOGY-OPENING"))
        for s in ("BOOK I. (_Folio_), CHAPTER I.", "OCTAVO-TEXT", "(_Huzza Porpoise_)", "CETOLOGY-CLOSING"):
            self.assertIn(s, texts[3])
        self.assertIn("CODA-TEXT", texts[5])
        self.assertFalse([t for t in texts if "Transcriber" in t or "ETYMOLOGY" in t])
        self.assertEqual(log, [])

    def test_shandy_shape(self):
        """1079：C H A P. 标题；目录里的「Volume III.」不能当第一卷的卷名；卷名上下「———」也算空行。"""
        body = gb.strip_boilerplate(fixture("pg_shandy_shape.txt"))
        parts, texts, _ = self.split(body)
        self.assertEqual([p[0] for p in parts],
                         ["C H A P. I", "C H A P. II", "C H A P. III",
                          "Volume the Second · C H A P. I", "Volume the Second · C H A P. II", "Volume the Second · CHAP. III",
                          "Volume the Third · C H A P. I", "Volume the Third · C H A P. II", "Volume the Third · C H A P. III"])
        self.assertTrue(texts[0].startswith("VOL1-CH1"))
        self.assertTrue(texts[2].startswith("It was a dull afternoon"))    # 一句话的短章也是一章
        self.assertTrue(texts[6].startswith("VOL3-CH1"))
        self.assertNotIn("FINIS", texts[-1])
        self.assertCovers(body, parts)

    def test_two_cities_shape(self):
        """98：「Book the Second--the Golden Thread」是卷名；目录后紧跟的真「Book the First」留着。
        加上 1342 的结尾：[Illustration: THE / END ] 后面的印刷厂行不算；/* NIND */ 记号去掉。"""
        parts, texts, _ = self.split(fixture("pg_two_cities_shape.txt"), strip=True)
        self.assertEqual([p[0] for p in parts],
                         ["Book the %s · CHAPTER %s" % (b, r) for b in ("First", "Second", "Third") for r in ("I", "II", "III")])
        self.assertEqual(parts[3][1], "Five Years Later")
        self.assertTrue(texts[0].startswith("BOOK-THE-FIRST-I"))
        self.assertIn("“My dear friend,", texts[4])
        self.assertFalse([t for t in texts if "/*" in t or "*/" in t])
        self.assertTrue(texts[-1].startswith("BOOK-THE-THIRD-III"))
        for s in ("CHISWICK", "END", "Illustration"):
            self.assertNotIn(s, texts[-1])

    def test_first_chapter_right_under_illustration(self):
        """「CHAPTER I.」上面直接是插图说明、没空行：后面紧跟空了行的 CHAPTER II，就认它。"""
        body = "[Illustration: The house]\nCHAPTER I.\n\nFIRST CHAPTER TEXT " + self.P + "".join(
            "\n\n\nCHAPTER %s.\n\n%s" % (r, self.P) for r in ("II", "III", "IV", "V"))
        parts, texts, _ = self.split(body)
        self.assertEqual([p[0] for p in parts], ["CHAPTER %s" % r for r in ("I", "II", "III", "IV", "V")])
        self.assertTrue(texts[0].startswith("FIRST CHAPTER TEXT"))

    def test_one_sentence_first_chapter(self):
        body = "CHAPTER I.\n\nShe was born on a Tuesday.\n\n" + "".join(
            "\n\nCHAPTER %s.\n\n%s" % (r, self.P) for r in ("II", "III", "IV", "V"))
        parts, texts, _ = self.split(body)
        self.assertEqual(parts[0][0], "CHAPTER I")
        self.assertEqual(texts[0], "She was born on a Tuesday.")
        self.assertEqual(len(parts), 5)

    def test_short_chapters_in_the_middle(self):
        """三个挨着的一句话短章不是目录，前面的章不能丢（旧逻辑把第 1–11 章全丢了）。"""
        body = "".join("\n\nCHAPTER %d.\n\n%s\n" % (i, "It rained all day and nobody went out." if i in (8, 9, 10) else self.P)
                       for i in range(1, 21))
        parts, texts, _ = self.split(body)
        self.assertEqual([p[0] for p in parts], ["CHAPTER %d" % i for i in range(1, 21)])
        self.assertEqual(texts[8], "It rained all day and nobody went out.")

    def test_short_first_chapter_of_a_book(self):
        """分卷的书第一卷第一章很短：不能把第一卷整个当目录丢掉。"""
        body = "".join("BOOK %s\n\n\n%s" % (bk, "".join(
            "CHAPTER %s.\n\n%s\n\n\n" % (r, "It rained all day." if (bk, r) == ("ONE", "I") else self.P)
            for r in ("I", "II", "III"))) for bk in ("ONE", "TWO"))
        parts, _, _ = self.split(body)
        self.assertEqual([p[0] for p in parts], ["BOOK %s · CHAPTER %s" % (b, r) for b in ("ONE", "TWO") for r in ("I", "II", "III")])

    def test_text_between_book_and_chapter_is_kept(self):
        """卷标题和下一章之间的正文挂在上一章末尾，不丢。"""
        body = "BOOK ONE\n\n" + "".join("CHAPTER %s.\n\n%s\n\n" % (r, self.P) for r in ("I", "II")) + \
               "BOOK TWO\n\nINTRO PARAGRAPH " + self.P + "\n\n" + "".join("CHAPTER %s.\n\n%s\n\n" % (r, self.P) for r in ("I", "II"))
        parts, texts, _ = self.split(body)
        self.assertEqual([p[0] for p in parts], ["BOOK ONE · CHAPTER I", "BOOK ONE · CHAPTER II",
                                                 "BOOK TWO · CHAPTER I", "BOOK TWO · CHAPTER II"])
        self.assertIn("INTRO PARAGRAPH", texts[1])
        self.assertCovers(body, parts)

    def test_toc_with_summaries_is_dropped(self):
        """目录条目隔得远（带提要）、每条下面几乎没字：编号从头再来的地方才是正文。"""
        toc = "".join("CHAPTER %s.\n\nIn which a short summary of the chapter is given.\n\n\n\n\n\n" % r for r in ("I", "II", "III", "IV"))
        body = "CONTENTS\n\n" + toc + "".join("CHAPTER %s.\n\nREAL-%s %s\n\n\n" % (r, r, self.P) for r in ("I", "II", "III", "IV"))
        parts, texts, _ = self.split(body)
        self.assertEqual([p[0] for p in parts], ["CHAPTER I", "CHAPTER II", "CHAPTER III", "CHAPTER IV"])
        self.assertTrue(texts[0].startswith("REAL-I"))

    def test_toc_with_wrapped_titles_and_long_front_matter(self):
        """Don Quixote 996 / Les Misérables 135：目录章名长、折行，串断成几截；最后一条底下是长长的序言。"""
        wrap = "OF THE THINGS THAT BEFELL OUR KNIGHT UPON THE ROAD, WITH OTHER\nMATTERS WORTHY OF RECORD"
        toc = ["CHAPTER %s.\n%s" % (r, wrap) for r in ("I", "II", "III")] + \
              ["CHAPTER IV.\n%s\n%s\nAND MANY MORE\nBESIDES THESE" % (wrap, wrap)] + \
              ["CHAPTER %s.\n%s" % (r, wrap) for r in ("V", "VI", "VII", "VIII")]
        body = "CONTENTS\n\n" + "\n\n".join(toc) + "\n\n\n\nPREFACE\n\n" + self.P * 2 + "\n\n\n" + \
               "".join("CHAPTER %s.\n\nREAL-%s %s\n\n\n" % (r, r, self.P) for r in ("I", "II", "III", "IV", "V", "VI", "VII", "VIII"))
        parts, texts, _ = self.split(body)
        self.assertEqual([p[0] for p in parts], ["CHAPTER %s" % r for r in ("I", "II", "III", "IV", "V", "VI", "VII", "VIII")])
        self.assertTrue(texts[0].startswith("REAL-I"))
        self.assertNotIn("PREFACE", texts[0])

    def test_missing_first_volume_falls_back(self):
        """1079 旧样子：章没认出来，只剩「Volume the Second/Third/Fourth」，第一卷整个落在外面 → 不切，按字数切块。"""
        prose = ("Plain prose paragraph without any heading in it at all. " * 20 + "\n\n") * 30
        body = prose + "".join("\n\nVolume the %s\n\n%s" % (w, prose) for w in ("Second", "Third", "Fourth"))
        parts, _, log = self.split(body)
        self.assertIsNone(parts)
        self.assertIn("可疑", log[0])
        self.assertIn("Volume the Second", log[0])

    def test_giant_volumes_fall_back(self):
        """按卷切、每卷十几万字：卷里的章没认出来，按字数切块。"""
        prose = ("Plain prose paragraph without any heading in it at all. " * 20 + "\n\n") * 120
        body = "".join("VOLUME %s\n\n%s" % (r, prose) for r in ("I", "II", "III"))
        parts, _, log = self.split(body)
        self.assertIsNone(parts)
        self.assertIn("卷里的章没认出来", log[0])


class TestPerson(unittest.TestCase):
    def test_names(self):
        self.assertEqual(gb._person("Austen, Jane", "en"), "Jane Austen")
        self.assertEqual(gb._person("Luo, Guanzhong", "zh"), "Luo Guanzhong")
        self.assertEqual(gb._person("King, Martin Luther, Jr.", "en"), "Martin Luther King, Jr.")
        self.assertEqual(gb._person("Langhuanshanqiao, active 18th century-19th century", "zh"), "Langhuanshanqiao")
        self.assertEqual(gb._person("Homer", "en"), "Homer")


# ---------------------------------------------------------------- Gutenberg：接口
class TestGutenbergSource(unittest.TestCase):
    def test_registered(self):
        self.assertIsInstance(sources.get("gutenberg"), gb.Gutenberg)

    def test_meta_units_text(self):
        net = FakeNet(gutendex(), fixture("pg_en_novel.txt"))
        src = gb.Gutenberg()
        with patched(net):
            m = src.meta("99999")
            units = src.units("99999")
            texts = [src.text("99999", u) for u in units]
        self.assertEqual(m["license"], "公版")
        self.assertEqual(m["author"], "A. Tester")
        self.assertEqual(m["lang"], "en")
        self.assertEqual(m["read_url"], "https://www.gutenberg.org/ebooks/99999")
        self.assertEqual([u["n"] for u in units], [1, 2, 3, 4])
        self.assertEqual(units[0]["label"], "CHAPTER I")
        self.assertTrue(texts[0].startswith("It was a cold morning"))
        self.assertIsNone(src.unit_url(units[2]))       # 各章共用一个落地页：不给章链接，前端只显示章名
        self.assertEqual(src.peek("99999", units[1]), texts[1])     # 整本在内存里：不联网就拿得到
        # 正文只取一次，而且只走镜像，不碰 www.gutenberg.org（那边明说程序访问会封 IP）
        self.assertEqual(sum(u.endswith(".txt") for u in net.urls), 1)
        self.assertEqual(sum("gutendex" in u for u in net.urls), 1)
        self.assertFalse([u for u in net.urls if u.endswith(".rdf")])       # Gutendex 回了就不读 RDF
        self.assertFalse([u for u in net.urls if "www.gutenberg.org" in u])

    def test_copyrighted_never_downloads_text(self):
        for flag, lic in ((True, "版权"), (None, "未知")):
            net = FakeNet(gutendex(copyright=flag), "SHOULD NOT BE READ")
            src = gb.Gutenberg()
            with patched(net):
                self.assertEqual(src.meta("99999")["license"], lic)
                with self.assertRaises(ValueError):
                    src.units("99999")
            self.assertFalse([u for u in net.urls if "pglaf" in u], lic)

    def test_audio_book_refused(self):
        net = FakeNet(gutendex(media="Sound"), "x")
        with patched(net), self.assertRaises(ValueError):
            gb.Gutenberg().units("99999")

    def test_bad_ids(self):
        src = gb.Gutenberg()
        net = FakeNet(book=None, rdf=None)
        with patched(net), redirect_stdout(io.StringIO()):
            with self.assertRaises(ValueError):
                src.meta("12345")        # Gutendex 404，镜像上也没有
            n = len(net.urls)
            for rid in ("abc", "01342", "0", "12345678"):      # 前导零会另起一份 data/gutenberg/01342.*，跟 request.py 同规则
                with self.assertRaises(ValueError):
                    src.meta(rid)
            self.assertEqual(len(net.urls), n)                  # 格式不对的连网都不上

    def test_suspicious_split_falls_back_to_chunks(self):
        """切出来可疑 → units() 按字数切块，并在日志里说为什么。"""
        prose = ("Plain prose paragraph without any heading in it at all. " * 20 + "\n\n") * 30
        raw = "*** START OF THE PROJECT GUTENBERG EBOOK X ***\n\n" + prose + \
              "".join("\n\nVolume the %s\n\n%s" % (w, prose) for w in ("Second", "Third", "Fourth")) + \
              "\n\n*** END OF THE PROJECT GUTENBERG EBOOK X ***\n"
        out = io.StringIO()
        with patched(FakeNet(gutendex(), raw)), redirect_stdout(out):
            units = gb.Gutenberg().units("99999")
        self.assertEqual(units[0]["label"], "第1段")
        self.assertGreater(len(units), 10)
        self.assertIn("章节切分可疑", out.getvalue())

    def test_parse_rdf(self):
        b = gb.parse_rdf(fixture("pg_catalog.rdf"))
        self.assertEqual(b, {"title": "測試演義", "authors": [author("Mou, Ren", 1330, 1400)], "languages": ["zh"],
                             "copyright": False, "media_type": "Text"})
        no_dates = re.sub(r"\s*<pgterms:(birth|death)date[^>]*>\d+</pgterms:\1date>", "", fixture("pg_catalog.rdf"))
        self.assertEqual(gb.parse_rdf(no_dates)["authors"], [author("Mou, Ren", None, None)])
        cr = gb.parse_rdf(fixture("pg_catalog.rdf").replace(
            "Public domain in the USA.", "Copyrighted. Read the copyright notice inside this book for details."))
        self.assertIs(cr["copyright"], True)
        self.assertIsNone(gb.parse_rdf(fixture("pg_catalog.rdf").replace("Public domain in the USA.", ""))["copyright"])

    def test_gutendex_timeout_falls_back_to_rdf(self):
        net = FakeNet(book=TimeoutError("The read operation timed out"), text=fixture("pg_zh_novel.txt"),
                      rdf=fixture("pg_catalog.rdf"))
        src = gb.Gutenberg()
        with patched(net), redirect_stdout(io.StringIO()):
            m = src.meta("88888")
            units = src.units("88888")
        self.assertEqual((m["title"], m["author"], m["lang"], m["license"]), ("測試演義", "Mou Ren", "zh", "公版"))
        self.assertEqual(len(units), 4)
        self.assertEqual([u.split("/")[2] for u in net.urls], ["gutendex.com", "gutenberg.pglaf.org", "gutenberg.pglaf.org"])

    def test_rdf_copyrighted_never_downloads_text(self):
        rdf = fixture("pg_catalog.rdf").replace("Public domain in the USA.", "Copyrighted. Read the notice.")
        net = FakeNet(book=TimeoutError(), text="SHOULD NOT BE READ", rdf=rdf)
        with patched(net), redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
            gb.Gutenberg().units("88888")
        self.assertFalse([u for u in net.urls if u.endswith(".txt")])

    def test_chunk_fallback(self):
        para = "A plain paragraph without any heading in it, repeated to make the book long. " * 10
        raw = "*** START OF THE PROJECT GUTENBERG EBOOK X ***\n\n" + "\n\n".join([para] * 20) + \
              "\n\n*** END OF THE PROJECT GUTENBERG EBOOK X ***\n"
        src = gb.Gutenberg()
        with patched(FakeNet(gutendex(), raw)):
            units = src.units("99999")
            texts = [src.text("99999", u) for u in units]
        self.assertGreater(len(units), 1)
        self.assertEqual([u["label"] for u in units], ["第%d段" % (i + 1) for i in range(len(units))])
        self.assertTrue(all(u["title"] == "" for u in units))
        self.assertTrue(all(0 < len(t) <= 6000 for t in texts))
        self.assertEqual("".join(texts).count("A plain paragraph"), 200)

    def test_short_fixture_fallback_is_one_chunk(self):
        src = gb.Gutenberg()
        with patched(FakeNet(gutendex(), fixture("pg_no_headings.txt"))):
            units = src.units("99999")
            text = src.text("99999", units[0])
        self.assertEqual([u["label"] for u in units], ["第1段"])
        self.assertTrue(text.startswith("A SHORT ESSAY"))
        self.assertNotIn("Produced by", text)

    def test_fetch_meta_only(self):
        """走一遍 fetch.py：目录进 data/，正文不拉。临时目录，不碰真 data/。"""
        import fetch
        tmp = tempfile.mkdtemp()
        try:
            src = gb.Gutenberg()
            with patched(FakeNet(gutendex(), fixture("pg_en_novel.txt"))), \
                    mock.patch.object(core, "ROOT", tmp), mock.patch.dict(sources.REGISTRY, {"gutenberg": src}):
                meta, units = fetch.fetch("gutenberg:99999", meta_only=True, log=lambda *a: None)
            self.assertEqual(meta["units_total"], 4)
            self.assertEqual(meta["ruler"], "novel_v1")
            saved = core.load_json(os.path.join(tmp, "data", "gutenberg", "99999.units.json"))
            self.assertEqual(saved[0], {"n": 1, "label": "CHAPTER I", "title": "The Arrival", "url": None})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestGutenbergLicence(unittest.TestCase):
    """许可规则（跟 site/connectors.js 同一条）：copyright true → 版权，null → 未知，
    false → 每位作者卒年 ≤ 1955，或卒年为空而生年 ≤ 1850 → 公版；否则美国公版（不试读）。"""

    def lic(self, authors, copyright=False):
        with patched(FakeNet(gutendex(authors=authors, copyright=copyright), "x")):
            m = gb.Gutenberg().meta("99999")
        return m["license"], m.get("license_note")

    def test_required_cases(self):
        us = ("美国公版", "仅在美国是公版")
        self.assertEqual(self.lic([author(born=1775, died=1817)]), ("公版", None))
        self.assertEqual(self.lic([author(born=1880, died=1960)]), us)
        self.assertEqual(self.lic([author(born=1800, died=None)]), ("公版", None))
        self.assertEqual(self.lic([author(born=1900, died=None)]), us)
        self.assertEqual(self.lic([]), ("公版", None))

    def test_edges(self):
        self.assertEqual(self.lic([author(died=1955)])[0], "公版")
        self.assertEqual(self.lic([author(died=1956)])[0], "美国公版")
        self.assertEqual(self.lic([author(born=1850, died=None)])[0], "公版")
        self.assertEqual(self.lic([author(born=1851, died=None)])[0], "美国公版")
        self.assertEqual(self.lic([author(born=None, died=None)])[0], "美国公版")      # 生卒都不知道
        self.assertEqual(self.lic([author(born=-750, died=-650)])[0], "公版")          # 公元前
        self.assertEqual(self.lic([author(died=1817), author(died=1970)])[0], "美国公版")   # 每一位都得过
        self.assertEqual(self.lic([author(born="1800", died=None)])[0], "美国公版")    # 不是数不算
        self.assertEqual(self.lic([author(born=1800, died=True)])[0], "美国公版")
        self.assertEqual(self.lic([author(died=1817)], copyright=True), ("版权", None))
        self.assertEqual(self.lic([author(died=1817)], copyright=None), ("未知", None))

    def test_us_only_never_downloads_text(self):
        net = FakeNet(gutendex(authors=[author(born=1900, died=1970)]), "SHOULD NOT BE READ")
        with patched(net), self.assertRaises(ValueError) as cm:
            gb.Gutenberg().units("99999")
        self.assertIn("仅在美国是公版", str(cm.exception))
        self.assertFalse([u for u in net.urls if u.endswith(".txt")])

    def test_fetch_refuses_us_only(self):
        import fetch
        tmp = tempfile.mkdtemp()
        try:
            net = FakeNet(gutendex(authors=[author(born=1900, died=1970)]), "SHOULD NOT BE READ")
            with patched(net), mock.patch.object(core, "ROOT", tmp), \
                    mock.patch.dict(sources.REGISTRY, {"gutenberg": gb.Gutenberg()}):
                with self.assertRaises(ValueError) as cm:
                    fetch.fetch("gutenberg:99999", meta_only=True, log=lambda *a: None)
            self.assertIn("美国公版", str(cm.exception))
            self.assertIn("仅在美国是公版", str(cm.exception))
            self.assertEqual(os.listdir(tmp), [])          # 什么都没写
            self.assertNotIn("美国公版", sources.TASTEABLE_LICENSES)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_rdf_fallback_uses_author_years(self):
        died_1960 = fixture("pg_catalog.rdf").replace(">1400<", ">1960<").replace(">1330<", ">1880<")
        for rdf, lic in ((fixture("pg_catalog.rdf"), "公版"), (died_1960, "美国公版")):
            with patched(FakeNet(book=TimeoutError(), rdf=rdf)), redirect_stdout(io.StringIO()):
                self.assertEqual(gb.Gutenberg().meta("88888")["license"], lic)


# ---------------------------------------------------------------- 礼貌 HTTP
class TestHttp(unittest.TestCase):
    def test_retry_after(self):
        self.assertEqual(http.retry_after("120"), 120)
        self.assertEqual(http.retry_after(" 7 "), 7)
        for v in (None, "", "soon", "Wed, 99 Foo 2015"):
            self.assertEqual(http.retry_after(v), 30, v)
        future = email.utils.formatdate(time.time() + 90, usegmt=True)
        self.assertTrue(85 <= http.retry_after(future) <= 91, http.retry_after(future))
        self.assertEqual(http.retry_after("Wed, 21 Oct 2015 07:28:00 GMT"), 0)     # 过去的日期：不用等

    def test_429_with_http_date_backs_off(self):
        """🔴 Retry-After 是日期时以前 int() 直接崩在退避分支里。"""
        future = email.utils.formatdate(time.time() + 120, usegmt=True)
        calls, sleeps = [], []

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def urlopen(req, timeout=None):
            calls.append(req.full_url)
            if len(calls) == 1:
                raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {"Retry-After": future}, None)
            return Resp(b'{"ok": 1}')

        with mock.patch.object(http.urllib.request, "urlopen", urlopen), \
                mock.patch.object(http.time, "sleep", sleeps.append), redirect_stdout(io.StringIO()):
            self.assertEqual(http.get("https://example.org/x"), {"ok": 1})
        self.assertEqual(len(calls), 2)
        self.assertTrue(115 <= sleeps[0] <= 121, sleeps)


# ---------------------------------------------------------------- 维基文库：渲染后的正文
# 旧版清洗（改动前的原样），只用来核对：zh 形状的页面改前改后一字不差（三國演義已读 10 章的正文指纹靠它）
def old_clean(h):
    import html as _html
    h = re.sub(r"<(table|style|sup)\b.*?</\1>", "", h, flags=re.S)
    h = re.sub(r'<(div|span)[^>]*class="[^"]*(ws-noexport|mw-editsection)[^"]*"[^>]*>.*?</\1>', "", h, flags=re.S)
    h = re.sub(r"<br\s*/?>|</(p|dd|div|li|h\d)>", "\n", h)
    t = _html.unescape(re.sub(r"<[^>]+>", "", h))
    t = re.sub(r"^\s*(返回頁首|返回页首|Back to top)\s*$", "", t, flags=re.M)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


# 照维基文库页面的形状编的 HTML（字是编的）：zh 页眉是两张 table，正文包在 div.prose 里，诗是 dl/dd
ZH_PAGE = (
    '<div class="mw-content-ltr mw-parser-output" lang="zh" dir="ltr"><div class="prose">\n'
    '<table style="width:100%" class="ws-header">\n<tbody><tr>\n<td><a href="/wiki/%E6%B8%AC" title="測">目錄</a>\n</td>'
    '<td><b>測試演義</b>\n</td></tr>\n<tr><td><a href="/wiki/x/%E5%87%A1" title="x">◀上一回</a></td>'
    '<td>第一回　<b>甲乙丙丁</b>\n</td></tr></tbody></table>\n'
    '<table class="ws-header"><tbody><tr><td>\n</td></tr></tbody></table>\n'
    '<p>　　詞曰：\n</p>\n<dl><dd><dl><dd>長江東逝水，浪花淘盡。</dd>\n<dd>是非成敗轉頭空。</dd></dl></dd></dl>\n'
    '<p>　　話說天下大勢，<style data-mw-deduplicate="x">.a{color:red}</style><span class="nianhao-link">'
    '<a href="/wiki/Category:169"><span class="nianhao" title="169年">某年</span></a></span>四月，'
    '帝御殿。<sup id="cite_ref-1" class="reference"><a href="#cite_note-1">[1]</a></sup>&#160;&amp;\n</p>'
    '<p>　　時有兄弟三人。\n</p><div class="noprint">返回頁首</div></div></div>')

# en 页眉：好几层 div，外层带 ws-noexport（Pride and Prejudice (1813) 各章就是这样）；页码 span 里也有 ws-noexport
EN_PAGE = (
    '<div class="mw-content-ltr mw-parser-output" lang="en" dir="ltr"><div class="ws-noexport"><style>.x{}</style></div>'
    '<div class="ws-header wst-header-structure wst-header ws-noexport noprint dynlayout-exempt">'
    '<div class="wst-header-mainblock headertemplate"><div class="wst-header-back searchaux wst-header-nav-empty">'
    '<div class="wst-header-back-arrow">←</div><div class="wst-header-back-link"></div></div>'
    '<div class="wst-header-central-cell"><span class="wst-header-title"><span class="wst-header-title-text">'
    '<a href="/wiki/Book">A Test Book</a>, <a href="/wiki/Book/Volume_1">Volume I</a></span> (<span>1813</span>)'
    '<br class="wst-header-title-break" /><span class="contributor-text">by&#160;<span class="fn">'
    '<a href="/wiki/Author:Some_Writer">Some Writer</a></span></span><div class="header-section-text">Chapter 1</div></span></div>'
    '<div class="wst-header-forward searchaux"><div class="wst-header-forward-link"><a href="/wiki/Book/Volume_1/Chapter_2">'
    'Chapter 2</a></div><div class="wst-header-forward-arrow">→</div></div></div>'
    '<div class="wst-header-notes searchaux"><ul class="plainSister"><li>sister projects: Wikidata item</li></ul></div>'
    '<div class="ws-noexport" id="ws-data"><span id="ws-title">A Test Book — Chapter 1</span></div></div>\n'
    '<div class="prp-pages-output" lang="en">\n<span><span class="pagenum ws-pagenum" id="1">'
    '<span id="pageindex_8" class="pagenum-inner ws-noexport">&#8203;</span></span></span>'
    '<div class="wst-dhr">&#160;</div>\n<div class="wst-center"><p><b>A TEST BOOK.</b>\n</p></div>\n'
    '<p>It was a quiet morning in the village, and nobody was about yet.\n</p>'
    '<p>"Good day," said the<span class="mw-editsection"><span>[</span><a href="#">edit</a><span>]</span></span> baker.\n</p>'
    '<div class="wst-nop"></div>\n</div></div>')


class TestWikisourceClean(unittest.TestCase):
    def test_zh_shape_unchanged(self):
        """zh 页面（页眉是 table、没有嵌套的 ws-noexport）：新清洗跟旧的一字不差。"""
        new = wikisource.clean_html(ZH_PAGE)
        self.assertEqual(new, old_clean(ZH_PAGE))
        self.assertEqual(new, "詞曰：\n長江東逝水，浪花淘盡。\n是非成敗轉頭空。\n"
                              "　　話說天下大勢，某年四月，帝御殿。\xa0&\n　　時有兄弟三人。")

    def test_en_header_removed(self):
        t = wikisource.clean_html(EN_PAGE)
        self.assertTrue(t.startswith("A TEST BOOK.\nIt was a quiet morning"), t[:80])
        for junk in ("Volume I", "Some Writer", "Chapter 2", "→", "←", "sister projects", "1813", "edit", "\u200b"):
            self.assertNotIn(junk, t)
        self.assertTrue(t.endswith('"Good day," said the baker.'), t[-40:])
        self.assertIn("Some Writer", old_clean(EN_PAGE))      # 旧版就是漏在这里

    def test_unbalanced_block_does_not_eat_text(self):
        h = '<p>Before.</p><div class="ws-noexport"><div>inner</div><p>After.</p>'
        self.assertEqual(wikisource.clean_html(h), "Before.\ninner\nAfter.")


def moby_like():
    """扫描本转载的作品（Moby-Dick (1851) US edition 的形状）：wikitext 里只有两条子页面链接，
    目录在转载进来的扫描页里，渲染后才有；顺序是 1, 2, …, 11（prop=links 会按字母排成 1, 10, 11, 2…）。"""
    rid = "Whale Book (1851)"
    wt = ("{{other versions|Whale Book}}\n{{header\n | title = Whale Book\n | author = Some Writer\n | year = 1851\n}}\n"
          '<pages index="Whale Book.djvu" include=9 />\n{{page break|label=}}(Not listed: '
          "[[Whale Book (1851)/Etymology|Etymology]], [[Whale Book (1851)/Extracts|Extracts]])\n"
          '<pages index="Whale Book.djvu" from=12 to=14/>\n{{PD-old}}\n')
    names = ["Loomings", "The Bag", "The Inn", "The Quilt", "Breakfast", "The Street", "The Chapel", "The Pulpit",
             "The Sermon", "A Bosom Friend", "Nightgown"]
    a = lambda sub, text, cls="": '<a href="/wiki/Whale_Book_(1851)/%s"%s>%s</a>' % (sub, cls, text)
    toc = "".join('<tr><td>CHAPTER %d.</td><td>%s</td></tr>' % (i, a("Chapter_%d" % i, n)) for i, n in enumerate(names, 1))
    page = ('<div class="mw-parser-output"><div class="ws-header ws-noexport"><div><a href="/wiki/Author:Some_Writer">'
            'Some Writer</a></div></div><p>WHALE BOOK. By Some Writer.</p>'
            '<p>(Not listed: %s, %s)</p><table>%s</table>'
            '<p>%s %s %s %s %s</p></div>') % (
        a("Etymology", "Etymology"), a("Extracts", "Extracts"), toc,
        a("Chapter_3#top", "again"),                                              # 重复、带锚点：只算一次
        '<a href="/w/index.php?title=Whale_Book_(1851)/Chapter_12&amp;action=edit&amp;redlink=1" class="new">Twelve</a>',
        '<a href="/wiki/Other_Book/Chapter_1">elsewhere</a>',                   # 别的作品
        a("Epilogue", "Epilogue"), '<a href="/wiki/Whale_Book_(1851)">top</a>')
    chapter = '<div class="mw-parser-output"><p>%s</p></div>' % ("Call me Tester. " * 40)
    return rid, wt, {rid: page, rid + "/Chapter 1": chapter}


class TestWikisourceToc(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(wikisource.Wikisource, "GAP", 0)      # 测试里不等 3 秒
        p.start()
        self.addCleanup(p.stop)

    def test_zh_fanli_then_first_chapter(self):
        """🔴 2026-09-25：目录正则用 \\s* 会跨行，「凡例」把下一行第一回当标题吞掉，第一回就没了。"""
        src = wikisource.Wikisource("zh")
        with patched(FakeNet(wikitext=fixture("wikisource_zh_toc.wikitext"))):
            units = src.units("三國演義")
            meta = src.meta("三國演義")
        self.assertEqual([u["label"] for u in units], ["第一回", "第二回", "第三回"])
        self.assertEqual(units[0]["n"], 1)
        self.assertEqual(units[0]["title"], "宴桃園豪傑三結義 斬黃巾英雄首立功")
        self.assertEqual(units[0]["ref"], {"page": "三國演義/第001回"})
        self.assertEqual(meta["author"], "羅貫中")

    def test_en_volumes_and_chapters(self):
        src = wikisource.Wikisource("en")
        with patched(FakeNet(wikitext=fixture("wikisource_en_toc.wikitext"))):
            units = src.units("Pride and Prejudice (1813)")
        self.assertEqual([u["label"] for u in units],
                         ["Volume 1 · Chapter 1", "Volume 1 · Chapter 2", "Volume 1 · Chapter 3",
                          "Volume 2 · Chapter 1", "Volume 2 · Chapter 2"])       # 卷页不算章，重复的链接只算一次
        self.assertEqual(units[3]["ref"], {"page": "Pride and Prejudice (1813)/Volume 2/Chapter 1"})

    def test_en_versions_page_refused(self):
        src = wikisource.Wikisource("en")
        net = FakeNet(wikitext=fixture("wikisource_en_versions.wikitext"))
        with patched(net):
            with self.assertRaises(ValueError) as cm:
                src.units("Pride and Prejudice")
        self.assertIn("wikisource-en:Pride and Prejudice (1813)", str(cm.exception))
        self.assertEqual(len(net.urls), 1)                  # 多版本页不用再取渲染后的页面

    def test_wikitext_fetched_once(self):
        """meta() 和 units() 共用一次 wikitext；目录在 wikitext 里就不取渲染后的页面（zh 三國演義走这条）。"""
        src = wikisource.Wikisource("zh")
        net = FakeNet(wikitext=fixture("wikisource_zh_toc.wikitext"))
        with patched(net):
            src.meta("三國演義")
            src.units("三國演義")
            src.units("三國演義")
        self.assertEqual(len(net.urls), 1)
        self.assertIn("prop=wikitext", net.urls[0])

    def test_transcluded_toc_from_rendered_page(self):
        """🔴 2026-09-25：扫描本转载的作品整本成了一块「书名页 + 版权声明」。目录要从渲染后的页面里按顺序取。"""
        rid, wt, pages = moby_like()
        src = wikisource.Wikisource("en")
        net = FakeNet(wikitext=wt, html=pages)
        with patched(net):
            units = src.units(rid)
            text = src.text(rid, units[0])
            self.assertIsNone(src.peek(rid, units[1]))       # 第二章还没取过：不联网拿不到
            self.assertEqual(src.peek(rid, units[0]), text)
        self.assertEqual([u["label"] for u in units], ["Chapter %d" % i for i in range(1, 12)] + ["Epilogue"])
        self.assertEqual([u["title"] for u in units[:3]], ["Loomings", "The Bag", "The Inn"])
        self.assertEqual(units[-1]["title"], "")
        self.assertEqual(units[0]["ref"], {"page": "Whale Book (1851)/Chapter 1"})
        self.assertEqual(units[10]["ref"], {"page": "Whale Book (1851)/Chapter 11"})
        self.assertTrue(text.startswith("Call me Tester."))
        self.assertEqual(sum("prop=wikitext" in u for u in net.urls), 1)
        self.assertEqual(sum("prop=text" in u for u in net.urls), 2)       # 主页面一次，第一章一次

    def test_transcluded_without_chapters_refused(self):
        """转载了扫描页、渲染后也找不到 3 个子页面链接、整页又没几个字：拆不出章节，拒绝（fetch 不拉、不试读）。"""
        rid, wt, pages = moby_like()
        pages = {rid: '<div class="mw-parser-output"><p>WHALE BOOK. By Some Writer. Published 1851.</p>'
                      '<p><a href="/wiki/Whale_Book_(1851)/Etymology">Etymology</a></p></div>'}
        with patched(FakeNet(wikitext=wt, html=pages)), self.assertRaises(ValueError) as cm:
            wikisource.Wikisource("en").units(rid)
        self.assertIn("拆不出章节", str(cm.exception))

    def test_long_single_page_is_chunked(self):
        """单页作品正文够长：照旧按字数切块；渲染后的页面只取一次（找目录和取正文共用）。"""
        rid = "A Long Essay"
        body = "".join("<p>%s</p>" % ("Paragraph %d of a long essay without any chapters at all. " % i * 12)
                       for i in range(30))
        net = FakeNet(wikitext="{{header\n | title = A Long Essay\n}}\nplain text", html={rid: body})
        src = wikisource.Wikisource("en")
        with patched(net):
            units = src.units(rid)
            texts = [src.text(rid, u) for u in units]
        self.assertGreater(len(units), 2)
        self.assertEqual(units[0]["label"], "第1段")
        self.assertIn("Paragraph 0 of a long essay", texts[0])
        self.assertEqual(sum("prop=text" in u for u in net.urls), 1)

    def test_missing_chapter_page_is_empty(self):
        """目录里的红链（章还没录入）：正文回空串，fetch.py 记「取不到正文」跳过，别的章照拉。"""
        src = wikisource.Wikisource("zh")
        with patched(FakeNet(wikitext=fixture("wikisource_zh_toc.wikitext"))):
            units = src.units("三國演義")
            self.assertEqual(src.text("三國演義", units[0]), "")

    def test_calls_are_spaced(self):
        src = wikisource.Wikisource("zh")
        src.GAP = 3.0
        sleeps = []
        with patched(FakeNet(wikitext="x")), mock.patch.object(wikisource.time, "sleep", sleeps.append):
            src._api(action="parse", page="a", prop="wikitext")
            src._api(action="parse", page="b", prop="wikitext")
        self.assertEqual(len(sleeps), 1)
        self.assertGreater(sleeps[0], 2.5)


# ---------------------------------------------------------------- 打包
class TestBuild(unittest.TestCase):
    """build.py 在临时 ROOT 里跑：没读过的不上书架；章链接可以是 null。"""

    def test_unread_resources_left_off_shelf(self):
        import build
        tmp = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(tmp, "rulers"))
            os.makedirs(os.path.join(tmp, "site"))
            shutil.copy(os.path.join(core.ROOT, "rulers", "novel_v1.json"), os.path.join(tmp, "rulers"))
            with mock.patch.object(core, "ROOT", tmp), mock.patch.object(build, "ROOT", tmp):
                fp = core.ruler_fp(core.load_ruler("novel_v1"))
                units = [{"n": 1, "label": "CHAPTER I", "title": "", "url": None},
                         {"n": 2, "label": "CHAPTER II", "title": "", "url": None}]
                for rid, recs in (("1", [{"n": 1, "label": "CHAPTER I"}]), ("2", []),
                                  ("3", [{"n": 1, "label": "旧标签"}])):
                    key = "gutenberg:" + rid
                    core.save_json(core.meta_path(key), {"key": key, "title": "Book " + rid, "ruler": "novel_v1"})
                    core.save_json(core.data_path(key, "units.json"), units)
                    with open(core.data_path(key), "w", encoding="utf-8") as f:
                        for r in recs:
                            r.update({"title": "", "url": None, "字数": 1, "送出字数": 1, "utc": "x", "answers": {},
                                      "正文指纹": "00000000", "尺指纹": fp})
                            f.write(json.dumps(r, ensure_ascii=False) + "\n")
                out = io.StringIO()
                with redirect_stdout(out):
                    bundle = build.build()
            self.assertEqual([r["key"] for r in bundle["shelf"]], ["gutenberg:1"])
            self.assertEqual(bundle["shelf"][0]["units_read"], 1)
            self.assertIsNone(bundle["shelf"][0]["units"][0]["url"])
            self.assertIn("一章都没读过，不上书架", out.getvalue())
            self.assertIn("2 个没读过的没上架", out.getvalue())
            with open(os.path.join(tmp, "site", "data.js"), encoding="utf-8") as f:
                self.assertNotIn("Book 2", f.read())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
