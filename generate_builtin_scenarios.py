from __future__ import annotations

import json
import re
import time
from pathlib import Path

import requests

from settings import AppSettings


OUT_PATH = Path(__file__).with_name("builtin_training_scenarios.json")
TARGET_COUNT = 1000
BATCH_SIZE = 10


SYSTEM_PROMPT = """你是微信私域客服对话话术库生成器。
生成的数据用于本地规则库，不是真实客户隐私。
只输出 JSON，不要解释。顶层格式为：
{"scenarios": [ ... ]}
每个 scenarios 元素格式：
{
  "intent": "英文场景名",
  "incoming": ["客户连续发来的1-3条消息"],
  "avoid_reply_types": ["应该避免的错误类型"],
  "good_replies": ["3条自然、具体、像微信真人的话术"]
}
要求：
1. 场景围绕国学/祈福/福宝/聚宝盆/财库/领取方法/放置位置/质疑真假/价格犹豫/已收到/感谢/连续追问。
2. good_replies 必须回答客户具体问题，禁止“收到、我看下、确认下”这类空话。
3. 不要把 OCR 错误、软件名、微信电脑版当业务答案。
4. 回复自然、短一些，但不要弱智，不要客服腔。
5. 可以轻微引用客户具体问题，比如“你问放哪里这个...”。"""


def main() -> None:
    settings = AppSettings().load()
    if not settings.api_key:
        raise SystemExit("没有配置智谱 API Key，无法生成内置训练库。")

    scenarios: list[dict] = _load_existing()
    batch = 1
    while len(scenarios) < TARGET_COUNT:
        need = min(BATCH_SIZE, TARGET_COUNT - len(scenarios))
        print(f"generating batch={batch} need={need} current={len(scenarios)}")
        items = _generate_batch_with_retry(settings, need, batch)
        scenarios = _dedupe(scenarios + items)
        _save(scenarios)
        batch += 1
        time.sleep(0.8)
    _save(scenarios[:TARGET_COUNT])
    print(f"done: {OUT_PATH} count={len(scenarios[:TARGET_COUNT])}")


def _generate_batch_with_retry(settings: AppSettings, count: int, batch: int) -> list[dict]:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            return _generate_batch(settings, count, batch, attempt)
        except Exception as exc:
            last_error = exc
            print(f"batch={batch} attempt={attempt} failed: {exc}")
            time.sleep(2 * attempt)
    raise RuntimeError(f"batch={batch} failed after retries: {last_error}")


def _generate_batch(settings: AppSettings, count: int, batch: int, attempt: int) -> list[dict]:
    prompt = f"生成 {count} 组训练对话。批次 {batch}，尽量覆盖不同客户问法和不同回复方式。只返回合法 JSON。"
    payload = {
        "model": settings.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.85,
        "max_tokens": 2200,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
    }
    response = requests.post(
        f"{settings.base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {settings.api_key}"},
        json=payload,
        timeout=120,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    return _parse_json_array(content)


def _parse_json_array(content: str) -> list[dict]:
    text = content.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    match = re.search(r"\[.*\]", text, re.S)
    if match:
        text = match.group(0)
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("scenarios", [])
    if not isinstance(data, list):
        return []
    return [_clean_item(item) for item in data if isinstance(item, dict)]


def _load_existing() -> list[dict]:
    if not OUT_PATH.exists():
        return []
    try:
        data = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("scenarios", [])
    if not isinstance(data, list):
        return []
    return [_clean_item(item) for item in data if isinstance(item, dict)]


def _clean_item(item: dict) -> dict:
    return {
        "intent": str(item.get("intent", "general")).strip() or "general",
        "incoming": _string_list(item.get("incoming"))[:3],
        "avoid_reply_types": _string_list(item.get("avoid_reply_types"))[:5],
        "good_replies": _string_list(item.get("good_replies"))[:3],
    }


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _dedupe(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for item in items:
        if not item.get("incoming") or not item.get("good_replies"):
            continue
        key = re.sub(r"\W+", "", " ".join(item["incoming"]) + item["good_replies"][0])
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _save(scenarios: list[dict]) -> None:
    OUT_PATH.write_text(
        json.dumps({"scenarios": scenarios}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
