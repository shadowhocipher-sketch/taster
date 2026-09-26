#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
「请试读」Issue → 试读。GitHub Actions（.github/workflows/taste.yml）调用，本机可 --dry-run 演练。

  python tools/request.py --job taste                  # Actions：事件从 $GITHUB_EVENT_PATH 读
  python tools/request.py --dry-run --event e.json     # 本机演练：不调判断模型、正文不落盘、不打包、不写 data/

谁能触发真跑（ARCHITECTURE §5）：仓库主人开的 Issue；或维护者加了「批准」标签。其他人开的只回「等批准」+ 预估。
预估和 dry-run 都不写盘；但 Gutenberg、维基文库单页作品要从全文切目录，全文会读进内存（只是不存）。
这一批 = 没读过的章（core.is_read）按目录顺序前 max_calls 章：预估、dry-run、真跑都用 batch_of() 这一个算法，
  真跑只拉、只读这一批（fetch / taste 的 only=），批外一章不碰。
🔴 已读的章正文变了，请求不重读——钱只花在没读过的章上。能不联网看出来的（本机 texts/、书源内存里的全文）
   在评论里报出来，要重读由维护者本机跑 taste.py（2026-09-25）。
--job estimate 只做预估（没密钥的 job 用），--job taste 只做真跑；不是自己那份的事件一律 ignored。
输出给 workflow：评论正文写进 --comment-file；
$GITHUB_OUTPUT 里 status=done|partial|rejected|error|pending|changed|ignored|dry-run。

🔴 Issue 里的东西全是外人写的：只从事件文件读，不经 shell；资源键白名单校验；备注不读、不回显。
🔴 不打印任何密钥；公开评论和公开的 Actions 日志里，判断模型的报错只给固定措辞（public_error），
   不转贴接口原文、不打「实际模型 ≠ 尺上的」那行（2026-09-25）。
