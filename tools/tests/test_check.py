# -*- coding: utf-8 -*-
"""
红线检查（tools/check.py）的单元测试。在临时 ROOT 里造数据，全离线。

  python -m unittest discover -s tools/tests
"""
import io, json, os, shutil, sys, tempfile, unittest
from contextlib import redirect_stdout
from unittest import mock

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
import build, check, core  # noqa: E402

KEY = "gutenberg:1"
UNITS = [{"n": 1, "label": "CHAPTER I", "title": "", "url": None},
         {"n": 2, "label": "CHAPTER II", "title": "", "url": None}]


class TempRoot(unittest.TestCase):
    """临时 ROOT：一把真尺 + 一本两章、读过第一章的书 + 跟它一致的 site/data.js。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "rulers"))
        os.makedirs(os.path.join(self.tmp, "site"))
        shutil.copy(os.path.join(core.ROOT, "rulers", "novel_v1.json"), os.path.join(self.tmp, "rulers"))
        for m in (mock.patch.object(core, "ROOT", self.tmp), mock.patch.object(build, "ROOT", self.tmp)):
            m.start()
            self.addCleanup(m.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ruler = core.load_ruler("novel_v1")
        core.save_json(core.meta_path(KEY), {"key": KEY, "title": "Book", "ruler": "novel_v1", "units_total": 2})
        core.save_json(core.data_path(KEY, "units.json"), UNITS)
        self.write_recs([self.rec()])
        with redirect_stdout(io.StringIO()):
            build.build()

    def rec(self, **kw):
        r = {"utc": "x", "n": 1, "label": "CHAPTER I", "title": "", "url": None, "字数": 1, "送出字数": 1,
             "正文指纹": "00000000", "尺": self.ruler["版本"], "尺指纹": core.ruler_fp(self.ruler),
             "实际模型": self.ruler["模型"], "answers": {k: {} for k in self.ruler["题"]}}
        r.update(kw)
        return r

    def write_recs(self, recs):
        with open(core.data_path(KEY), "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def site(self, name, text):
        with open(os.path.join(self.tmp, "site", name), "w", encoding="utf-8") as f:
            f.write(text)


class TestData(TempRoot):
    def test_clean_passes(self):
        self.assertEqual(check.check_data(), [])
        self.assertEqual(check.check_datajs(), [])

    def test_text_field_rejected(self):
        self.write_recs([self.rec(正文="第一章全文……")])
        self.assertTrue(any("多了字段 正文" in b for b in check.check_data()))

    def test_ruler_and_model_mismatch(self):
        self.write_recs([self.rec(尺指纹="deadbeef"), self.rec(实际模型="other-9.9")])
        bad = check.check_data()
        self.assertTrue(any("尺指纹 deadbeef" in b for b in bad))
        self.assertTrue(any(":2：模型" in b for b in bad))

    def test_label_and_chapter_mismatch(self):
        self.write_recs([self.rec(label="旧标签"), self.rec(n=9)])
        bad = check.check_data()
        self.assertTrue(any("目录是「CHAPTER I」" in b for b in bad))
        self.assertTrue(any("第 9 章不在目录里" in b for b in bad))

    def test_questions_must_match_ruler(self):
        self.write_recs([self.rec(answers={"随便问": {}})])
        self.assertTrue(any("题跟尺不一致" in b for b in check.check_data()))

    def test_units_total_and_orphans(self):
        core.save_json(core.meta_path(KEY), {"key": KEY, "title": "Book", "ruler": "novel_v1", "units_total": 5})
        with open(core.data_path("gutenberg:2"), "w", encoding="utf-8") as f:
            f.write("")
        bad = check.check_data()
        self.assertTrue(any("units_total=5" in b for b in bad))
        self.assertTrue(any("gutenberg/2.jsonl：没有对应的 meta.json" in b for b in bad))

    def test_stale_datajs(self):
        self.write_recs([self.rec(), self.rec(n=2, label="CHAPTER II")])
        self.assertEqual(check.check_data(), [])
        self.assertTrue(check.check_datajs())


class TestSite(TempRoot):
    def test_names_come_from_ruler_and_endpoint(self):
        names = check.banned_names()
        self.assertIn(self.ruler["模型"].split("-")[0].lower(), names)
        self.assertFalse({"api", "ai", "v1"} & names)

    def test_model_name_flagged(self):
        name = self.ruler["模型"].split("-")[0]
        self.site("a.html", "<p>%s 读数</p>" % name.capitalize())
        self.site("b.js", "// model: %s" % self.ruler["模型"])
        self.assertEqual(len(check.check_site()), 2)

    def test_long_names_flagged_with_spaces(self):
        long = sorted((n for n in check.banned_names() if len(n) >= 6), key=len)[0]
        spaced = " ".join([long[:4], long[4:]]).upper()
        self.site("c.html", "<p>by %s</p>" % spaced)
        self.assertEqual(len(check.check_site()), 1)

    def test_longer_words_not_flagged(self):
        name = self.ruler["模型"].split("-")[0]
        self.site("d.html", "<p>%sons paradox; 第三方判断模型 1.13.0</p>" % name.capitalize())
        self.assertEqual(check.check_site(), [])

    def test_message_does_not_print_name(self):
        name = self.ruler["模型"].split("-")[0]
        self.site("e.html", name)
        bad = check.check_site()
        self.assertEqual(len(bad), 1)
        self.assertNotIn(name, bad[0].lower())   # Actions 日志是公开的


if __name__ == "__main__":
    unittest.main()
