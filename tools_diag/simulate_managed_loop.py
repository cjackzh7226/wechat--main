"""模拟全托管自动回复的多轮状态机，复现"必须重启才能回复新消息"问题。
模拟流程：对方发A → 程序回复R → 对方发B → 程序应回复（但可能卡住）"""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\Vibe C\客服\wechat--main")

from main import (
    _managed_reply_gate_allows_generation,
    _context_has_own_echo,
    _current_unreplied_turn,
    _turn_has_real_new_message,
    _should_auto_generate_for_context,
    _managed_context_allows_generation,
    _should_skip_unchanged_context,
)


def simulate(rounds: int = 4) -> None:
    waiting_for_own_echo = False
    handled_messages: list[str] = []
    last_sent_text = ""
    current_context = ""
    current_hash = ""
    import hashlib

    # 模拟一个多轮对话：每轮对方发一条新消息
    conversation = [
        "你是谁",
        "在吗",
        "今天天气怎么样",
        "好的谢谢",
    ]
    context = ""
    for i, other_msg in enumerate(conversation):
        print(f"\n===== 第 {i+1} 轮：对方发消息「{other_msg}」 =====")
        # 模拟截图识别到的完整上下文（对方消息 + 如果程序发过回复，窗口里会出现[我]的消息）
        context_lines = []
        for j in range(i + 1):
            context_lines.append(f"[对方] {conversation[j]}")
            # 假设程序回复后窗口里能看到自己发的消息
            if j < i:  # 前面几轮程序都回复了
                context_lines.append(f"[我] 回复第{j+1}轮")
        context = "\n".join(context_lines)
        digest = hashlib.sha1(f"企业微信\n{context}".encode("utf-8")).hexdigest()

        print(f"上下文:\n{context}")
        print(f"waiting_for_own_echo={waiting_for_own_echo}, handled={handled_messages}")

        # 1) 是否跳过未变化上下文
        skip = _should_skip_unchanged_context(
            force=False,
            managed=True,
            current_context=current_context,
            new_context=context,
            current_hash=current_hash,
            digest=digest,
            handled_messages=handled_messages,
        )
        print(f"跳过未变化上下文: {skip}")
        if skip:
            current_hash = digest
            print("→ 程序认为上下文没变化，跳过！")
            continue

        # 2) 等待回声检查
        if waiting_for_own_echo:
            allow, still_waiting = _managed_reply_gate_allows_generation(
                context,
                waiting_for_own_echo,
                last_sent_text,
                handled_messages,
                allow_new_without_own_echo=True,
            )
            waiting_for_own_echo = still_waiting
            print(f"回声门控: allow={allow}, still_waiting={still_waiting}")
            if still_waiting:
                print("→ 程序卡在等待自己的回声！新消息来了也不处理！！")
                continue

        # 3) 是否允许自动生成
        allow_gen = _should_auto_generate_for_context(
            context, force=False, managed=True, handled_messages=handled_messages
        )
        print(f"允许自动生成: {allow_gen}")
        if not allow_gen:
            print("→ 程序认为不需要回复（最新消息是[我]？），等待对方下一条")
            current_context = context
            current_hash = digest
            continue

        # 4) 模拟生成回复并发送
        reply = f"自动回复第{i+1}轮"
        print(f"→ 生成并发送: {reply}")
        handled_messages = list(_current_unreplied_turn(context))
        last_sent_text = reply
        waiting_for_own_echo = True
        current_context = context
        current_hash = digest

    print("\n\n========== 模拟结束 ==========")
    print(f"最终状态: waiting_for_own_echo={waiting_for_own_echo}")


if __name__ == "__main__":
    simulate()
