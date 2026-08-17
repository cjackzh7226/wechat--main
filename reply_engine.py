from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Iterable

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

from llm_clients import build_text_generation_request, extract_text_from_response, provider_source_label
from memory import ENTERPRISE_ANNUAL_REPORT_SOURCE, ENTERPRISE_LOCAL_MIN_SCORE, PHRASEBOOK_CONFIDENT_SCORE, StyleMemory
from scenario_bank import replies_for_intent
from settings import AppSettings
from web_search import format_search_context, search_web, should_search


LOW_INFORMATION_ACKS = {"好", "好的", "嗯", "嗯嗯", "知道了", "收到", "ok", "OK", "行", "可以"}
GENERIC_LOW_EFFORT = {
    "收到",
    "收到啊",
    "收到啦",
    "好的",
    "嗯嗯",
    "了解了",
    "理解了",
    "我看下",
    "帮你看看",
    "晚点回你",
    "我试试看",
    "怎么弄",
    "怎么弄？",
}
UNSUPPORTED_JARGON = {"福宝", "能量", "加持", "祝福孩子", "考试顺利"}
ENTERPRISE_MIN_LOCAL_CANDIDATE_SCORE = ENTERPRISE_LOCAL_MIN_SCORE
ENTERPRISE_NO_ANSWER_REPLY = "未在当前企业年报知识库中找到对应答案。"
NO_DIRECT_ANSWER_REPLY = "暂时未查到对应答案。"


def parse_numbered_replies(content: str) -> list[str]:
    text = (content or "").strip()
    if not text:
        return []
    matches = re.findall(
        r"(?:^|\n)\s*\d+\s*[\.\)、]\s*(.+?)(?=\n\s*\d+\s*[\.\)、]|\Z)",
        text,
        re.S,
    )
    raw_items = matches if matches else text.splitlines()
    replies: list[str] = []
    for item in raw_items:
        cleaned = _clean_reply_line(str(item))
        if cleaned:
            replies.append(cleaned)
        if len(replies) >= 3:
            break
    return replies


def classify_intent(conversation_text: str) -> str:
    last = last_meaningful_other_message(conversation_text)
    all_text = conversation_text or ""
    if _is_low_information_ack(last):
        return "low_ack"
    if _is_how_to_receive_scene(all_text):
        return "how_to_receive"
    if _is_thanks_or_received_scene(last):
        return "thanks"
    if _is_job_or_recruitment_card(all_text):
        return "job_card"
    if _is_food_share_scene(all_text):
        return "food_share"
    if _is_venting_scene(all_text):
        return "venting"
    if _is_question(last):
        return "question"
    return "general"


def auto_reply_decision(conversation_text: str) -> tuple[bool, str]:
    intent = classify_intent(conversation_text)
    if intent in {"low_ack", "thanks"}:
        return True, intent
    return False, intent