🔴 批准的必须是预估过的那个键：评论里埋 <!-- taster-key: … -->，批准时对不上就只重新预估、不开读（2026-09-25）。
"""
import argparse, json, os, re, secrets, sys, tempfile, time, unicodedata, urllib.error, urllib.parse, urllib.request

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core, sources
import build as build_mod
import fetch as fetch_mod
import taste as taste_mod

SITE = "https://shadowhocipher-sketch.github.io/taster/"
REQUEST_LABEL, APPROVE_LABEL = "试读请求", "批准"
MAX_CALLS = 200            # 单次上限（ARCHITECTURE §5）
TIME_BUDGET_MIN = 100      # workflow 的 timeout-minutes 是 130：到点先停手，已读的还来得及提交
KEY_MAX = 200
# 编号白名单：各种文字的字母数字 + 书名里常见的标点。
# 🔴 不放 ; $ ` | < > { } [ ] # " \ / 和换行——键会进文件名、URL、Markdown，一律不给机会
ID_RE = re.compile(r"[\w .,'’·・()（）:：!！?？&、，—–「」『』-]+")
ID_RULES = {"gutenberg": re.compile(r"[1-9][0-9]{0,6}")}   # 各来源更严的编号规则
# 🔴 Windows 设备名当文件名（data/…/AUX.jsonl、Nul.meta.json）：Git for Windows 拒绝检出，整个仓库在 G10/WIN4 上
#    clone、pull 都会失败（2026-09-25）。点后面跟什么都算（NUL.tar.gz 也是 NUL）
WIN_RESERVED = re.compile(r"(?i)(con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³]) *(\..*)?")
BOT = "github-actions[bot]"
MARK = "<!-- taster-key: %s -->"
MARK_RE = re.compile(r"<!-- taster-key: ([^<>\n]+?) -->")


class Rejected(ValueError):
    pass


class OutOfTime(Exception):
    pass


# ------------------------------------------------------------------ 事件 → 请求
def labels_of(issue):
    return [l.get("name") for l in (issue.get("labels") or []) if isinstance(l, dict)]


def decide(event):
    """→ ("run" | "pending" | "ignore", 说明)。只看事件里 GitHub 自己填的字段，不看标题、正文。"""
    issue = event.get("issue") or {}
    if not issue or issue.get("pull_request"):
        return "ignore", "不是 Issue"
    if REQUEST_LABEL not in labels_of(issue):
        return "ignore", "没有「%s」标签" % REQUEST_LABEL
    action = event.get("action")
    if action == "opened":
        # 开的时候就带着「批准」：随后那个 labeled 事件会跑，这里再跑就同一本书读两遍、花两遍钱
        if APPROVE_LABEL in labels_of(issue):
            return "ignore", "开的时候已带「%s」，交给 labeled 事件" % APPROVE_LABEL
        if issue.get("author_association") == "OWNER":
            return "run", "仓库主人开的"
        return "pending", "等维护者批准"
    if action == "labeled":
        if (event.get("label") or {}).get("name") == APPROVE_LABEL:
            return "run", "维护者加了「%s」" % APPROVE_LABEL
        return "ignore", "加的不是「%s」标签" % APPROVE_LABEL
    return "ignore", "不处理 %s 事件" % action


def form_fields(body):
    """Issue 表单渲染出的正文：「### 标签名\\n\\n值」一段一段。没填的是 _No response_。
    同名标题只认第一个：备注里再写一段「### 资源键」也盖不掉表单填的那个。"""
    out, name, buf = {}, None, []
    for line in (body or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        m = re.match(r"^###[ \t]+(.+?)[ \t]*$", line)
        if m:
            if name is not None:
                out.setdefault(name, "\n".join(buf).strip())
            name, buf = m.group(1), []
        elif name is not None:
            buf.append(line)
    if name is not None:
        out.setdefault(name, "\n".join(buf).strip())
    return {k: ("" if v == "_No response_" else v) for k, v in out.items()}


def validate_key(raw):
    """外人写的资源键 → 干净的键：首尾的 Unicode 空白（全角空格、不换行空格……）一律去掉，返回去掉后的。
    不合格就 Rejected（说明文字只用固定措辞，不回显原文）。
    🔴 以前只去空格、tab、换行：开头带全角空格（U+3000）的键能过 core.parse_key（它用 str.strip()），
       却原样返回，数据文件名、评论里的键都带着看不见的空格（2026-09-25）"""
    key = (raw or "").strip()
    if not key:
        raise Rejected("没填资源键")
    if len(key) > KEY_MAX:
        raise Rejected("资源键太长（上限 %d 字）" % KEY_MAX)
    if any(unicodedata.category(c)[0] == "C" for c in key):
        raise Rejected("资源键里有控制字符或不可见字符")
    try:
        src, rid = core.parse_key(key)
    except ValueError:
        raise Rejected("资源键格式不对，应为「来源:编号」，如 wikisource-zh:三國演義")
    if src not in sources.REGISTRY:
        raise Rejected("来源「%s」不能试读（能试读的：%s）" % (src, "、".join(sorted(sources.REGISTRY))))
    rule = ID_RULES.get(src, ID_RE)
    if not rule.fullmatch(rid) or rid != rid.strip() or rid[0] in ".-":
        raise Rejected("编号里有不允许的字符")
    if WIN_RESERVED.fullmatch(rid):
        raise Rejected("编号是 Windows 保留的设备名，存不成文件")
    return key


def canonical_key(key):
    """→ (规范键, 请求里原写的键；本来就规范则 None)。要联网。
    维基文库：跟跳转、按站上规则规范化（_ 当空格、首字母大写），只收正文名字空间（ns 0）。
    🔴 编号里能有「:」：Wikisource:Sandbox、User:谁 这类页谁都能随手写，却会被当成「公版作品」送去花钱试读；
       别名（三国演义 → 三國演義、Pride_and_Prejudice）又会另起一份数据、整本重读（2026-09-25）"""
    src_id, rid = core.parse_key(key)
    if not src_id.startswith("wikisource-"):
        return key, None
    q = sources.get(src_id)._api(action="query", titles=rid, redirects=1).get("query") or {}
    pages = q.get("pages") or []
    if q.get("interwiki") or not pages:
        raise Rejected("维基文库上查不到这个页面")
    p = pages[0]
    if p.get("invalid"):
        raise Rejected("页名不合维基文库的规则")
    if p.get("ns") != 0:
        raise Rejected("只试读维基文库正文里的作品页；用户页、讨论页、沙盒、Author:、Page: 这类页不收")
    if p.get("missing"):
        raise Rejected("维基文库上没有这个页面")
    try:
        canon = validate_key("%s:%s" % (src_id, p.get("title") or ""))
    except Rejected:
        raise Rejected("维基文库上的规范页名里有不允许的字符")
    return canon, (key if canon != key else None)


def parse_request(event):
    f = form_fields((event.get("issue") or {}).get("body"))
    key = validate_key(f.get("资源键"))
    rtype = f.get("类型") or "小说"
    if rtype not in fetch_mod.DEFAULT_RULER:
        raise Rejected("这类资源还没有尺，目前只试读：%s" % "、".join(fetch_mod.DEFAULT_RULER))
    return key, rtype


# ------------------------------------------------------------------ 数据
def split_units(key, fp, units):
    """→ (已读, 未读)。「读过」= core.is_read（跟 build.py 上站、taste.py 跳过同一个定义）。"""
    latest = core.latest_reads(key, fp)
    read = [u for u in units if core.is_read(latest.get(u["n"]), u)]
    ids = {u["n"] for u in read}
    return read, [u for u in units if u["n"] not in ids]


def batch_of(todo, max_calls):
    """这一轮要读的章：未读的按目录顺序取前 max_calls 章。预估、dry-run、真跑都用它，批准的就是真跑的。"""
    return todo[:max_calls]


def changed_units(key, fp, read):
    """已读的章里，正文跟读的时候不一样了的（正文指纹对不上）。只查不用再联网就拿得到正文的章：
    本机 texts/ 里有的，或书源已经读进内存的（Gutenberg 整本、维基文库单页作品）。
    维基文库分章的作品在 Actions 上查不出（要一章章联网拉），就不报。"""
    src_id, rid = core.parse_key(key)
    peek = getattr(sources.get(src_id), "peek", None)
    latest, out = core.latest_reads(key, fp), []
    for u in read:
        p = os.path.join(core.text_dir(key), "%03d.txt" % u["n"])
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                t = f.read()
        else:
            try:
                t = (peek(rid, u) or "").strip() if peek and "ref" in u else ""   # fetch.py 存盘前也 strip
            except Exception:
                t = ""
        if t and core.text_fp(t) != latest[u["n"]].get("正文指纹"):
            out.append(u)
    return out


def license_refusal(m):
    note = "（%s）" % m["license_note"] if m.get("license_note") else ""
    return "许可是「%s」%s，只列出、不试读" % (m["license"], note)


def estimate(key, rtype, max_calls=MAX_CALLS):
    """不花钱的预估：拉元数据和目录，跟已有读数比。不调判断模型、不写盘；
    Gutenberg、维基文库单页作品要从全文切目录，全文会读进内存（只是不存）。"""
    src_id, rid = core.parse_key(key)
    src = sources.get(src_id)
    m = src.meta(rid)
    if m["license"] not in sources.TASTEABLE_LICENSES:
        raise Rejected(license_refusal(m))
    old = core.load_json(core.meta_path(key), {})
    ruler_name = old.get("ruler") or fetch_mod.DEFAULT_RULER.get(rtype)
    ruler = core.load_ruler(ruler_name)
    fp = core.ruler_fp(ruler)
    units = src.units(rid)
    if not units:
        raise Rejected("拆不出章节")
    read, todo = split_units(key, fp, units)
    return {"title": m.get("title") or rid, "author": m.get("author") or "", "license": m["license"],
            "source_name": m.get("source_name") or src.name, "read_url": m.get("read_url"), "ruler": ruler["版本"],
            "total": len(units), "read": len(read), "todo": todo, "batch": batch_of(todo, max_calls),
            "changed": changed_units(key, fp, read)}


def marked_key(event):
    """这个 Issue 上 bot 最近一次预估 / 试读时埋下的键；没有就 None。只认 github-actions[bot] 发的评论。"""
    e = os.environ
    repo, token = e.get("GITHUB_REPOSITORY"), e.get("GH_TOKEN") or e.get("GITHUB_TOKEN")
    if not repo or not token:
        raise RuntimeError("没有 GITHUB_REPOSITORY / GH_TOKEN")
    n = int((event.get("issue") or {})["number"])
    last = None
    for page in range(1, 31):
        url = "%s/repos/%s/issues/%d/comments?per_page=100&page=%d" % (
            e.get("GITHUB_API_URL") or "https://api.github.com", repo, n, page)
        req = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
            "User-Agent": "Taster (https://github.com/shadowhocipher-sketch/taster)"})
        with urllib.request.urlopen(req, timeout=30) as r:
            items = json.load(r)
        for c in items:                                    # 按时间先后排，后面的覆盖前面的
            u = c.get("user") or {}
            m = MARK_RE.search(c.get("body") or "") if u.get("login") == BOT and u.get("type") == "Bot" else None
            if m:
                last = m.group(1)
        if len(items) < 100:
            break
    try:
        return validate_key(last) if last else None
    except Rejected:
        return None


