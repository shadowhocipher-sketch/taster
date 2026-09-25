# -*- coding: utf-8 -*-
"""
共用的小工具：资源键、路径、尺、切块。其他脚本都从这里拿，别各写一份。

资源键 = "<来源>:<该来源里的编号>"，例：wikisource-zh:三國演義、gutenberg:1342。
全站只认这一种写法——数据文件名、请求 Issue、前端查读数，都用它。
"""
import hashlib, json, os, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY_RE = re.compile(r"^([a-z][a-z0-9-]{1,30}):(.{1,200})$")


def parse_key(key):
    m = KEY_RE.match(key.strip())
    if not m:
        raise ValueError("资源键格式不对：%r（应为 来源:编号）" % key)
    return m.group(1), m.group(2)


def safe_name(rid):
    """编号 → 文件名。只转义 Windows / URL 里有特殊含义的字符，中文原样保留。"""
    return re.sub(r'[\\/:*?"<>|%]', lambda m: "%%%02X" % ord(m.group()), rid)


def data_path(key, ext="jsonl"):
    src, rid = parse_key(key)
    return os.path.join(ROOT, "data", src, "%s.%s" % (safe_name(rid), ext))


def meta_path(key):
    return data_path(key, "meta.json")


def text_dir(key):
    src, rid = parse_key(key)
    return os.path.join(ROOT, "texts", src, safe_name(rid))


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write("\n")


def load_ruler(name):
    r = load_json(os.path.join(ROOT, "rulers", name + ".json"))
    if r is None:
        raise ValueError("没有这把尺：%s" % name)
    return r


def ruler_fp(r):
    core = {k: r[k] for k in ("模型", "截断字数", "题")}
    return hashlib.sha256(json.dumps(core, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:8]


def text_fp(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def chunk(text, size=6000):
    """没有章节结构的长文，按段落边界切成不超过 size 字的块。单段超长就硬切。"""
    parts, cur = [], ""
    for para in re.split(r"\n+", text):
        while len(para) > size:
            if cur:
                parts.append(cur); cur = ""
            parts.append(para[:size]); para = para[size:]
        if len(cur) + len(para) + 1 > size and cur:
            parts.append(cur); cur = ""
        cur = (cur + "\n" + para) if cur else para
    if cur.strip():
        parts.append(cur)
    return parts