class ReplyEngine:
    def __init__(self, settings: AppSettings, memory: StyleMemory) -> None:
        self.settings = settings
        self.memory = memory
        self.last_source = "未生成"
        self.last_error = ""
        self.last_search = ""
        self.last_enterprise_tie_candidates: list[dict[str, str]] = []

    def generate(self, conversation_text: str, partner: str = "", target_turn: list[str] | None = None) -> list[str]:
        self.last_error = ""
        effective_target_turn = target_turn if target_turn is not None else _infer_unreplied_other_messages(conversation_text)

        enterprise_query = "\n".join(message.strip() for message in effective_target_turn if message.strip())
        enterprise_context = enterprise_query or conversation_text
        enterprise_answer = self._select_enterprise_answer(enterprise_context)
        if enterprise_answer:
            # 命中后不直接发送：交大模型做一致性判断（客户问题 vs 候选对应问题）。
            # 一致 → 发送；不一致/判断失败 → 未命中文案。
            return self._enterprise_answer_via_judgement(enterprise_query or enterprise_context, [enterprise_answer])
        # 并列/接近候选：大模型先从并列候选中选出最匹配的一条，再判断一致性。
        tie_candidates = self.memory.enterprise_candidates(enterprise_context, limit=3)
        if (
            len(tie_candidates) >= 2
            and str(tie_candidates[0].get("enterprise_decision", "")) == "tie"
        ):
            tie_replies = [
                str(item.get("reply", ""))
                for item in tie_candidates
                if str(item.get("reply", "")).strip()
            ][:3]
            if tie_replies:
                return self._enterprise_answer_via_judgement(
                    enterprise_query or enterprise_context, list(tie_candidates[:3])
                )
        if (
            self.memory.has_enterprise_annual_report_knowledge()
            and self.memory.is_enterprise_annual_report_query(enterprise_context)
        ):
            # 企业范围未命中/有歧义时统一返回固定文案，不区分问句或祈使句形式，
            # 避免"帮我查一下年检时间"这类请求逃过拦截、被模型自由发挥。
            self.last_source = "企业年报知识库 · 未命中"
            return [ENTERPRISE_NO_ANSWER_REPLY]

        # 话术库优先：memory / model_memory 模式都先按共享打分取第一名，
        # 与测试命中页同一把尺子；匹配值不足时再考虑模型。
        phrasebook_attempted = self.settings.reply_source_mode in ("memory", "model_memory")
        if phrasebook_attempted:
            memory_replies = self._memory_replies(conversation_text)
            if memory_replies:
                self.last_source = "话术库"
                return memory_replies

        # 大模型只做并列裁决（见企业分支）；"大模型+话术库"（model_memory）模式
        # 不再自由生成回复。model（纯大模型）与 memory（纯话术库，缺数据时模型兜底）保留。
        if self.settings.reply_source_mode in ("memory", "model") and self.settings.api_key and requests is not None:
            api_replies = self._generate_from_api(conversation_text, partner, effective_target_turn)
            if api_replies or self.settings.reply_source_mode == "model":
                return api_replies

        if self.settings.reply_source_mode == "model":
            self.last_source = "未生成"
            if not self.last_error:
                self.last_error = "未配置文本接口 API Key，无法使用大模型生成"
            return []

        # 话术库有条目但匹配值不足（或已被质量过滤）→ 知识库未准备这类问题，
        # 老实回答不知道，不再硬凑候选或交给本地规则自由发挥。
        if phrasebook_attempted and self.memory.has_phrasebook_entries():
            self.last_source = "话术库未命中"
            return [NO_DIRECT_ANSWER_REPLY]

        return self._fallback(conversation_text)

    def _generate_from_api(
        self,
        conversation_text: str,
        partner: str,
        target_turn: list[str] | None,
    ) -> list[str]:
        original_model = self.settings.model
        models = _candidate_models(self.settings.model) if self.settings.api_provider == "zhipu" else [self.settings.model]
        errors: list[str] = []
        for model in models:
            self.settings.model = model
            try:
                replies, call_count = self._generate_with_current_model(conversation_text, partner, target_turn)
                if replies:
                    label = provider_source_label(self.settings)
                    if self.settings.api_provider == "zhipu":
                        label = f"{label}（{self.settings.model}）"
                    self.last_source = f"{label}，调用{call_count}次"
                    self.last_error = ""
                    return replies
            except Exception as exc:
                errors.append(f"{model}: {exc}")
                continue
            finally:
                self.settings.model = original_model

        if errors:
            self.last_error = f"{provider_source_label(self.settings)} 调用失败：" + "；".join(errors)
        if self.settings.reply_source_mode == "model":
            self.last_source = provider_source_label(self.settings)
            return []
        return []

    def _generate_with_current_model(
        self,
        conversation_text: str,
        partner: str,
        target_turn: list[str] | None,
    ) -> tuple[list[str], int]:
        collected: list[str] = []
        call_count = 0
        prompt = self._build_prompt(conversation_text, partner, target_turn=target_turn)
        base_messages = [
            {
                "role": "system",
                "content": (
                    "你是微信回复建议器。只输出3条候选回复，每条一行并用1/2/3编号。"
                    "必须优先回答当前未回复轮次和最后一句，不能复述对方原句，不能编造事实。"
                ),
            },
            {"role": "user", "content": prompt},
        ]

        for attempt in range(3):
            messages = base_messages if attempt == 0 else _refill_messages(base_messages, collected, 3 - len(collected))
            content = self._request_model_content(messages)
            call_count += 1
            if not (content or "").strip():
                self.last_error = (
                    f"{provider_source_label(self.settings)} 返回里没有识别到文本，"
                    "请检查接口、模型或自定义返回文本路径"
                )
                return [], call_count
            raw = parse_numbered_replies(content)
            raw = _drop_parrot_replies(raw, last_meaningful_other_message("\n".join(target_turn or []) or conversation_text))
            raw = self._quality_filter(raw, "\n".join(target_turn or []) or conversation_text)
            raw = _filter_context_echo_replies(raw, conversation_text)
            collected = _merge_unique_replies(collected, raw)
            normalized = _normalize_api_replies(collected)
            if len(normalized) >= 3:
                return normalized[:3], call_count

        normalized = _normalize_api_replies(collected)
        if normalized:
            return normalized[:3], call_count
        self.last_error = (
            f"{provider_source_label(self.settings)} 返回内容未通过质量过滤，"
            "请检查模型能力、提示词或接口返回内容"
        )
        self.last_source = provider_source_label(self.settings)
        return [], call_count

    def _request_model_content(self, messages: list[dict[str, str]]) -> str:
        request = build_text_generation_request(self.settings, messages)
        response = requests.post(
            request.url,
            headers=request.headers,
            json=request.payload,
            timeout=20,
        )
        if int(getattr(response, "status_code", 200) or 200) >= 400:
            detail = _response_error_detail(response)
            raise RuntimeError(f"HTTP {response.status_code}: {detail}")
        response.raise_for_status()
        return extract_text_from_response(response.json(), self.settings)

    def _build_prompt(self, conversation_text: str, partner: str = "", target_turn: list[str] | None = None) -> str:
        target_messages = [message.strip() for message in (target_turn or []) if message and message.strip()]
        if not target_messages:
            target_messages = _infer_unreplied_other_messages(conversation_text)
        latest_target = target_messages[-1] if target_messages else last_meaningful_other_message(conversation_text)
        target_text = "\n".join(target_messages).strip()
        complex_requirement = _is_complex_requirement(target_text or conversation_text)
        length_rule = (
            "复杂需求、方案咨询允许每条35-70字；普通闲聊尽量30字内。"
            if complex_requirement
            else "每条尽量不超过30个中文字符，像微信里随手发的话。"
        )
        source_rule = (
            "回复来源：大模型。你必须自己理解整句和上下文，不要按关键词套模板，不要参考本地场景库。"
            if self.settings.reply_source_mode == "model"
            else "回复来源：大模型 + 长期记忆。长期记忆只提供历史表达参考，当前回复必须重新理解上下文。"
        )
        memory_block = (
            ""
            if self.settings.reply_source_mode == "model"
            else self.memory.prompt_block(conversation_text, exclude_sources={ENTERPRISE_ANNUAL_REPORT_SOURCE})
        )
        memory_rule = (
            "7. 不使用历史话术库，只根据当前上下文自然生成。"
            if self.settings.reply_source_mode == "model"
            else "7. 优先参考相似历史聊天片段与真实回复的接话方式，但不要照抄。"
        )
        return f"""当前聊天对象：{partner or "当前微信联系人"}

最近聊天记录，越下面越新：
{conversation_text.strip() or "（没有识别到聊天内容）"}

当前需要回复的未回复轮次：
{target_text or latest_target or "（使用最后一轮未回复对方消息）"}

本轮最后一句（最高优先级）：
{latest_target or "（无）"}

{memory_block}

回复要求：
1. 整段上下文只作参考，对方发的内容优先级更高，对方最后一句优先级最高。
2. 如果对方有多个未回复问题，每个未回复的问题都要照顾到，并合并成一条自然回复。
3. 如果前面的问题我已经回复过，只参考背景，只回复最后一句。
4. 不能编造，不懂就自然确认，不要假装看过链接或图片细节。
5. 不要输出“理解了 / 帮你看看 / 怎么弄？”这类敷衍句。
6. {length_rule}
{memory_rule}
8. {source_rule}

请输出3条候选，只要候选本身：
1. xxx
2. xxx
3. xxx"""

    def _fallback(self, conversation_text: str) -> list[str]:
        intent = classify_intent(conversation_text)
        target = "\n".join(_infer_unreplied_other_messages(conversation_text)) or conversation_text
        if self.settings.reply_source_mode != "model":
            memory = self._memory_replies(conversation_text)
            if memory:
                self.last_source = "长期记忆"
                return memory
        self.last_source = "本地兜底规则"
        candidates = _rule_replies(intent, target)
        final_replies = _normalize_api_replies(self._quality_filter(candidates, target))
        if final_replies:
            return final_replies
        if _is_question(target):
            return [NO_DIRECT_ANSWER_REPLY]
        return candidates[:3]

    def _memory_replies(self, conversation_text: str) -> list[str]:
        """普通话术库共享打分：返回第一名（匹配值 >= PHRASEBOOK_CONFIDENT_SCORE）。

        与"管理话术 > 测试命中"使用同一套打分排序（memory.phrasebook_candidates），
        保证测试页第一位 = 运行时实际采用的第一位。低分/无条目返回空列表，
        由 generate() 决定回退模型还是老实回答"暂时未查到对应答案"。
        """
        candidates = self.memory.phrasebook_candidates(conversation_text, limit=12)
        if not candidates:
            return []
        top_score = int(candidates[0].get("phrasebook_score", 0) or 0)
        if top_score < PHRASEBOOK_CONFIDENT_SCORE:
            return []
        replies = [
            str(item.get("reply", ""))
            for item in candidates
            if str(item.get("source", "")) != ENTERPRISE_ANNUAL_REPORT_SOURCE
        ]
        replies = self._quality_filter(replies, conversation_text)
        # 话术库答案保留完整内容（max_chars=4000），不做 70 字截断
        return _normalize_api_replies(replies, max_chars=4000)[:1]

    def _select_enterprise_answer(self, conversation_text: str) -> dict[str, str] | None:
        candidates = self.memory.enterprise_candidates(conversation_text, limit=6)
        if not candidates:
            return None

        best_local = candidates[0]
        local_score = _enterprise_local_score(best_local)
        if local_score < ENTERPRISE_MIN_LOCAL_CANDIDATE_SCORE:
            return None
        if _enterprise_is_exact_question(best_local) or _enterprise_is_local_confident(best_local):
            return best_local
        # 企业年报只允许共享的本地决策直接发送；并列或接近候选不再直接发送，
        # 由 generate() 交给大模型从并列候选中裁决（见 _arbitrate_enterprise_tie）。
        return None

    def _arbitrate_enterprise_tie(self, question: str, candidates: list[dict[str, str]]) -> dict[str, str] | None:
        """大模型从并列候选中裁决：prompt 只允许输出候选编号，返回被选中的候选。

        任何异常（无 Key、请求失败、输出无法解析）都返回 None，由调用方回退
        固定未命中文案；绝不把模型自由文本当作答案发送。
        """
        if not self.settings.api_key or requests is None or len(candidates) < 2:
            return None
        lines = []
        for index, cand in enumerate(candidates[:3], 1):
            cand_question = str(cand.get("question", "")).strip()
            cand_reply = str(cand.get("reply", "")).strip()
            lines.append(f"{index}. 候选问题：{cand_question}\n   候选答案：{cand_reply[:150]}")
        prompt = (
            "客户问题：\n"
            f"{question}\n\n"
            "以下是从企业年报知识库中检索到的分数接近的候选问答：\n"
            + "\n".join(lines)
            + "\n\n请选出与客户问题语义最匹配的一条。只输出候选编号（如 2），"
              "不要输出任何其他文字，不要自己编写答案。"
        )
        messages = [
            {"role": "system", "content": "你是企业年报知识库答案选择器，只能输出候选编号。"},
            {"role": "user", "content": prompt},
        ]
        try:
            request = build_text_generation_request(self.settings, messages)
            response = requests.post(request.url, headers=request.headers, json=request.payload, timeout=20)
            response.raise_for_status()
            content = extract_text_from_response(response.json(), self.settings)
        except Exception:
            return None
        match = re.search(r"[1-9]", str(content or ""))
        if not match:
            return None
        index = int(match.group())
        if 1 <= index <= len(candidates):
            return candidates[index - 1]
        return None

    def _enterprise_answer_via_judgement(self, question: str, candidates: list[dict[str, str]]) -> list[str]:
        """企业库命中后的最终把关：大模型先选再判（一致性），一致才发送。

        - 一致 → 返回 [被选中候选答案]；
        - 不一致 / 无 Key / 判断失败 → 返回 [企业未命中文案]（安全降级，绝不误发）。
        """
        judgement = self._judge_enterprise_consistency(question, candidates)
        if judgement and judgement.get("consistent"):
            chosen = candidates[judgement["chosen_index"]]
            self.last_source = _enterprise_source_label(chosen) + " · 大模型确认一致"
            return [str(chosen.get("reply", ""))]
        self.last_source = "企业年报知识库 · 判断不一致"
        return [ENTERPRISE_NO_ANSWER_REPLY]

    def _judge_enterprise_consistency(self, question: str, candidates: list[dict[str, str]]) -> dict | None:
        """大模型先选再判：从候选中选出最匹配的一条，并判断客户问题与它是否同一问题。

        返回 {"chosen_index": int, "consistent": bool}；调用失败/输出无法解析返回 None。
        """
        if not self.settings.api_key or requests is None or not candidates:
            return None
        lines = []
        for index, cand in enumerate(candidates[:3], 1):
            cand_question = str(cand.get("question", "")).strip()
            cand_reply = str(cand.get("reply", "")).strip()
            lines.append(f"{index}. 知识库问题：{cand_question}\n   知识库答案：{cand_reply[:150]}")
        messages = [
            {
                "role": "system",
                "content": "你是企业年报知识库答案选择与一致性判断器，只输出 JSON。",
            },
            {
                "role": "user",
                "content": (
                    f"客户问题：{question}\n\n"
                    "以下是从企业年报知识库中匹配到的候选问答：\n"
                    + "\n".join(lines)
                    + "\n\n请完成两个任务：\n"
                    "1. chosen：从候选中选出与客户问题语义最匹配的一条（输出编号）；\n"
                    "2. consistent：判断客户问题与选中候选的“知识库问题”是否为同一个问题（true/false）。"
                    "客户问法不同但意思相同算 true；候选与客户问题无关或无法确认算 false。\n"
                    "只输出 JSON：{\"chosen\": 1, \"consistent\": true}，不要输出任何其他文字。"
                ),
            },
        ]
        try:
            request = build_text_generation_request(self.settings, messages)
            request.payload["max_tokens"] = 500
            response = requests.post(request.url, headers=request.headers, json=request.payload, timeout=60)
            response.raise_for_status()
            content = extract_text_from_response(response.json(), self.settings)
        except Exception:
            return None
        try:
            text = (content or "").strip().strip("`")
            data = json.loads(text)
            chosen = int(data.get("chosen", 0))
            consistent = bool(data.get("consistent", False))
        except Exception:
            return None
        if not (1 <= chosen <= len(candidates)):
            return None
        return {"chosen_index": chosen - 1, "consistent": consistent}

    def _quality_filter(self, replies: list[str], context: str) -> list[str]:
        if not replies:
            return []
        substantive = _is_substantive_context(context)
        complex_requirement = _is_complex_requirement(context)
        context_tokens = _text_tokens(context)
        result: list[str] = []
        for reply in replies:
            cleaned = _clean_reply_line(reply)
            if not cleaned:
                continue
            compact = _compact(cleaned)
            if _is_question(context) and _is_pending_reply(cleaned):
                continue
            if substantive and _is_generic_low_effort(cleaned):
                continue
            if _contains_unsupported_jargon(cleaned, context):
                continue
            if complex_requirement and len(cleaned) < 12:
                continue
            if complex_requirement and not (_text_tokens(cleaned) & context_tokens):
                continue
            result.append(cleaned)
        return result