# ------------------------------------------------------------------ 真跑
def run_taste(key, rtype, max_calls, deadline, log=print):
    """拉目录 → 只拉这一批的正文 → 只试读这一批（到点停）→ 打包。返回给评论用的数。
    这一批 = batch_of(未读, max_calls)，跟预估同一个算法；已读的章正文变了也不重读（只报出来）。"""
    try:
        meta, units = fetch_mod.fetch(key, rtype=rtype, meta_only=True, log=log)
    except ValueError as e:
        raise Rejected(str(e))
    ruler = core.load_ruler(meta["ruler"])
    fp = core.ruler_fp(ruler)
    read0, todo = split_units(key, fp, units)
    batch = batch_of(todo, max_calls)
    only = {u["n"] for u in batch}
    res = {"title": meta.get("title") or key, "author": meta.get("author") or "", "license": meta.get("license"),
           "source_name": meta.get("source_name"), "ruler": ruler["版本"],
           "model_version": build_mod.model_version(ruler["模型"]), "calls": None, "stopped": None, "err": None,
           "changed": changed_units(key, fp, read0)}
    if batch:
        # 🔴 只拉、只读这一批：本机 texts/ 里别的章有正文、已读的章正文变了，都不碰（2026-09-25）
        try:
            fetch_mod.fetch(key, rtype=rtype, log=log, only=only)
        except ValueError as e:
            raise Rejected(str(e))
        except Exception as e:
            # 拉到一半被书源限流：已拉到的照读，没拉到的留给下一轮
            log("⚠️ 拉正文中途出错：%s" % scrub(e))
            res["fetch_err"] = e

        def timed_log(msg):
            # taste 每章先落盘再打日志，所以在这里抛出去不会丢已读的章
            log(msg)
            if time.time() > deadline:
                raise OutOfTime()

        try:
            res["calls"] = (taste_mod.taste(key, max_calls=max_calls, log=timed_log, only=only) or {}).get("calls")
        except OutOfTime:
            res["stopped"] = "time"
        except (Exception, SystemExit) as e:          # plan() 缺数据时会 sys.exit
            res["err"] = e
    try:
        build_mod.build()
    except Exception as e:
        res["err"] = res["err"] or e
    read, todo = split_units(key, fp, units)
    if res["calls"] is None:                          # 中途停了：拿前后已读数之差
        res["calls"] = max(0, len(read) - len(read0))
    res.update(total=len(units), read=len(read), todo=todo,
               no_text=[u for u in todo if u["n"] in only
                        and not os.path.exists(os.path.join(core.text_dir(key), "%03d.txt" % u["n"]))])
    return res


