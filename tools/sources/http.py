# -*- coding: utf-8 -*-
"""所有书源共用的礼貌 HTTP：带 UA、超时、遇 429/5xx 按 Retry-After 或指数退避。"""
import email.utils, json, math, time, urllib.error, urllib.request
from datetime import datetime, timezone

UA = {"User-Agent": "Taster/0.2 (https://github.com/shadowhocipher-sketch/taster; reading-data research; slow, backs off on 429)"}


def retry_after(value, default=30):
    """Retry-After → 要等的秒数。按 RFC 9110 它可以是秒数，也可以是 HTTP 日期；认不出就等 default 秒。
    🔴 以前直接 int()：服务器给日期（「Wed, 21 Oct 2026 07:28:00 GMT」）时在退避分支里抛 ValueError，整个拉取崩掉（2026-09-25）"""
    v = (value or "").strip()
    if not v:
        return default
    if v.isdigit():
        return int(v)
    try:
        when = email.utils.parsedate_to_datetime(v)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0, math.ceil((when - datetime.now(timezone.utc)).total_seconds()))
    except (TypeError, ValueError, IndexError, OverflowError):
        return default


def get(url, as_json=True, timeout=30, tries=5):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                raw = r.read()
                return json.loads(raw) if as_json else raw.decode("utf-8", "replace")
        except Exception as e:
            if i == tries - 1:
                raise
            if isinstance(e, urllib.error.HTTPError) and e.code not in (429, 500, 502, 503, 504):
                raise
            # 429 = 嫌我们快了（2026-09-25 维基文库拉到第九回就撞上）
            wait = 5 * 2 ** i
            if isinstance(e, urllib.error.HTTPError):
                if e.code == 429:
                    wait = max(wait, retry_after((e.headers or {}).get("Retry-After")), 30)
                e.close()
            print("  重试（%s），等 %d 秒" % (e, wait))
            time.sleep(wait)
