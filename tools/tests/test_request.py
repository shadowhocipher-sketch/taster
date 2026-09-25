# -*- coding: utf-8 -*-
"""
tools/request.py 的单元测试。全离线：假书源 + 假判断模型 + 临时目录，不花钱、不碰真 data/ 和 site/。

  python -m unittest discover -s tools/tests
"""
import io, json, os, re, shutil, sys, tempfile, unittest, urllib.error
from contextlib import redirect_stdout
from unittest import mock

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
import core, judge, sources, request  # noqa: E402

RULER = core.load_ruler("novel_v1")
KEY = "fake-src:测试书"


def body(key="wikisource-zh:三國演義", rtype="小说", note="_No response_"):
    return "### 资源键\n\n%s\n\n### 类型\n\n%s\n\n### 备注\n\n%s" % (key, rtype, note)


def event(action="opened", assoc="OWNER", key=KEY, labels=("试读请求",), label=None, text=None):
    ev = {"action": action,
          "issue": {"number": 7, "title": "试读：$(rm -rf /) @everyone", "body": text if text is not None else body(key),
                    "author_association": assoc, "labels": [{"name": n} for n in labels], "user": {"login": "someone"}},
          "sender": {"login": "someone"}}
    if label:
        ev["label"] = {"name": label}
    return ev


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


class FakeSource:
    """离线书源：三章，第 n 章正文 = 「第n章」重复几遍。"""
    id, name = "fake-src", "假书库"

    def __init__(self, n=3, license="公版", broken=()):
        self.n, self.license, self.broken, self.text_calls = n, license, set(broken), 0

    def meta(self, rid):
        return {"title": rid, "author": "@mallory [x](http://evil)", "lang": "zh", "license": self.license,
                "source_name": self.name, "read_url": "https://example.org/" + rid}

    def units(self, rid):
        return [{"n": i, "label": "第%d回" % i, "title": "题%d" % i, "ref": i} for i in range(1, self.n + 1)]

    def text(self, rid, unit):
        self.text_calls += 1
        if unit["n"] in self.broken:
            raise urllib.error.URLError("fake network down")
        return ("第%d章正文。" % unit["n"]) * 50


class FakeWikisource(FakeSource):
    """离线维基文库：_api 只答 action=query（跟跳转、规范化、名字空间），答案按页名查表。"""
    id, name = "wikisource-en", "Wikisource (en)"
    PAGES = {  # 请求的页名 → (normalized 到, redirect 到, 最终页)
        "Pride and Prejudice": (None, None, {"ns": 0, "title": "Pride and Prejudice", "pageid": 1}),
        "Pride_and_Prejudice": ("Pride and Prejudice", None, {"ns": 0, "title": "Pride and Prejudice", "pageid": 1}),
        "pride and Prejudice": ("Pride and Prejudice", None, {"ns": 0, "title": "Pride and Prejudice", "pageid": 1}),
        "P and P": (None, "Pride and Prejudice", {"ns": 0, "title": "Pride and Prejudice", "pageid": 1}),
        "Wikisource:Sandbox": (None, None, {"ns": 4, "title": "Wikisource:Sandbox", "pageid": 9}),
        "User:Mallory": (None, None, {"ns": 2, "title": "User:Mallory", "missing": True}),
        "User talk:Mallory": (None, None, {"ns": 3, "title": "User talk:Mallory", "pageid": 8}),
        "Page:Foo.djvu": (None, None, {"ns": 104, "title": "Page:Foo.djvu", "missing": True}),
        "Redirect to author": (None, "Author:Jane Austen", {"ns": 102, "title": "Author:Jane Austen", "pageid": 7}),
        "Redirect to subpage": (None, "Book/Chapter 1", {"ns": 0, "title": "Book/Chapter 1", "pageid": 6}),
        "No such book": (None, None, {"ns": 0, "title": "No such book", "missing": True}),
        "Special:Random": (None, None, {"ns": -1, "title": "Special:Random", "special": True}),
    }

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.api_calls = []

    def _api(self, **p):
        self.api_calls.append(p)
        assert p.get("action") == "query" and p.get("redirects") == 1, p
        t = p["titles"]
        if t.startswith("w:"):
            return {"batchcomplete": True, "query": {"interwiki": [{"title": t, "iw": "w"}]}}
        if t not in self.PAGES:
            return {"batchcomplete": True, "query": {"pages": [{"title": t, "invalidreason": "x", "invalid": True}]}}
        norm, redir, page = self.PAGES[t]
        q = {"pages": [page]}
        if norm:
            q["normalized"] = [{"fromencoded": False, "from": t, "to": norm}]
        if redir:
            q["redirects"] = [{"from": norm or t, "to": redir}]
        return {"batchcomplete": True, "query": q}

    def meta(self, rid):
        m = super().meta(rid)
        m["read_url"] = "https://en.wikisource.org/wiki/" + rid.replace(" ", "_")
        return m


def fake_answer(state, questions, model, **kw):
    ans = {}
    for k, q in questions.items():
        ans[k] = {"type": "noul", "noul": 0.8} if q["type"] == "noul" else \
                 {"type": q["type"], "confidence": 0.9, "probabilities": {}}
    return {"model": model, "answers": ans, "usage": {"input_tokens": 1, "output_tokens": 1}}, 5.0