# ------------------------------------------------------------------ 评论（固定措辞，只有事实，没有观点）
def md(s):
    """外来文字（书名、作者、章名）进 Markdown：转义标记符，@ 后插零宽空格免得变成提人，
    裸网址（http://、www.）插零宽空格断开，免得变成可点的链接。& 也转义：&#64; 渲染出来就是 @，照样提人。"""
    s = re.sub(r"[\x00-\x1f\x7f]", "", str(s or ""))[:120]
    s = re.sub(r"([\\`*_\[\]<>#|~&])", r"\\\1", s).replace("@", "@\u200b")
    return re.sub(r"(?i)(?<=:)(?=//)|(?<=www)(?=\.)", "\u200b", s)


def url_ok(u):
    """评论里要出现的原页链接：只放书源代码拼出来的 https 地址，形状不对就不放。"""
    return bool(u) and re.fullmatch(r"https://[a-z0-9.-]+/[A-Za-z0-9%._~/?=&:+-]*", u) is not None


def site_link(key):
    return SITE + "#/r/" + urllib.parse.quote(key, safe="")


def run_link():
    e = os.environ
    if e.get("GITHUB_RUN_ID") and e.get("GITHUB_REPOSITORY"):
        return "%s/%s/actions/runs/%s" % (e.get("GITHUB_SERVER_URL", "https://github.com"),
                                          e["GITHUB_REPOSITORY"], e["GITHUB_RUN_ID"])
    return None