def latest_meaningful_message(conversation_text: str) -> tuple[str, str]:
    for role, text in reversed(_parse_messages(conversation_text)):
        if _is_meaningful_text(text):
            return role, text
    return "", ""


def last_meaningful_other_message(conversation_text: str) -> str:
    for role, text in reversed(_parse_messages(conversation_text)):
        if role == "对方" and _is_meaningful_text(text):
            return text
    return ""


def unreplied_other_messages(conversation_text: str) -> list[str]:
    messages = [(role, text) for role, text in _parse_messages(conversation_text) if _is_meaningful_text(text)]
    if not messages or messages[-1][0] != "对方":
        return []
    # 自动客服只处理屏幕上最新一条对方消息。不能向前追补旧问题：旧消息
    # 可能早已过时，也会让生成流程在积压会话中逐条排队而无法切换聊天。
    return [messages[-1][1]]


def select_best_reply_for_context(conversation_text: str, replies: list[str]) -> str:
    latest_role, _latest_text = latest_meaningful_message(conversation_text)
    if latest_role != "对方":
        return ""
    filtered = ReplyEngine(AppSettings(), StyleMemory())._quality_filter(replies, conversation_text)
    if not filtered:
        return ""
    last_other = last_meaningful_other_message(conversation_text)
    own_texts = {text for role, text in _parse_messages(conversation_text) if role == "我"}
    intent = classify_intent(conversation_text)
    scored: list[tuple[int, int, str]] = []
    context_tokens = _text_tokens(conversation_text)
    for index, reply in enumerate(filtered):
        score = 0
        if any(_is_same_or_contained_reply(reply, own) for own in own_texts):
            continue
        if _similar(_compact(reply), _compact(last_other)) > 0.82:
            score -= 100
        score += len(_text_tokens(reply) & context_tokens) * 5
        if _is_generic_low_effort(reply):
            score -= 30
        if intent == "food_share":
            score += _keyword_score(reply, ["香", "好吃", "上头", "想吃", "辣", "麻", "米线"], 8)
        elif intent == "venting":
            score += _keyword_score(reply, ["确实", "绷", "血压", "离谱", "普通员工"], 8)
        elif intent == "job_card":
            score += _keyword_score(reply, ["岗位", "五险", "机会", "转给", "招聘"], 8)
        elif intent == "how_to_receive":
            score += _keyword_score(reply, ["领取", "方法", "位置", "放", "使用"], 8)
        scored.append((score, -index, reply))
    scored.sort(reverse=True)
    return scored[0][2] if scored and scored[0][0] > -80 else ""


