# -*- coding: utf-8 -*-
"""
Project Gutenberg。编号 = 电子书编号，如 gutenberg:1342。

元数据走 Gutendex（Gutenberg 目录的 JSON 版），它不回就读镜像上的 RDF 目录。许可（跟 site/connectors.js 同一条规则）：
  copyright === true → 版权；null → 未知；false → 再看作者（world_safe）：过了记「公版」（能试读），
  没过记「美国公版」（仅在美国是公版：只列出、不试读，fetch.py 按 TASTEABLE_LICENSES 自动拒）。
正文取官方镜像上的 UTF-8 纯文本，一本书只取一次，存在实例里，各章都从这份切。
🔴 不从 www.gutenberg.org 取正文（Gutendex formats 里给的就是它）：它的「Information About Robot Access」
   写明网站只给人用，程序访问会被封 IP，例外只有 robot/harvest 和镜像。所以改走 MIRRORS.ALL 里
   PGLAF 自营的 gutenberg.pglaf.org（含 cache/ 生成文件）。robots.txt 只禁 /ebooks/search。（2026-09-25 查）
   Gutendex 的 copyright === false 只是「美国公版」，所以还要过 world_safe 才记「公版」。

章节 = 正文里前面空一行的标题行：CHAPTER I / Chapter 1 / CHAP. I / 单独一行的罗马数字 / 第X回 / 第X章…
编号得大致连号才认；目录、序言等第一章之前的东西丢掉。认不出 3 个标题、或者切出来可疑（第一章前面丢太多、
各章盖不住正文、按卷切一卷太大）就按 6000 字切块，记「第N段」。
目录（units）是从全文切出来的，所以 --meta-only / 预估也要把整本拉进内存（不落盘）；有版权的、美国公版的照样一个字不拉。
各章没有单独的网页（全书一个落地页），unit_url 回 None。
"""
import bisect, re, urllib.error
import xml.etree.ElementTree as ET
from collections import namedtuple

from core import chunk
from . import http

GUTENDEX = "https://gutendex.com/books/%s/"                          # 不带末尾斜杠会先 301 一次
MIRROR = "https://gutenberg.pglaf.org/cache/epub/{0}/pg{0}.txt"
RDF = "https://gutenberg.pglaf.org/cache/epub/{0}/pg{0}.rdf"
BOOK_PAGE = "https://www.gutenberg.org/ebooks/%s"
CHUNK = 6000
# 全球放心的公版线：作者死后 70 年是主流里最长的保护期，2026 年起 1955 年及以前去世的都过了；
# 卒年不详的，生于 1850 年及以前才算（活过 1955 年要 105 岁以上）
WORLD_PD_DEATH, WORLD_PD_BIRTH = 1955, 1850
US_ONLY = "仅在美国是公版"


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def world_safe(authors):
    """每位作者：卒年是数且 ≤ 1955，或者卒年为空而生年是数且 ≤ 1850。没有作者（佚名）算过。
    🔴 跟 site/connectors.js 的同名规则必须一模一样：一边判公版能试读、一边判不能，按钮和 Actions 就对不上（2026-09-25）"""
    for a in authors or []:
        d, b = a.get("death_year"), a.get("birth_year")
        if not ((_is_num(d) and d <= WORLD_PD_DEATH) or (d is None and _is_num(b) and b <= WORLD_PD_BIRTH)):
            return False
    return True


def license_of(book):
    """Gutendex 形状的目录条目 → 许可。"""
    c = book.get("copyright")
    if c is True:
        return "版权"
    if c is False:
        return "公版" if world_safe(book.get("authors")) else "美国公版"
    return "未知"


# ---------------------------------------------------------------- 页眉页脚
_START = re.compile(r"^[ \t]*\*{3}\s*START OF (?:THE|THIS) PROJECT GUTENBERG E-?(?:BOOK|TEXT).*$", re.I | re.M)
_SMALL_PRINT = re.compile(r"^.*\*END\*?\s*THE SMALL PRINT.*$", re.M)     # 1990 年代的老文本，没有 START 行
# 法文、西文、德文书页脚那行是本国话，排在标准 END 行前面
_END = re.compile(r"^[ \t]*(?:\*{3}\s*END OF (?:THE|THIS) PROJECT GUTENBERG|End of (?:the |this )?Project Gutenberg"
                  r"|Fin d(?:e|el|u) (?:Project|Proyecto|Projet) Gutenberg|Ende (?:dieses|diese|des) Proje[ck]ts? Gutenberg)",
                  re.I | re.M)
_PRODUCED = re.compile(r"(?:Produced by|E-?text prepared by|Transcribed (?:from|by)|This e-?book was produced)", re.I)