def head(key, info):
    t = "`%s`" % key
    if info and info.get("title"):
        t += "《%s》" % md(info["title"])
        extra = [md(x) for x in (info.get("author"), info.get("source_name"), info.get("license")) if x]
        if extra:
            t += "（%s）" % " · ".join(extra)
    return t


def labels_line(units, cap=10):
    names = "、".join(md(u["label"]) for u in units[:cap])
    return names + ("等 %d 章" % len(units) if len(units) > cap else "")


def batch_range(b):
    if not b:
        return "0 章"
    return md(b[0]["label"]) if len(b) == 1 else "%s 到 %s" % (md(b[0]["label"]), md(b[-1]["label"]))


def changed_lines(info):
    ch = info.get("changed") or []
    if not ch:
        return []
    return ["已读的章里 %d 章正文在读过之后变了：%s。试读请求只读没读过的章，这些不重读" % (len(ch), labels_line(ch))]


def public_error(e):
    """报错进公开评论、公开日志前换成固定措辞：接口原文可能带供应商名、模型名。"""
    s = str(e)
    if isinstance(e, Rejected):
        return md(s)
    if "没有 key" in s:
        return "仓库没配判断模型的 key（Secrets：TASTER_API_KEY）"
    m = re.match(r"HTTP (\d{3})", s)
    if m:
        return "判断模型返回 HTTP %s" % m.group(1)
    if "连不上判断模型" in s:
        return "连不上判断模型"
    if "实际模型" in s:
        return "判断模型版本和尺上钉的不一致，停了（不可比的读数不落盘）"
    if isinstance(e, taste_mod.JudgeError):
        cause = e.__cause__
        return "判断模型调用出错（%s）" % type(cause if cause is not None else e).__name__
    if isinstance(e, urllib.error.HTTPError):
        return "书源返回 HTTP %d" % e.code
    if isinstance(e, (urllib.error.URLError, TimeoutError)):
        return "连不上书源"
    return "内部错误（%s），详情见 Actions 日志" % type(e).__name__


