# -*- coding: utf-8 -*-
"""所有书源共用的礼貌 HTTP：带 UA、超时、遇 429/5xx 按 Retry-After 或指数退避。"""
import json, time, urllib.error, urllib.request

UA = {"User-Agent": "Taster/0.2 (https://github.com/Keepexperiencing/taster; reading-data research; slow, backs off on 429)"}


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
            if isinstance(e, urllib.error.HTTPError) and e.code == 429:
                wait = max(wait, int(e.headers.get("Retry-After") or 0), 30)
            print("  重试（%s），等 %d 秒" % (e, wait))
            time.sleep(wait)