def strip_boilerplate(raw):
    """去掉 PG 页眉（START 行及以前）、页脚（END 行及许可条款）和开头的「Produced by…」。"""
    t = raw.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    m = _START.search(t) or _SMALL_PRINT.search(t)
    if m:
        t = t[m.end():]
        # 书名长时 START 行会折行，「***」落在下一两行末尾
        if m.re is _START and not m.group().rstrip().endswith("***"):
            k = re.match(r"(?:\n[^\n]*){0,2}?\*{3}[ \t]*(?=\n|$)", t)
            if k:
                t = t[k.end():]
    m = _END.search(t)   # 老文本 END 前面还有一行「End of the Project Gutenberg EBook of…」，取最早的
    if m:
        t = t[:m.start()]
    t = t.strip("\n")
    while _PRODUCED.match(t.lstrip()):
        s = t.lstrip()
        cut = re.search(r"\n[ \t]*\n", s)
        t = s[cut.end():] if cut else ""
    return t.strip("\n")


# ---------------------------------------------------------------- 编号
_ROMAN = re.compile(r"^M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$")
_RV = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def roman(s):
    s = s.upper()
    if not s or not _ROMAN.match(s):
        return None
    return sum(-_RV[a] if _RV[a] < _RV[b] else _RV[a] for a, b in zip(s, s[1:] + "I"))


_NUMW = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                    "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_NUMW.update({w: 10 * i for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), 2)})
_ODD_ORD = {1: "first", 2: "second", 3: "third", 5: "fifth", 8: "eighth", 9: "ninth", 12: "twelfth"}
_NUMW.update({_ODD_ORD.get(i) or (w[:-1] + "ieth" if w.endswith("y") else w + "th"): i for w, i in list(_NUMW.items()) if i})


def words(s):
    """ONE / Twenty-First / the first → 数；认不出返回 None。"""
    n = 0
    for w in re.split(r"[-\s]+", re.sub(r"^the\s+", "", s.strip().lower())):
        if w not in _NUMW:
            return None
        n += _NUMW[w]
    return n or None


_ZD = {c: d for d, cs in enumerate(["零〇○", "一壹", "二貳贰两兩", "三參叁", "四肆", "五伍", "六陸陆", "七柒", "八捌", "九玖"]) for c in cs}
_ZU = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
ZH_DIGITS = "0-9０-９零〇○一二三四五六七八九十百千两兩廿卅壹貳贰叁參肆伍陸陆柒捌玖拾佰仟"


def zh_num(s):
    """一百二十 / 一二○（PG 三國就这么写）/ 廿一 / １２ → 数。"""
    if s.isdigit():
        return int(s)
    if not any(c in _ZU or c in "廿卅" for c in s):
        return int("".join(str(_ZD[c]) for c in s)) if s and all(c in _ZD for c in s) else None
    n, cur = 0, 0
    for c in s:
        if c in _ZD:
            cur = _ZD[c]
        elif c in "廿卅":
            n += 20 if c == "廿" else 30
        elif c in _ZU:
            n, cur = n + (cur or 1) * _ZU[c], 0
        else:
            return None
    return n + cur


# ---------------------------------------------------------------- 标题行
Head = namedtuple("Head", "line level style num label rest")   # level 0 = 卷/部/Book/Part，1 = 章/回/Chapter

# 「CHAP. I」「C H A P. I」是 18 世纪的写法（Tristram Shandy 1079），跟 CHAPTER 算同一样式
_EN_HEAD = re.compile(r"^(CHAPTER|Chapter|C ?H ?A ?P\.?|Chap\.|STAVE|Stave|LETTER|Letter|BOOK|Book|PART|Part|VOLUME|Volume)\s+"
                      r"((?i:the\s+)?[A-Za-z]+(?:-[A-Za-z]+)?|\d{1,3})\b([\s.:—–-]*)(.*?)[\s\]]*$")