def _infer_unreplied_other_messages(conversation_text: str) -> list[str]:
    return unreplied_other_messages(conversation_text)


def _parse_messages(conversation_text: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for raw in (conversation_text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("[可见内容理解]") or line.startswith("[鍙鍐呭鐞嗚В]"):
            continue
        match = re.match(
            r"^\[(对方|我|系统|瀵规柟|鎴[^\]\s]*)\]?\s*(.*)$",
            line,
        )
        if match:
            role = _normalize_role(match.group(1))
            text = match.group(2).strip()
            result.append((role, text))
        elif result:
            role, prev = result[-1]
            result[-1] = (role, f"{prev} {line}".strip())
        else:
            result.append(("对方", line))
    return result


def _normalize_role(role: str) -> str:
    if role.startswith("对方") or role.startswith("瀵规柟"):
        return "对方"
    if role.startswith("系统"):
        return "系统"
    return "我"


def _is_meaningful_text(text: str) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    if re.fullmatch(r"\d{1,3}\s*['\"]", cleaned):
        return False
    if re.fullmatch(r"(昨天|今天|星期.|周.)?\s*\d{1,2}:\d{2}", cleaned):
        return False
    if cleaned in {"[表情]", "[图片]", "[视频]", "[语音]", "动画表情"}:
        return False
    return True


def _clean_reply_line(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^\s*\d+\s*[\.\)、]\s*", "", cleaned)
    cleaned = re.sub(r"^\s*[-*]\s*", "", cleaned)
    if re.match(r"^(消息理解|回复建议|分析|原因|候选回复|对方在|当前需要回复|最近聊天记录)[:：]", cleaned):
        return ""
    cleaned = cleaned.strip("\"'“”")
    return cleaned.strip()


def _is_low_information_ack(text: str) -> bool:
    compact = _compact(text)
    return compact in {_compact(item) for item in LOW_INFORMATION_ACKS}


def _is_thanks_or_received_scene(text: str) -> bool:
    if _is_question(text):
        return False
    return any(word in text for word in ["谢谢", "谢啦", "收了", "收到", "拿到了"])


def _is_job_or_recruitment_card(text: str) -> bool:
    return any(word in text for word in ["招聘", "岗位", "五险", "一金", "招人"])


def _is_food_share_scene(text: str) -> bool:
    return any(word in text for word in ["米线", "麻麻", "好吃", "香", "辣", "菜", "饭"])


def _is_venting_scene(text: str) -> bool:
    return any(word in text for word in ["血压", "普通员工", "离谱", "绷不住", "烦", "吐槽"])


def _is_how_to_receive_scene(text: str) -> bool:
    if "领取" in text and any(word in text for word in ["使用", "方法", "步骤", "哪里", "怎么", "放"]):
        return True
    return any(word in text for word in ["收到这个该放哪里", "聚宝盆里面放", "放点什么"])


def _is_question(text: str) -> bool:
    return "?" in text or "？" in text or any(word in text for word in ["怎么", "什么", "哪里", "哪", "吗", "能不能", "可不可以"])


def _is_complex_requirement(text: str) -> bool:
    markers = ["想用", "希望", "需要", "能不能", "怎么", "自动", "机器人", "系统", "流程", "对接", "处理", "查单", "核销"]
    return len(text) >= 35 and sum(1 for marker in markers if marker in text) >= 2


def _is_substantive_context(text: str) -> bool:
    intent = classify_intent(text)
    if intent in {"low_ack", "thanks"}:
        return False
    return intent != "general" or len(_compact(text)) > 20


def _rule_replies(intent: str, target: str) -> list[str]:
    direct: dict[str, list[str]] = {
        "low_ack": ["嗯嗯", "好的", "可以，先这样"],
        "thanks": ["不客气，拿到就行", "收到就好", "哈哈不客气"],
        "food_share": ["看着就挺香的", "麻麻的是花椒味吗", "这碗米线有点上头"],
        "job_card": ["这个岗位看着还可以", "有五险一金还挺实在", "可以转给需要的人看看"],
        "venting": ["这谁看了不血压上来", "普通员工也太难了", "绷不住了这工作量"],
        "how_to_receive": ["领取方法很简单，我发你看下", "你看这个位置就行", "我把使用方法发你"],
    }
    if intent in direct:
        return direct[intent]
    seeded = replies_for_intent(intent)
    if seeded:
        return seeded
    if _is_complex_requirement(target):
        return [
            "这个可以做，核心是先识别对方意图，再接到上架和售后流程里",
            "可以先拆成意图识别、活动上架、查单核销这几步",
            "这个思路可行，但要先把商家发活动的字段整理清楚",
        ]
    return ["可以，我看下具体情况", "这个我确认下再回你", "我先看下，晚点回你"]


def _normalize_api_replies(replies: list[str], max_chars: int = 70) -> list[str]:
    """清理候选并去重。

    ``max_chars`` 只压缩模型生成的短候选（默认 70 字）；话术库/知识库答案
    传较大上限（如 4000）以保留完整内容，避免官方长答案被截断。
    """
    normalized: list[str] = []
    seen: set[str] = set()
    for reply in replies:
        cleaned = _clean_generated_reply(reply, max_chars=max_chars)
        key = _compact(cleaned)
        if cleaned and key not in seen:
            normalized.append(cleaned)
            seen.add(key)
    return normalized[:3]


def _enterprise_local_score(candidate: dict[str, str]) -> int:
    try:
        return int(float(str(candidate.get("enterprise_local_score", "0"))))
    except (TypeError, ValueError):
        return 0


def _enterprise_is_exact_question(candidate: dict[str, str]) -> bool:
    return str(candidate.get("enterprise_exact_question", "")).strip() == "1"


def _enterprise_is_local_confident(candidate: dict[str, str]) -> bool:
    return str(candidate.get("enterprise_local_confident", "")).strip() == "1"


def _enterprise_source_label(candidate: dict[str, str]) -> str:
    parts = ["企业年报知识库"]
    for key in ("organization_type", "module", "subcategory"):
        value = str(candidate.get(key, "")).strip()
        if value:
            parts.append(value)
    sheet = str(candidate.get("source_sheet", "")).strip()
    cell = str(candidate.get("source_cell", "")).strip()
    if sheet or cell:
        parts.append(f"{sheet}!{cell}".strip("!"))
    confidence = str(candidate.get("enterprise_match_confidence", "")).strip()
    parts.append(f"模型匹配 {confidence}%" if confidence else "本地高相关匹配")
    return " · ".join(parts)


def _drop_parrot_replies(replies: list[str], last_message: str) -> list[str]:
    if not last_message:
        return replies
    return [reply for reply in replies if _similar(_compact(reply), _compact(last_message)) < 0.82]


def _filter_context_echo_replies(replies: list[str], context: str) -> list[str]:
    own_texts = [_compact(text) for role, text in _parse_messages(context) if role == "我"]
    result = []
    for reply in replies:
        compact = _compact(reply)
        if any(_similar(compact, own) > 0.86 for own in own_texts):
            continue
        result.append(reply)
    return result


def _merge_unique_replies(existing: list[str], incoming: list[str]) -> list[str]:
    result = list(existing)
    seen = {_compact(item) for item in result}
    for item in incoming:
        key = _compact(item)
        if key and key not in seen:
            result.append(item)
            seen.add(key)
    return result


def _refill_messages(messages: list[dict[str, str]], collected: list[str], needed: int) -> list[dict[str, str]]:
    cloned = [dict(item) for item in messages]
    cloned.append(
        {
            "role": "user",
            "content": (
                f"上一次只有{len(collected)}条可用回复，还需要补充{needed}条。"
                "不要重复已有回复，不要输出敷衍句，继续只输出编号候选。"
            ),
        }
    )
    return cloned


def _candidate_models(model: str) -> list[str]:
    models = [model]
    if model != "glm-4-flash-250414":
        models.append("glm-4-flash-250414")
    return models


def _response_error_detail(response: object) -> str:
    text = str(getattr(response, "text", "") or "").strip()
    return text[:500] if text else "empty response"


def _contains_unsupported_jargon(reply: str, context: str) -> bool:
    return any(word in reply and word not in context for word in UNSUPPORTED_JARGON)


def _is_generic_low_effort(reply: str) -> bool:
    compact = _compact(reply)
    generic = {_compact(item) for item in GENERIC_LOW_EFFORT}
    if compact in generic:
        return True
    return any(compact.startswith(item) and len(compact) <= len(item) + 2 for item in generic if item)


def _clean_generated_reply(text: str, max_chars: int = 70) -> str:
    result = _clean_reply_line(text)
    result = re.sub(r"\s+", " ", result).strip(" ，。；;")
    if len(result) > max_chars:
        parts = re.split(r"[，。；;！!？?]", result)
        result = "，".join(part.strip() for part in parts[:2] if part.strip()) or result[:max_chars]
    return result[:max_chars].rstrip("，。；; ")


def _keyword_score(text: str, words: Iterable[str], weight: int) -> int:
    return sum(weight for word in words if word in text)


def _text_tokens(text: str) -> set[str]:
    cleaned = _compact(re.sub(r"\[(?:对方|我|瀵规柟|鎴[^\]]*)\]", "", text or ""))
    tokens = set(re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z0-9_]{2,}", cleaned))
    for size in (2, 3, 4):
        for index in range(max(0, len(cleaned) - size + 1)):
            piece = cleaned[index : index + size]
            if re.fullmatch(r"[\u4e00-\u9fff]+", piece):
                tokens.add(piece)
    return tokens


def _compact(text: str) -> str:
    return re.sub(r"[\W_]+", "", text or "").lower()


# 承诺式/未完成回复：说要做某事但没给出结果（“我查查”“稍等”“我看下”等）
# 这类回复会触发自动补全下一轮，直到给出实质内容。
_PENDING_REPLY_PATTERNS = (
    "我查",
    "我查查",
    "查一下",
    "查查",
    "查下",
    "我看",
    "我看看",
    "看一下",
    "看下",
    "稍等",
    "等一下",
    "等我",
    "等会",
    "等等",
    "让我",
    "我先",
    "我去",
    "我问",
    "问一下",
    "问问",
    "确认一下",
    "核实",
    "核对",
    "查证",
    "帮你查",
    "帮你问",
    "帮你看看",
    "帮你确认",
    "马上",
    "一会",
    "一会儿",
    "待会",
    "回头",
    "正在确认",
    "正在查询",
    "正在核实",
    "正在查",
    "正在看",
    "正在问",
    "正在处理",
    "正在了解",
    "正在帮你",
    "正在跟进",
    "稍后",
    "晚点",
    "等下",
    "回头再说",
    "先看看",
    "先查",
    "先确认",
    "先了解",
    "先问",
)

# 即使句子较长，只要它明确表示“尚未查到/稍后再答”，就不能被当作最终答案发送。
_RESULTLESS_PROMISE_MARKERS = (
    "确认下",
    "晚点回",
    "稍后回复",
    "稍后回",
    "确认后",
    "核实后",
    "有结果了第一时间",
    "暂时无法直接查到",
    "暂时查不到",
    "暂时没法直接查到",
    "我得再看",
    "再看一眼",
    "我再看",
    "先看一下",
    "先看下",
)

# 反问/确认词：回复以这些词收尾且很短，说明在向对方反问确认而非给出答案
_RHETORICAL_ENDING = (
    "吗",
    "么",
    "吧",
    "呢",
    "呀",
    "哈",
    "？",
    "?",
    "？",
)


def _is_pending_reply(text: str) -> bool:
    """判断回复是否为“承诺未完成”或“反问确认”式（需要自动补全后续内容）。

    两类判定：
    1. 承诺未完成：短句含“我查查/稍等/我看下/正在确认”等承诺词，但没给结果；
    2. 反问确认：短句以“吗/么/吧/呢”或“？”结尾，是在反问对方而不是回答问题。
    """
    raw = (text or "").strip()
    compact = _compact(raw)
    if not compact:
        return False
    if any(marker in compact for marker in _RESULTLESS_PROMISE_MARKERS):
        return True
    # 回复较长（>=14 字符）说明已有实质内容，不算未完成
    if len(raw) >= 14:
        return False
    # 反问确认式：短问句（“要查吗？”“需要吗？”“你问的是这个吗”）
    if len(raw) <= 10 and raw.rstrip().endswith(_RHETORICAL_ENDING):
        return True
    for pattern in _PENDING_REPLY_PATTERNS:
        if pattern in compact:
            return True
    return False


def _is_complete_answer(text: str) -> bool:
    """判断回复是否给出了实质结果（补全后可停止）。"""
    compact = _compact(text)
    if not compact:
        return False
    if _is_pending_reply(text):
        return False
    # 有句号/感叹号/问号结尾或包含实质信息词的视为完整
    if len(compact) >= 6:
        return True
    return text.rstrip().endswith(("。", "！", "？", "～", "~", "!", "?"))


# 问句关键词：用户最后一条是问句时，若回复未给出实质答案则视为未回答
_QUESTION_MARKERS = ("什么", "怎么", "怎么样", "多少", "几", "哪", "哪里", "哪些", "吗", "呢", "是否", "能不能", "可不可以", "如何", "为啥", "为什么")

# 承诺/拖延动作词：短回复中出现这些词说明只是"说要去做"还没给结果
_STALL_MARKERS = (
    "确认", "核实", "核对", "查证", "查询", "查一下", "查下", "查查",
    "看看", "看一下", "看下", "问一下", "问问", "等等", "等一下", "稍等",
    "稍后", "晚点", "待会", "马上", "一会", "回头", "我再", "让我", "需要查",
)


def _looks_like_question(text: str) -> bool:
    """判断文本是否像提问（问句）。"""
    if "?" in text or "？" in text:
        return True
    return any(marker in text for marker in _QUESTION_MARKERS)


# 短反馈反问/感叹词：用户对回答的即时反馈（"是吗？""真的？""这样啊"），不是新问题
_FEEDBACK_QUESTION_PATTERNS = (
    "是吗", "真的", "真的假的", "这样啊", "这样吗", "好吧", "对呀", "对啊",
    "哦哦", "嗯嗯", "哈哈", "呵呵", "不会吧", "怎么可能", "开玩笑", "不是吧",
    "好呀", "行吧", "可以啊", "好的吧", "唉", "哎", "哦", "嗯？", "哦？",
)


def _is_feedback_question(text: str) -> bool:
    """判断是否为短反馈反问/感叹（如"是吗？""真的？""这样啊"），而非真正的新问题。
    这类消息是对上一条回答的即时反馈，不应触发新一轮生成。"""
    cleaned = (text or "").strip()
    if not cleaned or len(cleaned) > 8:
        return False
    return any(pattern in cleaned for pattern in _FEEDBACK_QUESTION_PATTERNS)


def _question_needs_completion(user_text: str, sent_reply: str) -> bool:
    """用户最后消息是问句、但回复未给出实质答案（如“正在确认”“需要查下”“好的”）时返回 True，
    用于触发补全，直到问题真正被回答。"""
    if not _looks_like_question(user_text or ""):
        return False
    reply = (sent_reply or "").strip()
    if not reply:
        return True
    # 未完成式（我查查/稍等/正在确认等）→ 未回答
    if _is_pending_reply(reply):
        return True
    # 极短无标点敷衍回复（"好的""嗯嗯""哦"）→ 未实质回答
    if len(reply) <= 4 and not reply.rstrip().endswith(("。", "！", "?", "？", "!", ".", "～", "~")):
        return True
    # 短回复（≤12字符）且含承诺/拖延动作词 → 仍未给出实质结果
    if len(reply) <= 12 and any(marker in reply for marker in _STALL_MARKERS):
        return True
    # 短回复以"！/。"结尾（明确陈述）→ 视为已回答
    if len(reply) <= 12 and reply.rstrip().endswith(("！", "。", "!", ".")):
        return False
    return False


def _similar(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _is_same_or_contained_reply(reply: str, old_text: str) -> bool:
    left = _compact(reply)
    right = _compact(old_text)
    if not left or not right:
        return False
    if left in right or right in left:
        return True
    return _similar(left, right) > 0.82


_ORIGINAL_QUALITY_FILTER = ReplyEngine._quality_filter


def _readable_build_prompt(
    self: ReplyEngine,
    conversation_text: str,
    partner: str = "",
    target_turn: list[str] | None = None,
) -> str:
    target_messages = [message.strip() for message in (target_turn or []) if message and message.strip()]
    if not target_messages:
        target_messages = _infer_unreplied_other_messages(conversation_text)
    latest_target = target_messages[-1] if target_messages else last_meaningful_other_message(conversation_text)
    target_text = "\n".join(target_messages).strip()
    complex_requirement = _is_complex_requirement(target_text or conversation_text)
    length_rule = (
        "复杂需求、方案咨询允许每条 45-70 字；普通闲聊尽量 30 字内。"
        if complex_requirement
        else "每条尽量不超过 30 个中文字符，像微信里随手发的话。"
    )
    source_rule = (
        "回复来源：大模型。你必须自己理解整句和上下文，不要按关键词套模板，不要参考本地话术库。"
        if self.settings.reply_source_mode == "model"
        else "回复来源：大模型 + 话术库。话术库只提供历史表达参考，当前回复必须重新理解上下文。"
    )
    memory_block = "" if self.settings.reply_source_mode == "model" else self.memory.prompt_block(conversation_text)
    memory_rule = (
        "7. 不使用历史话术库，只根据当前上下文自然生成。"
        if self.settings.reply_source_mode == "model"
        else "7. 优先参考相似历史聊天片段与真实回复的接话方式，但不要照抄。"
    )
    search_block = ""
    self.last_search = ""
    self.last_search_numbers = set()
    if getattr(self.settings, "enable_web_search", True):
        query_text = latest_target or last_meaningful_other_message(conversation_text)
        if should_search(query_text):
            results = search_web(query_text, getattr(self.settings, "search_max_results", 3))
            self.last_search = f"已联网检索({len(results)}条)" if results else "联网检索无结果"
            search_block = format_search_context(results)
            if search_block:
                self.last_search_numbers = set(re.findall(r"\d+(?:\.\d+)?%?", search_block))
                search_block += (
                    "\n\n搜索结果的时效性高于模型训练记忆；回答事实性问题时，参考信息里"
                    "明确给出的数字、数量、规定可以直接采用（如'5个及以上发起人'）；"
                    "参考信息里没有的具体细节不要编造。"
                )
        else:
            self.last_search = "无需联网"
    else:
        self.last_search = "联网搜索已关闭"
    search_rule = (
        "★ 有「实时参考信息」：直接给出明确结论（含具体数字、数量），以参考信息为准；"
        "参考信息不足以确定时，可结合常识给出“通常/一般”的表述，并提示以官方最新规定为准。"
        "不要回复“我再帮您核实一下/以官方为准”这类不含任何实质信息的敷衍句。"
        if search_block
        else ""
    )
    no_fabricate_rule = (
        "4. 不能编造。数字/数量以「实时参考信息」为准：参考信息中明确的数字可以直接采用；"
        "参考信息中没有的具体数字不能自己编数字；不知道或参考信息不足时，要说明无法确定。"
        if search_block
        else "4. 不能编造，不知道就说需要确认；不能自己编数字、金额、订单量、比例、时间或效果数据。"
    )
    visible_rule = (
        "10. 如果上下文包含[可见内容理解]，它是图片/卡片/链接预览的可见内容说明；"
        "必须结合图片/卡片内容和最后一句文字来回复，可以使用其中的可见信息；"
        "不要只按文字或只按图片泛泛回复。"
        if "[可见内容理解]" in conversation_text
        else ""
    )
    image_detail_rule = (
        "5. 已有[可见内容理解]时，可以引用其中的可见信息；不在可见内容里的细节不要编造。"
        if "[可见内容理解]" in conversation_text
        else "5. 不要假装看过链接或图片细节；看不清就让对方补充或说我确认下。"
    )
    return f"""当前聊天对象：{partner or "当前聊天联系人"}

最近聊天记录，越下面越新：
{conversation_text.strip() or "（没有识别到聊天内容）"}

当前需要回复的未回复轮次：
{target_text or latest_target or "（使用最后一轮未回复对方消息）"}

本轮最后一句（最高优先级）：
{latest_target or "（无）"}

{memory_block}

{search_block}

回复要求：
1. 整段上下文只作参考，对方发的内容优先级更高，对方最后一句优先级最高。
2. 如果对方有多个未回复问题，每个未回复的问题都要照顾到，并合并成一条自然回复。
3. 如果前面的问题我已经回复过，只参考背景，只回复最后一句。
{no_fabricate_rule}
{image_detail_rule}
6. 不要输出“理解了 / 帮你看看 / 怎么弄？”这类敷衍句。
{memory_rule}
8. {length_rule}
9. {source_rule}
{search_rule}
{visible_rule}

请输出 3 条候选，只要候选本身：
1. xxx
2. xxx
3. xxx"""


def _readable_generate_with_current_model(
    self: ReplyEngine,
    conversation_text: str,
    partner: str,
    target_turn: list[str] | None,
) -> tuple[list[str], int]:
    collected: list[str] = []
    call_count = 0
    prompt = self._build_prompt(conversation_text, partner, target_turn=target_turn)
    has_search_results = str(getattr(self, "last_search", "")).startswith("已联网检索")
    if has_search_results:
        system_content = (
            "你是微信回复建议器。只输出 3 条候选回复，每条一行并用 1/2/3 编号。"
            "必须优先回答当前未回复轮次和最后一句，不能复述对方原句。"
            "消息中已附实时参考信息：以参考信息为准给出明确回答（包括具体数字），"
            "不要回复“我再核实一下”这类没有实质信息的敷衍句。"
        )
    else:
        system_content = (
            "你是微信回复建议器。只输出 3 条候选回复，每条一行并用 1/2/3 编号。"
            "必须优先回答当前未回复轮次和最后一句，不能复述对方原句，不能编造事实、数字或效果数据。"
        )
    base_messages = [
        {
            "role": "system",
            "content": system_content,
        },
        {"role": "user", "content": prompt},
    ]

    for attempt in range(3):
        messages = base_messages if attempt == 0 else _refill_messages(base_messages, collected, 3 - len(collected))
        content = self._request_model_content(messages)
        call_count += 1
        if not (content or "").strip():
            self.last_error = (
                f"{provider_source_label(self.settings)} 返回里没有识别到文本，"
                "请检查接口、模型或自定义返回文本路径"
            )
            return [], call_count
        raw = parse_numbered_replies(content)
        raw = _drop_parrot_replies(raw, last_meaningful_other_message("\n".join(target_turn or []) or conversation_text))
        raw = self._quality_filter(raw, "\n".join(target_turn or []) or conversation_text)
        raw = _filter_context_echo_replies(raw, conversation_text)
        collected = _merge_unique_replies(collected, raw)
        normalized = _normalize_api_replies(collected)
        if len(normalized) >= 3:
            return normalized[:3], call_count

    normalized = _normalize_api_replies(collected)
    if normalized:
        return normalized[:3], call_count
    self.last_error = (
        f"{provider_source_label(self.settings)} 返回内容未通过质量过滤，"
        "可能是模型输出太敷衍、编造数据，或没有按编号返回候选"
    )
    self.last_source = provider_source_label(self.settings)
    return [], call_count


def _quality_filter_with_numeric_guard(self: ReplyEngine, replies: list[str], context: str) -> list[str]:
    filtered = _ORIGINAL_QUALITY_FILTER(self, replies, context)
    allowed = set(getattr(self, "last_search_numbers", set()) or set())
    return [reply for reply in filtered if not _reply_invents_numeric_data(reply, context, allowed)]


def _reply_invents_numeric_data(reply: str, context: str, allowed_numbers: set[str] | None = None) -> bool:
    if not _asks_for_numeric_or_effect_data(context):
        return False
    context_numbers = set(re.findall(r"\d+(?:\.\d+)?%?", context or ""))
    if allowed_numbers:
        # 搜索摘要里出现过的数字视为有据可依，不算编造
        context_numbers |= set(allowed_numbers)
    reply_numbers = set(re.findall(r"\d+(?:\.\d+)?%?", reply or ""))
    if reply_numbers - context_numbers:
        return True
    chinese_number_markers = ["几十", "几百", "几千", "上万", "几万", "十几", "百分之", "一半", "翻倍"]
    return any(marker in reply and marker not in context for marker in chinese_number_markers)


def _asks_for_numeric_or_effect_data(context: str) -> bool:
    text = context or ""
    question_words = ["多少", "几个", "几单", "多大", "多高", "数据", "订单", "转化", "比例", "金额", "效果", "销量", "能带来"]
    return any(word in text for word in question_words)


def _refill_messages(messages: list[dict[str, str]], collected: list[str], needed: int) -> list[dict[str, str]]:
    cloned = [dict(item) for item in messages]
    has_search = any("实时参考信息" in str(item.get("content", "")) for item in messages)
    numeric_rule = (
        "参考信息中有明确数字可直接采用，不要编造参考信息里没有的数字。"
        if has_search
        else "不要编造数字、金额、订单量、比例或效果数据。"
    )
    cloned.append(
        {
            "role": "user",
            "content": (
                f"上一轮只有 {len(collected)} 条可用回复，还需要补充 {needed} 条。"
                f"不要重复已有回复，不要敷衍，{numeric_rule}"
                "继续只输出编号候选。"
            ),
        }
    )
    return cloned


ReplyEngine._build_prompt = _readable_build_prompt
ReplyEngine._generate_with_current_model = _readable_generate_with_current_model
ReplyEngine._quality_filter = _quality_filter_with_numeric_guard
