# -*- coding: utf-8 -*-
"""
判断模型的调用口。试读只认 ask(state, questions, model) → (结果, 毫秒)，换模型只改这里。

配置（环境变量）：
  TASTER_API_KEY    必填（GitHub Actions 里放 Secrets）
  TASTER_ENDPOINT   可选，默认 System One 接口
本机没设 TASTER_API_KEY 时，退回去读 JEV_DIR（默认 ../../Ops/network）下 .env 的 TYPESAFE_API_KEY。

结果格式（尺和数据都按这个存）：
  {"model": "...", "answers": {题: {...}}, "usage": {"input_tokens": n, "output_tokens": n}}
  noul → {"type":"noul","noul":0.82}
  score → {"type":"score","score":3.1,"confidence":0.7,"legend":{"0":标签,...},"probabilities":{...}}
  choice → {"type":"choice","choice":"紧张","confidence":..,"probabilities":{选项:概率}}
"""
import json, os, re, time, urllib.error, urllib.request

from core import ROOT

DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"


def _key():
    k = os.environ.get("TASTER_API_KEY")
    if not k:
        jev_dir = os.environ.get("JEV_DIR") or os.path.normpath(os.path.join(ROOT, "..", "..", "Ops", "network"))
        env = os.path.join(jev_dir, ".env")
        if os.path.exists(env):
            m = re.search(r"TYPESAFE_API_KEY\s*=\s*(\S+)", open(env, encoding="utf-8").read())
            k = m.group(1) if m else None
    if not k:
        raise RuntimeError("没有 key：设 TASTER_API_KEY，或让 JEV_DIR/.env 里有 TYPESAFE_API_KEY")
    # 控制台显示的 key 带 apikey_ 前缀，复制时常漏，漏了会 401（2026-09-21 踩过）
    return k if k.startswith("apikey_") else "apikey_" + k


def ask(state, questions, model, timeout=60, retries=3):
    body = json.dumps({"state": state, "model": model, "questions": questions}).encode("utf-8")
    url = os.environ.get("TASTER_ENDPOINT") or DEFAULT_ENDPOINT
    for i in range(retries):
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Authorization": "Bearer " + _key(), "Content-Type": "application/json"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8")), (time.time() - t0) * 1000
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:600]
            if e.code in (429, 500, 502, 503, 504) and i < retries - 1:
                time.sleep(5 * 2 ** i)
                continue
            hint = {401: "（key 无效，常见原因：漏了 apikey_ 前缀）",
                    422: "（题的结构不对：score/choice 必须有 criteria）"}.get(e.code, "")
            raise RuntimeError("HTTP %s%s\n%s" % (e.code, hint, detail))
        except (urllib.error.URLError, TimeoutError) as e:
            if i < retries - 1:
                time.sleep(5 * 2 ** i)
                continue
            raise RuntimeError("连不上判断模型：%s" % e)