_EN_LEVEL = {"chapter": 1, "stave": 1, "letter": 1, "book": 0, "part": 0, "volume": 0}
_NESTED = re.compile(r"\b(?:CHAPTER|Chapter|CHAP|Chap)\b\.?\s*(?:[IVXLCDM]+\b|\d)")
_RUN_ON = re.compile(r"(?:[,;—–-]|\b(?:the|a|an|of|and|or|to|in|on|at|by|for|with|from|as|is|was|that|which|but|his|her))$")
_ROMAN_LINE = re.compile(r"^([IVXLCDM]+)\.?(?:\s+([^a-z]{2,60}))?$")        # II. / XIV / I. A SCANDAL IN BOHEMIA
_ARABIC_LINE = re.compile(r"^(\d{1,3})\.?$")
_ZH_HEAD = re.compile(r"^(第([%s]{1,6})([回章節节卷部篇集]))[\s:：.、．]*(.*?)$" % ZH_DIGITS)
_ZH_JUAN = re.compile(r"^(卷之?([%s]{1,6}))[\s:：.、．]*(.*?)$" % ZH_DIGITS)
_ZH_LEVEL = {"回": 1, "章": 1, "節": 1, "节": 1, "卷": 0, "部": 0, "篇": 0, "集": 0}
# 序幕 / 尾声：没有编号，跟着章一起当单元
_EXTRA = re.compile(r"^((?:(?:FIRST|SECOND|THIRD|First|Second|Third)\s+)?(?:PROLOGUE|Prologue|EPILOGUE|Epilogue)"
                    r"|楔子|引子|引首|尾聲|尾声)\b[\s.:：—–-]*(.*?)$")
_BARE = ("roman-title", "roman", "arabic")      # 光秃秃的编号，按这个先后挑
_RULE = re.compile(r"^\s*(?:[-=_─—~]{5,}|[─—]{3,})\s*$")   # 分隔线（PG 紅樓夢回目下面「-----」，Tristram Shandy 卷名上面「———」）


def _rest(s):
    s = re.sub(r"(?:\s{2,}|\s*\.{2,}\s*)(?:\d+|[ivxlc]+)$", "", s)     # 目录行尾的页码
    return re.sub(r"\s+", " ", s).strip(" \t.:：—–-]_")


def heading(line, i=0):
    """一行 → Head；不像标题返回 None。"""
    s = line.strip()
    if not s or len(s) > 100:
        return None
    m = _ZH_HEAD.match(s)
    # 回目里只有逗号和空格；带句读的是正文碰巧以「第四回中…」打头（PG 紅樓夢），「第二回合」也是正文
    if m and len(s) <= 60 and not m.group(4).startswith("合") and not re.search(r"[。．！？；：「」『』]", m.group(4)):
        n = zh_num(m.group(2))
        if n is not None:
            return Head(i, _ZH_LEVEL[m.group(3)], m.group(3).replace("节", "節"), n, m.group(1), _rest(m.group(4)))
    m = _ZH_JUAN.match(s)
    if m and len(s) <= 60 and zh_num(m.group(2)) is not None:
        return Head(i, 0, "卷", zh_num(m.group(2)), m.group(1), _rest(m.group(3)))
    m = _EN_HEAD.match(s)
    if m:
        kw, num, sep, rest = m.groups()
        style = re.sub(r"[\s.]", "", kw.lower())
        style = "chapter" if style == "chap" else style
        n = roman(num) if re.fullmatch(r"[IVXLCDMivxlcdm]+", num) else (int(num) if num.isdigit() else words(num))
        last = num.lower() in ("the last", "last", "the final")
        # 「Book the first coach to…」这种正文：编号后只隔空格就接小写字。
        # 🔴 隔了「--」「:」的小写是章名（Tale of Two Cities 98「Book the Second--the Golden Thread」），
        #    一刀切会让第二、三部全挂在「Book the First」下（2026-09-25）
        lower_ok = (re.search(r"[.:—–-]", sep) and len(rest) <= 40 and not re.search(r"[.,;!?]$", rest))
        ok = (n is not None or last) and len(rest) <= 80 and (not rest[:1].islower() or lower_ok)
        # 🔴 卷/部标题后面又带「CHAPTER I」、或一长串小写字折到下一行接着说 = 正文里的交叉引用
        #    （Moby-Dick 2701 Cetology 章「BOOK I. (_Folio_), CHAPTER I. (_Sperm Whale_).—This whale, among the」，2026-09-25）
        if ok and _EN_LEVEL[style] == 0 and (_NESTED.search(rest) or (
                len(rest) > 40 and len(re.findall(r"\b[a-z][a-z’']{3,}", rest)) >= 3 and _RUN_ON.search(rest))):
            ok = False
        if ok:
            return Head(i, _EN_LEVEL[style], style, n, "%s %s" % (kw, num), _rest(rest))
    m = _EXTRA.match(s)
    if m and len(m.group(2)) <= 60 and not m.group(2)[:1].islower():
        return Head(i, 1, "extra", None, m.group(1), _rest(m.group(2)))
    m = _ROMAN_LINE.match(s)
    if m and roman(m.group(1)):
        style = "roman-title" if m.group(2) else "roman"
        return Head(i, 1, style, roman(m.group(1)), m.group(1), _rest(m.group(2) or ""))
    m = _ARABIC_LINE.match(s)
    if m:
        return Head(i, 1, "arabic", int(m.group(1)), m.group(1), "")
    return None


