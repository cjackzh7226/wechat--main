"""模拟真实场景：企业微信中"我"的消息被误识别为[对方] 或 根本识别不到
验证：程序发送回复后窗口出现自己的消息，但OCR无法识别为[我]时，状态机是否卡死"""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\Vibe C\客服\wechat--main")

from main import (
    _managed_reply_gate_allows_generation,
    _current_unreplied_turn,
    _should_auto_generate_for_context,
    _should_skip_unchanged_context,
    _turn_has_real_new_message,
    _context_has_own_echo,
)
import hashlib


def run_scenario(name: str, echo_recognized: bool, wait_after_send: bool) -> None:
    """echo_recognized: 程序发送后，窗口里自己的消息能否被识别为[我]
       wait_after_send: 发送后是否等待自己的回声确认"""
    print(f"\n{'='*60}\n场景: {name} (echo可识别={echo_recognized})")
    waiting_for_own_echo = False
    handled_messages: list[str] = []
    last_sent_text = ""
    current_context = ""
    current_hash = ""
    context = ""

    # 4轮对话
    others = ["你是谁", "在吗", "今天天气怎么样", "好的谢谢"]
    for i, msg in enumerate(others):
        print(f"\n-- 第{i+1}轮 对方:「{msg}」--")
        # 重建当前窗口可见上下文
        lines = []
        for j in range(i + 1):
            lines.append(f"[对方] {others[j]}")
            # 程序回复后，窗口里会出现自己的消息
            if j < i:
                if echo_recognized:
                    lines.append(f"[我] 自动回复第{j+1}轮")
                else:
                    # 真实问题：企业微信里"我"的消息被识别成[对方]！
                    lines.append(f"[对方] 自动回复第{j+1}轮")
        context = "\n".join(lines)
        digest = hashlib.sha1(f"企业微信\n{context}".encode()).hexdigest()

        print(f"上下文: {[l for l in context.splitlines()]}")

        # 1) 跳过未变化检查
        skip = _should_skip_unchanged_context(
            force=False, managed=True,
            current_context=current_context, new_context=context,
            current_hash=current_hash, digest=digest,
            handled_messages=handled_messages,
        )
        if skip:
            current_hash = digest
            print("→ 跳过（上下文未变化）")
            continue

        # 2) 等待回声门控
        if waiting_for_own_echo:
            allow, still = _managed_reply_gate_allows_generation(
                context, waiting_for_own_echo, last_sent_text,
                handled_messages, allow_new_without_own_echo=True,
            )
            waiting_for_own_echo = still
            print(f"→ 回声门控: allow={allow}, still_waiting={still}")
            if still:
                print("  ⚠️ 卡死在等待自己的回声！")
                # 对方下一条新消息也检查一遍
                continue

        # 3) 是否自动生成
        allow_gen = _should_auto_generate_for_context(
            context, force=False, managed=True, handled_messages=handled_messages
        )
        print(f"→ 允许生成: {allow_gen}")
        if not allow_gen:
            current_context = context
            current_hash = digest
            print("  → 等待对方下一条")
            continue

        # 4) 生成并发送
        reply = f"自动回复第{i+1}轮"
        print(f"→ ✅ 生成并发送: {reply}")
        handled_messages = list(_current_unreplied_turn(context))
        last_sent_text = reply
        waiting_for_own_echo = wait_after_send
        current_context = context
        current_hash = digest

    print(f"\n【结论】{'✅ 多轮正常' if not waiting_for_own_echo else '❌ 卡死：最后一次发送后仍在等待回声'}")


if __name__ == "__main__":
    run_scenario("我方消息被识别为[对方]（真实企业微信问题）", echo_recognized=False, wait_after_send=True)