def log_error(e):
    """报错进 Actions 日志（公开仓库谁都能看）：判断模型的一律固定措辞，别的原文照打（去掉 key），方便查。"""
    if isinstance(e, taste_mod.JudgeError) or re.match(r"(HTTP \d{3}|连不上判断模型|实际模型|没有 key)", str(e)):
        return "%s: %s" % (type(e).__name__, public_error(e))
    return "%s: %s" % (type(e).__name__, scrub(e))


def scrub(s):
    """日志里也不许出现 key：Actions 会自动打码，本机不会。"""
    s = str(s)
    k = os.environ.get("TASTER_API_KEY") or ""
    for v in (k, k.removeprefix("apikey_")):
        if len(v) >= 6:
            s = s.replace(v, "***")
    return s


def render(status, key=None, info=None, why=None, alias=None, before=None):
    """before：changed 时上次评论里埋的键（没找到就 None）。alias：请求里原写的别名键。"""
    L = []
    if status == "rejected":
        L += ["这条试读请求没法处理：%s。" % why, "",
              "资源键写法：`来源:编号`，例 `wikisource-zh:三國演義`、`wikisource-en:Pride and Prejudice (1813)`、`gutenberg:1342`。"
              "只试读公版 / 自由许可的资源；站上搜索结果的「请试读」按钮会自动填好。",
              "改好后请维护者加「%s」标签重新检查。" % APPROVE_LABEL]
    elif status in ("pending", "changed"):
        if status == "changed":
            L += [("请求在上次预估之后改过（上次是 `%s`），这次没有开始读，按现在的请求重新预估。" % before) if before
                  else "这个 Issue 上找不到预估记录，这次没有开始读，先预估。", ""]
        L += ["收到试读请求：%s" % head(key, info)]
        if alias:
            L += ["", "请求里写的是 `%s`，按维基文库的规范页名算。" % alias]
        if info and url_ok(info.get("read_url")):
            L += ["", "原页：%s" % info["read_url"]]
        L += ["", "等维护者%s加「%s」标签后开始试读（每章调用一次第三方判断模型）。"
              % ("看过这份预估、再" if status == "changed" else "", APPROVE_LABEL)]
        if info:
            b = info["batch"]
            L += ["", "预估（没调用判断模型）：目录 %d 章，已试读 %d 章，待读 %d 章；单次上限 %d 章 → 批准后读 %s，最多调用 %d 次。"
                  % (info["total"], info["read"], len(info["todo"]), MAX_CALLS, batch_range(b), len(b))]
            L += changed_lines(info)
        else:
            L += ["", "预估暂时拿不到（%s）。" % why]
    elif status == "dry-run":
        todo = info["todo"]
        L += ["**dry-run**：没调用判断模型、没打包、没写 data/ 和 texts/（Gutenberg、单页作品为切目录把全文读进了内存，没存盘）。", "",
              "试读请求：%s" % head(key, info), "",
              "- 尺：%s" % info["ruler"],
              "- 目录 %d 章，已试读 %d 章，待读 %d 章" % (info["total"], info["read"], len(todo))]
        b = info["batch"]
        if b:
            L += ["- 真跑这次只读 %s，%d 章，之后剩 %d 章" % (batch_range(b), len(b), len(todo) - len(b))]
        if info.get("plan"):
            L += ["- 这批里本机已有正文的：%d 章，送出约 %s 字" % (info["plan"]["planned"], "{:,}".format(info["plan"]["chars"]))]
        L += ["- " + x for x in changed_lines(info)]
        L += ["- 读数页：%s" % site_link(key)]
    else:  # done / partial / error
        first = {"done": "试读完成", "partial": "试读了一部分", "error": "试读中断"}[status]
        L += ["%s：%s" % (first, head(key, info)), "",
              "- 这次读了 %d 章（第三方判断模型 %s · 尺 %s）"
              % (info["calls"], md(info["model_version"]), md(info["ruler"])),
              "- 全书 %d 章，已试读 %d 章，剩 %d 章" % (info["total"], info["read"], len(info["todo"])),
              "- 读数页：%s" % site_link(key)]
        if info.get("err") is not None:
            L += ["- 出错：%s。已读的章节已保存" % public_error(info["err"])]
        if info.get("fetch_err") is not None:
            L += ["- 拉正文中途出错：%s。先读了已拉到的" % public_error(info["fetch_err"])]
        if info.get("stopped") == "time":
            L += ["- 到了单次运行的时间上限，先停在这里"]
        if info.get("no_text"):
            L += ["- 取不到正文、没读：%s" % labels_line(info["no_text"])]
        L += ["- " + x for x in changed_lines(info)]
        if status != "done":
            L += ["", "维护者再加一次「%s」标签接着读。" % APPROVE_LABEL]
    rl = run_link()
    if rl:
        L += ["", "<sub>[运行日志](%s)</sub>" % rl]
    if key and status != "rejected":
        L += ["", MARK % key]                      # 批准时拿来核对（marked_key）
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ 入口
def needs_mark_check(event):
    """外人开的 Issue 上加「批准」：要核对批准的就是预估过的那个键。仓库主人自己的 Issue 不核对。"""
    return event.get("action") == "labeled" and (event.get("issue") or {}).get("author_association") != "OWNER"