def _seq_ok(nums):
    """编号大致连号（下一个 = 上一个 + 1，或回到 1 = 新的一卷 / 目录完了进正文）才像章节。"""
    good = sum(1 for a, b in zip(nums, nums[1:]) if a is None or b is None or b in (a + 1, 1))
    return good >= 0.7 * (len(nums) - 1)


def _cjk(c):
    return ord(c) >= 0x2E80 or 0x2500 <= ord(c) <= 0x257F       # 汉字、全角标点、PG 中文本当破折号用的「─」


def _subtitle(lines, i):
    """标题下面（最多隔一个空行、一条分隔线）短短一行、不像正文 → 当章名。返回 (章名, 行号)。"""
    j, blanks = i + 1, 0
    while j < len(lines) and (not lines[j].strip() or _RULE.match(lines[j])):
        blanks += not lines[j].strip()
        j += 1
    if j >= len(lines) or blanks > 1:
        return "", None
    raw, s = lines[j], lines[j].strip()
    alone = j + 1 >= len(lines) or not lines[j + 1].strip()        # 下面空一行
    if s[0] in "[“\"‘'(（「『" or heading(s):
        return "", None
    if _cjk(s[0]):
        # 回目：不缩进、不长、没有句读（PG 紅樓夢用全角「．」当句号）
        if raw.startswith("　") or len(s) > 40 or re.search(r"[。．！？「」『』：；…]", s) or (not alone and "，" in s):
            return "", None
    else:
        big = [w for w in re.findall(r"[A-Za-z][\w'’-]*", s) if len(w) > 3]
        titlish = s.isupper() or (big and sum(w[0].isupper() for w in big) * 2 >= len(big))
        if not alone or len(s) > 60 or not titlish or s[-1] in ",;:\"”’'":
            return "", None
    return _rest(s), j


_TOC_GAP = 6          # 目录里标题挨得近；真正的章之间不会只隔几行
_DENSE = 150          # 标题下面不到这么多字（不算空白）= 目录条目
_REAL = 1000          # 标题下面有这么多字才拿来当「第一个真章」的锚（目录里折行的长章名也有一两百字）
_THE_END = re.compile(r"^\s*(?:THE END|The End|FINIS|Finis|全書完|全书完|全書終|（全書完）|（完）)[.。]?\s*$")
_EPI = re.compile(r"(?i)epilogue|尾聲|尾声")
# 切出来的章要过这几道关，过不了就按字数切块。章名是读数对号的钥匙：切错了再改，读过的都得重读
_FRONT_OK = 0.30      # 从第 1 章起：前面丢掉的（序言、导读、目录）最多占全书这么多
_FRONT_ODD = 0.05     # 第一章编号不是 1：前面多半有没认出来的章，只许丢一点
_COVER, _LOST = 0.95, 1000   # 第一章到最后一章之间，章外落下的正文不许超过 5%（小书按 1000 字算）
_BIG = 80000          # 按卷 / Book 切时一卷的中位字数（不算空白）超过这个 = 卷里的章没认出来


def split_units(body, log=print):
    """正文 → [(标签, 章名, 起行, 止行)]，行号是 body.split("\\n") 的下标，不含标题行。
    认不出 3 个以上章节标题、或者切出来的样子可疑，返回 None（调用方按字数切块）。"""
    lines = body.split("\n")
    cum = [0]                                           # cum[k] = 前 k 行的字数（不算空白）
    for ln in lines:
        cum.append(cum[-1] + len(re.sub(r"\s", "", ln)))
    heads = [h for i, ln in enumerate(lines) if (h := heading(ln, i))]
    strict = {h.line for h in heads if h.line == 0 or not lines[h.line - 1].strip() or _RULE.match(lines[h.line - 1])}
    doubts = []
    for level in (1, 0):
        chains = {}
        for style in {h.style for h in heads if h.level == level} - {"extra"}:
            c = _chain([h for h in heads if h.style == style], strict, style in _BARE)
            if len(c) >= 3 and _seq_ok([h.num for h in c]):
                chains[style] = c
        kw = [s for s in chains if s not in _BARE]
        if kw:      # Letter 1–4 + Chapter 1–24（Frankenstein）这种并起来
            chosen = [h for s in kw for h in chains[s]]
        else:       # 有 CHAPTER / 第X回 就不用光秃秃的数字
            style = next((s for s in _BARE if s in chains), None)
            if not style:
                continue
            chosen = list(chains[style])
        if level == 1:
            chosen += [h for h in heads if h.style == "extra" and h.line in strict]
        chosen.sort(key=lambda h: h.line)
        outer = [h for h in heads if h.level < level and h.line in strict]
        chosen, outer = _drop_front(lines, cum, chosen, heads, outer)
        parts = _regions(lines, cum, chosen, outer) if len(chosen) >= 3 else None
        if not parts:
            continue
        why = _doubt(lines, cum, chosen, parts, level)
        if not why:
            return parts
        doubts.append(why)
    if doubts:
        log("  ⚠️ 章节切分可疑（%s），改按字数切块" % "；".join(doubts))
    return None


