from __future__ import annotations

import json
import re
from itertools import product
from pathlib import Path


OUT_PATH = Path(__file__).with_name("builtin_training_scenarios.json")
TARGET_COUNT = 1000


SCENE_TEMPLATES = {
    "placement_question": {
        "incoming": [
            "这里面放什么？",
            "聚宝盆里面放点什么？",
            "这个放哪里比较好？",
            "收到以后要放在哪？",
            "可以放手机后面吗？",
            "放卧室还是客厅？",
            "这个要随身带着吗？",
            "放包里可以吗？",
        ],
        "good": [
            "你问放哪里这个，放干净稳妥的位置就行",
            "这个别乱放，我把具体放法发你看下",
            "里面不用额外乱放东西，先按我发你的放法来",
            "这个主要看位置，我给你说下怎么放稳妥",
            "可以放家里干净的位置，别压着别乱丢就行",
        ],
    },
    "receive_method": {
        "incoming": [
            "怎么领？",
            "领取方法在哪里？",
            "这个怎么使用？",
            "我收到以后怎么弄？",
            "步骤发我一下",
            "具体怎么操作？",
        ],
        "good": [
            "领取方法很简单，我发你步骤你照着来就行",
            "你问使用方法这个，我直接把步骤发你",
            "这个不复杂，我按顺序给你说一下",
            "我把领取和使用方法一起发你，照着做就行",
        ],
    },
    "trust_doubt": {
        "incoming": [
            "你们真的假的？",
            "不会是骗人的吧？",
            "现在骗子太多了",
            "我有点不太相信",
            "这个靠谱吗？",
            "怎么证明是真的？",
        ],
        "good": [
            "你有顾虑正常，这种事本来就得看清楚再决定",
            "不是让你盲目相信，我把流程说明白你自己判断",
            "你担心这个我理解，我先把来龙去脉跟你说清楚",
            "这个不强求，信不信都看你自己，我只把方法说清楚",
        ],
    },
    "price_hesitation": {
        "incoming": [
            "有点贵",
            "能便宜点吗？",
            "我考虑一下",
            "现在手头有点紧",
            "随喜多少合适？",
            "最低多少可以？",
        ],
        "good": [
            "这个看你心意，不用硬撑，量力就行",
            "不用有压力，按你方便的来就可以",
            "你先考虑清楚，别为了这个让自己为难",
            "随喜主要看心意，不是逼着你出多少",
        ],
    },
    "birth_info": {
        "incoming": [
            "需要生日吗？",
            "生辰是农历还是阳历？",
            "要不要具体时辰？",
            "不知道出生时间怎么办？",
            "名字需要真名吗？",
        ],
        "good": [
            "生日尽量给准确点，农历阳历你注明一下就行",
            "不知道具体时辰也没事，先按你知道的发我",
            "名字和生日是用来对应你的信息，不是做别的",
            "你把知道的信息发我，我看缺什么再跟你说",
        ],
    },
    "effect_question": {
        "incoming": [
            "多久有效果？",
            "这个真的有用吗？",
            "什么时候能见效？",
            "效果明显吗？",
            "是不是马上就有变化？",
        ],
        "good": [
            "这个别当成立刻见效的东西，主要是慢慢调顺",
            "效果因人而异，先按方法做，别太急",
            "这种不能承诺马上变化，更多是帮你稳一稳",
            "不要抱着立刻翻盘的心态，平常心做就好",
        ],
    },
    "thanks_ack": {
        "incoming": [
            "好的谢谢",
            "收到啦",
            "明白了",
            "知道了",
            "谢谢师兄",
        ],
        "good": [
            "不客气，按我发你的来就行",
            "好，有不懂的你再问我",
            "嗯嗯，照着步骤来就可以",
            "没事，你先按这个放好",
        ],
    },
    "continuous_question": {
        "incoming": [
            "这个放哪里？|需要每天看吗？",
            "怎么领？|收到以后怎么弄？",
            "真的假的？|有没有案例？",
            "要生日吗？|农历还是阳历？",
        ],
        "good": [
            "你这两个问题我分开说，先说放的位置",
            "我先回你后面这个，操作方法其实很简单",
            "你问的重点是流程和效果，我给你说清楚",
            "这几个问题连在一起了，我按顺序给你讲",
        ],
    },
}


AVOID = ["复读客户原句", "复读我方旧回复", "收到/我看下/确认下空话", "没有回答具体问题", "把 OCR 误识别当答案"]


def main() -> None:
    scenarios = _load_existing()
    for item in scenarios:
        item.setdefault("source", "zhipu_api")

    generated = _generate_template_scenarios()
    scenarios = _dedupe(scenarios + generated)
    OUT_PATH.write_text(
        json.dumps({"scenarios": scenarios[:TARGET_COUNT]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"written {min(len(scenarios), TARGET_COUNT)} scenarios to {OUT_PATH}")


def _generate_template_scenarios() -> list[dict]:
    result: list[dict] = []
    index = 0
    incoming_prefixes = ["", "刚才说的，", "我想确认下，", "再问一下，", "那这个"]
    reply_prefixes = ["", "你问这个，", "这个我直接跟你说，", "我先说重点，", "这块别弄复杂，"]
    combinations_by_intent = []
    for intent, data in SCENE_TEMPLATES.items():
        rows = list(product(incoming_prefixes, reply_prefixes, data["incoming"], data["good"]))
        combinations_by_intent.append((intent, data["good"], rows))

    while len(result) < TARGET_COUNT:
        for intent, replies, rows in combinations_by_intent:
            row = rows[index % len(rows)]
            incoming_prefix, reply_prefix, incoming, reply = row
            incoming_list = [f"{incoming_prefix}{part}".strip() for part in incoming.split("|")]
            alt1 = replies[(index + 1) % len(replies)]
            alt2 = replies[(index + 2) % len(replies)]
            result.append(
                {
                        "intent": intent,
                        "incoming": incoming_list,
                        "avoid_reply_types": AVOID,
                        "good_replies": [
                            _clean_reply_variant(f"{reply_prefix}{reply}"),
                            _clean_reply_variant(f"{reply_prefix}{alt1}"),
                            _clean_reply_variant(f"{reply_prefix}{alt2}"),
                        ],
                        "source": "template_expanded_from_api_seed",
                }
            )
            if len(result) >= TARGET_COUNT:
                return result
        index += 1
    return result


def _clean_reply_variant(text: str) -> str:
    text = text.replace("这个这个", "这个")
    text = text.replace("你问这个，你问", "你问")
    return text[:80]


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
    return [item for item in data if isinstance(item, dict)]


def _dedupe(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for item in items:
        incoming = item.get("incoming", [])
        replies = item.get("good_replies", [])
        if isinstance(incoming, str):
            incoming = [incoming]
        if isinstance(replies, str):
            replies = [replies]
        if not incoming or not replies:
            continue
        key = re.sub(r"\W+", "", " ".join(map(str, incoming)) + str(replies[0]))
        if key in seen:
            continue
        seen.add(key)
        item["incoming"] = [str(x).strip() for x in incoming if str(x).strip()]
        item["good_replies"] = [str(x).strip() for x in replies if str(x).strip()][:3]
        item["avoid_reply_types"] = item.get("avoid_reply_types") or AVOID
        result.append(item)
    return result


if __name__ == "__main__":
    main()