def handle(event, dry_run=False, max_calls=MAX_CALLS, budget_min=TIME_BUDGET_MIN, log=print, job=None):
    """→ (status, 评论正文或 None)。job="estimate" 只接 pending，job="taste" 只接 run（见 taste.yml 两个 job）。"""
    t0 = time.time()
    verdict, why = decide(event)
    log("事件：%s → %s（%s）" % (event.get("action"), verdict, why))
    if verdict == "ignore":
        return "ignored", None
    want = {"estimate": "pending", "taste": "run"}.get(job)
    if want and verdict != want:
        log("这个 job 只处理 %s，跳过" % want)
        return "ignored", None
    try:
        key, rtype = parse_request(event)
    except Rejected as e:
        log("✗ 资源键不合格：%s" % e)
        return "rejected", render("rejected", why=md(e))
    try:
        key, alias = canonical_key(key)
    except Rejected as e:
        log("✗ %s" % e)
        return "rejected", render("rejected", why=md(e))
    except Exception as e:
        log("⚠️ 查规范页名失败：%s" % scrub(e))
        if verdict != "pending" or dry_run:
            raise
        return "pending", render("pending", key, None, why=public_error(e))
    log("资源键：%s%s · 类型：%s" % (key, "（请求里写的 %s）" % alias if alias else "", rtype))

    before = None
    if verdict == "run" and not dry_run and needs_mark_check(event):
        try:
            before = marked_key(event)
        except Exception as e:
            log("✗ 读 Issue 评论失败：%s" % scrub(e))
            return "rejected", render("rejected", why="读不到这个 Issue 的评论，核对不了批准的是哪个请求")
        if before != key:
            # 🔴 预估之后改了正文：批准的不是维护者看过的那本，只重新预估（2026-09-25）
            log("✗ 预估时的键是 %s，现在是 %s：不开读，重新预估" % (before, key))
            verdict = "changed"

    if verdict in ("pending", "changed") or dry_run:
        status = "dry-run" if dry_run else verdict
        try:
            info = estimate(key, rtype, max_calls)
        except Rejected as e:
            return "rejected", render("rejected", why=md(e))
        except ValueError as e:                   # 书源说没有这个页面之类
            return "rejected", render("rejected", why=md(e))
        except Exception as e:
            log("⚠️ 预估失败：%s" % scrub(e))
            if dry_run:
                raise
            return status, render(status, key, None, why=public_error(e), alias=alias, before=before)
        if not dry_run:
            return status, render(status, key, info, alias=alias, before=before)
        if os.path.exists(core.meta_path(key)):
            meta, ruler, fp, todo, missing = taste_mod.plan(key, only={u["n"] for u in info["batch"]})
            cut = ruler["截断字数"]
            info["plan"] = {"planned": len(todo), "chars": sum(min(len(t), cut) for _, t in todo)}
        return "dry-run", render("dry-run", key, info)

    try:
        info = run_taste(key, rtype, max_calls, t0 + budget_min * 60, log=log)
    except Rejected as e:
        return "rejected", render("rejected", why=md(e))
    if info["err"] is not None:
        log("✗ %s" % log_error(info["err"]))
        status = "error"
    else:
        status = "partial" if info["todo"] else "done"
    return status, render(status, key, info)


