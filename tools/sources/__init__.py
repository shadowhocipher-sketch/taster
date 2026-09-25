# -*- coding: utf-8 -*-
"""
书源（连接器）注册表。一个书源 = 一个能「给编号 → 拿元数据、章节、正文」的对象。

接口（新书源照这个写）：
  id           资源键前缀，如 "wikisource-zh"
  name         给人看的名字
  meta(rid)    → {"title","author","lang","license","read_url","source_name", ...}
                 license 只有两种能试读：「公版」「自由许可」；其他（版权、未知、美国公版……）一律拒读，
                 拒读的理由放 "license_note"（如「仅在美国是公版」），评论里原样给出
  units(rid)   → [{"n":1,"label":"第一回","title":"……","ref":<text() 要用的东西>}, ...]
                 拆不出像样的章节就 raise ValueError（fetch.py、request.py 当「拒绝」处理）
  text(rid, unit) → 这一章的纯文本
可选：
  unit_url(unit)  → 这一章原文的网址；各章没有单独网页就回 None（前端只显示章名）
  peek(rid, unit) → 不联网就拿得到的这一章正文，拿不到回 None（request.py 用来查已读的章正文变没变）

🔴 只走官方 API / 镜像，慢速、带 UA、遇 429 退避。不爬网页，不接盗版站。
"""
from .gutenberg import Gutenberg
from .wikisource import Wikisource

REGISTRY = {s.id: s for s in (Wikisource("zh"), Wikisource("en"), Gutenberg())}

TASTEABLE_LICENSES = ("公版", "自由许可")


def get(source_id):
    if source_id not in REGISTRY:
        raise ValueError("不认识的书源：%s（现有：%s）" % (source_id, ", ".join(REGISTRY)))
    return REGISTRY[source_id]