def _chain(cands, strict, bare):
    """同一样式的候选 → 章节序列。前面空一行的都算；没空行的只在编号正好接上时算（光秃秃的数字不算）：
    接上前一章（+1），或者后面紧跟着空了行的下一章（插图说明底下直接顶着的「CHAPTER I.」）。
    🔴 PG 三國第八十九回末尾折出一行「。」，第九十回就贴在它下面；PG 紅樓夢大半回目前面都不空行（2026-09-25）。
    不能干脆不要空行：行首碰巧是「第二回」「Chapter」的正文就混进来了。"""
    out = []
    for k, h in enumerate(cands):
        ok = h.line in strict
        if not ok and not bare and h.num is not None:
            prev, nxt = (out[-1] if out else None), (cands[k + 1] if k + 1 < len(cands) else None)
            ok = ((prev is not None and prev.num is not None and h.num == prev.num + 1) or
                  (nxt is not None and nxt.line in strict and nxt.num == h.num + 1))
        if ok:
            out.append(h)
    return out


def _runs(heads, styles):
    """同样式、编号递增、彼此隔不到 _TOC_GAP 行的标题，三个以上连成一串（不管前面空不空行）= 像目录。"""
    out = []
    for style in styles:
        run = []
        for h in (x for x in heads if x.style == style):
            if run and (h.line - run[-1].line > _TOC_GAP or
                        (h.num is not None and run[-1].num is not None and h.num <= run[-1].num)):
                if len(run) >= 3:
                    out.append(run)
                run = []
            run.append(h)
        if len(run) >= 3:
            out.append(run)
    return out


def _drop_front(lines, cum, chosen, heads, outer):
    """去掉第一章前面的目录条目 → (chosen, outer)。outer 里目录中的「Volume I.」「BOOK I」也去掉。"""
    def below(h, stops):                # 标题下面到下一个标题有多少字
        k = bisect.bisect_right(stops, h.line)
        return cum[stops[k] if k < len(stops) else len(lines)] - cum[h.line + 1]

    main = [h for h in chosen if h.style != "extra"]
    mine = {h.style for h in main}
    runs = _runs(heads, mine | {o.style for o in outer})
    in_run = {h.line for r in runs for h in r if h.style in mine}
    # 第一个真章：不在目录串里、下面有正经一段正文。🔴 光看「不在串里」不行：Les Misérables 135、Don Quixote 996
    # 的目录章名长、折行，串断成好几截，落单的条目（底下折行的章名一两百字）会被当成第一章，后面的目录全留下来（2026-09-25）
    stops = sorted(x.line for x in chosen + outer)
    real = next((h for h in main if h.line not in in_run and below(h, stops) >= _REAL), None)
    # ① 目录：只认第一个真章之前的串，而且串里的编号后面还会再出现（真章）。
    # 🔴 不能全书找：书里随便哪儿连着三个短章，它前面整本都会被当成目录丢掉（2026-09-25）
    toc = [r for r in runs if r[0].style in mine and real is not None and r[0].line < real.line and
           any(h.num == r[0].num and h.line > r[-1].line for h in main)]
    # 目录里的「Volume I.」不能当第一卷的卷名（Tristram Shandy 1079）：卷名挨着排三个以上，在哪儿都是目录。
    # 目录后面紧跟的真「Book the First」要留（A Tale of Two Cities 98）
    toc_outer = toc + [r for r in runs if r[0].style not in mine]
    outer = [o for o in outer if not any(r[0].line - _TOC_GAP <= o.line <= r[-1].line for r in toc_outer)]
    last = max(toc, key=lambda r: r[-1].line, default=None)
    if last:
        chosen = [h for h in chosen if h.line > last[-1].line]
        # 串后面编号还在往上走的是目录的尾巴（条目折行隔得远，没连进串）；编号回头的才是正文
        n = last[-1].num
        while chosen and n is not None and chosen[0].num is not None and chosen[0].num > n:
            n = chosen.pop(0).num
    # 🔴 目录末行的「Epilogue」隔着空行、不在串里，会被当成第一章（Moby-Dick 2701，2026-09-25）。尾声不会在第一章前面
    first = next((h.line for h in chosen if h.num is not None), None)
    chosen = [h for h in chosen if not (h.style == "extra" and _EPI.search(h.label) and first is not None
                                        and h.line < first)]
    # ② 带长提要的目录：条目隔得开（①认不出），每条下面几乎没字；编号头一回往回走的地方才是正文。
    # 单个短章不算（Looking-Glass 第十章一句话），分卷的书每卷从 1 数起也不算（前面各章有正文）
    nums = [(k, h.num) for k, h in enumerate(chosen) if h.num is not None]
    k = next((k for (_, a), (k, b) in zip(nums, nums[1:]) if b <= a), None)
    stops = sorted(x.line for x in chosen + outer)
    if k and k >= 2 and 3 * sum(below(h, stops) < _DENSE for h in chosen[:k]) >= 2 * k:
        chosen = chosen[k:]
    return chosen, outer