def set_output(**kv):
    p = os.environ.get("GITHUB_OUTPUT")
    if p:
        with open(p, "a", encoding="utf-8") as f:
            for k, v in kv.items():
                f.write("%s=%s\n" % (k, str(v).replace("\n", " ")))


def main(argv=None):
    ap = argparse.ArgumentParser(description="处理「请试读」Issue 事件")
    ap.add_argument("--event", help="事件 JSON 路径（默认 $GITHUB_EVENT_PATH）")
    ap.add_argument("--dry-run", action="store_true",
                    help="不调判断模型、不打包、不写 data/ 和 texts/（有的书源切目录要把全文读进内存，但不存盘）")
    ap.add_argument("--comment-file", help="评论正文写到哪（默认临时目录下 taster-comment.md）")
    ap.add_argument("--max-calls", type=int, default=MAX_CALLS)
    ap.add_argument("--budget-min", type=float, default=TIME_BUDGET_MIN, help="试读阶段的时间上限（分钟）")
    ap.add_argument("--live", action="store_true", help="在 Actions 以外真跑（会花钱）")
    ap.add_argument("--job", choices=("estimate", "taste"),
                    help="只处理这一种：estimate 只预估（不花钱），taste 只真跑。不给就都处理（本机）")
    a = ap.parse_args(argv)

    path = a.event or os.environ.get("GITHUB_EVENT_PATH")
    if not path or not os.path.exists(path):
        print("✗ 没有事件文件：用 --event 指定，或在 Actions 里跑", file=sys.stderr)
        return 2
    with open(path, encoding="utf-8") as f:
        event = json.load(f)
    max_calls = max(1, min(a.max_calls, MAX_CALLS))
    if not a.dry_run and os.environ.get("GITHUB_ACTIONS") != "true" and not a.live:
        # 本机手滑少打 --dry-run 就是真金白银（CLAUDE.md：跑前先报调用次数）
        if decide(event)[0] == "run":
            print("✗ 这会真调判断模型。本机演练加 --dry-run；确要真跑加 --live", file=sys.stderr)
            return 2

    comment_file = a.comment_file or os.path.join(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir(),
                                                  "taster-comment.md")
    # 🔴 书名、章名来自书源页面（维基文库谁都能改），会打进日志。runner 把一行开头的 ::cmd:: 和行中任意位置的
    #    ##[cmd] 当命令执行（add-mask、set-output……）。整段停掉命令解析，结束再用随机口令恢复（2026-09-25）
    resume = secrets.token_hex(16) if os.environ.get("GITHUB_ACTIONS") == "true" else None
    if resume:
        print("::stop-commands::%s" % resume, flush=True)
    try:
        try:
            status, body = handle(event, a.dry_run, max_calls, a.budget_min, job=a.job)
        except Exception as e:
            print("✗ %s" % log_error(e))
            status, body = "error", "试读脚本出错了：%s。\n\n维护者修好后再加「%s」标签重试。\n" % (public_error(e), APPROVE_LABEL)
            rl = run_link()
            if rl:
                body += "\n<sub>[运行日志](%s)</sub>\n" % rl
        if body is not None:
            with open(comment_file, "w", encoding="utf-8", newline="\n") as f:
                f.write(body)
            print("\n—— 评论 → %s ——\n%s" % (comment_file, body))
        set_output(status=status, comment=comment_file if body is not None else "")
        print("status=%s" % status)
    finally:
        if resume:
            sys.stderr.flush()
            print("::%s::" % resume, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