class Sandbox(unittest.TestCase):
    """临时 ROOT（只放尺）+ 假书源进注册表 + 判断模型换成假的。build() 换成计数器：它写死了真 site/data.js。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="taster-test-")
        os.makedirs(os.path.join(self.tmp, "rulers"))
        shutil.copy(os.path.join(core.ROOT, "rulers", "novel_v1.json"), os.path.join(self.tmp, "rulers"))
        self.src = FakeSource()
        self.ask = mock.Mock(side_effect=fake_answer)
        self.build = mock.Mock()
        # 🔴 保险：万一真 ask 漏网，没有 key 也连不出去（JEV_DIR 指到空目录，本机 .env 读不到）
        env = {"TASTER_API_KEY": "", "JEV_DIR": self.tmp, "TASTER_ENDPOINT": "http://127.0.0.1:9/",
               "GITHUB_OUTPUT": "", "GITHUB_RUN_ID": "", "GITHUB_ACTIONS": ""}
        for p in (mock.patch.object(core, "ROOT", self.tmp),
                  mock.patch.dict(sources.REGISTRY, {"fake-src": self.src}),
                  mock.patch.object(judge, "ask", self.ask),
                  mock.patch.object(request.build_mod, "build", self.build),
                  mock.patch.dict(os.environ, env)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def handle(self, ev, **kw):
        with redirect_stdout(io.StringIO()):
            return request.handle(ev, **kw)

    def records(self, key=KEY):
        p = core.data_path(key)
        return [json.loads(l) for l in read(p).splitlines()] if os.path.exists(p) else []


# ------------------------------------------------------------------ 谁能触发
class TestDecide(unittest.TestCase):
    def test_owner_opened_runs(self):
        self.assertEqual(request.decide(event("opened", "OWNER"))[0], "run")

    def test_stranger_opened_pending(self):
        for assoc in ("NONE", "CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "MEMBER", "COLLABORATOR", None):
            self.assertEqual(request.decide(event("opened", assoc))[0], "pending", assoc)

    def test_labeled_approve_runs(self):
        ev = event("labeled", "NONE", labels=("试读请求", "批准"), label="批准")
        self.assertEqual(request.decide(ev)[0], "run")

    def test_labeled_other_ignored(self):
        for name in ("试读请求", "bug", "已试读", "批准 ", "Approve"):
            ev = event("labeled", "OWNER", labels=("试读请求", name), label=name)
            self.assertEqual(request.decide(ev)[0], "ignore", name)

    def test_needs_request_label(self):
        self.assertEqual(request.decide(event("opened", "OWNER", labels=()))[0], "ignore")
        ev = event("labeled", "OWNER", labels=("批准",), label="批准")
        self.assertEqual(request.decide(ev)[0], "ignore")

    def test_opened_with_approve_left_to_labeled(self):
        # 开的时候就带「批准」：opened 和 labeled 两个事件都会来，只让 labeled 跑，免得一本书读两遍
        for assoc in ("OWNER", "NONE", "MEMBER"):
            ev = event("opened", assoc, labels=("试读请求", "批准"))
            self.assertEqual(request.decide(ev)[0], "ignore", assoc)
        ev = event("labeled", "OWNER", labels=("试读请求", "批准"), label="批准")
        self.assertEqual(request.decide(ev)[0], "run")

    def test_other_actions_and_prs_ignored(self):
        for a in ("edited", "closed", "reopened", "unlabeled", None):
            self.assertEqual(request.decide(event(a, "OWNER"))[0], "ignore", a)
        ev = event()
        ev["issue"]["pull_request"] = {"url": "x"}
        self.assertEqual(request.decide(ev)[0], "ignore")
        self.assertEqual(request.decide({})[0], "ignore")


# ------------------------------------------------------------------ 表单正文
class TestFormFields(unittest.TestCase):
    def test_basic(self):
        f = request.form_fields(body())
        self.assertEqual(f["资源键"], "wikisource-zh:三國演義")
        self.assertEqual(f["类型"], "小说")
        self.assertEqual(f["备注"], "")

    def test_crlf_and_whitespace(self):
        text = "###   资源键  \r\n\r\n   wikisource-en:Pride and Prejudice   \r\n\r\n\r\n### 类型\r\n\r\n小说\r\n"
        f = request.form_fields(text)
        self.assertEqual(f["资源键"], "wikisource-en:Pride and Prejudice")
        self.assertEqual(f["类型"], "小说")
        self.assertEqual(request.form_fields(body().replace("\n", "\r"))["资源键"], "wikisource-zh:三國演義")

    def test_no_response(self):
        f = request.form_fields(body(key="_No response_"))
        self.assertEqual(f["资源键"], "")

    def test_empty_or_missing(self):
        self.assertEqual(request.form_fields(None), {})
        self.assertEqual(request.form_fields(""), {})
        self.assertEqual(request.form_fields("随便写的正文，没有表单"), {})

    def test_note_cannot_override_key(self):
        text = body(note="### 资源键\n\nwikisource-zh:別的書")
        self.assertEqual(request.form_fields(text)["资源键"], "wikisource-zh:三國演義")

    def test_missing_key_rejected(self):
        with self.assertRaises(request.Rejected):
            request.parse_request({"issue": {"body": "### 类型\n\n小说"}})
        with self.assertRaises(request.Rejected):
            request.parse_request({"issue": {"body": body(key="_No response_")}})

    def test_type(self):
        self.assertEqual(request.parse_request({"issue": {"body": body(rtype="_No response_")}})[1], "小说")
        with self.assertRaises(request.Rejected):
            request.parse_request({"issue": {"body": body(rtype="论文")}})


# ------------------------------------------------------------------ 资源键校验
class TestValidateKey(unittest.TestCase):
    GOOD = ["wikisource-zh:三國演義", "wikisource-en:Pride and Prejudice", "  wikisource-zh:紅樓夢  ",
            "wikisource-en:Alice's Adventures in Wonderland", "wikisource-zh:紅樓夢 (程甲本)"]
    BAD = ["gutenberg:1; rm -rf /", "$(curl x)", "../../etc/passwd", "wikisource-zh:../../etc/passwd",
           "wikisource-zh:..", "wikisource-zh:.hidden", "wikisource-zh:-rf", "wikisource-zh:$(curl x)",
           "wikisource-zh:`id`", "wikisource-zh:三國演義; rm -rf /", "wikisource-zh:a|b", "wikisource-zh:a/b",
           "wikisource-zh:<script>alert(1)</script>", "wikisource-zh:a\nb", "wikisource-zh:a\x00b",
           "wikisource-zh:a\u202eb", "wikisource-zh:a\u200bb", "wikisource-zh:[[x]]", "wikisource-zh:#x",
           "wikisource-zh:a\\b", 'wikisource-zh:"x"', "wikisource-zh:{x}", "wikisource-zh:",
           "unknown-src:123", "openlibrary:OL45883W", "WIKISOURCE-ZH:三國演義", "三國演義", "", "   ",
           "wikisource-zh:" + "長" * 300, "wikisource-zh：三國演義"]

    def test_good(self):
        for k in self.GOOD:
            self.assertEqual(request.validate_key(k), k.strip())

    def test_bad(self):
        for k in self.BAD:
            with self.assertRaises(request.Rejected, msg=repr(k)) as cm:
                request.validate_key(k)
            msg = str(cm.exception)
            for bad in ("rm -rf", "curl", "passwd", "<script", "`"):
                self.assertNotIn(bad, msg, "拒绝理由不许回显原文：%r" % k)

    def test_windows_reserved_names(self):
        # data/wikisource-en/AUX.jsonl 进了仓库，Git for Windows 就检出不了
        for rid in ("Con", "AUX", "Nul", "prn", "COM1", "com0", "LPT9", "COM¹", "aux.foo", "Nul .x", "CON.tar.gz"):
            with self.assertRaises(request.Rejected, msg=rid):
                request.validate_key("wikisource-en:" + rid)
        for rid in ("Conan", "Aux Foo", "Console", "Null", "COM10 and Friends", "Nulla.Dies"):
            self.assertEqual(request.validate_key("wikisource-en:" + rid), "wikisource-en:" + rid)

    def test_unicode_whitespace_normalised(self):
        """🔴 首尾的全角空格、不换行空格一律去掉，返回去掉后的键（以前原样返回，文件名、评论里都带着）。"""
        for pad in ("\u3000", "\u00a0", " \u3000\t", "\u00a0\n"):
            for k in (pad + "wikisource-zh:三國演義", "wikisource-zh:三國演義" + pad, pad + "wikisource-zh:三國演義" + pad):
                self.assertEqual(request.validate_key(k), "wikisource-zh:三國演義", repr(k))
        for k in ("wikisource-zh:三國\u3000演義", "wikisource-zh:三國\u00a0演義", "wikisource-zh:\u3000三國演義"):
            with self.assertRaises(request.Rejected, msg=repr(k)):
                request.validate_key(k)

    def test_per_source_rule(self):
        with mock.patch.dict(sources.REGISTRY, {"gutenberg": object()}):
            self.assertEqual(request.validate_key("gutenberg:1342"), "gutenberg:1342")
            for k in ("gutenberg:1; rm -rf /", "gutenberg:0", "gutenberg:01342", "gutenberg:1342a",
                      "gutenberg:99999999", "gutenberg: 1342", "gutenberg:Pride and Prejudice"):
                with self.assertRaises(request.Rejected, msg=k):
                    request.validate_key(k)


# ------------------------------------------------------------------ 规范页名（维基文库名字空间、别名）
class TestCanonical(unittest.TestCase):
    def setUp(self):
        self.ws = FakeWikisource()
        p = mock.patch.dict(sources.REGISTRY, {"wikisource-en": self.ws})
        p.start()
        self.addCleanup(p.stop)

    def test_already_canonical(self):
        self.assertEqual(request.canonical_key("wikisource-en:Pride and Prejudice"),
                         ("wikisource-en:Pride and Prejudice", None))

    def test_aliases_resolve_to_one_key(self):
        # 下划线、首字母小写、跳转：都归到同一个键、同一份数据，不重读
        for alias in ("Pride_and_Prejudice", "pride and Prejudice", "P and P"):
            k = "wikisource-en:" + alias
            self.assertEqual(request.canonical_key(k), ("wikisource-en:Pride and Prejudice", k), alias)

    def test_non_main_namespace_rejected(self):
        # 🔴 这些页谁都能随手改，validate_key 放得过（编号里能有「:」），这里必须挡住
        for rid in ("Wikisource:Sandbox", "User:Mallory", "User talk:Mallory", "Page:Foo.djvu",
                    "Special:Random", "Redirect to author"):
            k = "wikisource-en:" + rid
            self.assertEqual(request.validate_key(k), k)
            with self.assertRaises(request.Rejected, msg=rid) as cm:
                request.canonical_key(k)
            self.assertIn("正文", str(cm.exception))

    def test_missing_invalid_interwiki(self):
        for rid in ("No such book", "w:Pride and Prejudice", "Not in table"):
            with self.assertRaises(request.Rejected, msg=rid):
                request.canonical_key("wikisource-en:" + rid)

    def test_canonical_title_revalidated(self):
        # 跳转目标带「/」之类白名单外的字符：不收，也不回显
        with self.assertRaises(request.Rejected) as cm:
            request.canonical_key("wikisource-en:Redirect to subpage")
        self.assertNotIn("Book/Chapter", str(cm.exception))

    def test_other_sources_untouched(self):
        self.assertEqual(request.canonical_key("gutenberg:1342"), ("gutenberg:1342", None))
        self.assertEqual(self.ws.api_calls, [])


# ------------------------------------------------------------------ 整条流程（离线）
class TestHandle(Sandbox):
    def test_owner_opened_reads_everything(self):
        status, text = self.handle(event("opened", "OWNER"))
        self.assertEqual(status, "done")
        self.assertEqual(self.ask.call_count, 3)
        self.assertEqual(self.build.call_count, 1)
        self.assertEqual([r["n"] for r in self.records()], [1, 2, 3])
        self.assertIn("试读完成", text)
        self.assertIn("这次读了 3 章", text)
        self.assertIn("全书 3 章，已试读 3 章，剩 0 章", text)
        self.assertIn(request.SITE + "#/r/fake-src%3A%E6%B5%8B%E8%AF%95%E4%B9%A6", text)
        self.assertIn("第三方判断模型 1.13.0", text)
        self.assertNotIn(RULER["模型"], text)          # 站上、评论里不点名模型
        self.assertNotIn("@mallory", text)            # 外来文字里的 @ 不许变成提人
        self.assertNotIn("[x](http", text)
        self.assertNotIn("rm -rf", text)              # 标题从来不读

    def test_cap_then_resume(self):
        status, text = self.handle(event("opened", "OWNER"), max_calls=2)
        self.assertEqual(status, "partial")
        self.assertIn("剩 1 章", text)
        self.assertIn("再加一次「批准」", text)
        self.assertEqual(self.src.text_calls, 2)      # 只拉这批要读的正文
        self.assertIn(request.MARK % KEY, text)
        ev = event("labeled", "NONE", labels=("试读请求", "批准"), label="批准")
        with mock.patch.object(request, "marked_key", return_value=KEY):
            status, text = self.handle(ev, max_calls=2)
        self.assertEqual(status, "done")
        self.assertIn("这次读了 1 章", text)
        self.assertEqual(self.ask.call_count, 3)

    def test_stranger_gets_estimate_only(self):
        with mock.patch.object(request.fetch_mod, "fetch", side_effect=AssertionError("不许拉")):
            status, text = self.handle(event("opened", "CONTRIBUTOR"))
        self.assertEqual(status, "pending")
        self.assertIn("等维护者加「批准」标签", text)
        self.assertIn("目录 3 章，已试读 0 章，待读 3 章", text)
        self.assertIn("批准后读 第1回 到 第3回，最多调用 3 次", text)
        self.ask.assert_not_called()
        self.build.assert_not_called()
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "data")))   # 预估不写盘

    def test_pending_comment_carries_marker(self):
        status, text = self.handle(event("opened", "NONE"))
        self.assertEqual(status, "pending")
        self.assertTrue(text.rstrip().endswith(request.MARK % KEY))
        self.assertNotIn("原页", text)               # 没编码的网址不放

    def test_labeled_other_ignored(self):
        ev = event("labeled", "OWNER", labels=("试读请求",), label="试读请求")
        self.assertEqual(self.handle(ev), ("ignored", None))
        self.ask.assert_not_called()

    # ---- 批准的必须是预估过的那个键
    def approve(self, marked, **kw):
        ev = event("labeled", "NONE", labels=("试读请求", "批准"), label="批准", **kw)
        with mock.patch.object(request, "marked_key", **marked) as mk:
            out = self.handle(ev)
        return out, mk

    def test_approval_matches_estimate_runs(self):
        (status, text), mk = self.approve({"return_value": KEY})
        self.assertEqual(status, "done")
        self.assertEqual(self.ask.call_count, 3)
        mk.assert_called_once()

    def test_body_edited_after_estimate_only_reestimates(self):
        # 外人等预估出来后把正文改成别的书：批准的是旧键，不许开读
        (status, text), _ = self.approve({"return_value": "fake-src:别的书"})
        self.assertEqual(status, "changed")
        self.assertIn("上次是 `fake-src:别的书`", text)
        self.assertIn("目录 3 章，已试读 0 章，待读 3 章", text)
        self.assertIn("看过这份预估、再加「批准」", text)
        self.assertTrue(text.rstrip().endswith(request.MARK % KEY))   # 下一次批准就对得上了
        self.ask.assert_not_called()
        self.build.assert_not_called()
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "data")))

    def test_no_estimate_on_record_only_estimates(self):
        (status, text), _ = self.approve({"return_value": None})
        self.assertEqual(status, "changed")
        self.assertIn("找不到预估记录", text)
        self.ask.assert_not_called()

    def test_cannot_read_comments_fails_closed(self):
        (status, text), _ = self.approve({"side_effect": urllib.error.URLError("down")})
        self.assertEqual(status, "rejected")
        self.assertIn("核对不了", text)
        self.ask.assert_not_called()

    def test_owner_issue_not_checked(self):
        ev = event("labeled", "OWNER", labels=("试读请求", "批准"), label="批准")
        with mock.patch.object(request, "marked_key", side_effect=AssertionError("不该查")):
            status, _ = self.handle(ev)
        self.assertEqual(status, "done")

    # ---- 两个 job 各管各的
    def test_job_split(self):
        with mock.patch.object(request.fetch_mod, "fetch", side_effect=AssertionError("不许拉")):
            # 预估 job 接到仓库主人的 Issue / 批准事件：不管（那是 taste job 的）
            self.assertEqual(self.handle(event("opened", "OWNER"), job="estimate"), ("ignored", None))
            ev = event("labeled", "NONE", labels=("试读请求", "批准"), label="批准")
            self.assertEqual(self.handle(ev, job="estimate"), ("ignored", None))
            # taste job 接到外人开的 Issue：不管（那是 estimate job 的）
            self.assertEqual(self.handle(event("opened", "NONE"), job="taste"), ("ignored", None))
            self.assertEqual(self.handle(event("opened", "NONE"), job="estimate")[0], "pending")
        self.ask.assert_not_called()
        self.assertEqual(self.handle(event("opened", "OWNER"), job="taste")[0], "done")

    # ---- 维基文库：名字空间、别名
    def ws_sandbox(self):
        ws = FakeWikisource()
        p = mock.patch.dict(sources.REGISTRY, {"wikisource-en": ws})
        p.start()
        self.addCleanup(p.stop)
        return ws

    def test_namespace_page_rejected_before_estimate(self):
        ws = self.ws_sandbox()
        for rid in ("Wikisource:Sandbox", "User:Mallory"):
            with mock.patch.object(ws, "meta", side_effect=AssertionError("不许预估")):
                status, text = self.handle(event("opened", "NONE", key="wikisource-en:" + rid))
            self.assertEqual(status, "rejected", rid)
            self.assertIn("正文里的作品页", text)
            status, _ = self.handle(event("opened", "OWNER", key="wikisource-en:" + rid))
            self.assertEqual(status, "rejected", rid)
        self.ask.assert_not_called()

    def test_alias_key_reads_into_canonical_data(self):
        self.ws_sandbox()
        status, text = self.handle(event("opened", "NONE", key="wikisource-en:Pride_and_Prejudice"))
        self.assertEqual(status, "pending")
        self.assertIn("`wikisource-en:Pride and Prejudice`", text)
        self.assertIn("请求里写的是 `wikisource-en:Pride_and_Prejudice`", text)
        self.assertIn(request.MARK % "wikisource-en:Pride and Prejudice", text)
        self.assertIn("原页：https://en.wikisource.org/wiki/Pride_and_Prejudice", text)
        status, _ = self.handle(event("opened", "OWNER", key="wikisource-en:pride and Prejudice"))
        self.assertEqual(status, "done")
        self.assertEqual(len(self.records("wikisource-en:Pride and Prejudice")), 3)
        self.assertEqual(os.listdir(os.path.join(self.tmp, "data", "wikisource-en")),
                         ["Pride and Prejudice.jsonl", "Pride and Prejudice.meta.json",
                          "Pride and Prejudice.units.json"])
        # 再用另一个别名请求：同一份数据，一章都不重读
        status, text = self.handle(event("opened", "OWNER", key="wikisource-en:P and P"))
        self.assertEqual(status, "done")
        self.assertIn("这次读了 0 章", text)
        self.assertEqual(self.ask.call_count, 3)

    def test_canonical_lookup_down(self):
        ws = self.ws_sandbox()
        with mock.patch.object(ws, "_api", side_effect=urllib.error.URLError("down")):
            status, text = self.handle(event("opened", "NONE", key="wikisource-en:Pride and Prejudice"))
            self.assertEqual(status, "pending")
            self.assertIn("预估暂时拿不到（连不上书源）", text)
            with self.assertRaises(urllib.error.URLError):     # 真跑路径：不跑，交给 main 回「出错」
                self.handle(event("opened", "OWNER", key="wikisource-en:Pride and Prejudice"))
        self.ask.assert_not_called()

    def test_malicious_key_rejected_before_anything(self):
        for k in ("gutenberg:1; rm -rf /", "$(curl x)", "../../etc/passwd", "fake-src:../../etc/passwd", ""):
            with mock.patch.object(request.fetch_mod, "fetch", side_effect=AssertionError("不许拉")):
                status, text = self.handle(event("opened", "OWNER", key=k))
            self.assertEqual(status, "rejected", k)
            self.assertIn("没法处理", text)
            for bad in ("rm -rf", "curl", "passwd"):
                self.assertNotIn(bad, text)
        self.ask.assert_not_called()
        self.build.assert_not_called()

    def test_copyrighted_rejected(self):
        self.src.license = "版权"
        status, text = self.handle(event("opened", "OWNER"))
        self.assertEqual(status, "rejected")
        self.assertIn("版权", text)
        status, _ = self.handle(event("opened", "NONE"))
        self.assertEqual(status, "rejected")
        self.ask.assert_not_called()

    def test_judge_error_keeps_what_was_read(self):
        secret = "sk-SECRET-123456"
        os.environ["TASTER_API_KEY"] = secret
        calls = []

        def flaky(state, questions, model, **kw):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("HTTP 401（key 无效）\n{\"vendor\": \"SomeVendor\", \"key\": \"%s\"}" % secret)
            return fake_answer(state, questions, model)

        self.ask.side_effect = flaky
        out = io.StringIO()
        with redirect_stdout(out):
            status, text = request.handle(event("opened", "OWNER"))
        self.assertEqual(status, "error")
        self.assertIn("判断模型返回 HTTP 401", text)
        self.assertIn("已试读 1 章，剩 2 章", text)
        for leak in (secret, "SomeVendor"):
            self.assertNotIn(leak, text)
        self.assertNotIn(secret, out.getvalue())       # 日志也不许出现 key
        self.assertEqual(len(self.records()), 1)
        self.build.assert_called_once()

    def test_time_budget_stops_cleanly(self):
        status, text = self.handle(event("opened", "OWNER"), budget_min=0)
        self.assertEqual(status, "partial")
        self.assertIn("时间上限", text)
        self.build.assert_called_once()

    def test_fetch_failure_midway_reads_what_it_has(self):
        self.src.broken = {3}
        status, text = self.handle(event("opened", "OWNER"))
        self.assertEqual(status, "partial")
        self.assertIn("拉正文中途出错：连不上书源", text)
        self.assertIn("已试读 2 章，剩 1 章", text)
        self.assertEqual(self.ask.call_count, 2)

    def test_dry_run_never_calls_judge_or_writes(self):
        status, text = self.handle(event("opened", "OWNER"), dry_run=True)
        self.assertEqual(status, "dry-run")
        self.assertIn("待读 3 章", text)
        self.assertIn("真跑这次只读 第1回 到 第3回，3 章", text)
        self.assertIn("没写 data/ 和 texts/", text)
        self.assertNotIn("没拉正文", text)              # 有的书源切目录要把全文读进内存：不能说没拉
        self.ask.assert_not_called()
        self.build.assert_not_called()
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "data")))


    # ---- 「读过」只有一个定义；真跑只读批准的那一批
    def seed(self, n, label, fp_text, text_on_disk=None):
        """往临时 data/ 里写一条读数（当前尺），可选在 texts/ 里放一份正文。"""
        rec = {"utc": "x", "n": n, "label": label, "title": "", "url": None, "字数": 1, "送出字数": 1,
               "正文指纹": fp_text, "尺": RULER["版本"], "尺指纹": core.ruler_fp(RULER), "实际模型": RULER["模型"],
               "answers": {}}
        p = core.data_path(KEY)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if text_on_disk is not None:
            d = core.text_dir(KEY)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "%03d.txt" % n), "w", encoding="utf-8") as f:
                f.write(text_on_disk)

    def test_run_reads_exactly_the_approved_batch(self):
        """🔴 预估说读哪几章，真跑就只读哪几章：本机 texts/ 里批外的章有正文、已读的章正文变了，都不碰。"""
        self.src.n = 4
        now = ("第%d章正文。" % 2) * 50
        self.seed(2, "第2回", "deadbeef", text_on_disk=now)                  # 读过，但正文变了
        self.seed(4, "第4回", "0" * 8)                                       # 没这条就不会有 texts/004
        d = core.text_dir(KEY)
        with open(os.path.join(d, "004.txt"), "w", encoding="utf-8") as f:   # 批外的章：本机有正文
            f.write("本机留着的第四章")
        # 第 4 章读数的标签跟目录对不上 → 不算读过（跟 build.py 一个定义）
        with open(core.data_path(KEY), encoding="utf-8") as f:
            lines = f.read().replace('"label": "第4回"', '"label": "旧第4回"')
        with open(core.data_path(KEY), "w", encoding="utf-8") as f:
            f.write(lines)

        status, text = self.handle(event("opened", "NONE"), max_calls=2)
        self.assertEqual(status, "pending")
        self.assertIn("已试读 1 章，待读 3 章", text)
        self.assertIn("批准后读 第1回 到 第3回，最多调用 2 次", text)
        self.assertIn("1 章正文在读过之后变了：第2回", text)

        ev = event("labeled", "NONE", labels=("试读请求", "批准"), label="批准")
        with mock.patch.object(request, "marked_key", return_value=KEY):
            status, text = self.handle(ev, max_calls=2)
        self.assertEqual(status, "partial")
        read = [c.args[0] for c in self.ask.call_args_list]
        self.assertEqual(len(read), 2)
        self.assertTrue(read[0].startswith("第1章正文") and read[1].startswith("第3章正文"), read)
        self.assertEqual([r["n"] for r in self.records()], [2, 4, 1, 3])
        self.assertIn("这次读了 2 章", text)
        self.assertIn("1 章正文在读过之后变了：第2回", text)
        self.assertEqual(self.src.text_calls, 2)                              # 只拉这批

        # 下一轮：批 = 第4回（标签对不上的旧读数不算读过），第2回照样不重读
        with mock.patch.object(request, "marked_key", return_value=KEY):
            status, text = self.handle(ev, max_calls=2)
        self.assertEqual(status, "done")
        self.assertEqual(len(self.ask.call_args_list), 3)
        self.assertEqual(self.ask.call_args_list[-1].args[0], "本机留着的第四章")

    def test_taste_plan_only(self):
        """taste.plan(only=) 只看给的章；本机跑（不给 only）会重读正文变了的章。"""
        import fetch, taste
        with redirect_stdout(io.StringIO()):
            fetch.fetch(KEY)
        self.seed(1, "第1回", core.text_fp(("第1章正文。") * 50))
        self.seed(2, "第2回", "deadbeef")
        self.assertEqual([u["n"] for u, _ in taste.plan(KEY)[3]], [2, 3])
        self.assertEqual([u["n"] for u, _ in taste.plan(KEY, only={3})[3]], [3])
        self.assertEqual([u["n"] for u, _ in taste.plan(KEY, only=set())[3]], [])

    def test_us_only_rejected_with_reason(self):
        self.src.license = "美国公版"
        meta = self.src.meta
        self.src.meta = lambda rid: dict(meta(rid), license_note="仅在美国是公版")
        for assoc in ("NONE", "OWNER"):
            status, text = self.handle(event("opened", assoc))
            self.assertEqual(status, "rejected", assoc)
            self.assertIn("美国公版", text)
            self.assertIn("仅在美国是公版", text)
        self.ask.assert_not_called()

    def test_rejected_example_is_a_real_work_page(self):
        # 「Pride and Prejudice」本身是多版本页，拿它当例子，照抄的人一定被拒
        status, text = self.handle(event("opened", "NONE", key="nonsense"))
        self.assertEqual(status, "rejected")
        self.assertIn("`wikisource-en:Pride and Prejudice (1813)`", text)
        self.assertNotIn("`wikisource-en:Pride and Prejudice`", text)
        with open(os.path.join(TOOLS, "..", ".github", "ISSUE_TEMPLATE", "taste.yml"), encoding="utf-8") as f:
            form = f.read()
        self.assertIn("wikisource-en:Pride and Prejudice (1813)", form)
        self.assertNotRegex(form, r"wikisource-en:Pride and Prejudice(?! \(1813\))")

    def test_judge_errors_redacted_in_public_log(self):
        """🔴 公开的 Actions 日志里不许有判断模型接口的原文、也不打「实际模型 ≠ 尺上的」那行。"""
        for boom, shown in ((RuntimeError('HTTP 500\n{"vendor": "SomeVendor", "trace": "internal-model-x9"}'),
                             "判断模型返回 HTTP 500"),
                            (ValueError("Expecting value: SomeVendor internal-model-x9"), "判断模型调用出错（ValueError）")):
            self.ask.reset_mock()
            self.ask.side_effect = boom
            out = io.StringIO()
            with redirect_stdout(out):
                status, text = request.handle(event("opened", "OWNER"))
            self.assertEqual(status, "error")
            for leak in ("SomeVendor", "internal-model-x9"):
                self.assertNotIn(leak, out.getvalue())
                self.assertNotIn(leak, text)
            self.assertIn(shown, out.getvalue())
            self.assertIn(shown, text)

        self.ask.side_effect = lambda state, q, model, **kw: (dict(fake_answer(state, q, model)[0], model="other-model-9"), 1.0)
        out = io.StringIO()
        with redirect_stdout(out):
            status, text = request.handle(event("opened", "OWNER"))
        self.assertEqual(status, "error")
        for leak in ("other-model-9", "实际模型", RULER["模型"]):
            self.assertNotIn(leak, out.getvalue())
            self.assertNotIn(leak, text)
        self.assertIn("判断模型版本和尺上钉的不一致", out.getvalue())
        self.assertEqual(self.records(), [])                               # 不可比的读数不落盘

    def test_crash_log_redacts_judge_errors(self):
        import taste
        err = taste.JudgeError('HTTP 401\n{"vendor": "SomeVendor"}')
        self.assertEqual(request.log_error(err), "JudgeError: 判断模型返回 HTTP 401")
        self.assertIn("boom-detail", request.log_error(KeyError("boom-detail")))       # 别的报错原文照打，方便查


class TestMain(Sandbox):
    def run_main(self, ev, *args):
        d = tempfile.mkdtemp(dir=self.tmp)
        ep, cp, op = (os.path.join(d, n) for n in ("event.json", "comment.md", "out.txt"))
        with open(ep, "w", encoding="utf-8") as f:
            json.dump(ev, f, ensure_ascii=False)
        open(op, "w").close()
        os.environ["GITHUB_OUTPUT"] = op
        with redirect_stdout(io.StringIO()):
            rc = request.main(["--event", ep, "--comment-file", cp] + list(args))
        out = dict(l.split("=", 1) for l in read(op).splitlines() if "=" in l)
        return rc, out, read(cp) if os.path.exists(cp) else None

    def test_outputs(self):
        os.environ["GITHUB_ACTIONS"] = "true"
        rc, out, text = self.run_main(event("opened", "OWNER"))
        self.assertEqual((rc, out["status"]), (0, "done"))
        self.assertTrue(out["comment"].endswith("comment.md"))
        self.assertIn("试读完成", text)

    def test_ignored_writes_no_comment(self):
        rc, out, text = self.run_main(event("labeled", "OWNER", labels=("试读请求", "bug"), label="bug"))
        self.assertEqual((rc, out["status"], out["comment"], text), (0, "ignored", "", None))

    def test_local_real_run_needs_live(self):
        with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            rc, out, text = self.run_main(event("opened", "OWNER"))
        self.assertEqual(rc, 2)
        self.assertEqual(out, {})
        self.ask.assert_not_called()

    def test_crash_becomes_error_comment(self):
        os.environ["GITHUB_ACTIONS"] = "true"
        with mock.patch.object(request, "run_taste", side_effect=KeyError("boom")):
            rc, out, text = self.run_main(event("opened", "OWNER"))
        self.assertEqual((rc, out["status"]), (0, "error"))
        self.assertIn("内部错误（KeyError）", text)

    def test_missing_event_file(self):
        with mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(request.main(["--event", os.path.join(self.tmp, "nope.json")]), 2)

    def test_log_cannot_issue_workflow_commands(self):
        # 🔴 章名、书名来自书源页面（谁都能改）；fetch.py 把章名顶格打进日志 → runner 会当 ::命令:: 执行
        os.environ["GITHUB_ACTIONS"] = "true"
        evil = "::add-mask::x ##[set-output name=comment;]/etc/passwd"
        self.src.units = lambda rid: [{"n": 1, "label": evil, "title": "::error::t", "ref": 1}]
        d = tempfile.mkdtemp(dir=self.tmp)
        ep, cp = os.path.join(d, "event.json"), os.path.join(d, "comment.md")
        with open(ep, "w", encoding="utf-8") as f:
            json.dump(event("opened", "OWNER"), f, ensure_ascii=False)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(request.main(["--event", ep, "--comment-file", cp, "--job", "taste"]), 0)
        lines = out.getvalue().splitlines()
        self.assertTrue(lines[0].startswith("::stop-commands::"), lines[0])
        token = lines[0][len("::stop-commands::"):]
        self.assertRegex(token, r"^[0-9a-f]{32}$")
        self.assertEqual(lines[-1], "::%s::" % token)
        inner = "\n".join(lines[1:-1])
        self.assertIn(evil, inner)                    # 确实打进了日志——但在停用区间里
        self.assertNotIn(token, inner)
        self.assertEqual(self.ask.call_count, 1)

    def test_no_stop_commands_locally(self):
        out = io.StringIO()
        d = tempfile.mkdtemp(dir=self.tmp)
        ep = os.path.join(d, "event.json")
        with open(ep, "w", encoding="utf-8") as f:
            json.dump(event("opened", "NONE"), f, ensure_ascii=False)
        with redirect_stdout(out):
            request.main(["--event", ep, "--comment-file", os.path.join(d, "c.md")])
        self.assertNotIn("::stop-commands::", out.getvalue())


class TestMarkedKey(unittest.TestCase):
    """读 Issue 评论找 bot 埋的键：只认 github-actions[bot]，取最后一个，翻页。"""

    def comments(self, pages):
        calls = []

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def urlopen(req, timeout=None):
            calls.append(req)
            page = int(re.search(r"page=(\d+)$", req.full_url).group(1))
            return Resp(json.dumps(pages[page - 1] if page <= len(pages) else []).encode())

        env = {"GITHUB_REPOSITORY": "o/r", "GH_TOKEN": "t0ken", "GITHUB_API_URL": "https://api.example"}
        with mock.patch.dict(os.environ, env), mock.patch.object(request.urllib.request, "urlopen", urlopen), \
                mock.patch.dict(sources.REGISTRY, {"fake-src": FakeSource()}):
            return request.marked_key({"issue": {"number": 7}}), calls

    @staticmethod
    def c(login, body, typ="User"):
        return {"user": {"login": login, "type": typ}, "body": body}

    def test_last_bot_marker_wins_forgeries_ignored(self):
        bot = lambda k: self.c("github-actions[bot]", "预估……\n\n" + request.MARK % k, "Bot")
        page1 = [bot("fake-src:甲"), self.c("mallory", request.MARK % "fake-src:乙")] + \
                [self.c("x", "顶")] * 98
        page2 = [bot("fake-src:丙"), self.c("github-actions[bot]", request.MARK % "fake-src:丁"),  # type 不是 Bot
                 self.c("mallory", request.MARK % "fake-src:戊")]
        key, calls = self.comments([page1, page2])
        self.assertEqual(key, "fake-src:丙")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].full_url, "https://api.example/repos/o/r/issues/7/comments?per_page=100&page=1")
        self.assertEqual(calls[0].get_header("Authorization"), "Bearer t0ken")

    def test_none_when_no_marker(self):
        self.assertEqual(self.comments([[self.c("github-actions[bot]", "试读脚本出错了", "Bot")]])[0], None)
        self.assertEqual(self.comments([[]])[0], None)

    def test_bad_marker_is_none(self):
        bad = self.c("github-actions[bot]", request.MARK % "wikisource-zh:$(x)", "Bot")
        with mock.patch.dict(sources.REGISTRY, {"wikisource-zh": object()}):
            self.assertIsNone(self.comments([[bad]])[0])

    def test_needs_env(self):
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "", "GH_TOKEN": "", "GITHUB_TOKEN": ""}):
            with self.assertRaises(RuntimeError):
                request.marked_key({"issue": {"number": 7}})


class TestMarkdown(unittest.TestCase):
    def test_md_escapes(self):
        s = request.md("@user [x](http://e) `code` <b>*_#|~\n")
        self.assertNotIn("@user", s)
        self.assertNotIn("[x]", s)
        self.assertNotIn("\n", s)
        self.assertIn("\\`", s)

    def test_md_breaks_bare_links_and_entities(self):
        for raw, gone in (("see https://evil.example/x", "https://"), ("HTTP://EVIL.example", "HTTP://"),
                          ("go www.evil.example now", "www."), ("WWW.evil.example", "WWW.")):
            self.assertNotIn(gone, request.md(raw), raw)
        # &#64; 渲染出来是 @：& 必须转义成 \&，实体就不成立
        for raw in ("&#64;mallory", "&commat;mallory", "a & b"):
            self.assertIsNone(re.search(r"(?<!\\)&", request.md(raw)), raw)
        self.assertEqual(request.md("三國演義"), "三國演義")    # 正常书名原样

    def test_url_ok(self):
        self.assertTrue(request.url_ok("https://zh.wikisource.org/wiki/%E4%B8%89"))
        self.assertTrue(request.url_ok("https://www.gutenberg.org/ebooks/1342"))
        for u in (None, "", "http://x.org/a", "https://x.org/a b", "https://x.org/a)[y](z", "javascript:alert(1)",
                  "https://x.org/<b>"):
            self.assertFalse(request.url_ok(u), u)

    def test_public_error_is_fixed_wording(self):
        self.assertEqual(request.public_error(RuntimeError("HTTP 500\nvendor stack")), "判断模型返回 HTTP 500")
        self.assertEqual(request.public_error(RuntimeError("实际模型 x-2 ≠ 尺上钉的 x-1")),
                         "判断模型版本和尺上钉的不一致，停了（不可比的读数不落盘）")
        self.assertNotIn("secret", request.public_error(ValueError("secret stuff")))


if __name__ == "__main__":
    unittest.main()
