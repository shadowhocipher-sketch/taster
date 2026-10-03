# -*- coding: utf-8 -*-
"""
deck_data.py 的单元测试。在临时 ROOT 里造两三章假读数，全离线。
风格照 test_check.py：mock core.ROOT，造 data/ 再跑生成，检查 site/decks/.../data.js 的内容。

  python -m unittest discover -s tools/tests
"""
import contextlib, io, json, os, re, shutil, sys, tempfile, unittest
from unittest import mock

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
import core, deck_data  # noqa: E402

KEY = deck_data.KEY
UNITS = [{"n": 1, "label": "Chapter I", "title": "", "url": None},
         {"n": 2, "label": "Chapter II", "title": "", "url": None},
         {"n": 3, "label": "Chapter III", "title": "", "url": None}]


class DeckDataTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.makedirs(os.path.join(self.tmp, "rulers"))
        deck_dir = os.path.join(self.tmp, "site", "decks", "pride-and-prejudice")
        os.makedirs(deck_dir)
        self.deck_data_path = os.path.join(deck_dir, "data.js")
        shutil.copy(os.path.join(core.ROOT, "rulers", "novel_v1.json"), os.path.join(self.tmp, "rulers"))
        # mock 的都是模块属性，test_check.py 同款；load_ruler 读 core.ROOT，一并生效
        for patcher in (mock.patch.object(core, "ROOT", self.tmp),
                        mock.patch.object(deck_data, "DECK_DATA", self.deck_data_path)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.ruler = core.load_ruler("novel_v1")
        core.save_json(core.meta_path(KEY), {"key": KEY, "title": "Book", "ruler": "novel_v1", "units_total": 3})
        core.save_json(core.data_path(KEY, "units.json"), UNITS)

    def rec(self, n, want_next, feel, top_prob):
        """造一条读数：answers 里塞 deck_data 会读的两种题的形状。"""
        return {
            "utc": "2026-10-03 00:00Z", "n": n, "label": UNITS[n - 1]["label"], "title": "", "url": None,
            "字数": 100, "送出字数": 100, "正文指纹": "0000000%d" % n,
            "尺": self.ruler["版本"], "尺指纹": core.ruler_fp(self.ruler), "实际模型": self.ruler["模型"],
            "answers": {
                "想看下一章": {"type": "noul", "noul": want_next},
                "主要感受": {"type": "choice", "choice": feel, "confidence": 0.9,
                              "probabilities": {feel: top_prob, "平淡": round(1 - top_prob, 4)}},
            },
        }

    def write_recs(self, recs):
        with open(core.data_path(KEY), "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def generate(self):
        """在 mock 的 ROOT/DECK_DATA 下跑 main()，返回生成的 data.js 文本。"""
        with contextlib.redirect_stdout(io.StringIO()):
            deck_data.main([])
        with open(self.deck_data_path, encoding="utf-8") as f:
            return f.read()

    def readings_of(self, js):
        """生成的 data.js 是 JS 对象字面量（键没引号），readings 数组本身是纯 JSON，直接抠出来解析。"""
        m = re.search(r"readings: (\[.*\])\n\};", js, re.S)
        self.assertTrue(m, "没找到 readings 数组")
        return json.loads(m.group(1))

    def test_generates_readings(self):
        self.write_recs([self.rec(1, 0.82, "好笑", 0.96), self.rec(2, 0.44, "好奇", 0.4)])
        js = self.generate()
        self.assertIn("window.DECK", js)
        self.assertIn("units_total: %d" % deck_data.UNITS_TOTAL, js)
        rs = self.readings_of(js)
        self.assertEqual(len(rs), 2)
        r1 = rs[0]
        self.assertEqual((r1["n"], r1["want_next"], r1["feel"], r1["feel_top"]), (1, 0.82, "好笑", 0.96))
        self.assertNotIn("feel_conf", r1)          # 不导出 confidence——站规判定看最高概率
        self.assertNotIn("feel_probs", r1)         # 也不导出整个概率表

    def test_last_read_wins_per_chapter(self):
        self.write_recs([self.rec(1, 0.82, "好笑", 0.96), self.rec(1, 0.51, "紧张", 0.7)])
        rs = self.readings_of(self.generate())
        self.assertEqual(len(rs), 1)               # 同章重读：留最后一次
        self.assertEqual(rs[0]["want_next"], 0.51)

    def test_model_version_strips_prefix(self):
        self.write_recs([self.rec(1, 0.82, "好笑", 0.96)])
        js = self.generate()
        self.assertIn('model_version: "1.13.0"', js)   # xxx-1.13.0 → 1.13.0

    def test_no_readings_leaves_file_alone(self):
        with open(self.deck_data_path, "w", encoding="utf-8") as f:
            f.write("sentinel")
        with contextlib.redirect_stdout(io.StringIO()):
            deck_data.main([])
        with open(self.deck_data_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "sentinel")  # 没读数就不动文件


if __name__ == "__main__":
    unittest.main()
