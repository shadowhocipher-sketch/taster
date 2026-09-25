# -*- coding: utf-8 -*-
"""
书源（连接器）注册表。一个书源 = 一个能「给编号 → 拿元数据、章节、正文」的对象。

接口（新书源照这个写）：
  id           资源键前缀，如 "wikisource-zh"
  name         给人看的名字
  meta(rid)    → {"title","author","lang","license","read_url","source_name", ...}
                 license 只有两种能试读：「公版」「自由许可」；其他一律拒读
  units(rid)   → [{"n":1,"label":"第一回","title":"……","ref":<text() 要用的东西>}, ...]
  text(rid, unit) → 这一章的纯文本

🔴 只走官方 API / 镜像，慢速、带 UA、遇 429 退避。不爬网页，不接盗版站。
"""
from .wikisource import Wikisource

REGISTRY = {s.id: s for s in (Wikisource("zh"), Wikisource("en"))}

TASTEABLE_LICENSES = ("公版", "自由许可")


def get(source_id):
    if source_id not in REGISTRY:
        raise ValueError("不认识的书源：%s（现有：%s）" % (source_id, ", ".join(REGISTRY)))
    return REGISTRY[source_id]
