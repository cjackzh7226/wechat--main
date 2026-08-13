from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScenarioExample:
    intent: str
    incoming: str
    replies: tuple[str, str, str]


SEED_SCENARIOS: tuple[ScenarioExample, ...] = (
    ScenarioExample("low_ack", "好的 / 知道了 / 嗯嗯", ("嗯嗯", "好的", "可以，先这样")),
    ScenarioExample("thanks", "收到了，谢谢", ("不客气，拿到就行", "收到就好", "哈哈不客气")),
    ScenarioExample("food_share", "来自陕西的米线，麻麻的", ("看着就挺香的", "麻麻的是花椒味吗", "这碗米线有点上头")),
    ScenarioExample("job_card", "招聘卡片：岗位、五险一金", ("这个岗位看着还可以", "有五险一金还挺实在", "可以转给需要的人看看")),
    ScenarioExample("venting", "看到有些消息血压上来了", ("这谁看了不血压上来", "普通员工也太难了", "绷不住了这工作量")),
    ScenarioExample("question", "这个方便今天确认吗？", ("可以，我确认下", "我看下具体情况", "这个我得再看一眼")),
    ScenarioExample("how_to_receive", "领取/使用方法/该放哪里", ("领取方法很简单，我发你看下", "你看这个位置就行", "我把使用方法发你")),
    ScenarioExample("schedule", "周末有空吃饭吗？", ("可以啊，晚点定时间", "我看下周末安排", "可以，哪天方便")),
    ScenarioExample("document", "方案/文件能发我看下吗？", ("可以，我整理下发你", "我晚点发你一版", "行，我弄完给你")),
    ScenarioExample("money", "报价/付款/合同确认", ("我先核一下", "这个我确认下再回你", "我看完跟你说")),
    ScenarioExample("photo", "发了一张图让你看", ("这个看着还行", "有点意思", "这张还挺直观")),
)


def replies_for_intent(intent: str) -> list[str]:
    for item in SEED_SCENARIOS:
        if item.intent == intent:
            return list(item.replies)
    return []


def prompt_examples_for_intent(intent: str, limit: int = 3) -> str:
    matching = [item for item in SEED_SCENARIOS if item.intent == intent]
    others = [item for item in SEED_SCENARIOS if item.intent != intent]
    chosen = (matching + others)[:limit]
    lines = []
    for item in chosen:
        lines.append(f"- 场景：{item.incoming}")
        lines.append(f"  回复：{item.replies[0]} / {item.replies[1]} / {item.replies[2]}")
    return "\n".join(lines)


def seed_count() -> int:
    return len(SEED_SCENARIOS)