def _prose(lines, cum, a, b):
    """a..b 行里不算标题行的字数。"""
    return cum[b] - cum[a] - sum(cum[k + 1] - cum[k] for k in range(a, b) if heading(lines[k]))


def _the_end(lines, a, b):
    """最后一章里「THE END」所在行：单独一行，或者在插图框里（P&P 1342 是「[Illustration: THE / END ]」）。"""
    for k in range(a, b):
        if _THE_END.match(lines[k]):
            return k
        if _ILLUS.match(lines[k].strip()):
            blk = " ".join(lines[k:min(k + 8, b)])
            inner = re.sub(r"^\s*\[(?:Illustration|ILLUSTRATION):?", "", blk[:blk.find("]")] if "]" in blk else "")
            if _THE_END.match(re.sub(r"\s+", " ", inner)):
                return k
    return None


def _regions(lines, cum, chosen, outer):
    """选中的标题 → 各章行区间。外层（卷/Book）标题也是边界；各章标签重名时前面加上外层标签。"""
    # 第一章之前的外层标题只认最近那个（目录里的已经在 _drop_front 去掉）
    before = [o for o in outer if o.line < chosen[0].line][-1:]
    outer = before + [o for o in outer if o.line > chosen[0].line]
    mine = [h.line for h in chosen]
    stops = sorted(set(mine) | {h.line for h in outer if h.line > chosen[0].line})
    dup = len({h.label for h in chosen}) < len(chosen)
    prefix = sorted(outer + [h for h in chosen if h.style == "extra"], key=lambda h: h.line)
    out = []
    for h in chosen:
        title, sub = (h.rest, None) if h.rest else _subtitle(lines, h.line)
        start = (sub if sub is not None else h.line) + 1
        end = next((x for x in stops if x > h.line), len(lines))
        nxt = next((x for x in mine if x > h.line), len(lines))
        # 外层标题到下一章之间有正文（卷首引子、认错的「BOOK I」）：挂在这一章末尾，不丢
        if end < nxt and _prose(lines, cum, end, nxt) >= _DENSE:
            end = nxt
        # 底下直接是一串章的尾声（War and Peace 的 FIRST EPILOGUE）只是个壳；
        # 同一回目重复两遍（PG 紅樓夢第四十五回）前一个是空的。都不算一章
        size = len(re.sub(r"[\s\-=_─—~]", "", "".join(lines[start:end])))
        if not size and sub is not None and not _cjk(lines[sub].strip()[0]):
            # 一句话的章（「She was born on a Tuesday.」）被当成了章名：那一行其实是正文
            title, start = "", h.line + 1
            size = len(re.sub(r"\s", "", "".join(lines[start:end])))
        if not size or (h.style == "extra" and size < _DENSE):
            continue
        label = h.label
        if dup and h.style != "extra":
            up = [o for o in prefix if o.line < h.line]
            if up:
                label = "%s · %s" % (up[-1].label, label)
        out.append([label, title, start, end])
    if len(out) < 3:
        return None
    k = _the_end(lines, out[-1][2], out[-1][3])
    if k is not None:
        out[-1][3] = k
    return [tuple(x) for x in out]


def _doubt(lines, cum, chosen, parts, level):
    """切出来的样子不对劲 → 原因；没毛病 → None。"""
    nums = [h.num for h in chosen if h.num is not None]
    if nums and nums[0] != min(nums):
        return "第一章编号 %d，后面还有 %d" % (nums[0], min(nums))
    front = cum[parts[0][2]] / max(cum[-1], 1)
    if front > (_FRONT_OK if not nums or nums[0] <= 1 else _FRONT_ODD):
        return "第一章「%s」前面丢掉全书 %d%%" % (parts[0][0], round(100 * front))
    # 章与章之间除了标题、章名，不该有落下的正文
    gaps = [(chosen[0].line, parts[0][2])] + [(x[3], y[2]) for x, y in zip(parts, parts[1:])]
    lost = sum(_prose(lines, cum, a, b) for a, b in gaps)
    if lost > max(_LOST, (1 - _COVER) * (cum[parts[-1][3]] - cum[chosen[0].line])):
        return "章与章之间落下 %s 字正文" % "{:,}".format(lost)
    mid = sorted(cum[b] - cum[a] for _, _, a, b in parts)[len(parts) // 2]
    if level == 0 and mid > _BIG:
        return "按卷切一卷约 %s 字，卷里的章没认出来" % "{:,}".format(mid)
    return None


# ---------------------------------------------------------------- 清理
_ILLUS = re.compile(r"\[(?:Illustration|ILLUSTRATION)")
_PAGE = re.compile(r"\[(?:Pg|Page)\.?\s*[\divxlcdm]+\]", re.I)
# 校对组（pgdp）的排版记号：「/* NIND “My dear friend, */」只留中间的字（P&P 1342 信件抬头）
_PGDP = re.compile(r"^[ \t]*/\*[ \t]*(?:NIND|IND|RIGHT|CENTER|CENTRE)?[ \t]*|[ \t]*\*/[ \t]*$", re.M)


def _drop_illustrations(t):
    """去掉 [Illustration: …]（可跨行、可嵌套方括号）。不配对的只去掉那一行。"""
    out, i = [], 0
    while True:
        m = _ILLUS.search(t, i)
        if not m:
            out.append(t[i:])
            break
        out.append(t[i:m.start()])
        depth, j = 0, m.start()
        while j < len(t):
            depth += {"[": 1, "]": -1}.get(t[j], 0)
            j += 1
            if depth == 0:
                break
        if depth:
            j = t.find("\n", m.start())
            j = len(t) if j < 0 else j
        i = j
    return _PAGE.sub("", "".join(out))


def clean(text):
    """一段原文 → 纯文本：去插图说明、页码，拆掉 PG 的硬换行，一段一行（跟维基文库拉下来的一样）。"""
    paras = []
    for block in re.split(r"\n[ \t　]*\n", _drop_illustrations(_PGDP.sub("", text))):
        ls = [l.strip() for l in block.split("\n") if l.strip() and not _RULE.match(l)]
        if not ls:
            continue
        if len(ls) > 1 and not _cjk(ls[0][0]) and max(map(len, ls)) < 45:   # 诗、信的落款：短行保留换行
            paras.append("\n".join(ls))
            continue
        p = ls[0]
        for l in ls[1:]:
            a, b = p[-1], l[0]
            glue = "" if (_cjk(a) or _cjk(b)) and not (a.isascii() and a.isalnum()) and not (b.isascii() and b.isalnum()) else " "
            p += glue + l
        paras.append(p)
    return "\n".join(paras)


def _person(name, lang):
    """「Austen, Jane」→「Jane Austen」；中文书保持姓在前：「Luo, Guanzhong」→「Luo Guanzhong」。"""
    parts = [p.strip() for p in re.sub(r"\s*\([^)]*\)", "", name).split(",")]
    parts = [p for p in parts if p and not re.search(r"\d|^(?:active|approximately|fl\.|ca\.)\b", p, re.I)]
    if len(parts) < 2:
        return parts[0] if parts else name
    last, first, extra = parts[0], parts[1], parts[2:]
    s = "%s %s" % ((last, first) if lang == "zh" else (first, last))
    return ", ".join([s] + extra)


_NS = {"rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#", "dcterms": "http://purl.org/dc/terms/",
       "pgterms": "http://www.gutenberg.org/2009/pgterms/"}


def _year(agent, tag):
    try:
        return int((agent.findtext(tag, "", _NS) or "").strip())
    except ValueError:
        return None


def parse_rdf(xml):
    """PG 的 RDF 目录条目 → Gutendex 形状。copyright 跟 Gutendex 一个口径：美国公版 False，Copyrighted True，其他 None。
    作者的生卒年也要（pgterms:birthdate / deathdate）：没有它们 world_safe 一律判不过，Gutendex 一掉线好书全成「美国公版」。"""
    book = ET.fromstring(xml).find("pgterms:ebook", _NS)
    if book is None:
        raise ValueError("RDF 里没有 pgterms:ebook")
    rights = book.findtext("dcterms:rights", "", _NS)
    return {
        "title": book.findtext("dcterms:title", "", _NS),
        "authors": [{"name": a.findtext("pgterms:name", "", _NS), "birth_year": _year(a, "pgterms:birthdate"),
                     "death_year": _year(a, "pgterms:deathdate")}
                    for a in book.findall("dcterms:creator/pgterms:agent", _NS)],
        "languages": [v.text for v in book.findall("dcterms:language/rdf:Description/rdf:value", _NS) if v.text],
        "copyright": False if rights.startswith("Public domain in the USA") else (True if rights.startswith("Copyrighted") else None),
        "media_type": book.findtext("dcterms:type/rdf:Description/rdf:value", "Text", _NS),
    }


# ---------------------------------------------------------------- 接口
class Gutenberg:
    id = "gutenberg"
    name = "Project Gutenberg"

    def __init__(self):
        self._book, self._body, self._clean = {}, {}, {}

    def _gutendex(self, rid):
        """目录条目（Gutendex 的形状）。
        🔴 Gutendex 是志愿者办的，常常慢到超时（2026-09-25 查 23950 等 60 秒没回，1342 却秒回）。
           所以只给它两次、每次 20 秒，不行就读镜像上 PG 自己的 RDF 目录——Gutendex 的 copyright 就是从它的
           dcterms:rights 来的，口径一样。"""
        if not re.fullmatch(r"[1-9]\d{0,6}", rid):      # 跟 request.py 一样；「01342」会另起一份数据
            raise ValueError("Gutenberg 编号应是不带前导零的数字：%r" % rid)
        if rid not in self._book:
            try:
                self._book[rid] = http.get(GUTENDEX % rid, timeout=20, tries=2)
            except (OSError, ValueError) as e:        # 超时、连不上、404（Gutendex 收新书有延迟）、回的不是 JSON
                if isinstance(e, urllib.error.HTTPError):
                    e.close()
                print("  Gutendex 没回（%s），改读镜像上的 RDF 目录" % e)
                try:
                    self._book[rid] = parse_rdf(http.get(RDF.format(rid), as_json=False))
                except urllib.error.HTTPError as e2:
                    e2.close()
                    if e2.code == 404:
                        raise ValueError("Gutenberg 没有编号 %s 的书" % rid)
                    raise
        return self._book[rid]

    def _text(self, rid):
        if rid not in self._body:
            m = self.meta(rid)
            # 🔴 有版权 / 版权未知 / 只在美国公版的书连正文都不拉（fetch.py 也会拦，这里再挡一道）
            if m["license"] != "公版":
                note = "（%s）" % m["license_note"] if m.get("license_note") else ""
                raise ValueError("gutenberg:%s 的许可是「%s」%s，不拉正文" % (rid, m["license"], note))
            kind = self._gutendex(rid).get("media_type") or "Text"
            if kind != "Text":
                raise ValueError("gutenberg:%s 不是文字书（%s）" % (rid, kind))
            try:
                raw = http.get(MIRROR.format(rid), as_json=False, timeout=60)
            except urllib.error.HTTPError as e:
                e.close()
                if e.code == 404:
                    raise ValueError("镜像上没有 gutenberg:%s 的纯文本" % rid)
                raise
            self._body[rid] = strip_boilerplate(raw)
        return self._body[rid]

    def _chunks(self, rid):
        if rid not in self._clean:
            self._clean[rid] = chunk(clean(self._text(rid)), CHUNK)
        return self._clean[rid]

    def meta(self, rid):
        b = self._gutendex(rid)
        lang = (b.get("languages") or [""])[0]
        m = {
            "title": re.sub(r"\s+", " ", b.get("title") or rid).strip(),
            "author": "、".join(_person(a["name"], lang) for a in b.get("authors") or [] if a.get("name")),
            "lang": lang, "license": license_of(b),
            "source_name": self.name, "read_url": BOOK_PAGE % rid,
        }
        if m["license"] == "美国公版":
            m["license_note"] = US_ONLY
        return m

    def units(self, rid):
        parts = split_units(self._text(rid))
        if parts is None:
            return [{"n": i + 1, "label": "第%d段" % (i + 1), "title": "", "ref": {"id": rid, "chunk": i}}
                    for i in range(len(self._chunks(rid)))]
        return [{"n": i + 1, "label": label, "title": title, "ref": {"id": rid, "lines": [a, b]}}
                for i, (label, title, a, b) in enumerate(parts)]

    def text(self, rid, unit):
        ref = unit["ref"]
        if "chunk" in ref:
            return self._chunks(rid)[ref["chunk"]]
        a, b = ref["lines"]
        return clean("\n".join(self._text(rid).split("\n")[a:b]))

    def peek(self, rid, unit):
        """不联网就拿得到的这一章正文（整本已在内存里）；拿不到回 None。request.py 用它查已读的章正文变没变。"""
        return self.text(rid, unit) if rid in self._body else None

    def unit_url(self, unit):
        # 各章共用一个落地页，给了等于没给；前端拿到 null 就只显示章名
        return None
