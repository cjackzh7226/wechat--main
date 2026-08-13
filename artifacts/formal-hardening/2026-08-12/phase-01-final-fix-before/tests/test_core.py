import json
import os
import sys
import types
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import chat_training
import memory as memory_module
import settings as settings_module
from sop_analyzer import (
    analyze_chat_turns,
    analyze_chat_turns_with_llm,
    analyze_training_source_to_memory,
    build_sop_library_from_memory,
    ensure_sop_library_flow,
    format_sop_document,
    format_sop_library_html,
    format_sop_library_document,
    sop_document_from_memory,
)
from chat_training import (
    extract_phrasebook_examples,
    extract_reply_examples,
    import_chat_zip_to_memory,
    import_chat_zip_to_memory_with_llm,
    parse_ab_chat_text,
    parse_message_rows,
    _iter_export_files,
    _ensure_package_import,
)
from reply_humanize import humanize_reply, normalize_replies
from memory import StyleMemory
from reply_engine import (
    ReplyEngine,
    auto_reply_decision,
    classify_intent,
    last_meaningful_other_message,
    latest_meaningful_message,
    parse_numbered_replies,
    select_best_reply_for_context,
    unreplied_other_messages,
)
from settings import AppSettings
from llm_clients import build_text_generation_request, extract_text_from_response, supported_provider_help
from main import (
    _capture_chat_image_without_overlay,
    _current_unreplied_turn,
    _managed_context_allows_generation,
    _managed_api_failure_blocks_send,
    _managed_reply_gate_allows_generation,
    _managed_turn_signature,
    _should_learn_observed_chat,
    _should_auto_generate_for_context,
    _generation_result_still_current,
    _managed_generation_result_still_current,
    _should_learn_sent_reply,
    _choose_target_wechat_window,
    _contexts_are_same_or_similar,
    _single_wechat_message,
    _snap_geometry_if_changed,
    _should_clear_replies_for_skip,
    _should_skip_unchanged_context,
    _visible_skip_should_block_reply,
    _turn_has_real_new_message,
    _turn_key,
    _turns_are_same_or_similar,
)
from capture_ocr import ChatOCR, _detect_chat_bottom_boundary, _is_noise_text
from scenario_bank import prompt_examples_for_intent, replies_for_intent, seed_count
from sender import WeChatSender
from visible_content import VisibleContent, VisibleContentAnalyzer, build_augmented_context, infer_visible_content_from_context
from wechat_window import ChatPlatform, ChatWindow, WeChatWindowDetector, WindowRect


def _enterprise_history_lookup_examples(history_reply: str) -> list[dict[str, str]]:
    return [
        {
            "source": "enterprise_annual_report_kb",
            "question": "往年年度工作报告如何查看？",
            "cue": "企业年报 / 年检 / 填报 / 往年年度工作报告如何查看？",
            "reply": history_reply,
            "module": "年检",
            "subcategory": "填报",
            "source_cell": "C11:D11",
        },
        {
            "source": "enterprise_annual_report_kb",
            "question": "存在“从年检入口进入填写保存下一步，提示但本年度年度工作报告已存在”的问题该如何解决？",
            "cue": "企业年报 / 年检 / 填报 / 本年度年度工作报告已存在的问题该如何解决？",
            "reply": "在已生成本年度年度工作报告的操作栏点“修改”填写。",
            "module": "年检",
            "subcategory": "填报",
            "source_cell": "C15:D15",
        },
        {
            "source": "enterprise_annual_report_kb",
            "question": "存在“年度工作报告无法直接打印”的问题该如何解决？",
            "cue": "企业年报 / 年检 / 填报 / 年度工作报告无法直接打印的问题该如何解决？",
            "reply": "导出 PDF 后再打印。",
            "module": "年检",
            "subcategory": "填报",
            "source_cell": "C10:D10",
        },
    ]


def _minimal_docx_bytes(text: str) -> bytes:
    from io import BytesIO

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        body = "".join(f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in text.splitlines())
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body>{body}</w:body></w:document>",
        )
    return buffer.getvalue()


def _minimal_xlsx_bytes(lines: list[str]) -> bytes:
    from io import BytesIO

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        shared = "".join(f"<si><t>{line}</t></si>" for line in lines)
        rows = "".join(
            f'<row r="{index}"><c r="A{index}" t="s"><v>{index - 1}</v></c></row>'
            for index, _line in enumerate(lines, 1)
        )
        zf.writestr(
            "xl/sharedStrings.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"{shared}</sst>",
        )
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"<sheetData>{rows}</sheetData></worksheet>",
        )
    return buffer.getvalue()


def _enterprise_annual_report_xlsx_bytes(social_login_reply: str = "请使用社会组织法人账号登录年度报告系统。") -> bytes:
    from io import BytesIO
    from xml.sax.saxutils import escape

    values = [
        "模块",
        "社会团体",
        "民办非企业单位",
        "年检",
        "登录",
        f"问题：年度报告从哪里登录？\n答：{social_login_reply}",
        "问题：民办非企业单位如何登录？\n答：请使用已关联组织的法人账号登录。",
        "问题二：忘记密码怎么办？\n答：请在登录页选择忘记密码后按提示找回。",
    ]

    def cell(address: str, index: int) -> str:
        return f'<c r="{address}" t="s"><v>{index}</v></c>'

    rows = "".join(
        [
            '<row r="1">' + cell("A1", 0) + cell("C1", 1) + cell("D1", 2) + "</row>",
            '<row r="2">' + cell("A2", 3) + cell("B2", 4) + cell("C2", 5) + cell("D2", 6) + "</row>",
            '<row r="3">' + cell("C3", 7) + "</row>",
        ]
    )
    shared = "".join(f"<si><t>{escape(value)}</t></si>" for value in values)
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(
            "xl/sharedStrings.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"{shared}</sst>",
        )
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="年报问答" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet1.xml"/>'
            '</Relationships>',
        )
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"<sheetData>{rows}</sheetData>"
            '<mergeCells count="2"><mergeCell ref="A2:A3"/><mergeCell ref="B2:B3"/></mergeCells>'
            "</worksheet>",
        )
    return buffer.getvalue()


def _enterprise_annual_report_merged_org_xlsx_bytes() -> bytes:
    from io import BytesIO

    source = BytesIO(_enterprise_annual_report_xlsx_bytes())
    target = BytesIO()
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w") as rewritten:
        for info in original.infolist():
            content = original.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                text = content.decode("utf-8")
                text = text.replace('<c r="D2" t="s"><v>6</v></c>', "")
                text = text.replace(
                    '<mergeCells count="2"><mergeCell ref="A2:A3"/><mergeCell ref="B2:B3"/></mergeCells>',
                    '<mergeCells count="3"><mergeCell ref="A2:A3"/><mergeCell ref="B2:B3"/><mergeCell ref="C2:D2"/></mergeCells>',
                )
                content = text.encode("utf-8")
            rewritten.writestr(info, content)
    return target.getvalue()


class HumanizeTests(unittest.TestCase):
    def test_humanize_removes_common_ai_phrases_and_shortens(self):
        text = "好的，收到，我会尽快处理这个事情，并且第一时间同步给你。"

        result = humanize_reply(text)

        self.assertNotIn("第一时间", result)
        self.assertNotIn("尽快处理", result)
        self.assertLessEqual(len(result), 24)

    def test_normalize_replies_returns_three_unique_items(self):
        raw = [
            "好的，收到，我会尽快处理这个事情。",
            "好的，收到，我会尽快处理这个事情。",
            "没问题，我晚点看下。",
        ]

        result = normalize_replies(raw)

        self.assertEqual(len(result), 3)
        self.assertEqual(len(set(result)), 3)


class MemoryTests(unittest.TestCase):
    def test_enterprise_candidates_rank_fuzzy_historical_lookup_above_current_year_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = StyleMemory(Path(tmp) / "memory.json")
            memory.learn_from_training_examples(_enterprise_history_lookup_examples("历史报告请到公示页面查询。"))

            candidates = memory.enterprise_candidates("怎么查以前的年度报告", limit=3)

            self.assertEqual(candidates[0]["source_cell"], "C11:D11")
            self.assertEqual(candidates[0]["reply"], "历史报告请到公示页面查询。")
            self.assertEqual(candidates[0]["enterprise_local_confident"], "1")

    def test_enterprise_phrasebook_test_uses_the_same_top_candidate_as_runtime(self):
        import main

        with tempfile.TemporaryDirectory() as tmp:
            memory = StyleMemory(Path(tmp) / "memory.json")
            memory.learn_from_training_examples(_enterprise_history_lookup_examples("历史报告请到公示页面查询。"))
            app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
            app.memory = memory

            runtime = memory.enterprise_candidates("怎么查以前的年度报告", limit=1)
            display = app._get_phrasebook_examples("enterprise_annual_report_kb", "怎么查以前的年度报告")

            self.assertEqual(runtime[0]["source_cell"], "C11:D11")
            self.assertEqual(display[0]["source_cell"], runtime[0]["source_cell"])
            self.assertEqual(display[0]["score"], runtime[0]["enterprise_local_score"])

    def test_enterprise_decision_accepts_clear_margin_below_legacy_76_gate(self):
        import main

        with tempfile.TemporaryDirectory() as tmp:
            memory = StyleMemory(Path(tmp) / "memory.json")
            memory.learn_from_training_examples(_enterprise_history_lookup_examples("历史报告请到公示页面查询。"))
            candidates = memory.enterprise_candidates("想查一下之前的年报", limit=3)
            app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
            app.memory = memory

            self.assertLess(int(candidates[0]["enterprise_local_score"]), 76)
            self.assertEqual(candidates[0]["enterprise_local_confident"], "1")
            self.assertEqual(candidates[0]["enterprise_decision_label"], "可直接发送")

            display = app._get_phrasebook_examples("enterprise_annual_report_kb", "想查一下之前的年报")

            self.assertEqual(display[0]["enterprise_decision_label"], "可直接发送")
            self.assertEqual(display[0]["enterprise_decision"], "send")

    def test_enterprise_semantic_scope_without_annual_report_word_returns_official_answer(self):
        settings = AppSettings(api_key="configured", reply_source_mode="model")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        memory = StyleMemory(Path(tmp.name) / "memory.json")
        history_answer = "历史报告请到公示页面查询。"
        memory.learn_from_training_examples(_enterprise_history_lookup_examples(history_answer))
        engine = ReplyEngine(settings, memory)

        # 命中后交大模型一致性判断：一致才发送官方答案
        with patch("reply_engine.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = {"choices": [{"message": {"content": '{"chosen": 1, "consistent": true}'}}]}
            result = engine.generate("[对方] 之前那个报告怎么看", "客户")

        self.assertEqual(result, [history_answer])
        self.assertIn("C11:D11", engine.last_source)
        self.assertIn("大模型确认一致", engine.last_source)

    def test_save_keeps_previous_memory_in_managed_backup(self):
        """每次覆盖前由统一原子写入层保留上一份有效正文。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        memory = StyleMemory(Path(tmp.name) / "memory.json")

        # 磁盘先写入 60 条知识库
        big_examples = []
        for i in range(60):
            big_examples.append(
                {
                    "question": f"测试问题{i}",
                    "cue": f"[对方] 测试问题{i}",
                    "reply": f"测试回答{i}",
                    "source": "enterprise_annual_report_kb",
                    "conversation_hash": f"hash-{i}",
                }
            )
        memory.examples = big_examples
        memory.save()
        self.assertEqual(len(memory.load().examples), 60)

        # 模拟程序 load 到旧/空数据（只有 3 条）后 save
        memory2 = StyleMemory(Path(tmp.name) / "memory.json")
        memory2.load()
        memory2.examples = [
            {"question": "", "cue": "[对方] 测试", "reply": "测试", "source": "chat_zip_training", "conversation_hash": "x"}
        ] * 3
        memory2.save()

        # 统一原子写入层应把覆盖前正文放入最新的有效备份槽位。
        backup_path = Path(tmp.name) / "memory.json.bak.1"
        self.assertTrue(backup_path.exists(), "应生成托管备份")
        backup = json.loads(backup_path.read_text(encoding="utf-8"))
        self.assertEqual(len(backup.get("examples", [])), 60, "备份应包含 60 条知识库")

    def test_enterprise_ambiguous_runtime_and_phrasebook_both_refuse_to_send(self):
        import main

        settings = AppSettings(api_key="configured", reply_source_mode="model_memory")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        memory = StyleMemory(Path(tmp.name) / "memory.json")
        memory.learn_from_training_examples(_enterprise_history_lookup_examples("历史报告请到公示页面查询。"))
        engine = ReplyEngine(settings, memory)

        with patch("reply_engine.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.raise_for_status.return_value = None
            # 大模型先选再判：并列候选中选出第 1 条，判定一致 → 发送被选中答案
            post.return_value.json.return_value = {"choices": [{"message": {"content": '{"chosen": 1, "consistent": true}'}}]}
            result = engine.generate("[对方] 年度工作报告在哪里找", "客户")

        app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
        app.memory = memory
        display = app._get_phrasebook_examples("enterprise_annual_report_kb", "年度工作报告在哪里找")

        # 测试页与运行时共用 tie 决策（显示"并列待裁决"）；运行时交大模型先选再判，
        # 一致 → 发送被选中候选（不再是直接返回并列候选列表）。
        self.assertEqual(len(result), 1)
        self.assertIn("大模型确认一致", engine.last_source)
        self.assertEqual(display[0]["enterprise_decision_label"], "并列待裁决")
        self.assertEqual(display[0]["enterprise_decision"], "tie")

    def test_enterprise_imperative_request_ambiguous_returns_no_answer_without_api(self):
        settings = AppSettings(api_key="configured", reply_source_mode="model_memory")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        memory = StyleMemory(Path(tmp.name) / "memory.json")
        memory.learn_from_training_examples(_enterprise_history_lookup_examples("历史报告请到公示页面查询。"))
        engine = ReplyEngine(settings, memory)

        with patch("reply_engine.requests.post") as post:
            result = engine.generate("[对方] 帮我查一下年检时间", "客户")

        # 23 分 < 30（新低分阈值）：命中评分不足 → 直接回企业未命中文案，不调模型
        self.assertEqual(result, ["未在当前企业年报知识库中找到对应答案。"])
        self.assertIn("未命中", engine.last_source)
        post.assert_not_called()

    def test_llm_import_requires_api_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = StyleMemory(Path(tmp) / "memory.json")
            with self.assertRaises(RuntimeError) as ctx:
                import_chat_zip_to_memory_with_llm(Path(tmp) / "x.zip", memory, None)
            self.assertIn("API Key", str(ctx.exception))

    def test_llm_import_extracts_qa_pairs(self):
        settings = AppSettings(api_key="configured", reply_source_mode="model_memory")
        settings.enable_web_search = False
        with tempfile.TemporaryDirectory() as tmp:
            memory = StyleMemory(Path(tmp) / "memory.json")
            doc_path = Path(tmp) / "话术表.txt"
            doc_path.write_text("问题：年报在哪里查？\n答：在年度报告公示系统查询。\n问题：报告怎么看？\n答：在信息公开栏查看。", encoding="utf-8")

            with patch("chat_training.requests.post") as post:
                post.return_value.status_code = 200
                post.return_value.raise_for_status.return_value = None
                post.return_value.json.return_value = {
                    "choices": [{"message": {"content": '[{"question": "年报在哪里查？", "reply": "在年度报告公示系统查询。", "module": "查询"}, {"question": "报告怎么看？", "reply": "在信息公开栏查看。"}]'}}]
                }
                stats = import_chat_zip_to_memory_with_llm(doc_path, memory, settings)

            self.assertEqual(stats["examples"], 2)
            loaded = memory.load()
            self.assertEqual(len(loaded.examples), 2)
            replies = {e.get("reply", "") for e in loaded.examples}
            self.assertIn("在年度报告公示系统查询。", replies)
            self.assertIn("在信息公开栏查看。", replies)
            # 所有导入问答统一归入企业年报总库
            self.assertTrue(all(e.get("source") == "enterprise_annual_report_kb" for e in loaded.examples))

    def test_llm_import_accumulates_without_clearing_old_entries(self):
        # 话术库叠加：第二次导入不清空第一次的条目
        settings = AppSettings(api_key="configured", reply_source_mode="model_memory")
        settings.enable_web_search = False
        with tempfile.TemporaryDirectory() as tmp:
            memory = StyleMemory(Path(tmp) / "memory.json")
            doc_a = Path(tmp) / "a.txt"
            doc_a.write_text("问题：A\n答：AAA", encoding="utf-8")
            doc_b = Path(tmp) / "b.txt"
            doc_b.write_text("问题：B\n答：BBB", encoding="utf-8")

            with patch("chat_training.requests.post") as post:
                post.return_value.status_code = 200
                post.return_value.raise_for_status.return_value = None
                post.return_value.json.side_effect = [
                    {"choices": [{"message": {"content": '[{"question": "A", "reply": "AAA"}]'}}]},
                    {"choices": [{"message": {"content": '[{"question": "B", "reply": "BBB"}]'}}]},
                ]
                import_chat_zip_to_memory_with_llm(doc_a, memory, settings)
                import_chat_zip_to_memory_with_llm(doc_b, memory, settings)

            loaded = memory.load()
            self.assertEqual(len(loaded.examples), 2)
            replies = {e.get("reply", "") for e in loaded.examples}
            self.assertIn("AAA", replies)
            self.assertIn("BBB", replies)

    def test_llm_import_reports_progress_by_chunk(self):
        settings = AppSettings(api_key="configured", reply_source_mode="model_memory")
        settings.enable_web_search = False
        with tempfile.TemporaryDirectory() as tmp:
            memory = StyleMemory(Path(tmp) / "memory.json")
            doc_path = Path(tmp) / "a.txt"
            doc_path.write_text("问题：A\n答：AAA", encoding="utf-8")
            progress_calls: list[tuple[int, int]] = []

            with patch("chat_training.requests.post") as post:
                post.return_value.status_code = 200
                post.return_value.raise_for_status.return_value = None
                post.return_value.json.return_value = {
                    "choices": [{"message": {"content": '[{"question": "A", "reply": "AAA"}]'}}]
                }
                import_chat_zip_to_memory_with_llm(doc_path, memory, settings, progress=lambda done, total: progress_calls.append((done, total)))

            self.assertTrue(progress_calls)
            last_done, last_total = progress_calls[-1]
            self.assertEqual(last_done, last_total)
            self.assertGreaterEqual(last_total, 1)

    def test_org_type_tags_applied_to_cue(self):
        from chat_training import _apply_org_type_tags

        # 含"民办非企业单位" → 追加 / 民非
        item = {"question": "民非单位的年检要求是什么？", "cue": "[对方] 民非单位的年检要求是什么？"}
        _apply_org_type_tags(item)
        self.assertEqual(item["cue"], "[对方] 民非单位的年检要求是什么？ / 民非")

        # 含"社会团体" → 追加 / 社团
        item = {"question": "社会团体会员数量如何设置？", "cue": "[对方] 社会团体会员数量如何设置？"}
        _apply_org_type_tags(item)
        self.assertEqual(item["cue"], "[对方] 社会团体会员数量如何设置？ / 社团")

        # 两者都含 → / 民非 / 社团
        item = {"question": "社会团体、民办非企业单位的法定代表人任职有哪些规定？", "cue": "[对方] 社会团体、民办非企业单位的法定代表人任职有哪些规定？"}
        _apply_org_type_tags(item)
        self.assertIn("/ 民非", item["cue"])
        self.assertIn("/ 社团", item["cue"])

        # 已有标签不重复追加
        _apply_org_type_tags(item)
        self.assertEqual(item["cue"].count("民非"), 1)
        self.assertEqual(item["cue"].count("社团"), 1)

        # 回答里含明确条例关键词 → 补标签（cue 无机构类型字样时也能判定）
        item = {"question": "净资产少于注册资金怎么办？", "cue": "[对方] 净资产少于注册资金怎么办？", "reply": "…法律依据：《民办非企业单位登记管理暂行条例》第八条规定…"}
        _apply_org_type_tags(item)
        self.assertIn("/ 民非", item["cue"])
        item = {"question": "当年度未开展活动怎么办？", "cue": "[对方] 当年度未开展活动怎么办？", "reply": "…《社会团体年度检查暂行办法》第十一条规定…"}
        _apply_org_type_tags(item)
        self.assertIn("/ 社团", item["cue"])

        # 模糊推断（"常务理事会"等）不算明确信号：不因回答提到常务理事会而加标签
        item = {"question": "示例问题", "cue": "[对方] 示例问题", "reply": "召开理事会或常务理事会会议选举…"}
        _apply_org_type_tags(item)
        self.assertNotIn("/ 民非", item["cue"])
        self.assertNotIn("/ 社团", item["cue"])

    def test_llm_import_applies_org_type_tags(self):
        settings = AppSettings(api_key="configured", reply_source_mode="model_memory")
        settings.enable_web_search = False
        with tempfile.TemporaryDirectory() as tmp:
            memory = StyleMemory(Path(tmp) / "memory.json")
            doc_path = Path(tmp) / "a.txt"
            doc_path.write_text("问题：社会团体年检怎么报？\n答：登录系统填报。", encoding="utf-8")

            with patch("chat_training.requests.post") as post:
                post.return_value.status_code = 200
                post.return_value.raise_for_status.return_value = None
                post.return_value.json.return_value = {
                    "choices": [{"message": {"content": '[{"question": "社会团体年检怎么报？", "reply": "登录系统填报。"}]'}}]
                }
                import_chat_zip_to_memory_with_llm(doc_path, memory, settings)

            loaded = memory.load()
            self.assertEqual(len(loaded.examples), 1)
            self.assertIn("/ 社团", loaded.examples[0]["cue"])

    def test_llm_import_enterprise_workbook_marks_source(self):
        settings = AppSettings(api_key="configured", reply_source_mode="model_memory")
        settings.enable_web_search = False
        with tempfile.TemporaryDirectory() as tmp:
            memory = StyleMemory(Path(tmp) / "memory.json")
            doc_path = Path(tmp) / "年报表.txt"
            doc_path.write_text("机构类型：社会团体\n问题：年检怎么填报？\n答：登录系统后选择年检填报。", encoding="utf-8")

            with patch("chat_training.requests.post") as post:
                post.return_value.status_code = 200
                post.return_value.raise_for_status.return_value = None
                post.return_value.json.return_value = {
                    "choices": [{"message": {"content": '[{"question": "年检怎么填报？", "reply": "登录系统后选择年检填报。", "module": "填报", "organization_type": "社会团体"}]'}}]
                }
                stats = import_chat_zip_to_memory_with_llm(doc_path, memory, settings)

            self.assertEqual(stats["examples"], 1)
            loaded = memory.load()
            self.assertEqual(loaded.examples[0]["source"], "enterprise_annual_report_kb")
            self.assertEqual(loaded.examples[0]["module"], "填报")

    def test_llm_import_raises_on_invalid_json(self):
        settings = AppSettings(api_key="configured", reply_source_mode="model_memory")
        settings.enable_web_search = False
        with tempfile.TemporaryDirectory() as tmp:
            memory = StyleMemory(Path(tmp) / "memory.json")
            doc_path = Path(tmp) / "a.txt"
            doc_path.write_text("问题：A\n答：B", encoding="utf-8")

            with patch("chat_training.requests.post") as post:
                post.return_value.status_code = 200
                post.return_value.raise_for_status.return_value = None
                post.return_value.json.return_value = {"choices": [{"message": {"content": "我不是JSON"}}]}
                with self.assertRaises(RuntimeError) as ctx:
                    import_chat_zip_to_memory_with_llm(doc_path, memory, settings)
            self.assertIn("JSON", str(ctx.exception))

    def test_enterprise_candidates_recall_similar_login_question_with_source_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = StyleMemory(Path(tmp) / "memory.json")
            memory.learn_from_training_examples(
                [
                    {
                        "source": "enterprise_annual_report_kb",
                        "question": "年度报告从哪里登录？",
                        "cue": "企业年报 / 社会团体 / 年检 / 登录 / 年度报告从哪里登录？",
                        "reply": "请使用社会组织法人账号登录年度报告系统。",
                        "module": "年检",
                        "organization_type": "社会团体",
                        "source_cell": "C2",
                    },
                    {
                        "source": "enterprise_annual_report_kb",
                        "question": "忘记密码怎么办？",
                        "cue": "企业年报 / 社会团体 / 年检 / 登录 / 忘记密码怎么办？",
                        "reply": "请在登录页选择忘记密码后按提示找回。",
                        "module": "年检",
                        "organization_type": "社会团体",
                        "source_cell": "C3",
                    },
                ]
            )

            candidates = memory.enterprise_candidates("[对方] 年度工作报告的登录入口在哪？")

            self.assertEqual(candidates[0]["question"], "年度报告从哪里登录？")
            self.assertEqual(candidates[0]["source_cell"], "C2")
            self.assertGreater(candidates[0]["enterprise_local_score"], 0)

    def test_pdf_text_extracts_pages(self):
        from chat_training import _file_to_plain_text, _pdf_text

        fake_page = types.SimpleNamespace(extract_text=lambda: "问题：年报在哪查？\n答：在公示系统查询。")
        fake_reader = types.SimpleNamespace(pages=[fake_page, fake_page])

        with patch("pypdf.PdfReader") as pdf_reader_cls:
            pdf_reader_cls.return_value = fake_reader
            text = _pdf_text(b"%PDF-fake-bytes")

        self.assertIn("年报在哪查", text)
        self.assertIn("公示系统查询", text)

        with patch("pypdf.PdfReader") as pdf_reader_cls:
            pdf_reader_cls.return_value = fake_reader
            plain = _file_to_plain_text("知识库.pdf", b"%PDF-fake-bytes")
        self.assertIn("年报在哪查", plain)

    def test_llm_import_pdf_file(self):
        settings = AppSettings(api_key="configured", reply_source_mode="model_memory")
        settings.enable_web_search = False
        with tempfile.TemporaryDirectory() as tmp:
            memory = StyleMemory(Path(tmp) / "memory.json")
            pdf_path = Path(tmp) / "问答知识库.pdf"
            pdf_path.write_bytes(b"%PDF-fake")

            with patch("pypdf.PdfReader") as pdf_reader_cls:
                fake_page = types.SimpleNamespace(extract_text=lambda: "问题：年报怎么查？\n答：在公示系统输入名称查询。")
                pdf_reader_cls.return_value = types.SimpleNamespace(pages=[fake_page])
                with patch("chat_training.requests.post") as post:
                    post.return_value.status_code = 200
                    post.return_value.raise_for_status.return_value = None
                    post.return_value.json.return_value = {
                        "choices": [{"message": {"content": '[{"question": "年报怎么查？", "reply": "在公示系统输入名称查询。"}]'}}]
                    }
                    stats = import_chat_zip_to_memory_with_llm(pdf_path, memory, settings)

            self.assertEqual(stats["files"], 1)
            self.assertEqual(stats["examples"], 1)
            loaded = memory.load()
            self.assertIn("公示系统输入名称查询", loaded.examples[0]["reply"])

    def test_llm_import_reports_unsupported_formats(self):
        settings = AppSettings(api_key="configured", reply_source_mode="model_memory")
        settings.enable_web_search = False
        with tempfile.TemporaryDirectory() as tmp:
            memory = StyleMemory(Path(tmp) / "memory.json")
            png_path = Path(tmp) / "截图.png"
            png_path.write_bytes(b"\x89PNG-fake")
            with self.assertRaises(RuntimeError) as ctx:
                import_chat_zip_to_memory_with_llm(png_path, memory, settings)
            self.assertIn("支持的文档", str(ctx.exception))
            self.assertIn("PDF", str(ctx.exception))

    def test_enterprise_exact_question_outranks_overlapping_later_questions(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = StyleMemory(Path(tmp) / "memory.json")
            memory.learn_from_training_examples(
                [
                    {
                        "source": "enterprise_annual_report_kb",
                        "question": "往年年度工作报告如何查看？",
                        "cue": "企业年报 / 年检 / 填报 / 往年年度工作报告如何查看？",
                        "reply": "打开年度报告公示，输入社会组织名称查询。",
                        "source_cell": "C11:D11",
                    },
                    {
                        "source": "enterprise_annual_report_kb",
                        "question": "存在本年度年度工作报告已存在的问题该如何解决？",
                        "cue": "企业年报 / 年检 / 填报 / 本年度年度工作报告已存在的问题该如何解决？",
                        "reply": "在已生成本年度年度工作报告的操作栏点修改填写。",
                        "source_cell": "C15:D15",
                    },
                    {
                        "source": "enterprise_annual_report_kb",
                        "question": "年度工作报告无法直接打印如何解决？",
                        "cue": "企业年报 / 年检 / 填报 / 年度工作报告无法直接打印如何解决？",
                        "reply": "导出 PDF 后再打印。",
                        "source_cell": "C10:D10",
                    },
                ]
            )

            candidates = memory.enterprise_candidates("往年年度工作报告如何查看", limit=3)

            self.assertEqual(candidates[0]["source_cell"], "C11:D11")
            self.assertEqual(candidates[0]["reply"], "打开年度报告公示，输入社会组织名称查询。")

    def test_enterprise_answers_do_not_leak_into_generic_model_memory_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = StyleMemory(Path(tmp) / "memory.json")
            memory.learn_from_training_examples(
                [
                    {
                        "source": "chat_zip_training",
                        "cue": "[对方] 普通咨询",
                        "reply": "我先看看具体情况。",
                    },
                    {
                        "source": "enterprise_annual_report_kb",
                        "question": "年度报告从哪里登录？",
                        "cue": "企业年报 / 年检 / 登录 / 年度报告从哪里登录？",
                        "reply": "年报密钥。",
                    },
                ]
            )

            prompt = memory.prompt_block("[对方] 普通咨询", exclude_sources={"enterprise_annual_report_kb"})

            self.assertIn("我先看看具体情况", prompt)
            self.assertNotIn("年报密钥", prompt)

    def test_sop_analyzer_groups_scenes_and_reply_hit_rates(self):
        turns = parse_ab_chat_text(
            "\n".join(
                [
                    "A: 这个放哪里比较好",
                    "B: 放客厅干净的位置就行，别放太低",
                    "A: 这个应该怎么摆放",
                    "B: 放客厅干净的位置就行，别放太低",
                    "A: 怎么领取资料",
                    "B: 我发你看下，按步骤来就行",
                ]
            )
        )

        report = analyze_chat_turns(turns)

        self.assertEqual(report["total_customer_turns"], 3)
        scenes = {item["scene"]: item for item in report["scenes"]}
        self.assertIn("放置/使用方式", scenes)
        self.assertEqual(scenes["放置/使用方式"]["count"], 2)
        rule = scenes["放置/使用方式"]["rules"][0]
        self.assertEqual(rule["reply"], "放客厅干净的位置就行，别放太低")
        self.assertEqual(rule["count"], 2)
        self.assertEqual(rule["hit_rate"], 100.0)
        self.assertTrue(report["sop_steps"])

    def test_sop_analysis_import_writes_phrasebook_memory_examples(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "chat.txt"
            memory_path = root / "memory.json"
            source.write_text(
                "\n".join(
                    [
                        "A: 这个放哪里比较好",
                        "B: 放客厅干净的位置就行，别放太低",
                        "A: 这个应该怎么摆放",
                        "B: 放客厅干净的位置就行，别放太低",
                    ]
                ),
                encoding="utf-8",
            )

            report = analyze_training_source_to_memory(source, StyleMemory(memory_path))
            loaded = StyleMemory(memory_path).load()

            self.assertEqual(report["files"], 1)
            self.assertEqual(report["examples"], 1)
            self.assertIn("放置/使用方式", report["sop_steps"][0])
            self.assertEqual(loaded.examples[-1]["source"], "sop_analysis_training")
            self.assertEqual(loaded.examples[-1]["scenario_title"], "放置/使用方式")
            self.assertIn("命中率 100.0%", loaded.examples[-1]["why"])

    def test_sop_report_formats_as_readable_document(self):
        turns = parse_ab_chat_text(
            "\n".join(
                [
                    "A: 这个放哪里比较好",
                    "B: 放客厅干净的位置就行，别放太低",
                    "A: 怎么领取资料",
                    "B: 我发你看下，按步骤来就行",
                ]
            )
        )
        report = analyze_chat_turns(turns)

        document = format_sop_document(report)

        self.assertIn("聊小智 SOP 分析报告", document)
        self.assertIn("一、分析概览", document)
        self.assertIn("二、场景与命中话术", document)
        self.assertIn("三、建议 SOP 流程", document)
        self.assertIn("放置/使用方式", document)
        self.assertIn("命中率", document)

    def test_sop_document_can_be_opened_from_existing_phrasebook_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = StyleMemory(Path(tmp) / "memory.json")
            memory.learn_from_training_examples(
                [
                    {
                        "partner": "SOP话术分析",
                        "cue": "这个放哪里比较好\n这个应该怎么摆放",
                        "reply": "放客厅干净的位置就行，别放太低",
                        "source": "sop_analysis_training",
                        "scenario_title": "放置/使用方式",
                        "why": "场景 放置/使用方式，出现 2 次；该回复命中 2 次，命中率 100.0%。",
                        "priority": "95",
                    }
                ]
            )

            document = sop_document_from_memory(StyleMemory(memory.path).load())

            self.assertIn("聊小智 SOP 分析报告", document)
            self.assertIn("放置/使用方式", document)
            self.assertIn("放客厅干净的位置", document)
            self.assertIn("命中率 100.0%", document)

    def test_sop_library_can_be_generated_from_existing_phrasebook_without_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = StyleMemory(Path(tmp) / "memory.json")
            memory.learn_from_training_examples(
                [
                    {
                        "partner": "聊天记录训练",
                        "cue": "[对方] 你好，想了解霸王餐怎么上架\n[我] 您是想做店套餐还是霸王餐引流\n[对方] 霸王餐需要什么资料",
                        "reply": "您把商家名称、门店地址、套餐内容和活动要求发我，我先帮您整理上架",
                        "source": "chat_zip_training",
                    },
                    {
                        "partner": "聊天记录训练",
                        "cue": "[对方] 这个订单怎么查核销\n[我] 您把订单号发我\n[对方] 我发你了",
                        "reply": "我这边帮您查一下核销状态，查到马上回您",
                        "source": "chat_zip_training",
                    },
                ]
            )

            library = build_sop_library_from_memory(StyleMemory(memory.path).load())
            document = format_sop_library_document(library)

            self.assertEqual(library["source"], "current_phrasebook")
            self.assertGreaterEqual(len(library["scenes"]), 2)
            self.assertTrue(library["sop_steps"])
            self.assertIn("霸王餐", document)
            self.assertIn("核销", document)
            self.assertIn("推荐置信度", document)

    def test_sop_library_keeps_dynamic_scene_names_but_orders_by_customer_journey(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = StyleMemory(Path(tmp) / "memory.json")
            memory.learn_from_training_examples(
                [
                    {
                        "partner": "chat",
                        "cue": "[对方] 我刚添加你了，现在可以开始聊了吗",
                        "reply": "可以的，您这边主要想了解哪方面",
                        "source": "chat_zip_training",
                        "scenario_title": "刚添加后客户问能不能开始聊",
                    },
                    {
                        "partner": "chat",
                        "cue": "[对方] 我已经付款了，后面怎么安排",
                        "reply": "收到，我这边给您登记，安排好后同步给您",
                        "source": "chat_zip_training",
                        "scenario_title": "客户付款后询问后续安排",
                    },
                    {
                        "partner": "chat",
                        "cue": "[对方] 付款截图发你了",
                        "reply": "收到了，我先帮您核对信息",
                        "source": "chat_zip_training",
                        "scenario_title": "客户付款后询问后续安排",
                    },
                    {
                        "partner": "chat",
                        "cue": "[对方] 这个到期以后怎么处理",
                        "reply": "到期后您可以联系我，我帮您看具体处理方式",
                        "source": "chat_zip_training",
                        "scenario_title": "客户问到期后的处理方式",
                    },
                ]
            )

            library = build_sop_library_from_memory(StyleMemory(memory.path).load())
            names = [scene["scene"] for scene in library["scenes"]]

            self.assertEqual(names[0], "刚添加后客户问能不能开始聊")
            self.assertIn("客户付款后询问后续安排", names)
            self.assertIn("客户问到期后的处理方式", names)
            self.assertNotIn("客户进线", names)
            self.assertIn("我刚添加你了", library["sop_steps"][0])

    def test_sop_library_html_report_uses_flow_view(self):
        library = {
            "total_examples": 3,
            "scenes": [
                {
                    "scene": "客户付款后询问后续安排",
                    "count": 2,
                    "customer_examples": ["我已经付款了，后面怎么安排"],
                    "rules": [{"reply": "收到，我这边给您登记", "count": 2, "hit_rate": 100}],
                },
                {
                    "scene": "刚添加后客户问能不能开始聊",
                    "count": 1,
                    "customer_examples": ["我刚添加你了，现在可以开始聊了吗"],
                    "rules": [{"reply": "可以的，您这边主要想了解哪方面", "count": 1, "hit_rate": 100}],
                },
            ],
        }

        html = format_sop_library_html(library)

        self.assertIn("话术流程分析演示", html)
        self.assertIn("客户流程", html)
        self.assertIn("从进线到成交的主流程", html)
        self.assertLess(html.index("刚添加后客户问能不能开始聊"), html.index("客户付款后询问后续安排"))

    def test_old_sop_scene_library_is_upgraded_into_journey_flow(self):
        library = {
            "total_examples": 20,
            "scenes": [
                {
                    "scene": "客户说：我已经添加了你，现在我们可以开始聊天了。",
                    "count": 20,
                    "customer_examples": [
                        "我已经添加了你，现在我们可以开始聊天了。",
                        "师傅，您好，这个福宝刚刚收到，请问该怎么使用？",
                        "赵振伏，1975年6月21日",
                        "我已经付款了，后面怎么安排？",
                        "请问钱母是放置到手机后面么？",
                    ],
                    "rules": [
                        {"reply": "好的师兄，劳您留一下名字和生日", "count": 8, "hit_rate": 40},
                        {"reply": "我先帮您登记安排", "count": 5, "hit_rate": 25},
                    ],
                }
            ],
        }

        upgraded = ensure_sop_library_flow(library)
        flow_nodes = upgraded["flow_nodes"]
        joined = json.dumps(flow_nodes, ensure_ascii=False)

        self.assertGreaterEqual(len(flow_nodes), 4)
        self.assertIn("添加", joined)
        self.assertIn("生日", joined)
        self.assertIn("付款", joined)
        self.assertIn("钱母", joined)
        self.assertLess(flow_nodes[0]["flow_order"], flow_nodes[-1]["flow_order"])

    def test_sop_library_builds_flow_nodes_from_dialogue_sequence(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = StyleMemory(Path(tmp) / "memory.json")
            memory.learn_from_training_examples(
                [
                    {
                        "partner": "chat",
                        "cue": "[对方] 我已经添加了你，现在可以开始聊天了。",
                        "reply": "您好，是来咨询活动的吗",
                        "source": "chat_zip_training",
                    },
                    {
                        "partner": "chat",
                        "cue": "[对方] 我已经添加了你，现在可以开始聊天了。\n[我] 您好，是来咨询活动的吗\n[对方] 我想做霸王餐活动",
                        "reply": "您是想做到店套餐还是霸王餐引流",
                        "source": "chat_zip_training",
                    },
                    {
                        "partner": "chat",
                        "cue": "[对方] 我想做霸王餐活动\n[我] 您是想做到店套餐还是霸王餐引流\n[对方] 我已经付款了，后面怎么安排",
                        "reply": "收到，我这边给您登记并安排上架",
                        "source": "chat_zip_training",
                    },
                ]
            )

            library = build_sop_library_from_memory(StyleMemory(memory.path).load())
            flow_nodes = library["flow_nodes"]

            self.assertGreaterEqual(len(flow_nodes), 3)
            self.assertEqual(flow_nodes[0]["step"], 1)
            self.assertIn("我已经添加了你", flow_nodes[0]["customer_examples"][0])
            self.assertIn("霸王餐活动", "\n".join(flow_nodes[1]["customer_examples"]))
            self.assertLess(flow_nodes[0]["flow_order"], flow_nodes[-1]["flow_order"])

    def test_sop_analyzer_uses_llm_json_for_full_sales_flow(self):
        turns = parse_ab_chat_text(
            "\n".join(
                [
                    "A: 你好，想了解一下活动",
                    "B: 您好，您这边是想做到店套餐还是霸王餐引流",
                    "A: 霸王餐怎么上架",
                    "B: 您把商家发的活动内容给我，我先帮您整理上架",
                ]
            )
        )
        settings = AppSettings(api_provider="deepseek", api_key="key", base_url="https://api.deepseek.com", model="deepseek-chat")

        class FakeResponse:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "scenes": [
                                            {
                                                "scene": "客户进线开场",
                                                "count": 1,
                                                "customer_examples": ["你好，想了解一下活动"],
                                                "rules": [
                                                    {
                                                        "reply": "您好，您这边是想做到店套餐还是霸王餐引流",
                                                        "count": 1,
                                                        "hit_rate": 100,
                                                    }
                                                ],
                                            }
                                        ],
                                        "sop_steps": [
                                            "1. 客户进线：先判断客户要做的活动类型",
                                            "2. 资料收集：让客户发活动内容、门店信息和套餐",
                                            "3. 上架执行：整理活动并同步进度",
                                            "4. 成交完成：确认上线结果并引导后续售后查单",
                                        ],
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                }

        def fake_post(url, headers=None, json=None, timeout=None):
            self.assertIn("deepseek", url)
            self.assertIn("客户进线到成交完成", json["messages"][-1]["content"])
            return FakeResponse()

        report = analyze_chat_turns_with_llm(turns, settings, post=fake_post)
        document = format_sop_document(report)

        self.assertEqual(report["analysis_source"], "DeepSeek API")
        self.assertEqual(report["scenes"][0]["scene"], "客户进线开场")
        self.assertIn("成交完成", "\n".join(report["sop_steps"]))
        self.assertIn("DeepSeek API", document)
        self.assertIn("客户进线", document)

    def test_manual_reply_learning_creates_local_vector_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.json"
            memory = StyleMemory(path)

            memory.learn_from_sent_reply(
                partner="client",
                conversation_text="[other] where should I put it",
                sent_reply="put it near the study desk",
                source="manual_edit",
            )

            vector_path = path.with_name("vector_memory.json")
            self.assertTrue(vector_path.exists())
            data = json.loads(vector_path.read_text(encoding="utf-8"))
            self.assertEqual(data["version"], 1)
            self.assertEqual(len(data["items"]), 1)
            self.assertIn("vector", data["items"][0])

    def test_relevant_examples_can_use_local_vector_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.json"
            memory = StyleMemory(path)
            memory.learn_from_sent_reply(
                partner="client",
                conversation_text="[other] where should I put it",
                sent_reply="put it near the study desk",
                source="manual_edit",
            )

            loaded = StyleMemory(path).load()
            replies = [item["reply"] for item in loaded.relevant_examples("[other] where can this be placed", limit=1)]

            self.assertEqual(replies, ["put it near the study desk"])

    def test_memory_stores_full_context_examples_with_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.json"
            memory = StyleMemory(path)

            memory.learn_from_sent_reply(
                partner="张总",
                conversation_text="[对方] 把完整聊天原文长期保存是不允许的\n[我] 好",
                sent_reply="可以，我晚点发你一版。",
                source="manual_edit",
            )

            data = json.loads(path.read_text(encoding="utf-8"))
            serialized = json.dumps(data, ensure_ascii=False)
            self.assertIn("可以，我晚点发你一版。", serialized)
            self.assertEqual(data["examples"][-1]["source"], "manual_edit")
            self.assertIn("style_summary", data)
            self.assertIn("完整聊天原文长期保存", serialized)
            self.assertIn("created_at", data["examples"][-1])
            self.assertIn("conversation_hash", data["examples"][-1])
            self.assertGreaterEqual(memory.max_examples, 3000)

    def test_memory_marks_selected_candidate_only_after_send_learning(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.json"
            memory = StyleMemory(path)

            memory.learn_from_sent_reply(
                partner="李总",
                conversation_text="[对方] 这个放哪里",
                sent_reply="我先帮您安排好，晚点教您怎么放",
                source="selected_candidate",
            )

            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["examples"][-1]["source"], "selected_candidate")
            self.assertIn("我先帮您安排好", data["examples"][-1]["reply"])

    def test_sent_reply_learning_sources_exclude_managed_auto(self):
        self.assertTrue(_should_learn_sent_reply("selected_candidate"))
        self.assertTrue(_should_learn_sent_reply("manual_edit"))
        self.assertFalse(_should_learn_sent_reply("managed_auto"))

    def test_training_zip_import_stores_full_context_and_dedupes(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "chat.zip"
            memory_path = Path(tmp) / "memory.json"
            chat = "\n".join(
                [
                    "A: 这种能保平安的吗",
                    "B: 可以的，我先让师傅给您做加持，安排好后跟您说怎么放",
                    "A: 放在手机后面就行了嘛",
                    "B: 可以的，但是能放家里就尽量放家里面，主要怕带出去弄丢了",
                ]
            )
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("1.txt", chat)

            stats = import_chat_zip_to_memory(zip_path, StyleMemory(memory_path))
            stats_again = import_chat_zip_to_memory(zip_path, StyleMemory(memory_path))
            loaded = StyleMemory(memory_path).load()

            self.assertEqual(stats["files"], 1)
            self.assertGreaterEqual(stats["examples"], 2)
            self.assertEqual(stats["examples"], stats_again["examples"])
            self.assertTrue(any("怎么放" in item.get("cue", "") for item in loaded.examples))
            self.assertIn("放在手机后面就行了嘛", json.dumps(loaded.examples, ensure_ascii=False))
            self.assertTrue(all(item.get("conversation_hash") for item in loaded.examples))
            self.assertEqual(sum(1 for item in loaded.examples if item.get("source") == "chat_zip_training"), stats["examples"])

    def test_training_zip_import_accepts_docx_and_xlsx_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "chat.zip"
            memory_path = Path(tmp) / "memory.json"
            docx_bytes = _minimal_docx_bytes("A: 这个放哪里\nB: 放干净稳妥的位置就行")
            xlsx_bytes = _minimal_xlsx_bytes(["A: 领取方法在哪", "B: 我发你看下，很简单"])
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("chat.docx", docx_bytes)
                zf.writestr("chat.xlsx", xlsx_bytes)

            stats = import_chat_zip_to_memory(zip_path, StyleMemory(memory_path))
            loaded = StyleMemory(memory_path).load()
            serialized = json.dumps(loaded.examples, ensure_ascii=False)

            self.assertEqual(stats["files"], 2)
            self.assertGreaterEqual(stats["examples"], 2)
            self.assertIn("放干净稳妥的位置", serialized)
            self.assertIn("我发你看下", serialized)

    def test_training_zip_import_accepts_phrasebook_xls_rows(self):
        rows = [
            {
                "一级分类（必填）": "文昌话术",
                "二级分类（选填）": "收集信息",
                "话术标题（选填）": "客户进线不说话",
                "话术内容（必填）": "师兄，看到信息了吗？方便时回我一下就好",
            }
        ]

        examples = extract_reply_examples(parse_message_rows(rows))

        self.assertFalse(examples)
        phrasebook_examples = extract_phrasebook_examples(rows)
        self.assertEqual(len(phrasebook_examples), 1)
        self.assertIn("客户进线不说话", phrasebook_examples[0]["cue"])
        self.assertIn("方便时回我", phrasebook_examples[0]["reply"])

    def test_training_zip_import_accepts_phrasebook_xlsx_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "phrasebook.zip"
            memory_path = Path(tmp) / "memory.json"
            xlsx_bytes = _minimal_xlsx_bytes(
                [
                    "一级分类（必填）,二级分类（选填）,话术标题（选填）,话术内容（必填）",
                    "文昌话术,收集信息,客户问位置,这个放书房或者客厅都可以，避开潮湿和床头就行",
                ]
            )
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("kefubao.xlsx", xlsx_bytes)

            stats = import_chat_zip_to_memory(zip_path, StyleMemory(memory_path))
            loaded = StyleMemory(memory_path).load()
            serialized = json.dumps(loaded.examples, ensure_ascii=False)

            self.assertEqual(stats["files"], 1)
            self.assertEqual(stats["examples"], 1)
            self.assertIn("客户问位置", serialized)
            self.assertIn("避开潮湿和床头", serialized)

    def test_training_examples_keep_tiantian_html_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.json"
            memory = StyleMemory(path)

            memory.learn_from_training_examples(
                [
                    {
                        "partner": "tiantian",
                        "cue": "[other] customer just added contact",
                        "reply": "reply from tiantian phrasebook",
                        "source": "tiantian_html_training",
                    }
                ]
            )

            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["examples"][-1]["source"], "tiantian_html_training")

    def test_phrasebook_library_summaries_group_examples_by_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.json"
            memory = StyleMemory(path)
            memory.learn_from_training_examples(
                [
                    {"partner": "tiantian", "cue": "customer asks money mother placement", "reply": "put it behind phone case", "source": "tiantian_html_training"},
                    {"partner": "general", "cue": "customer says thanks", "reply": "you are welcome", "source": "chat_zip_training"},
                ]
            )

            loaded = StyleMemory(path).load()
            summaries = loaded.library_summaries()
            keys = {item["key"] for item in summaries}

            self.assertIn("tiantian_html_training", keys)
            self.assertIn("chat_zip_training", keys)
            tiantian = next(item for item in summaries if item["key"] == "tiantian_html_training")
            self.assertEqual(tiantian["count"], 1)
            self.assertEqual(tiantian["name"], "甜甜话术库")

    def test_phrasebook_scored_examples_include_match_score_and_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.json"
            memory = StyleMemory(path)
            memory.learn_from_training_examples(
                [
                    {
                        "partner": "tiantian",
                        "cue": "money mother phone case placement",
                        "reply": "put it behind phone case",
                        "source": "tiantian_html_training",
                    },
                    {
                        "partner": "tiantian",
                        "cue": "appointment time change",
                        "reply": "tell me when you are free",
                        "source": "tiantian_html_training",
                    },
                ]
            )

            loaded = StyleMemory(path).load()
            scored = loaded.scored_examples("money mother phone placement", library_key="tiantian_html_training", limit=2)

            self.assertEqual(scored[0]["reply"], "put it behind phone case")
            self.assertGreater(scored[0]["score"], scored[1]["score"])
            self.assertIn("vector", scored[0]["reasons"])

    def test_tiantian_phrasebook_priority_controls_reply_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.json"
            memory = StyleMemory(path)
            memory.learn_from_training_examples(
                [
                    {
                        "partner": "tiantian",
                        "cue": "[场景] 刚添加，客户说可以开始聊天了\n[对方] 我已经添加了你，现在我们可以开始聊天了。",
                        "reply": "低优先级回复",
                        "source": "tiantian_html_training",
                        "priority": 58,
                    },
                    {
                        "partner": "tiantian",
                        "cue": "[场景] 刚添加，客户说可以开始聊天了\n[对方] 我已经添加了你，现在我们可以开始聊天了。",
                        "reply": "高优先级回复",
                        "source": "tiantian_html_training",
                        "priority": 96,
                    },
                ]
            )

            loaded = StyleMemory(path).load()
            suggestions = loaded.suggest_replies("[对方] 我已经添加你了，可以开始聊了吗", limit=2)
            scored = loaded.scored_examples(
                "[对方] 我已经添加你了，可以开始聊了吗",
                library_key="tiantian_html_training",
                limit=2,
            )

            self.assertEqual(suggestions[0], "高优先级回复")
            self.assertEqual(scored[0]["reply"], "高优先级回复")

    def test_phrasebook_position_question_prefers_placement_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.json"
            memory = StyleMemory(path)
            memory.learn_from_training_examples(
                [
                    {
                        "partner": "tiantian",
                        "cue": "[场景] 收集资料\n[对方] 山海镇收到了师兄",
                        "reply": "收到了师兄，您这边主要是想求财运、事业顺利，还是单纯开小财库呢？",
                        "source": "tiantian_html_training",
                        "priority": 92,
                    },
                    {
                        "partner": "tiantian",
                        "cue": "[场景] 客户问钱母怎么放、是否放手机后面\n[对方] 这个要怎么放",
                        "reply": "我先去为您安排加持，安排好后教您怎么放🙏",
                        "source": "tiantian_html_training",
                        "priority": 70,
                    },
                ]
            )

            loaded = StyleMemory(path).load()
            suggestions = loaded.suggest_replies("[对方] 山海镇收到了，应该怎么放", limit=2)

            self.assertEqual(suggestions[0], "我先去为您安排加持，安排好后教您怎么放🙏")

    def test_update_phrasebook_example_rebuilds_memory_and_vector_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.json"
            memory = StyleMemory(path)
            memory.learn_from_training_examples(
                [
                    {
                        "partner": "tiantian",
                        "cue": "money mother phone case placement",
                        "reply": "put it behind phone case",
                        "source": "tiantian_html_training",
                    }
                ]
            )
            loaded = StyleMemory(path).load()
            old_hash = loaded.examples[-1]["conversation_hash"]

            self.assertTrue(
                loaded.update_example(
                    old_hash,
                    cue="money mother wallet placement",
                    reply="put it in phone case or wallet",
                )
            )
            reloaded = StyleMemory(path).load()
            updated = reloaded.examples[-1]
            vector_data = json.loads(reloaded.vector_path.read_text(encoding="utf-8"))

            self.assertNotEqual(updated["conversation_hash"], old_hash)
            self.assertEqual(updated["reply"], "put it in phone case or wallet")
            self.assertEqual(len(vector_data["items"]), 1)

    def test_memory_suggests_replies_from_similar_training_cue(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = StyleMemory(Path(tmp) / "memory.json")
            memory.learn_from_training_examples(
                [
                    {
                        "partner": "训练样本",
                        "cue": "对方问 福宝能不能放手机后面 怎么放",
                        "reply": "我先去为您安排加持，安排好后教您怎么放",
                    }
                ]
            )

            suggestions = memory.suggest_replies("[对方] 这个可以放手机后面吗", limit=3)

            self.assertIn("安排加持", " ".join(suggestions))

    def test_prompt_block_includes_similar_full_context_and_real_reply(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = StyleMemory(Path(tmp) / "memory.json")
            memory.learn_from_training_examples(
                [
                    {
                        "partner": "训练样本",
                        "cue": "[对方] 我想问一下领取使用方面的\n[对方] 我收到这个该放哪里",
                        "reply": "领取方法很简单，我发你看下",
                    }
                ]
            )

            block = memory.prompt_block("[对方] 我想问领取方法\n[对方] 这个该放哪里")

            self.assertIn("历史聊天片段", block)
            self.assertIn("我收到这个该放哪里", block)
            self.assertIn("当时真实回复", block)
            self.assertIn("领取方法很简单", block)

    def test_training_mode_learns_own_replies_from_observed_chat_with_full_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.json"
            memory = StyleMemory(path)
            context = "\n".join(
                [
                    "[对方] 这个领取方法在哪里",
                    "[我] 领取方法很简单，我发你看下",
                ]
            )

            memory.learn_from_chat_text(context)
            data = json.loads(path.read_text(encoding="utf-8"))
            serialized = json.dumps(data, ensure_ascii=False)

            self.assertIn("领取方法很简单，我发你看下", serialized)
            self.assertIn("observed_chat", serialized)
            self.assertIn("这个领取方法在哪里", serialized)
            self.assertIn("conversation_hash", data["examples"][-1])


    def test_observed_chat_learning_only_runs_in_training_observation(self):
        self.assertTrue(_should_learn_observed_chat(training_only=True, managed=False))
        self.assertFalse(_should_learn_observed_chat(training_only=False, managed=False))
        self.assertFalse(_should_learn_observed_chat(training_only=False, managed=True))
        self.assertFalse(_should_learn_observed_chat(training_only=True, managed=True))


class ChatTrainingTests(unittest.TestCase):
    def test_complex_enterprise_annual_report_xlsx_preserves_hierarchy_and_org_type(self):
        from enterprise_kb import (
            detect_enterprise_annual_report_workbook,
            parse_enterprise_annual_report_workbook,
        )

        raw = _enterprise_annual_report_xlsx_bytes()

        self.assertTrue(detect_enterprise_annual_report_workbook("annual-report.xlsx", raw))
        examples = parse_enterprise_annual_report_workbook("annual-report.xlsx", raw)

        self.assertEqual(len(examples), 3)
        social = examples[0]
        private = examples[1]
        numbered = examples[2]
        self.assertEqual(social["module"], "年检")
        self.assertEqual(social["subcategory"], "登录")
        self.assertEqual(social["organization_type"], "社会团体")
        self.assertEqual(private["organization_type"], "民办非企业单位")
        self.assertEqual(numbered["source_sheet"], "年报问答")
        self.assertEqual(numbered["source_cell"], "C3")
        self.assertIn("忘记密码", numbered["question"])
        self.assertIn("找回", numbered["reply"])

    def test_horizontally_merged_org_question_is_one_shared_knowledge_record(self):
        from enterprise_kb import parse_enterprise_annual_report_workbook

        examples = parse_enterprise_annual_report_workbook(
            "annual-report.xlsx", _enterprise_annual_report_merged_org_xlsx_bytes()
        )

        self.assertEqual(len(examples), 2)
        shared = examples[0]
        self.assertEqual(shared["organization_type"], "社会团体、民办非企业单位")
        self.assertEqual(shared["source_cell"], "C2:D2")
        self.assertIn("法人账号", shared["reply"])

    def test_enterprise_import_is_isolated_from_generic_training_and_replaces_only_its_own_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_path = root / "memory.json"
            (root / "generic.txt").write_text("A: 普通客户问题\nB: 普通客户回复", encoding="utf-8")
            enterprise_path = root / "annual-report.xlsx"
            enterprise_path.write_bytes(_enterprise_annual_report_xlsx_bytes())

            first = import_chat_zip_to_memory(root, StyleMemory(memory_path))
            loaded_first = StyleMemory(memory_path).load()
            first_enterprise = [item for item in loaded_first.examples if item.get("source") == "enterprise_annual_report_kb"]
            self.assertEqual(first["examples"], 4)
            self.assertEqual(len(first_enterprise), 3)
            self.assertTrue(any(item.get("source") == "chat_zip_training" for item in loaded_first.examples))
            self.assertEqual(first_enterprise[0]["module"], "年检")

            enterprise_path.write_bytes(_enterprise_annual_report_xlsx_bytes("这是更新后的官方登录答案。"))
            second = import_chat_zip_to_memory(enterprise_path, StyleMemory(memory_path))
            loaded_second = StyleMemory(memory_path).load()
            second_enterprise = [item for item in loaded_second.examples if item.get("source") == "enterprise_annual_report_kb"]

            self.assertEqual(second["examples"], 3)
            self.assertEqual(len(second_enterprise), 3)
            self.assertIn("这是更新后的官方登录答案。", "\n".join(item["reply"] for item in second_enterprise))
            self.assertNotIn("请使用社会组织法人账号登录年度报告系统。", "\n".join(item["reply"] for item in second_enterprise))
            self.assertTrue(any(item.get("source") == "chat_zip_training" for item in loaded_second.examples))

    def test_enterprise_import_preserves_official_answer_line_breaks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "annual-report.xlsx"
            memory_path = root / "memory.json"
            workbook.write_bytes(_enterprise_annual_report_xlsx_bytes("第一步：使用法人账号登录。\n第二步：完成经办人授权。"))

            import_chat_zip_to_memory(workbook, StyleMemory(memory_path))
            loaded = StyleMemory(memory_path).load()
            first_answer = next(item["reply"] for item in loaded.examples if item.get("source") == "enterprise_annual_report_kb")

            self.assertIn("第一步：使用法人账号登录。\n第二步：完成经办人授权。", first_answer)

    def test_import_real_chinese_phrasebook_docx_and_xlsx_counts_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "training.zip"
            memory_path = Path(tmp) / "memory.json"
            docx_bytes = _minimal_docx_bytes("A: 客户问活动怎么上架\nB: 可以先把商家发的活动信息整理出来，我这边帮你上架")
            xlsx_bytes = _minimal_xlsx_bytes(
                [
                    "一级分类（必填）,二级分类（选填）,话术标题（选填）,话术内容（必填）",
                    "售后,查单,客户问核销,您把订单号发我，我帮您查一下核销状态",
                ]
            )
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("chat.docx", docx_bytes)
                zf.writestr("phrasebook.xlsx", xlsx_bytes)

            stats = import_chat_zip_to_memory(zip_path, StyleMemory(memory_path))
            loaded = StyleMemory(memory_path).load()
            serialized = json.dumps(loaded.examples, ensure_ascii=False)

            self.assertEqual(stats["files"], 2)
            self.assertGreaterEqual(stats["examples"], 2)
            self.assertIn("活动怎么上架", serialized)
            self.assertIn("订单号发我", serialized)

    def test_import_common_question_answer_docx_and_xlsx_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "qa_training.zip"
            memory_path = Path(tmp) / "memory.json"
            docx_bytes = _minimal_docx_bytes("问题：这个活动能带来多少单\n回复：这个不能直接估固定数字，要看门店品类和投放力度")
            xlsx_bytes = _minimal_xlsx_bytes(
                [
                    "问题,答案",
                    "客户问怎么查核销,您把订单号发我，我帮您查一下核销状态",
                ]
            )
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("qa.docx", docx_bytes)
                zf.writestr("qa.xlsx", xlsx_bytes)

            stats = import_chat_zip_to_memory(zip_path, StyleMemory(memory_path))
            loaded = StyleMemory(memory_path).load()
            serialized = json.dumps(loaded.examples, ensure_ascii=False)

            self.assertEqual(stats["files"], 2)
            self.assertGreaterEqual(stats["examples"], 2)
            self.assertIn("不能直接估固定数字", serialized)
            self.assertIn("客户问怎么查核销", serialized)

    def test_import_generic_two_person_chat_by_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "two_people.zip"
            memory_path = Path(tmp) / "memory.json"
            chat_text = "\n".join(
                [
                    "张三：这个活动怎么上架",
                    "客服小李：你把商家发的活动内容给我，我这边帮你整理上架",
                    "张三：核销怎么查",
                    "客服小李：把订单号发我，我帮你查核销状态",
                ]
            )
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("two.txt", chat_text)

            stats = import_chat_zip_to_memory(zip_path, StyleMemory(memory_path))
            loaded = StyleMemory(memory_path).load()
            serialized = json.dumps(loaded.examples, ensure_ascii=False)

            self.assertEqual(stats["files"], 1)
            self.assertGreaterEqual(stats["examples"], 2)
            self.assertIn("活动怎么上架", serialized)
            self.assertIn("订单号发我", serialized)

    def test_import_generic_two_person_chat_with_timestamp_and_space(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "two_people_time.zip"
            memory_path = Path(tmp) / "memory.json"
            chat_text = "\n".join(
                [
                    "2026-06-22 10:01 张三 这个放哪个位置合适",
                    "2026-06-22 10:02 小李 放在客厅或者书房都可以，避开潮湿和床头",
                    "2026-06-22 10:03 张三 好的",
                    "2026-06-22 10:04 小李 嗯嗯，放好后可以拍给我看下",
                ]
            )
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("two_time.txt", chat_text)

            stats = import_chat_zip_to_memory(zip_path, StyleMemory(memory_path))
            loaded = StyleMemory(memory_path).load()
            serialized = json.dumps(loaded.examples, ensure_ascii=False)

            self.assertEqual(stats["files"], 1)
            self.assertGreaterEqual(stats["examples"], 2)
            self.assertIn("哪个位置合适", serialized)
            self.assertIn("避开潮湿和床头", serialized)

    def test_import_arbitrary_table_columns_as_two_person_chat(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "table_chat.zip"
            memory_path = Path(tmp) / "memory.json"
            xlsx_bytes = _minimal_xlsx_bytes(
                [
                    "记录时间,说话人,聊天内容,备注",
                    "2026-06-22 10:01,客户A,这个活动能带来多少订单,",
                    "2026-06-22 10:02,客服小李,这个不能直接估固定数字，要看门店品类和投放力度,",
                    "2026-06-22 10:03,客户A,那先怎么上架,",
                    "2026-06-22 10:04,客服小李,你把商家发的活动内容给我，我这边先帮你整理上架,",
                ]
            )
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("table_chat.xlsx", xlsx_bytes)

            stats = import_chat_zip_to_memory(zip_path, StyleMemory(memory_path))
            loaded = StyleMemory(memory_path).load()
            serialized = json.dumps(loaded.examples, ensure_ascii=False)

            self.assertEqual(stats["files"], 1)
            self.assertGreaterEqual(stats["examples"], 2)
            self.assertIn("不能直接估固定数字", serialized)
            self.assertIn("活动内容给我", serialized)

    def test_parse_ab_chat_text_keeps_multiline_turns(self):
        text = "A: 求财运\nB: 好的师兄\n一会师傅为您祈福\nA：谢谢"

        turns = parse_ab_chat_text(text)

        self.assertEqual(len(turns), 3)
        self.assertEqual(turns[1].speaker, "B")
        self.assertIn("祈福", turns[1].text)

    def test_extract_reply_examples_uses_previous_context_as_cue(self):
        turns = parse_ab_chat_text("A: 求财运\nB: 好，一会师傅也会为您祈福下财运")

        examples = extract_reply_examples(turns)

        self.assertEqual(len(examples), 1)
        self.assertIn("求财运", examples[0]["cue"])
        self.assertIn("祈福", examples[0]["reply"])

    def test_missing_xls_reader_is_installed_on_demand_in_source_mode(self):
        fake_module = types.SimpleNamespace(__name__="xlrd")
        with patch.object(chat_training.importlib, "import_module", side_effect=[ImportError("missing"), fake_module]) as importer:
            with patch.object(chat_training.subprocess, "check_call") as check_call:
                result = _ensure_package_import("xlrd", "xlrd>=2.0.1")

        self.assertIs(result, fake_module)
        self.assertEqual(importer.call_count, 2)
        check_call.assert_called_once()


    def test_training_import_accepts_md_html_and_tsv_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "chat.md").write_text("A: 这个怎么放\nB: 放客厅正南方就行", encoding="utf-8")
            (root / "chat.tsv").write_text("sender\tcontent\n客户\t怎么领取\n我\t我发你看下\n", encoding="utf-8")
            (root / "chat.html").write_text("<p>A: 还需要每天动吗</p><p>B: 不用每天动，放稳就行</p>", encoding="utf-8")

            names = sorted(name for name, _raw in _iter_export_files(root))

            self.assertEqual(names, ["chat.html", "chat.md", "chat.tsv"])


class ReplyParsingTests(unittest.TestCase):
    def test_enterprise_fuzzy_history_lookup_returns_official_answer_without_api_key(self):
        settings = AppSettings(api_key="configured", reply_source_mode="model_memory")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        memory = StyleMemory(Path(tmp.name) / "memory.json")
        history_answer = "打开浏览器搜索“福建省社会组织管理局”进入网页，找到“信息公开”栏，打开“年度报告公示”输入社会组织名称查询。"
        memory.learn_from_training_examples(_enterprise_history_lookup_examples(history_answer))
        engine = ReplyEngine(settings, memory)

        # 高置信命中 → 大模型确认一致 → 发送官方答案
        with patch("reply_engine.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = {"choices": [{"message": {"content": '{"chosen": 1, "consistent": true}'}}]}
            result = engine.generate("[对方] 怎么查以前的年度报告", "客户")

        self.assertEqual(result, [history_answer])
        self.assertIn("C11:D11", engine.last_source)
        self.assertIn("大模型确认一致", engine.last_source)

    def test_enterprise_generation_uses_current_target_turn_not_stale_history(self):
        settings = AppSettings(api_key="configured", reply_source_mode="model")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        memory = StyleMemory(Path(tmp.name) / "memory.json")
        correct_answer = "打开年度报告公示，输入社会组织名称查询。"
        memory.learn_from_training_examples(
            [
                {
                    "source": "enterprise_annual_report_kb",
                    "question": "往年年度工作报告如何查看？",
                    "cue": "企业年报 / 年检 / 填报 / 往年年度工作报告如何查看？",
                    "reply": correct_answer,
                    "source_cell": "C11:D11",
                },
                {
                    "source": "enterprise_annual_report_kb",
                    "question": "存在本年度年度工作报告已存在的问题该如何解决？",
                    "cue": "企业年报 / 年检 / 填报 / 本年度年度工作报告已存在的问题该如何解决？",
                    "reply": "在已生成本年度年度工作报告的操作栏点修改填写。",
                    "source_cell": "C15:D15",
                },
            ]
        )
        engine = ReplyEngine(settings, memory)

        with patch("reply_engine.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = {"choices": [{"message": {"content": '{"chosen": 1, "consistent": true}'}}]}
            result = engine.generate(
                "[对方] 存在本年度年度工作报告已存在的问题该如何解决？\n[我] 我先帮您查看。",
                "客户",
                target_turn=["往年年度工作报告如何查看"],
            )

        self.assertEqual(result, [correct_answer])
        self.assertIn("C11:D11", engine.last_source)

    def test_enterprise_ambiguous_local_match_returns_explicit_no_answer_without_selector(self):
        settings = AppSettings(api_key="configured", reply_source_mode="memory")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        memory = StyleMemory(Path(tmp.name) / "memory.json")
        print_answer = "请导出 PDF 后再打印。"
        edit_answer = "在已生成本年度年度工作报告的操作栏点修改填写。"
        memory.learn_from_training_examples(
            [
                {
                    "source": "enterprise_annual_report_kb",
                    "question": "年度工作报告无法直接打印如何解决？",
                    "cue": "企业年报 / 年检 / 填报 / 年度工作报告无法直接打印如何解决？",
                    "reply": print_answer,
                    "source_cell": "C10:D10",
                },
                {
                    "source": "enterprise_annual_report_kb",
                    "question": "存在本年度年度工作报告已存在的问题该如何解决？",
                    "cue": "企业年报 / 年检 / 填报 / 本年度年度工作报告已存在的问题该如何解决？",
                    "reply": edit_answer,
                    "source_cell": "C15:D15",
                },
            ]
        )
        engine = ReplyEngine(settings, memory)

        # 大模型从并列候选中选出第 1 条并判定一致 → 发送被选中候选
        with patch("reply_engine.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = {"choices": [{"message": {"content": '{"chosen": 1, "consistent": true}'}}]}
            result = engine.generate("[对方] 年度工作报告怎么处理？", "客户")

        self.assertEqual(len(result), 1)
        self.assertIn("大模型确认一致", engine.last_source)
        self.assertIn(result[0], (print_answer, edit_answer))

    def test_enterprise_ambiguous_candidate_does_not_use_model_selector(self):
        settings = AppSettings()
        settings.api_provider = "deepseek"
        settings.api_key = "configured"
        settings.base_url = "https://api.deepseek.com"
        settings.model = "deepseek-chat"
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        memory = StyleMemory(Path(tmp.name) / "memory.json")
        official_answer = (
            "请使用社会组织法人账号登录年度报告系统，并确认经办人已经完成组织关联授权；"
            "如仍无法登录，请核对法人类型和账号绑定状态后再联系平台客服处理。"
        )
        memory.learn_from_training_examples(
            [
                {
                    "source": "enterprise_annual_report_kb",
                    "question": "年度报告从哪里登录？",
                    "cue": "企业年报 / 社会团体 / 年检 / 登录 / 年度报告从哪里登录？",
                    "reply": official_answer,
                    "module": "年检",
                    "subcategory": "登录",
                    "organization_type": "社会团体",
                    "source_cell": "C2",
                },
                {
                    "source": "enterprise_annual_report_kb",
                    "question": "忘记密码怎么办？",
                    "cue": "企业年报 / 社会团体 / 年检 / 登录 / 忘记密码怎么办？",
                    "reply": "请在登录页选择忘记密码后按提示找回。",
                    "module": "年检",
                    "subcategory": "登录",
                    "organization_type": "社会团体",
                    "source_cell": "C3",
                },
            ]
        )
        engine = ReplyEngine(settings, memory)
        with patch("reply_engine.requests.post") as post:
            result = engine.generate("[对方] 年度工作报告怎么进去填？", "客户")

        # 27 分 < 30（新低分阈值）：命中评分不足 → 未命中文案，不调用模型 selector 自由生成
        self.assertEqual(result, ["未在当前企业年报知识库中找到对应答案。"])
        self.assertIn("未命中", engine.last_source)
        self.assertNotIn(official_answer, result)
        post.assert_not_called()

    def test_low_confidence_enterprise_selector_returns_no_answer_without_general_model_fallback(self):
        settings = AppSettings()
        settings.api_provider = "deepseek"
        settings.api_key = "configured"
        settings.base_url = "https://api.deepseek.com"
        settings.model = "deepseek-chat"
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        memory = StyleMemory(Path(tmp.name) / "memory.json")
        official_answer = "请使用社会组织法人账号登录年度报告系统。"
        memory.learn_from_training_examples(
            [
                {
                    "source": "enterprise_annual_report_kb",
                    "question": "年度报告从哪里登录？",
                    "cue": "企业年报 / 社会团体 / 年检 / 登录 / 年度报告从哪里登录？",
                    "reply": official_answer,
                    "module": "年检",
                    "organization_type": "社会团体",
                    "source_cell": "C2",
                },
                {
                    "source": "enterprise_annual_report_kb",
                    "question": "年度报告从哪里进去填报？",
                    "cue": "企业年报 / 社会团体 / 年检 / 填报 / 年度报告从哪里进去填报？",
                    "reply": "请先登录系统再进入填报页面。",
                    "module": "年检",
                    "organization_type": "社会团体",
                    "source_cell": "C3",
                },
            ]
        )
        engine = ReplyEngine(settings, memory)
        with patch("reply_engine.requests.post") as post:
            result = engine.generate("[对方] 社会组织", "客户")

        self.assertNotIn(official_answer, result)
        self.assertEqual(result, ["未在当前企业年报知识库中找到对应答案。"])
        post.assert_not_called()

    def test_enterprise_score_between_20_and_40_with_margin_is_sendable(self):
        # 新底线 20（2026-08-04 用户要求降到 25 以下）：20-40 分区间且明显领先的
        # 问法也应可直接发送，而不是沿用旧的 40 分硬门槛。
        with tempfile.TemporaryDirectory() as tmp:
            memory = StyleMemory(Path(tmp) / "memory.json")
            memory.learn_from_training_examples(_enterprise_history_lookup_examples("历史报告请到公示页面查询。"))
            candidates = memory.enterprise_candidates("想查一下之前的年报", limit=3)
            self.assertLess(int(candidates[0]["enterprise_local_score"]), 76)
            self.assertEqual(candidates[0]["enterprise_local_confident"], "1")
            self.assertEqual(candidates[0]["enterprise_decision_label"], "可直接发送")

    def test_enterprise_ratio_phrase_disambiguates_rectify_question(self):
        # 用户案例（2026-08-04）："负责人超理事数1/3怎么整改"必须命中
        # "负责人超过理事数的1/3"（C49），而不是与"常务理事数的1/2"（C50）并列。
        with tempfile.TemporaryDirectory() as tmp:
            memory = StyleMemory(Path(tmp) / "memory.json")
            memory.learn_from_training_examples(
                [
                    {
                        "source": "enterprise_annual_report_kb",
                        "question": "存在“负责人超过常务理事数的1/2”的问题如何整改？",
                        "cue": "企业年报 / 治理 / 整改 / 负责人超过常务理事数的1/2",
                        "reply": "常务理事整改答案。",
                        "module": "治理",
                        "source_cell": "C50",
                    },
                    {
                        "source": "enterprise_annual_report_kb",
                        "question": "存在“负责人超过理事数的1/3”的问题如何整改？",
                        "cue": "企业年报 / 治理 / 整改 / 负责人超过理事数的1/3",
                        "reply": "理事整改答案。",
                        "module": "治理",
                        "source_cell": "C49",
                    },
                ]
            )
            candidates = memory.enterprise_candidates("负责人超理事数1/3怎么整改", limit=3)
            self.assertEqual(candidates[0]["source_cell"], "C49")
            self.assertEqual(candidates[0]["enterprise_decision_label"], "可直接发送")
            self.assertGreaterEqual(
                int(candidates[0]["enterprise_local_score"]),
                int(candidates[1]["enterprise_local_score"]) + 8,
            )

    def test_enterprise_scope_accepts_rectify_governance_question(self):
        # 整改/治理类问法应进入企业年报知识库范围，不能被当作普通问题丢给模型。
        with tempfile.TemporaryDirectory() as tmp:
            memory = StyleMemory(Path(tmp) / "memory.json")
            memory.learn_from_training_examples(_enterprise_history_lookup_examples("历史报告请到公示页面查询。"))
            self.assertTrue(memory.is_enterprise_annual_report_query("负责人超理事数1/3怎么整改"))
            self.assertTrue(memory.is_enterprise_annual_report_query("理事人数超标怎么整改"))

    def test_enterprise_scope_accepts_branch_agency_questions(self):
        # 分支机构/代表机构管理办法问答库问法应进入企业年报知识库范围（2026-08-09 补充词表）。
        with tempfile.TemporaryDirectory() as tmp:
            memory = StyleMemory(Path(tmp) / "memory.json")
            memory.learn_from_training_examples(_enterprise_history_lookup_examples("历史报告请到公示页面查询。"))
            branch_questions = [
                "我们协会想设个分会需要什么条件",
                "分支机构能叫会长吗",
                "社会团体可以设立办事处吗",
                "代表机构的名称有什么要求",
                "分支机构能开银行账户吗",
                "专业委员会怎么设立",
                "分支机构下面还能再设分支机构吗",
                "学组能不能独立对外开展活动",
                "会费标准可以由分支机构自己定吗",
                "分支机构的印章怎么管理",
            ]
            for question in branch_questions:
                self.assertTrue(
                    memory.is_enterprise_annual_report_query(question),
                    f"应识别为企业知识库问题：{question}",
                )

    def test_enterprise_rectify_question_returns_official_answer_without_model(self):
        # 端到端：整改类问法命中后由大模型确认一致，返回知识库原始答案，不再让模型自由发挥。
        settings = AppSettings(api_key="configured", reply_source_mode="model_memory")
        with tempfile.TemporaryDirectory() as tmp:
            memory = StyleMemory(Path(tmp) / "memory.json")
            memory.learn_from_training_examples(
                [
                    {
                        "source": "enterprise_annual_report_kb",
                        "question": "存在“负责人超过常务理事数的1/2”的问题如何整改？",
                        "cue": "企业年报 / 治理 / 整改 / 负责人超过常务理事数的1/2",
                        "reply": "常务理事整改答案。",
                        "module": "治理",
                        "source_cell": "C50",
                    },
                    {
                        "source": "enterprise_annual_report_kb",
                        "question": "存在“负责人超过理事数的1/3”的问题如何整改？",
                        "cue": "企业年报 / 治理 / 整改 / 负责人超过理事数的1/3",
                        "reply": "情形一：填报人员填写错误，整改方式：填写情况说明。（以上材料需加盖单位公章）",
                        "module": "治理",
                        "source_cell": "C49",
                    },
                ]
            )
            engine = ReplyEngine(settings, memory)
            with patch("reply_engine.requests.post") as post:
                post.return_value.status_code = 200
                post.return_value.raise_for_status.return_value = None
                post.return_value.json.return_value = {"choices": [{"message": {"content": '{"chosen": 1, "consistent": true}'}}]}
                result = engine.generate("[对方] 负责人超理事数1/3怎么整改", "客户")
            post.assert_called_once()
            self.assertIn("填写情况说明", " ".join(result))
            self.assertIn("C49", engine.last_source)

    def test_enterprise_tie_arbitration_picks_chosen_candidate(self):
        settings = AppSettings(api_key="configured", reply_source_mode="model_memory")
        settings.enable_web_search = False
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        memory = StyleMemory(Path(tmp.name) / "memory.json")
        memory.learn_from_training_examples(_enterprise_history_lookup_examples("历史报告请到公示页面查询。"))
        engine = ReplyEngine(settings, memory)
        question = "年度工作报告在哪里找"
        tie_candidates = memory.enterprise_candidates(question, limit=3)
        self.assertEqual(tie_candidates[0].get("enterprise_decision"), "tie")

        with patch("reply_engine.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = {"choices": [{"message": {"content": "2"}}]}
            chosen = engine._arbitrate_enterprise_tie(question, tie_candidates)

        self.assertIsNotNone(chosen)
        self.assertEqual(chosen["source_cell"], tie_candidates[1]["source_cell"])
        self.assertEqual(chosen["reply"], tie_candidates[1]["reply"])

    def test_enterprise_tie_arbitration_failure_returns_none(self):
        settings = AppSettings(api_key="configured", reply_source_mode="model_memory")
        settings.enable_web_search = False
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        memory = StyleMemory(Path(tmp.name) / "memory.json")
        memory.learn_from_training_examples(_enterprise_history_lookup_examples("历史报告请到公示页面查询。"))
        engine = ReplyEngine(settings, memory)
        question = "年度工作报告在哪里找"
        tie_candidates = memory.enterprise_candidates(question, limit=3)

        with patch("reply_engine.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = {"choices": [{"message": {"content": "我不确定，随便吧"}}]}
            chosen = engine._arbitrate_enterprise_tie(question, tie_candidates)

        self.assertIsNone(chosen)

    def test_enterprise_judgement_inconsistent_returns_no_answer(self):
        settings = AppSettings(api_key="configured", reply_source_mode="model_memory")
        settings.enable_web_search = False
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        memory = StyleMemory(Path(tmp.name) / "memory.json")
        memory.learn_from_training_examples(_enterprise_history_lookup_examples("历史报告请到公示页面查询。"))
        engine = ReplyEngine(settings, memory)

        # 高置信命中但大模型判定不一致 → 未命中文案，不发送
        with patch("reply_engine.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = {"choices": [{"message": {"content": '{"chosen": 1, "consistent": false}'}}]}
            result = engine.generate("[对方] 想查一下之前的年报", "客户")

        self.assertEqual(result, ["未在当前企业年报知识库中找到对应答案。"])
        self.assertIn("判断不一致", engine.last_source)

    def test_enterprise_judgement_failure_returns_no_answer(self):
        settings = AppSettings(api_key="configured", reply_source_mode="model_memory")
        settings.enable_web_search = False
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        memory = StyleMemory(Path(tmp.name) / "memory.json")
        memory.learn_from_training_examples(_enterprise_history_lookup_examples("历史报告请到公示页面查询。"))
        engine = ReplyEngine(settings, memory)

        # 判断调用失败（异常/乱码）→ 安全降级为未命中文案，绝不误发
        with patch("reply_engine.requests.post", side_effect=RuntimeError("timeout")):
            result = engine.generate("[对方] 想查一下之前的年报", "客户")

        self.assertEqual(result, ["未在当前企业年报知识库中找到对应答案。"])
        self.assertIn("判断不一致", engine.last_source)

    def test_enterprise_tie_managed_send_arbitrates_chosen_candidate(self):
        import main as main_module

        class _FakeQueue:
            def __init__(self):
                self.items = []

            def put(self, item):
                self.items.append(item)

        settings = AppSettings(api_key="configured", reply_source_mode="model_memory")
        settings.enable_web_search = False
        settings.managed_auto_reply = True
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        memory = StyleMemory(Path(tmp.name) / "memory.json")
        memory.learn_from_training_examples(_enterprise_history_lookup_examples("历史报告请到公示页面查询。"))
        engine = ReplyEngine(settings, memory)
        app = main_module.WeChatStyleReplyAssistant.__new__(main_module.WeChatStyleReplyAssistant)
        app.engine = engine
        app.settings = settings
        app.memory = memory
        app.current_window = types.SimpleNamespace(title="测试窗口")
        app.queue = _FakeQueue()
        app.managed_last_handled_turn_key = ""
        app.managed_last_handled_turn_messages = []
        app.last_auto_reply_signature = ""
        app.pending_auto_reply_signature = ""
        app.handled_auto_reply_signatures = set()
        app.managed_send_inflight = False
        app.managed_waiting_for_own_echo = False

        context = "[对方] 年度工作报告在哪里找"
        # generate 阶段已由大模型完成"先选再判"（一致 → 发送被选中答案）
        with patch("reply_engine.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = {"choices": [{"message": {"content": '{"chosen": 1, "consistent": true}'}}]}
            replies = engine.generate(context, "客户")
        self.assertIn("大模型确认一致", engine.last_source)

        with patch("main.threading.Thread") as thread_cls:
            app._maybe_managed_send(context, replies)

        self.assertTrue(app.pending_auto_reply_signature)
        status_texts = [str(item) for item in app.queue.items]
        self.assertTrue(any("历史报告请到公示页面查询。" in text for text in status_texts), status_texts)

    def test_enterprise_low_score_returns_no_answer_without_api_in_model_memory(self):
        # model_memory 模式：低分（<20）企业范围问法直接回未命中文案，不再让模型生成
        settings = AppSettings(api_key="configured", reply_source_mode="model_memory")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        memory = StyleMemory(Path(tmp.name) / "memory.json")
        memory.learn_from_training_examples(_enterprise_history_lookup_examples("历史报告请到公示页面查询。"))
        engine = ReplyEngine(settings, memory)

        with patch("reply_engine.requests.post") as post:
            result = engine.generate("[对方] 社会组织", "客户")

        post.assert_not_called()
        self.assertEqual(result, ["未在当前企业年报知识库中找到对应答案。"])
        self.assertIn("未命中", engine.last_source)

    def test_seed_scenario_bank_covers_common_intents(self):
        self.assertGreaterEqual(seed_count(), 8)
        self.assertTrue(replies_for_intent("food_share"))
        self.assertIn("米线", prompt_examples_for_intent("food_share"))

    def test_classify_intent_for_common_scenarios(self):
        self.assertEqual(classify_intent("[对方] 好的"), "low_ack")
        self.assertEqual(classify_intent("[对方] 收到了，谢谢"), "thanks")
        self.assertEqual(classify_intent("[对方] 米线\n[对方] 麻麻的"), "food_share")
        self.assertEqual(classify_intent("[对方] 岗位招聘，五险一金"), "job_card")
        self.assertEqual(classify_intent("[对方] 看见消息血压上来了"), "venting")
        self.assertEqual(classify_intent("[对方] 我想问一下领取使用方面的\n[对方] 我收到这个该放哪里"), "how_to_receive")

    def test_generic_method_or_position_words_do_not_force_receive_scene(self):
        self.assertEqual(classify_intent("[对方] 这个方法靠谱吗"), "question")
        self.assertEqual(classify_intent("[对方] 这个位置可以吗"), "question")
        self.assertEqual(classify_intent("[对方] 使用起来方便吗"), "question")

    def test_auto_reply_decision_only_allows_low_risk_scenarios(self):
        self.assertEqual(auto_reply_decision("[对方] 收到了，谢谢")[0], True)
        self.assertEqual(auto_reply_decision("[对方] 好的")[0], True)
        self.assertEqual(auto_reply_decision("[对方] 这个怎么处理？")[0], False)
        self.assertEqual(auto_reply_decision("[对方] 米线\n[对方] 麻麻的")[0], False)

    def test_parse_numbered_replies_extracts_three_items(self):
        content = "1. 可以，我晚点看下\n2. 行，我先确认下\n3. 我处理完跟你说"

        result = parse_numbered_replies(content)
        self.assertEqual(result, ["可以，我晚点看下", "行，我先确认下", "我处理完跟你说"])

    def test_parse_replies_filters_model_analysis_lines(self):
        content = "消息理解：对方在吐槽\n回复建议：\n哈哈，确实有点烦人"

        result = parse_numbered_replies(content)

        self.assertEqual(result, ["哈哈，确实有点烦人"])

    def test_pending_reply_detection_identifies_incomplete_promises(self):
        from reply_engine import _is_pending_reply

        for text in [
            "我查查",
            "我查一下",
            "稍等",
            "让我看看",
            "我去问一下",
            "马上",
            "等我一下",
            "正在确认",
            "正在查询",
            "正在核实",
            "稍后回复你",
            "可以，我确认下",
            "我先看下，晚点回你",
            "这个问题我这边暂时无法直接查到，我先核实一下，确认后尽快答复您",
        ]:
            self.assertTrue(_is_pending_reply(text), f"应识别为未完成回复: {text}")
        for text in ["今天晴天，18-25度", "嗯嗯", "好的，知道了", "快递显示明天到", "我查一下今天天气怎么样，马上告诉你结果"]:
            self.assertFalse(_is_pending_reply(text), f"不应识别为未完成回复: {text}")

    def test_question_needs_completion_detects_unanswered_questions(self):
        from reply_engine import _question_needs_completion

        self.assertTrue(_question_needs_completion("你的信息更新最新能看到什么日期的信息？", "正在确认"))
        self.assertTrue(_question_needs_completion("今天天气怎么样", "我查查"))
        self.assertTrue(_question_needs_completion("快递到哪了？", "稍等，我看看"))
        # 短回复含承诺/拖延动作词（确认/查/核实等）→ 仍未实质回答
        self.assertTrue(_question_needs_completion("你的信息更新最新能看到什么日期的信息？", "需要查下"))
        self.assertTrue(_question_needs_completion("你的信息更新最新能看到什么日期的信息？", "需要确认"))
        self.assertTrue(_question_needs_completion("你的信息更新最新能看到什么日期的信息？", "我再看看"))
        self.assertFalse(_question_needs_completion("今天天气怎么样", "今天晴天，18到25度"))
        self.assertFalse(_question_needs_completion("好的", "嗯嗯"))
        self.assertFalse(_question_needs_completion("快递到哪了？", "快递显示明天到"))
        # 短回复以"！/。"明确陈述结尾 → 视为已回答
        self.assertFalse(_question_needs_completion("你吃饭了吗？", "吃了。"))
        self.assertFalse(_question_needs_completion("你吃饭了吗？", "我很好！"))

    def test_visible_skip_keeps_answering_when_question_unanswered(self):
        from main import _visible_skip_should_block_reply, _context_has_unanswered_question

        class V:
            def __init__(self, skip): self.should_skip_reply = skip

        # 前有未答问题 + 最新表情包 → 不跳过（继续回复遗留问题）
        ctx = "[对方] 你的信息更新最新能看到什么日期的信息？\n[我] 正在确认\n[对方] [表情包]"
        self.assertFalse(_visible_skip_should_block_reply(ctx, V(True)))
        self.assertTrue(_context_has_unanswered_question(ctx))

        # 问题已被实质回答 → 表情包可跳过
        ctx2 = "[对方] 今天天气怎么样\n[我] 今天晴天，18到25度\n[对方] [表情包]"
        self.assertFalse(_context_has_unanswered_question(ctx2))

    def test_feedback_question_not_treated_as_new_question(self):
        from main import _context_has_unanswered_question, _managed_context_allows_generation
        from reply_engine import _is_feedback_question

        # 短反馈反问识别
        for text in ["是吗？", "真的？", "这样啊", "好吧", "对呀", "哦哦", "嗯嗯", "哈哈", "不会吧"]:
            self.assertTrue(_is_feedback_question(text), f"应识别为反馈反问: {text}")
        for text in ["今天天气怎么样？", "你的信息更新最新能看到什么日期？", "快递到哪了？", "那明天呢？"]:
            self.assertFalse(_is_feedback_question(text), f"不应识别为反馈反问: {text}")

        # 问题已回答后，用户发"是吗？真的？"→ 不算未答问题，不触发生成
        ctx = "[对方] 今天天气怎么样？\n[我] 今天晴天，18-25度\n[对方] 是吗？\n[对方] 真的？"
        self.assertFalse(_context_has_unanswered_question(ctx))
        self.assertFalse(_managed_context_allows_generation(ctx))

        # 反馈反问后还有真问题 → 仍应触发生成
        ctx2 = "[对方] 今天天气怎么样？\n[我] 今天晴天，18-25度\n[对方] 是吗？\n[对方] 那明天呢？"
        self.assertTrue(_managed_context_allows_generation(ctx2))

    def test_build_completion_context_appends_own_sent_text(self):
        from main import _build_completion_context

        context = _build_completion_context("[对方] 今天天气怎么样", "我查查")
        self.assertIn("[对方] 今天天气怎么样", context)
        self.assertIn("[我] 我查查", context)
        self.assertIn("补充完整", context)

        context2 = _build_completion_context("", "我查查")
        self.assertIn("[我] 我查查", context2)
        self.assertIn("补充完整", context2)

    def test_fallback_does_not_invent_topic_for_simple_ack(self):
        engine = ReplyEngine(AppSettings(), StyleMemory())
        context = "[对方] 8\"\n[对方] 5\"\n[我] 对 相当于没有资质 不能说医疗相关\n[对方] 好的\n[对方] 知道了"

        result = engine.generate(context, "好友")

        joined = " ".join(result)
        self.assertNotIn("市场", joined)
        self.assertNotIn("细分", joined)
        self.assertTrue(any("嗯" in item or "好" in item or "不用回" in item for item in result))

    def test_last_meaningful_other_message_skips_timestamps_and_own_messages(self):
        context = "\n".join(
            [
                "[我] 之前感觉你老晚还在上",
                "[对方] 是这样的",
                "[对方] 同事问题很多的",
                "[我] 这不挺正常的吗",
                "[对方] 昨天22:20",
                "[我] 我知道了，中流砥柱是这样的",
                "[对方] 我看见有的人的消息就血压上来了",
                "[对方] 不不不，普通员工而已。",
            ]
        )

        self.assertEqual(last_meaningful_other_message(context), "不不不，普通员工而已。")

    def test_latest_meaningful_message_reports_role(self):
        context = "[对方] 你们都是假的吧\n[我] 孩子考试加油哦"

        role, text = latest_meaningful_message(context)

        self.assertEqual(role, "我")
        self.assertEqual(text, "孩子考试加油哦")

    def test_latest_meaningful_message_handles_current_chinese_ocr_roles(self):
        context = "\n".join(
            [
                "[对方] 这里面放什么？先生?",
                "[我] 里面是微信电脑版",
                "[对方] 对。我的聚宝盆里面放点什么？先生",
            ]
        )

        role, text = latest_meaningful_message(context)

        self.assertEqual(role, "对方")
        self.assertEqual(text, "对。我的聚宝盆里面放点什么？先生")

    def test_unreplied_other_messages_returns_current_customer_turn(self):
        context = "\n".join(
            [
                "[对方] 这个放哪里",
                "[我] 我发你看下",
                "[对方] 收到了",
                "[对方] 放在房间哪个位置",
                "[对方] 需要每天动它吗",
            ]
        )

        result = unreplied_other_messages(context)

        self.assertEqual(result, ["收到了", "放在房间哪个位置", "需要每天动它吗"])

    def test_single_character_other_message_is_unreplied_turn(self):
        context = "\n".join(
            [
                "[对方] 没一个说对的",
                "[我] 那这烤肉是啥情况，你们自己烤的？",
                "[对方] 鱼",
            ]
        )

        self.assertEqual(unreplied_other_messages(context), ["鱼"])

    def test_managed_reply_selection_returns_empty_when_latest_is_own_message(self):
        context = "[对方] 你们都是假的吧\n[我] 孩子考试加油哦"

        selected = select_best_reply_for_context(context, ["祝福孩子", "加油"])

        self.assertEqual(selected, "")

    def test_managed_reply_selection_rejects_own_previous_reply(self):
        context = "\n".join(
            [
                "[对方] 这里面放什么？先生?",
                "[我] 里面是微信电脑版",
                "[对方] 对。我的聚宝盆里面放点什么？先生",
            ]
        )
        replies = ["里面是微信电脑版", "可以放在干净稳妥的位置", "微信电脑版"]

        selected = select_best_reply_for_context(context, replies)

        self.assertEqual(selected, "可以放在干净稳妥的位置")

    def test_fallback_handles_workplace_venting_with_context(self):
        engine = ReplyEngine(AppSettings(), StyleMemory())
        context = "\n".join(
            [
                "[我] 这不挺正常的吗",
                "[对方] 我看见有的人的消息就血压上来了",
                "[对方] 不不不，普通员工而已。",
            ]
        )

        result = engine.generate(context, "业务系统二开")

        joined = " ".join(result)
        self.assertNotIn("累了吧", joined)
        self.assertNotIn("知道了", joined)
        self.assertTrue(any("普通员工" in item or "血压" in item or "绷" in item for item in result))

    def test_fallback_handles_thanks_with_playful_nickname_without_echoing_nickname(self):
        engine = ReplyEngine(AppSettings(), StyleMemory())
        context = "\n".join(
            [
                "[对方] 好，我吃了饭去拿",
                "[对方] 王晨，呀背收了，谢谢儿子",
            ]
        )

        result = engine.generate(context, "韩洪英")

        joined = " ".join(result)
        self.assertNotIn("孝顺", joined)
        self.assertNotIn("儿子真", joined)
        self.assertTrue(any("客气" in item or "收到" in item or "拿到" in item for item in result))

    def test_thanks_scene_uses_configured_api(self):
        settings = AppSettings()
        settings.api_key = "configured"
        settings.reply_source_mode = "model"
        engine = ReplyEngine(settings, StyleMemory())
        context = "[对方] 好，我吃了饭去拿\n[对方] 王晨，呀背收了，谢谢儿子"

        with patch("reply_engine.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = {"choices": [{"message": {"content": "1. 不客气\n2. 收到就行\n3. 好的"}}]}
            result = engine.generate(context, "韩洪英")

        post.assert_called_once()
        self.assertTrue(any("客气" in item or "收到" in item or "拿到" in item for item in result))

    def test_job_card_reply_uses_context_instead_of_parroting_last_line(self):
        engine = ReplyEngine(AppSettings(), StyleMemory())
        context = "\n".join(
            [
                "[我] 牛逼",
                "[对方] 马钻",
                "[对方] 不客气",
                "[对方] 小那会",
                "[对方] 昭通4家单位招人，部分岗位有五险一金",
                "[对方] 量，关注昭通，就来这里！",
            ]
        )

        result = engine.generate(context, "人间油")

        joined = " ".join(result)
        self.assertNotIn("有兴趣吗", joined)
        self.assertNotIn("关注了", joined)
        self.assertTrue(any("岗位" in item or "五险" in item or "转给" in item or "机会" in item for item in result))

    def test_reply_candidates_do_not_copy_last_message_verbatim(self):
        engine = ReplyEngine(AppSettings(), StyleMemory())
        context = "[对方] 昭通4家单位招人，部分岗位有五险一金"

        result = engine.generate(context, "人间油")

        self.assertFalse(any("昭通4家单位招人" in item for item in result))

    def test_food_photo_context_gets_specific_food_reply(self):
        engine = ReplyEngine(AppSettings(), StyleMemory())
        context = "\n".join(
            [
                "[对方] 来自陕西的米线",
                "[对方] 麻麻的",
            ]
        )

        result = engine.generate(context, "璇仔")

        joined = " ".join(result)
        self.assertNotIn("我试试看", joined)
        self.assertNotIn("收到啦", joined)
        self.assertTrue(any("香" in item or "花椒" in item or "上头" in item or "米线" in item for item in result))

    def test_substantive_context_filters_low_effort_replies(self):
        engine = ReplyEngine(AppSettings(), StyleMemory())

        result = engine._quality_filter(
            ["收到啦", "我看下", "这个岗位看着还可以"],
            "[对方] 昭通4家单位招人，部分岗位有五险一金",
        )

        self.assertEqual(result, ["这个岗位看着还可以"])

    def test_quality_filter_removes_unsupported_training_jargon(self):
        engine = ReplyEngine(AppSettings(), StyleMemory())

        result = engine._quality_filter(
            ["福宝能量星，加油", "不是不是，刚刚没说清楚"],
            "[对方] 你们都是假的吧",
        )

        self.assertEqual(result, ["不是不是，刚刚没说清楚"])

    def test_how_to_receive_reply_uses_actionable_context(self):
        engine = ReplyEngine(AppSettings(), StyleMemory())
        context = "\n".join(
            [
                "[对方] 我想问一下领取使用方面的",
                "[对方] 我收到这个该放哪里",
            ]
        )

        result = engine.generate(context, "百年赤水")

        joined = " ".join(result)
        self.assertNotIn("不客气", joined)
        self.assertTrue(any("领" in item or "放" in item or "位置" in item or "方法" in item for item in result))

    def test_managed_reply_selection_prefers_contextual_candidate(self):
        context = "\n".join(
            [
                "[对方] 需要增强工作流，自动化提效，装 SuperPowers。",
                "[对方] 这10个插件一套配齐，Codex不只是写代码，而是帮你把活干完。",
            ]
        )
        replies = ["嗯嗯，挺厉害的", "这一套确实是效率工具箱了", "收到啦"]

        selected = select_best_reply_for_context(context, replies)

        self.assertEqual(selected, "这一套确实是效率工具箱了")

    def test_managed_reply_selection_avoids_parroting_last_message(self):
        context = "[对方] 真是高能量女孩"
        replies = ["真是高能量女孩", "哈哈，确实挺有能量的", "收到啦"]

        selected = select_best_reply_for_context(context, replies)

        self.assertEqual(selected, "哈哈，确实挺有能量的")

    def test_selection_avoids_training_jargon_not_in_current_context(self):
        context = "[对方] 你们都是假的吧"
        replies = ["福宝能量星，加油", "不是不是，刚刚没说清楚", "祝福孩子考试顺利"]

        selected = select_best_reply_for_context(context, replies)

        self.assertEqual(selected, "不是不是，刚刚没说清楚")

    def test_complex_requirement_filters_fragmented_replies(self):
        engine = ReplyEngine(AppSettings(), StyleMemory())

        result = engine._quality_filter(
            ["自动上架就行", "客服会对接", "可以按流程做：识别意图、上架活动、查单核销"],
            "[对方] 商家会在群里对接霸王餐活动，想用机器人理解意图，自动上架活动，售后查单核销",
        )

        self.assertEqual(result, ["可以按流程做：识别意图、上架活动、查单核销"])


class OCRCleaningTests(unittest.TestCase):
    def test_chat_lines_filter_numeric_visual_artifacts(self):
        items = [
            {"text": "7.48 r9 38±± 7185 m2", "x": 430, "left": 360, "right": 560, "y": 20},
            {"text": "收到，等您确认结果", "x": 120, "left": 90, "right": 260, "y": 60},
        ]

        lines = ChatOCR._to_chat_lines(items, 700)

        self.assertEqual([line.text for line in lines], ["收到，等您确认结果"])

    def test_chat_lines_filter_short_parameter_artifacts_from_cards(self):
        items = [
            {"text": "RP: 8+8SD", "x": 160, "left": 120, "right": 220, "y": 20},
            {"text": "这个截图里的参数不用回复", "x": 120, "left": 90, "right": 290, "y": 60},
        ]

        lines = ChatOCR._to_chat_lines(items, 700)

        self.assertEqual([line.text for line in lines], ["这个截图里的参数不用回复"])

    def test_chat_lines_filter_voice_durations_and_keep_real_text(self):
        items = [
            {"text": '8"', "x": 110, "y": 10},
            {"text": '5"', "x": 112, "y": 42},
            {"text": "好的", "x": 120, "y": 88},
            {"text": "知道了", "x": 120, "y": 130},
        ]

        lines = ChatOCR._to_chat_lines(items, 600)

        self.assertEqual([line.text for line in lines], ["好的", "知道了"])

    def test_voice_duration_can_be_kept_for_voice_bubble_detection(self):
        raw = [
            ([(90, 80), (145, 80), (145, 108), (90, 108)], '12"', 0.98),
            ([(90, 130), (210, 130), (210, 158), (90, 158)], "hello", 0.98),
        ]

        normal = ChatOCR._normalize_ocr_result(raw)
        voice = ChatOCR._normalize_ocr_result(raw, keep_voice=True)

        self.assertEqual([item["text"] for item in normal], ["hello"])
        self.assertEqual([item["text"] for item in voice], ['12"', "hello"])

    def test_latest_voice_bubble_detects_other_voice_duration(self):
        from PIL import Image

        ocr = ChatOCR(AppSettings())
        ocr._recognize = lambda image, keep_voice=False: [
            {"text": '12"', "x": 118, "left": 90, "right": 145, "y": 94}
        ] if keep_voice else []

        bubble = ocr.latest_voice_bubble(Image.new("RGB", (700, 300), "white"))

        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.role, "对方")
        self.assertEqual(bubble.seconds, 12)

    def test_chat_lines_filter_wechat_timestamps(self):
        items = [
            {"text": "昨天 22:20", "x": 300, "y": 10},
            {"text": "不不不，普通员工而已。", "x": 120, "y": 48},
        ]

        lines = ChatOCR._to_chat_lines(items, 600)

        self.assertEqual([line.text for line in lines], ["不不不，普通员工而已。"])

    def test_chat_lines_filter_qq_voice_input_hint(self):
        items = [
            {"text": "按住 Win + Alt，使用语音输入文字", "x": 260, "y": 10},
            {"text": "视频太大，打不开", "x": 120, "y": 48},
        ]

        lines = ChatOCR._to_chat_lines(items, 600)

        self.assertEqual([line.text for line in lines], ["视频太大，打不开"])

    def test_chat_lines_filter_chinese_weekday_timestamps(self):
        items = [
            {"text": "星期三 18:54", "x": 320, "y": 10},
            {"text": "星期三", "x": 120, "y": 42},
            {"text": "周三", "x": 125, "y": 72},
            {"text": "去违禁词111.mp4 183.65MB 11天后过期", "x": 130, "y": 112},
        ]

        lines = ChatOCR._to_chat_lines(items, 600)

        self.assertEqual([line.text for line in lines], ["去违禁词111.mp4 183.65MB 11天后过期"])

    def test_long_other_message_keeps_other_role_after_merge(self):
        items = [
            {"text": "需要增强工作流", "x": 120, "left": 70, "right": 210, "y": 40},
            {"text": "自动化提效", "x": 365, "left": 310, "right": 420, "y": 42},
        ]

        lines = ChatOCR._to_chat_lines(items, 600)

        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].role, "对方")
        self.assertIn("自动化提效", lines[0].text)

    def test_long_own_message_keeps_own_role_when_it_crosses_center(self):
        items = [
            {"text": "这个自动化思路可以先按这个方式处理", "x": 420, "left": 250, "right": 590, "y": 80},
        ]

        lines = ChatOCR._to_chat_lines(items, 720)

        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].role, "我")

    def test_role_detection_scales_with_window_width(self):
        narrow_items = [
            {"text": "右边长消息", "x": 300, "left": 160, "right": 438, "y": 80},
            {"text": "左边长消息", "x": 150, "left": 30, "right": 270, "y": 130},
        ]
        wide_items = [
            {"text": "右边长消息", "x": 720, "left": 430, "right": 1050, "y": 80},
            {"text": "左边长消息", "x": 260, "left": 70, "right": 560, "y": 130},
        ]

        narrow_lines = ChatOCR._to_chat_lines(narrow_items, 480)
        wide_lines = ChatOCR._to_chat_lines(wide_items, 1200)

        self.assertEqual([line.role for line in narrow_lines], ["我", "对方"])
        self.assertEqual([line.role for line in wide_lines], ["我", "对方"])

    def test_green_bubble_color_marks_own_message(self):
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (700, 300), "white")
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((330, 70, 650, 118), radius=8, fill=(149, 236, 105))
        items = [{"text": "这条是我发的", "x": 430, "left": 340, "right": 520, "y": 94}]

        lines = ChatOCR._to_chat_lines(items, 700, image)

        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].role, "我")

    def test_embedded_image_green_text_without_avatar_is_ignored(self):
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (700, 300), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((390, 82, 650, 104), fill=(149, 236, 105))
        draw.rectangle((390, 114, 650, 136), fill=(149, 236, 105))
        items = [
            {"text": "用codex做了生成模板视频的软件", "x": 520, "left": 400, "right": 640, "y": 93},
            {"text": "用codex升级了生成模板视频的软件", "x": 520, "left": 400, "right": 640, "y": 125},
        ]

        lines = ChatOCR._to_chat_lines(items, 700, image)

        self.assertEqual(lines, [])

    def test_single_word_small_bubble_without_avatar_is_kept(self):
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (700, 300), "white")
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((90, 82, 132, 112), radius=8, fill=(235, 235, 235))
        items = [{"text": "鱼", "x": 112, "left": 104, "right": 120, "y": 96}]

        lines = ChatOCR._to_chat_lines(items, 700, image)

        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].role, "对方")
        self.assertEqual(lines[0].text, "鱼")

    def test_bottom_boundary_prefers_lower_input_line_over_image_line(self):
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (700, 620), "white")
        draw = ImageDraw.Draw(image)
        left, right = 180, 680
        draw.line((left + 25, 350, right - 25, 350), fill=(224, 224, 224), width=2)
        draw.line((left + 25, 530, right - 25, 530), fill=(224, 224, 224), width=2)

        bottom = _detect_chat_bottom_boundary(image, left, right, "wechat")

        self.assertIsNotNone(bottom)
        self.assertGreaterEqual(bottom or 0, 520)

    def test_visible_image_text_can_be_read_without_being_chat_context(self):
        ocr = ChatOCR(AppSettings())
        ocr._recognize = lambda image: [
            {"text": "用codex做了生成模板视频的软件", "x": 520, "left": 400, "right": 640, "y": 93},
            {"text": "用codex升级了生成模板视频的软件", "x": 520, "left": 400, "right": 640, "y": 125},
        ]

        text = ocr.read_visible_text(object())

        self.assertIn("codex", text)
        self.assertIn("生成模板视频", text)

    def test_short_ascii_visual_artifact_is_filtered_from_chat_context(self):
        self.assertTrue(_is_noise_text("ETJ"))
        self.assertTrue(_is_noise_text("E1J"))

    def test_gray_bubble_color_marks_other_message(self):
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (700, 300), "white")
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((40, 70, 280, 118), radius=8, fill=(235, 235, 235))
        items = [{"text": "这条是对方发的", "x": 150, "left": 62, "right": 235, "y": 94}]

        lines = ChatOCR._to_chat_lines(items, 700, image)

        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].role, "对方")

    def test_right_avatar_marks_own_even_when_text_crosses_center(self):
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (700, 300), "white")
        draw = ImageDraw.Draw(image)
        draw.ellipse((620, 72, 662, 114), fill=(76, 130, 190))
        draw.rectangle((628, 80, 654, 106), fill=(205, 160, 95))
        items = [{"text": "这是一条很长的我方消息，文字已经跨到中间", "x": 340, "left": 120, "right": 560, "y": 94}]

        lines = ChatOCR._to_chat_lines(items, 700, image)

        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].role, "我")

    def test_left_avatar_marks_other_even_when_text_is_wide(self):
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (700, 300), "white")
        draw = ImageDraw.Draw(image)
        draw.ellipse((38, 72, 80, 114), fill=(88, 115, 160))
        draw.rectangle((46, 80, 72, 106), fill=(210, 170, 110))
        items = [{"text": "这是一条很长的对方消息，文字也占了很宽", "x": 370, "left": 160, "right": 580, "y": 94}]

        lines = ChatOCR._to_chat_lines(items, 700, image)

        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].role, "对方")

    def test_wrapped_long_bubble_lines_are_merged(self):
        items = [
            {"text": "商家会在群里对接面发放霸王餐活动，都是我们客服处理的，", "x": 210, "left": 80, "right": 520, "y": 100},
            {"text": "我想用机器人理解他们的意图，自动上架霸王餐活动，", "x": 205, "left": 82, "right": 510, "y": 126},
            {"text": "给他们做售后查单查核销", "x": 180, "left": 84, "right": 410, "y": 152},
        ]

        lines = ChatOCR._to_chat_lines(items, 900)

        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].role, "对方")
        self.assertIn("机器人理解他们的意图", lines[0].text)
        self.assertIn("售后查单查核销", lines[0].text)

    def test_wrapped_other_bubble_keeps_role_when_tail_text_is_right_aligned(self):
        items = [
            {"text": "today is the last round, after this a few customers may reply", "x": 290, "left": 70, "right": 520, "y": 100},
            {"text": "send the follow-up video", "x": 560, "left": 500, "right": 640, "y": 126},
        ]

        lines = ChatOCR._to_chat_lines(items, 700)

        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].role, "对方")
        self.assertIn("send the follow-up video", lines[0].text)

    def test_nearby_other_and_own_bubbles_are_not_merged_as_wrapped_text(self):
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (700, 360), "white")
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((70, 80, 430, 116), radius=8, fill=(235, 235, 235))
        draw.rounded_rectangle((410, 124, 640, 160), radius=8, fill=(149, 236, 105))
        draw.rounded_rectangle((70, 168, 390, 204), radius=8, fill=(235, 235, 235))
        items = [
            {"text": "first customer question about batch sending", "x": 250, "left": 92, "right": 405, "y": 98},
            {"text": "own answer, split send and watch limit", "x": 520, "left": 430, "right": 620, "y": 142},
            {"text": "what if it becomes violation later", "x": 230, "left": 92, "right": 365, "y": 186},
        ]

        lines = ChatOCR._to_chat_lines(items, 700, image)

        self.assertEqual(len(lines), 3)
        self.assertEqual([line.role for line in lines], ["对方", "我", "对方"])
        self.assertEqual(lines[-1].text, "what if it becomes violation later")


class VisibleContentTests(unittest.TestCase):
    def test_link_context_is_visible_link_without_fetching(self):
        context = "[对方] 链接：https://pan.quark.cn/s/025f43b57d39 怎么样?"

        visible = infer_visible_content_from_context(context)

        self.assertEqual(visible.message_type, "link")
        self.assertIn("不打开链接", visible.summary)
        self.assertFalse(visible.should_skip_reply)

    def test_sticker_context_skips_reply(self):
        context = "[对方] [表情]"

        visible = infer_visible_content_from_context(context)

        self.assertEqual(visible.message_type, "sticker")
        self.assertTrue(visible.should_skip_reply)

    def test_augmented_context_includes_visible_understanding(self):
        context = "[对方] 昭通4家单位招人，部分岗位有五险一金"
        visible = VisibleContent(
            message_type="card",
            summary="招聘卡片：昭通4家单位招人，部分岗位有五险一金",
            confidence="high",
            source="screen_ocr",
            should_skip_reply=False,
        )

        augmented = build_augmented_context(context, visible)

        self.assertIn("[可见内容理解] 类型：card", augmented)
        self.assertIn("招聘卡片", augmented)

    def test_augmented_context_replaces_existing_visible_understanding(self):
        context = "\n".join(
            [
                "[对方] [图片]",
                "[可见内容理解] 类型：image；置信度：high；来源：zhipu_vision；旧图片内容",
            ]
        )
        visible = VisibleContent(
            message_type="image",
            summary="新图片内容：白色花卉上衣",
            confidence="high",
            source="zhipu_vision",
            should_skip_reply=False,
        )

        augmented = build_augmented_context(context, visible)

        self.assertEqual(augmented.count("[可见内容理解]"), 1)
        self.assertIn("新图片内容：白色花卉上衣", augmented)
        self.assertNotIn("旧图片内容", augmented)


    def test_vision_analyzer_uses_dedicated_vision_api_settings(self):
        from PIL import Image

        settings = AppSettings()
        settings.api_provider = "deepseek"
        settings.api_key = "text-key"
        settings.base_url = "https://api.deepseek.com"
        settings.model = "deepseek-chat"
        settings.vision_provider = "zhipu"
        settings.vision_api_key = "vision-key"
        settings.vision_base_url = "https://vision.example/api/paas/v4"
        settings.vision_model = "glm-4v-flash"
        image = Image.new("RGB", (12, 12), "white")

        with patch("visible_content.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {
                "choices": [
                    {
                        "message": {
                            "content": '{"message_type":"image","summary":"图片里有一张菜单截图","confidence":"high","should_skip_reply":false}'
                        }
                    }
                ]
            }
            post.return_value.raise_for_status.return_value = None

            visible = VisibleContentAnalyzer(settings).analyze("[对方] [图片]", image=image)

        self.assertEqual(visible.message_type, "image")
        self.assertIn("菜单截图", visible.summary)
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://vision.example/api/paas/v4/chat/completions")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer vision-key")
        self.assertEqual(kwargs["json"]["model"], "glm-4v-flash")

    def test_vision_analyzer_runs_for_text_context_when_large_image_is_visible(self):
        from PIL import Image, ImageDraw

        settings = AppSettings()
        settings.vision_provider = "zhipu"
        settings.vision_api_key = "vision-key"
        settings.vision_base_url = "https://vision.example/api/paas/v4"
        settings.vision_model = "glm-4v-flash"
        image = Image.new("RGB", (420, 420), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((90, 120, 260, 330), fill=(70, 70, 70))

        with patch("visible_content.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {
                "choices": [
                    {
                        "message": {
                            "content": '{"message_type":"image","summary":"图片里有人走在路上，疑似表示头痛还是来了","confidence":"high","should_skip_reply":false}'
                        }
                    }
                ]
            }
            post.return_value.raise_for_status.return_value = None

            visible = VisibleContentAnalyzer(settings).analyze("[对方] oh no 头痛还是来了", image=image)

        self.assertEqual(visible.source, "zhipu_vision")
        self.assertIn("走在路上", visible.summary)
        _args, kwargs = post.call_args
        prompt_text = kwargs["json"]["messages"][0]["content"][0]["text"]
        self.assertIn("最后一句文字和图片同时出现", prompt_text)
        self.assertIn("把它们当成同一轮消息", prompt_text)
        self.assertIn("图片与最后一句文字的关系", prompt_text)

    def test_vision_analyzer_rejects_unsupported_customer_said_summary_for_image_placeholder(self):
        from PIL import Image

        settings = AppSettings()
        settings.vision_provider = "zhipu"
        settings.vision_api_key = "vision-key"
        image = Image.new("RGB", (12, 12), "white")

        with patch("visible_content.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {
                "choices": [
                    {
                        "message": {
                            "content": '{"message_type":"text","summary":"' + chr(0x5BA2) + chr(0x6237) + chr(0x8BF4) + chr(0x5E7F) + chr(0x57DF) + chr(0x7F51) + '","confidence":"high","should_skip_reply":false}'
                        }
                    }
                ]
            }
            post.return_value.raise_for_status.return_value = None

            visible = VisibleContentAnalyzer(settings).analyze("[" + chr(0x5BF9) + chr(0x65B9) + "] [" + chr(0x56FE) + chr(0x7247) + "]", image=image)

        self.assertNotIn(chr(0x5E7F) + chr(0x57DF) + chr(0x7F51), visible.summary)
        self.assertTrue(visible.should_skip_reply)
        self.assertEqual(visible.confidence, "low")

    def test_vision_analyzer_rejects_narrative_image_hallucination_without_context(self):
        from PIL import Image

        settings = AppSettings()
        settings.vision_provider = "zhipu"
        settings.vision_api_key = "vision-key"
        image = Image.new("RGB", (12, 12), "white")
        summary = (
            "\u5bf9\u65b9\u5206\u4eab\u4e86\u4e00\u6bb5\u5173\u4e8e"
            "\u201c\u6211\u7684\u6587\u5b57\u63cf\u8ff0\uff0c\u60f3\u5230"
            "\u67d0\u4eba\u4e00\u76f4\u966a\u4f34\u5e76\u8868\u8fbe\u4e86"
            "\u6df1\u6df1\u7684\u611f\u6fc0\u4e4b\u60c5\u201d"
        )

        with patch("visible_content.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"message_type":"text","summary":"'
                                + summary
                                + '","confidence":"high","should_skip_reply":false}'
                            )
                        }
                    }
                ]
            }
            post.return_value.raise_for_status.return_value = None

            visible = VisibleContentAnalyzer(settings).analyze("", image=image)

        self.assertNotIn(summary, visible.summary)
        self.assertTrue(visible.should_skip_reply)
        self.assertEqual(visible.confidence, "low")


class SettingsTests(unittest.TestCase):
    def test_formal_ui_exposes_only_knowledge_source_and_all_managed_modes(self):
        import float_ui

        self.assertEqual([value for value, _label in float_ui.SOURCE_MODE_OPTIONS], ["knowledge_only"])
        self.assertEqual(
            [value for value, _label in float_ui.MANAGED_MODE_OPTIONS],
            ["off", "observe", "controlled", "full"],
        )

    def test_managed_allowlist_parser_deduplicates_in_display_order(self):
        import float_ui

        self.assertEqual(
            float_ui.parse_managed_allowed_chats("客户甲\n客户乙, 客户甲；客户丙"),
            ["客户甲", "客户乙", "客户丙"],
        )

    def test_managed_mode_callback_preserves_new_mode_with_legacy_boolean_callback(self):
        from float_ui import ReplyAssistantUI

        class FakeVar:
            def __init__(self, value=None):
                self.value = value

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        callback_values = []
        ui = ReplyAssistantUI.__new__(ReplyAssistantUI)
        ui.settings = AppSettings()
        ui.managed_var = FakeVar("off")
        ui.auto_var = FakeVar(False)
        ui.callbacks = {"managed_changed": callback_values.append}

        ui._set_managed_mode("controlled")

        self.assertEqual(ui.settings.managed_mode, "controlled")
        self.assertTrue(ui.settings.auto_recognize)
        self.assertEqual(callback_values, [False])

    def test_source_mode_callback_cannot_enable_free_generation(self):
        from float_ui import ReplyAssistantUI

        class FakeVar:
            def __init__(self):
                self.value = None

            def set(self, value):
                self.value = value

        callback_values = []
        ui = ReplyAssistantUI.__new__(ReplyAssistantUI)
        ui.settings = AppSettings()
        ui.source_mode_var = FakeVar()
        ui.source_mode_label_var = FakeVar()
        ui.callbacks = {"source_mode_changed": callback_values.append}
        ui._refresh_source_menu = lambda: None

        ui._set_source_mode("model")

        self.assertEqual(ui.settings.reply_source_mode, "knowledge_only")
        self.assertEqual(callback_values, [])

    def test_fixed_source_mode_ignores_real_legacy_callback_and_remains_reloadable(self):
        import main as main_module
        from float_ui import ReplyAssistantUI

        class FakeVar:
            def __init__(self):
                self.value = None

            def set(self, value):
                self.value = value

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            settings = AppSettings(config_path=path)
            legacy_app = types.SimpleNamespace(
                settings=settings,
                ui=types.SimpleNamespace(set_status=lambda _text: None),
            )
            ui = ReplyAssistantUI.__new__(ReplyAssistantUI)
            ui.settings = settings
            ui.source_mode_var = FakeVar()
            ui.source_mode_label_var = FakeVar()
            ui._refresh_source_menu = lambda: None
            ui.callbacks = {
                "source_mode_changed": lambda mode: main_module.WeChatStyleReplyAssistant._source_mode_changed(
                    legacy_app,
                    mode,
                )
            }

            ui._set_source_mode("model")
            loaded = AppSettings(config_path=path).load()

            self.assertEqual(settings.reply_source_mode, "knowledge_only")
            self.assertEqual(loaded.reply_source_mode, "knowledge_only")

    def test_observe_and_controlled_survive_real_legacy_managed_callback_on_disk(self):
        import main as main_module
        from float_ui import ReplyAssistantUI

        class FakeVar:
            def __init__(self, value=None):
                self.value = value

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        for mode in ("observe", "controlled"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "settings.json"
                settings = AppSettings(config_path=path)
                legacy_app = types.SimpleNamespace(
                    settings=settings,
                    ui=types.SimpleNamespace(set_status=lambda _text: None),
                )
                ui = ReplyAssistantUI.__new__(ReplyAssistantUI)
                ui.settings = settings
                ui.managed_var = FakeVar("off")
                ui.auto_var = FakeVar(False)
                ui.callbacks = {
                    "managed_changed": lambda enabled: main_module.WeChatStyleReplyAssistant._managed_changed(
                        legacy_app,
                        enabled,
                    )
                }

                ui._set_managed_mode(mode)
                loaded = AppSettings(config_path=path).load()

                self.assertEqual(settings.managed_mode, mode)
                self.assertEqual(loaded.managed_mode, mode)

    def test_settings_dialog_managed_mode_path_uses_same_callback_safe_transition(self):
        import main as main_module
        from float_ui import ReplyAssistantUI

        class FakeVar:
            def __init__(self, value=None):
                self.value = value

            def set(self, value):
                self.value = value

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            settings = AppSettings(config_path=path)
            legacy_app = types.SimpleNamespace(
                settings=settings,
                ui=types.SimpleNamespace(set_status=lambda _text: None),
            )
            ui = ReplyAssistantUI.__new__(ReplyAssistantUI)
            ui.settings = settings
            ui.managed_var = FakeVar("off")
            ui.auto_var = FakeVar(False)
            ui.callbacks = {
                "managed_changed": lambda enabled: main_module.WeChatStyleReplyAssistant._managed_changed(
                    legacy_app,
                    enabled,
                )
            }

            ui._set_managed_mode("controlled", persist=False)
            settings.save()

            self.assertEqual(AppSettings(config_path=path).load().managed_mode, "controlled")
    def test_model_prompt_is_readable_chinese_and_forbids_invented_data(self):
        settings = AppSettings()
        settings.reply_source_mode = "model"
        settings.enable_web_search = False
        engine = ReplyEngine(settings, StyleMemory())

        prompt = engine._build_prompt(
            "[对方] 这个活动大概能带来多少订单\n[我] 这个要看商家情况\n[对方] 那你估计能有多少",
            "客户",
        )

        self.assertIn("不能编造", prompt)
        self.assertIn("不知道", prompt)
        self.assertIn("不能自己编数字", prompt)
        self.assertNotIn("鍥炲", prompt)
        self.assertNotIn("瀵规柟", prompt)

    def test_search_prompt_keeps_nonfabrication_invariants(self):
        settings = AppSettings()
        settings.reply_source_mode = "model"
        engine = ReplyEngine(settings, StyleMemory())

        with patch(
            "reply_engine.search_web",
            return_value=[{"title": "参考资料", "snippet": "一般需要 5 个发起人", "url": "https://example.com"}],
        ):
            prompt = engine._build_prompt("[对方] 成立社会团体需要多少个发起人？", "客户")

        self.assertIn("不能编造", prompt)
        self.assertIn("不知道", prompt)
        self.assertIn("不能自己编数字", prompt)

    def test_managed_send_preflight_blocks_when_window_or_turn_changed(self):
        import main

        original = ChatWindow(
            hwnd=9,
            title="QQ contact",
            rect=WindowRect(10, 20, 970, 660),
            platform=ChatPlatform.for_key("qq"),
        )
        other_window = ChatWindow(
            hwnd=88,
            title="Codex",
            rect=WindowRect(10, 20, 970, 660),
            platform=ChatPlatform.for_key("qq"),
        )
        context = "[对方] 这个放哪里"

        allowed, _reason = main._managed_send_preflight_allows(original, original, context, context)
        self.assertTrue(allowed)

        same_chat_new_handle = ChatWindow(
            hwnd=19,
            title="QQ contact",
            rect=WindowRect(10, 20, 970, 660),
            platform=ChatPlatform.for_key("qq"),
        )
        allowed, _reason = main._managed_send_preflight_allows(original, same_chat_new_handle, context, context)
        self.assertTrue(allowed)

        allowed, _reason = main._managed_send_preflight_allows(original, other_window, context, context)
        self.assertFalse(allowed)

        changed_context = "[对方] 这个放哪里\n[我] 放书房就行"
        allowed, _reason = main._managed_send_preflight_allows(original, original, context, changed_context)
        self.assertFalse(allowed)

        new_turn_context = "[对方] 这个放哪里\n[我] 放书房就行\n[对方] 那客厅可以吗"
        allowed, _reason = main._managed_send_preflight_allows(original, original, context, new_turn_context)
        self.assertFalse(allowed)

    def test_managed_send_preflight_allows_same_image_with_different_vision_wording(self):
        import main

        window = ChatWindow(
            hwnd=9,
            title="微信 contact",
            rect=WindowRect(10, 20, 970, 660),
            platform=ChatPlatform.for_key("wechat"),
        )
        generated = "\n".join(
            [
                "[对方] [图片]",
                "[可见内容理解] 类型：image；置信度：high；来源：zhipu_vision；一张展示白色上衣的图片，衣服上有花卉刺绣图案",
            ]
        )
        latest = "\n".join(
            [
                "[对方] [图片]",
                "[可见内容理解] 类型：image；置信度：high；来源：zhipu_vision；一张白色短袖上衣，带有花朵刺绣和绿色边线",
            ]
        )

        allowed, reason = main._managed_send_preflight_allows(window, window, generated, latest)

        self.assertTrue(allowed, reason)

    def test_managed_send_preflight_blocks_when_image_content_changes(self):
        import main

        window = ChatWindow(
            hwnd=9,
            title="微信 contact",
            rect=WindowRect(10, 20, 970, 660),
            platform=ChatPlatform.for_key("wechat"),
        )
        generated = "\n".join(
            [
                "[对方] [图片]",
                "[可见内容理解] 类型：image；置信度：high；来源：zhipu_vision；一张白色上衣的图片，衣服上有花卉刺绣图案",
            ]
        )
        latest = "\n".join(
            [
                "[对方] [图片]",
                "[可见内容理解] 类型：image；置信度：high；来源：zhipu_vision；一张表格截图，上面有蓝色高亮数据和日期列表",
            ]
        )

        allowed, reason = main._managed_send_preflight_allows(window, window, generated, latest)

        self.assertFalse(allowed)
        self.assertIn("变化", reason)

    def test_generation_result_is_discarded_when_context_changed(self):
        window = ChatWindow(
            hwnd=9,
            title="QQ contact",
            rect=WindowRect(10, 20, 970, 660),
            platform=ChatPlatform.for_key("qq"),
        )
        old_context = "[对方] 第一条问题"
        new_context = "[对方] 第一条问题\n[对方] 第二条问题"

        self.assertTrue(_generation_result_still_current(old_context, window, old_context, window))
        self.assertFalse(_generation_result_still_current(old_context, window, new_context, window))

    def test_generation_result_allows_same_chat_after_window_handle_changes(self):
        old_window = ChatWindow(
            hwnd=9,
            title="QQ contact",
            rect=WindowRect(10, 20, 970, 660),
            platform=ChatPlatform.for_key("qq"),
        )
        live_window = ChatWindow(
            hwnd=18,
            title="QQ contact",
            rect=WindowRect(10, 20, 970, 660),
            platform=ChatPlatform.for_key("qq"),
        )
        context = "[对方] 第一条问题"

        self.assertTrue(_generation_result_still_current(context, old_window, context, live_window))

    def test_managed_generation_result_survives_qq_ocr_noise_for_same_turn(self):
        window = ChatWindow(
            hwnd=9,
            title="QQ contact",
            rect=WindowRect(10, 20, 970, 660),
            platform=ChatPlatform.for_key("qq"),
        )
        other = "[" + chr(0x5BF9) + chr(0x65B9) + "] "
        target = chr(0x8FD9) + chr(0x4E2A) + chr(0x653E) + chr(0x54EA) + chr(0x91CC)
        target_turn = [target]
        noisy_context = "\n".join(
            [
                other + "12:01",
                other + target,
                other + (chr(0x56FE) + chr(0x7247)),
            ]
        )

        self.assertTrue(_managed_generation_result_still_current(target_turn, window, noisy_context, window))

    def test_managed_generation_result_is_discarded_when_qq_gets_new_turn_message(self):
        window = ChatWindow(
            hwnd=9,
            title="QQ contact",
            rect=WindowRect(10, 20, 970, 660),
            platform=ChatPlatform.for_key("qq"),
        )
        other = "[" + chr(0x5BF9) + chr(0x65B9) + "] "
        target = chr(0x8FD9) + chr(0x4E2A) + chr(0x653E) + chr(0x54EA) + chr(0x91CC)
        new_message = chr(0x8FD8) + chr(0x6709) + chr(0x4E00) + chr(0x4E2A) + chr(0x5C0F) + chr(0x7684) + chr(0x600E) + chr(0x4E48) + chr(0x653E)
        target_turn = [target]
        new_context = "\n".join(
            [
                other + target,
                other + new_message,
            ]
        )

        self.assertFalse(_managed_generation_result_still_current(target_turn, window, new_context, window))

    def test_managed_send_preflight_failure_does_not_mark_turn_handled(self):
        import queue
        import time
        import main

        app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
        app.settings = AppSettings()
        app.settings.managed_auto_reply = True
        app.settings.auto_reply_delay_seconds = 0
        app.engine = types.SimpleNamespace(last_error="", last_source="DeepSeek API")
        app.queue = queue.Queue()
        app.sender = types.SimpleNamespace(send=lambda *_args, **_kwargs: (True, "sent"))
        app.detector = types.SimpleNamespace(window_from_hwnd=lambda _hwnd: live_window)
        app.current_window = ChatWindow(
            hwnd=9,
            title="QQ contact",
            rect=WindowRect(10, 20, 970, 660),
            platform=ChatPlatform.for_key("qq"),
        )
        live_window = ChatWindow(
            hwnd=9,
            title="Other QQ contact",
            rect=WindowRect(10, 20, 970, 660),
            platform=ChatPlatform.for_key("qq"),
        )
        other = "[" + chr(0x5BF9) + chr(0x65B9) + "] "
        target = chr(0x8FD9) + chr(0x4E2A) + chr(0x653E) + chr(0x54EA) + chr(0x91CC)
        context = other + target
        app._managed_preflight_context = lambda _window: context
        app.last_auto_reply_signature = ""
        app.pending_auto_reply_signature = ""
        app.handled_auto_reply_signatures = set()
        app.managed_send_inflight = False
        app.managed_waiting_for_own_echo = False
        app.last_managed_sent_text = ""
        app.managed_last_handled_turn_messages = []
        app.managed_last_handled_turn_key = ""
        app.managed_last_sent_at = 0.0
        app.managed_last_handled_partner = ""

        app._maybe_managed_send(context, ["放书房就行"])
        time.sleep(0.05)

        self.assertEqual(app.handled_auto_reply_signatures, set())
        self.assertEqual(app.managed_last_handled_turn_messages, [])
        self.assertEqual(app.last_auto_reply_signature, "")
        self.assertFalse(app.managed_send_inflight)

    def test_managed_send_preflight_uses_current_foreground_chat_without_hwnd_lock(self):
        import queue
        import time
        import main

        app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
        app.settings = AppSettings()
        app.settings.managed_auto_reply = True
        app.settings.auto_reply_delay_seconds = 0
        app.memory = types.SimpleNamespace(
            vector_ready=True,
            assert_ready_for_managed_reply=lambda: None,
        )
        app.engine = types.SimpleNamespace(last_error="", last_source="DeepSeek API")
        app.queue = queue.Queue()
        app.sender = types.SimpleNamespace(
            calls=[],
            send=lambda window, text: app.sender.calls.append((window.hwnd, text)) or (True, "sent"),
        )
        app.current_window = ChatWindow(
            hwnd=9,
            title="QQ contact",
            rect=WindowRect(10, 20, 970, 660),
            platform=ChatPlatform.for_key("qq"),
        )
        foreground = ChatWindow(
            hwnd=19,
            title="QQ contact",
            rect=WindowRect(10, 20, 970, 660),
            platform=ChatPlatform.for_key("qq"),
        )
        app.detector = types.SimpleNamespace(
            foreground_chat=lambda: foreground,
            window_from_hwnd=lambda _hwnd: (_ for _ in ()).throw(AssertionError("hwnd lookup should not be used")),
        )
        other = "[" + chr(0x5BF9) + chr(0x65B9) + "] "
        target = chr(0x8FD9) + chr(0x4E2A) + chr(0x653E) + chr(0x54EA) + chr(0x91CC)
        context = other + target
        app._managed_preflight_context = lambda _window: context
        app.last_auto_reply_signature = ""
        app.pending_auto_reply_signature = ""
        app.handled_auto_reply_signatures = set()
        app.managed_send_inflight = False
        app.managed_waiting_for_own_echo = False
        app.last_managed_sent_text = ""
        app.managed_last_handled_turn_messages = []
        app.managed_last_handled_turn_key = ""
        app.managed_last_sent_at = 0.0
        app.managed_last_handled_partner = ""

        app._maybe_managed_send(context, ["放书房就行"])
        time.sleep(0.05)

        self.assertEqual(app.sender.calls, [(19, "放书房就行")])

    def test_managed_send_falls_back_to_discovered_same_chat_when_foreground_unavailable(self):
        import queue
        import time
        import main

        app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
        app.settings = AppSettings()
        app.settings.managed_auto_reply = True
        app.settings.auto_reply_delay_seconds = 0
        app.memory = types.SimpleNamespace(
            vector_ready=True,
            assert_ready_for_managed_reply=lambda: None,
        )
        app.engine = types.SimpleNamespace(last_error="", last_source="DeepSeek API")
        app.queue = queue.Queue()
        app.sender = types.SimpleNamespace(
            calls=[],
            send=lambda window, text: app.sender.calls.append((window.hwnd, text)) or (True, "sent"),
        )
        app.current_window = ChatWindow(
            hwnd=9,
            title="微信 contact",
            rect=WindowRect(10, 20, 970, 660),
            platform=ChatPlatform.for_key("wechat"),
        )
        discovered = ChatWindow(
            hwnd=29,
            title="微信 contact",
            rect=WindowRect(10, 20, 970, 660),
            platform=ChatPlatform.for_key("wechat"),
        )
        app.detector = types.SimpleNamespace(
            foreground_chat=lambda: None,
            any_chat_window=lambda: discovered,
        )
        context = "[对方] 这个人有4个金"
        app._managed_preflight_context = lambda _window: context
        app.last_auto_reply_signature = ""
        app.pending_auto_reply_signature = ""
        app.handled_auto_reply_signatures = set()
        app.managed_send_inflight = False
        app.managed_waiting_for_own_echo = False
        app.last_managed_sent_text = ""
        app.managed_last_handled_turn_messages = []
        app.managed_last_handled_turn_key = ""
        app.managed_last_sent_at = 0.0
        app.managed_last_handled_partner = ""

        app._maybe_managed_send(context, ["对，金正恩名字里就有四个金"])
        time.sleep(0.05)

        self.assertEqual(app.sender.calls, [(29, "对，金正恩名字里就有四个金")])

    def test_managed_send_uses_current_context_when_preflight_ocr_is_temporarily_empty(self):
        import queue
        import time
        import main

        app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
        app.settings = AppSettings()
        app.settings.managed_auto_reply = True
        app.settings.auto_reply_delay_seconds = 0
        app.memory = types.SimpleNamespace(
            vector_ready=True,
            assert_ready_for_managed_reply=lambda: None,
        )
        app.engine = types.SimpleNamespace(last_error="", last_source="DeepSeek API")
        app.queue = queue.Queue()
        app.sender = types.SimpleNamespace(
            calls=[],
            send=lambda window, text: app.sender.calls.append((window.hwnd, text)) or (True, "sent"),
        )
        window = ChatWindow(
            hwnd=9,
            title="寰俊 contact",
            rect=WindowRect(10, 20, 970, 660),
            platform=ChatPlatform.for_key("wechat"),
        )
        app.current_window = window
        app.current_context = "[" + chr(0x5BF9) + chr(0x65B9) + "] " + (
            chr(0x8FD9) + chr(0x4E2A) + chr(0x600E) + chr(0x4E48) + chr(0x653E)
        )
        app.detector = types.SimpleNamespace(
            foreground_chat=lambda: window,
            any_chat_window=lambda: None,
        )
        app._managed_preflight_context = lambda _window: ""
        app.last_auto_reply_signature = ""
        app.pending_auto_reply_signature = ""
        app.handled_auto_reply_signatures = set()
        app.managed_send_inflight = False
        app.managed_waiting_for_own_echo = False
        app.last_managed_sent_text = ""
        app.managed_last_handled_turn_messages = []
        app.managed_last_handled_turn_key = ""
        app.managed_last_sent_at = 0.0
        app.managed_last_handled_partner = ""

        app._maybe_managed_send(app.current_context, ["放客厅干净的位置就行"])
        time.sleep(0.05)

        self.assertEqual(app.sender.calls, [(9, "放客厅干净的位置就行")])

    def test_managed_preflight_uses_cached_context_when_latest_ocr_misses_other_turn(self):
        import main

        window = ChatWindow(
            hwnd=9,
            title="wechat contact",
            rect=WindowRect(10, 20, 970, 660),
            platform=ChatPlatform.for_key("wechat"),
        )
        generated_context = "\n".join(
            [
                "[对方] first customer question about batch sending",
                "[我] own answer, split send and watch limit",
                "[对方] what if it becomes violation later",
            ]
        )
        temporary_latest_context = "\n".join(
            [
                "[对方] first customer question about batch sending",
                "[我] own answer, split send and watch limit",
            ]
        )

        effective = main._managed_preflight_context_or_cached(
            window,
            window,
            generated_context,
            temporary_latest_context,
            generated_context,
        )
        allowed, reason = main._managed_send_preflight_allows(window, window, generated_context, effective)

        self.assertEqual(effective, generated_context)
        self.assertTrue(allowed, reason)

    def test_managed_preflight_does_not_use_cache_after_manual_own_reply(self):
        import main

        window = ChatWindow(
            hwnd=9,
            title="wechat contact",
            rect=WindowRect(10, 20, 970, 660),
            platform=ChatPlatform.for_key("wechat"),
        )
        generated_context = "[对方] what if it becomes violation later"
        latest_context = "\n".join(
            [
                "[对方] what if it becomes violation later",
                "[我] I already answered manually",
            ]
        )

        effective = main._managed_preflight_context_or_cached(
            window,
            window,
            generated_context,
            latest_context,
            generated_context,
        )
        allowed, _reason = main._managed_send_preflight_allows(window, window, generated_context, effective)

        self.assertEqual(effective, latest_context)
        self.assertFalse(allowed)

    def test_managed_preflight_context_includes_visible_image_understanding(self):
        import main

        app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
        app.ocr = types.SimpleNamespace(
            capture=lambda _window, allow_screen_fallback=False: object(),
            read_image=lambda _image: "[对方] [图片]",
        )
        app.visible_analyzer = types.SimpleNamespace(
            analyze=lambda _context, _image: VisibleContent(
                message_type="image",
                summary="一张花色衣服照片",
                confidence="high",
                source="zhipu_vision",
            )
        )
        window = ChatWindow(
            hwnd=9,
            title="微信 contact",
            rect=WindowRect(10, 20, 970, 660),
            platform=ChatPlatform.for_key("wechat"),
        )

        latest_context = app._managed_preflight_context(window)
        generated_context = "\n".join(
            [
                "[对方] [图片]",
                "[可见内容理解] 类型：image；置信度：high；来源：zhipu_vision；一张花色衣服照片",
            ]
        )

        allowed, reason = main._managed_send_preflight_allows(window, window, generated_context, latest_context)

        self.assertIn("一张花色衣服照片", latest_context)
        self.assertTrue(allowed, reason)

    def test_text_provider_settings_roundtrip_supports_custom_http(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            settings = AppSettings(config_path=path)
            settings.api_provider = "custom_http"
            settings.reply_source_mode = "knowledge_only"
            settings.api_key = "test-key"
            settings.base_url = "https://example.com/generate"
            settings.model = "custom-model"
            settings.custom_headers = '{"Authorization":"Bearer {api_key}"}'
            settings.custom_body = '{"model":"{model}","prompt":"{prompt}"}'
            settings.custom_response_path = "data.answer"
            settings.save()

            loaded = AppSettings(config_path=path).load()

            self.assertEqual(loaded.api_provider, "custom_http")
            self.assertEqual(loaded.reply_source_mode, "knowledge_only")
            self.assertEqual(loaded.custom_headers, '{"Authorization":"Bearer {api_key}"}')
            self.assertEqual(loaded.custom_body, '{"model":"{model}","prompt":"{prompt}"}')
            self.assertEqual(loaded.custom_response_path, "data.answer")

    def test_supported_provider_help_lists_common_text_interfaces(self):
        help_text = supported_provider_help()

        for provider in ["Zhipu", "DeepSeek", "Qwen", "Doubao", "Moonshot", "SiliconFlow", "Custom HTTP"]:
            self.assertIn(provider, help_text)

    def test_deepseek_request_uses_provider_base_without_forcing_zhipu(self):
        settings = AppSettings()
        settings.api_provider = "deepseek"
        settings.api_key = "deepseek-key"
        settings.base_url = "https://api.deepseek.com"
        settings.model = "deepseek-chat"

        request = build_text_generation_request(settings, [{"role": "user", "content": "hello"}])

        self.assertEqual(request.url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(request.headers["Authorization"], "Bearer deepseek-key")
        self.assertEqual(request.payload["model"], "deepseek-chat")

    def test_custom_http_request_and_response_path(self):
        settings = AppSettings()
        settings.api_provider = "custom_http"
        settings.api_key = "abc"
        settings.base_url = "https://example.com/text"
        settings.model = "m1"
        settings.custom_headers = '{"X-Key":"{api_key}"}'
        settings.custom_body = '{"m":"{model}","q":"{prompt}","messages":{messages_json}}'
        settings.custom_response_path = "data.0.answer"

        request = build_text_generation_request(settings, [{"role": "user", "content": "hello"}])
        text = extract_text_from_response({"data": [{"answer": "1. hi\n2. ok\n3. yes"}]}, settings)

        self.assertEqual(request.url, "https://example.com/text")
        self.assertEqual(request.headers["X-Key"], "abc")
        self.assertEqual(request.payload["m"], "m1")
        self.assertEqual(request.payload["q"], "hello")
        self.assertEqual(text, "1. hi\n2. ok\n3. yes")

    def test_reply_engine_calls_api_for_ack_when_key_is_configured(self):
        settings = AppSettings()
        settings.api_provider = "deepseek"
        settings.api_key = "configured"
        settings.base_url = "https://api.deepseek.com"
        settings.model = "deepseek-chat"
        settings.reply_source_mode = "model"
        engine = ReplyEngine(settings, StyleMemory())
        response_payload = {"choices": [{"message": {"content": "1. ok\n2. got it\n3. no problem"}}]}

        with patch("reply_engine.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = response_payload
            result = engine.generate("[对方] 收到了，谢谢", "friend")

        post.assert_called_once()
        self.assertIn("API", engine.last_source)
        self.assertTrue(result)

    def test_model_only_mode_does_not_include_training_examples_in_prompt(self):
        settings = AppSettings()
        settings.reply_source_mode = "model"
        memory = StyleMemory()
        memory.learn_from_training_examples(
            [{"cue": "[对方] 放哪里", "reply": "这个放客厅正南方就行"}]
        )
        engine = ReplyEngine(settings, memory)

        prompt = engine._build_prompt("[对方] 放哪里", "好友")

        self.assertNotIn("相似历史聊天片段", prompt)
        self.assertNotIn("这个放客厅正南方就行", prompt)

    def test_model_only_prompt_does_not_use_keyword_scenario_examples(self):
        settings = AppSettings()
        settings.reply_source_mode = "model"
        engine = ReplyEngine(settings, StyleMemory())

        prompt = engine._build_prompt("[对方] 我想用机器人理解意图，自动处理售后查单核销", "好友")

        self.assertNotIn("当前判断场景", prompt)
        self.assertNotIn("常见高质量示例", prompt)
        self.assertNotIn("场景：", prompt)
        self.assertNotIn("领取方法很简单", prompt)

    def test_model_memory_prompt_uses_memory_without_keyword_scenario_examples(self):
        settings = AppSettings()
        settings.reply_source_mode = "model_memory"
        memory = StyleMemory()
        memory.learn_from_training_examples(
            [{"cue": "[对方] 我想做自动查单核销", "reply": "可以先把查单和核销状态打通"}]
        )
        engine = ReplyEngine(settings, memory)

        prompt = engine._build_prompt("[对方] 我想做自动查单核销", "好友")

        self.assertIn("相似历史聊天片段", prompt)
        self.assertIn("可以先把查单和核销状态打通", prompt)
        self.assertNotIn("常见高质量示例", prompt)
        self.assertNotIn("场景：", prompt)

    def test_prompt_marks_unreplied_turn_and_latest_priority(self):
        settings = AppSettings()
        settings.reply_source_mode = "model"
        engine = ReplyEngine(settings, StyleMemory())

        prompt = engine._build_prompt(
            "[我] 这个啊\n[对方] 发给我看一下\n[对方] 山海镇收到了，应该怎么放",
            "好友",
            target_turn=["发给我看一下", "山海镇收到了，应该怎么放"],
        )

        self.assertIn("当前需要回复的未回复轮次：\n发给我看一下\n山海镇收到了，应该怎么放", prompt)
        self.assertIn("本轮最后一句（最高优先级）：\n山海镇收到了，应该怎么放", prompt)
        self.assertIn("每个未回复的问题都要照顾到，并合并成一条自然回复", prompt)
        self.assertIn("不能编造", prompt)

    def test_prompt_requires_visible_content_when_present(self):
        settings = AppSettings()
        settings.reply_source_mode = "model"
        engine = ReplyEngine(settings, StyleMemory())

        prompt = engine._build_prompt(
            "[对方] oh no 头痛还是来了\n"
            "[可见内容理解] 类型：image；置信度：high；来源：zhipu_vision；图片里是一个人穿白鞋站在路边",
            "好友",
        )

        self.assertIn("上下文包含[可见内容理解]", prompt)
        self.assertIn("必须结合图片/卡片内容和最后一句文字", prompt)
        self.assertIn("可以使用其中的可见信息", prompt)

    def test_model_only_mode_does_not_local_fallback_when_api_replies_are_invalid(self):
        settings = AppSettings()
        settings.reply_source_mode = "model"
        settings.api_provider = "deepseek"
        settings.api_key = "configured"
        settings.base_url = "https://api.deepseek.com"
        settings.model = "deepseek-chat"
        engine = ReplyEngine(settings, StyleMemory())

        with patch("reply_engine.requests.post") as post:
            responses = []
            for _ in range(3):
                response = unittest.mock.Mock()
                response.status_code = 200
                response.raise_for_status.return_value = None
                response.json.return_value = {"choices": [{"message": {"content": "1. 理解了\n2. 帮你看看\n3. 怎么弄？"}}]}
                responses.append(response)
            post.side_effect = responses
            result = engine.generate("[对方] 山海镇收到了，应该怎么放", "好友")

        self.assertEqual(result, [])
        self.assertIn("API", engine.last_source)
        self.assertIn("未通过质量过滤", engine.last_error)

    def test_model_only_mode_does_not_use_training_data_to_pad_api_replies(self):
        settings = AppSettings()
        settings.reply_source_mode = "model"
        settings.api_key = "configured"
        memory = StyleMemory()
        memory.learn_from_training_examples(
            [{"cue": "[对方] 福宝放哪里", "reply": "这个放客厅正南方就行"}]
        )
        engine = ReplyEngine(settings, memory)

        with patch("reply_engine.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = {"choices": [{"message": {"content": "1. 我看下具体情况"}}]}
            result = engine.generate("[对方] 福宝放哪里", "好友")

        self.assertIn("API", engine.last_source)
        self.assertNotIn("客厅正南方", " ".join(result))

    def test_model_only_mode_does_not_use_neutral_memory_when_api_returns_one_reply(self):
        settings = AppSettings()
        settings.reply_source_mode = "model"
        settings.api_key = "configured"
        memory = StyleMemory()
        memory.learn_from_training_examples(
            [{"cue": "[对方] alpha beta question?", "reply": "MEMORY_ONLY_REPLY_SHOULD_NOT_APPEAR"}]
        )
        engine = ReplyEngine(settings, memory)

        with patch("reply_engine.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = {"choices": [{"message": {"content": "1. MODEL_ONLY_REPLY"}}]}
            result = engine.generate("[对方] alpha beta question?", "好友")

        self.assertIn("API", engine.last_source)
        self.assertNotIn("MEMORY_ONLY_REPLY_SHOULD_NOT_APPEAR", " ".join(result))

    def test_model_only_mode_refills_short_candidate_list_from_api(self):
        settings = AppSettings()
        settings.reply_source_mode = "model"
        settings.api_key = "configured"
        engine = ReplyEngine(settings, StyleMemory())

        with patch("reply_engine.requests.post") as post:
            first = unittest.mock.Mock()
            first.status_code = 200
            first.raise_for_status.return_value = None
            first.json.return_value = {"choices": [{"message": {"content": "1. MODEL_REPLY_ONE"}}]}
            second = unittest.mock.Mock()
            second.status_code = 200
            second.raise_for_status.return_value = None
            second.json.return_value = {
                "choices": [{"message": {"content": "1. MODEL_REPLY_TWO\n2. MODEL_REPLY_THREE"}}]
            }
            post.side_effect = [first, second]
            result = engine.generate("[对方] alpha beta question?", "好友")

        self.assertEqual(post.call_count, 2)
        self.assertEqual(result, ["MODEL_REPLY_ONE", "MODEL_REPLY_TWO", "MODEL_REPLY_THREE"])

    def test_model_only_mode_retries_when_api_returns_generic_low_effort_replies(self):
        settings = AppSettings()
        settings.reply_source_mode = "model"
        settings.api_key = "configured"
        engine = ReplyEngine(settings, StyleMemory())

        with patch("reply_engine.requests.post") as post:
            first = unittest.mock.Mock()
            first.status_code = 200
            first.raise_for_status.return_value = None
            first.json.return_value = {"choices": [{"message": {"content": "1. 理解了\n2. 帮你看看\n3. 怎么弄？"}}]}
            second = unittest.mock.Mock()
            second.status_code = 200
            second.raise_for_status.return_value = None
            second.json.return_value = {
                "choices": [{"message": {"content": "1. 山海镇可以放客厅\n2. 先放干净稳的位置\n3. 我发你具体摆法"}}]
            }
            post.side_effect = [first, second]
            result = engine.generate("[对方] 山海镇收到了，应该怎么放", "好友")

        self.assertEqual(post.call_count, 2)
        self.assertEqual(result, ["山海镇可以放客厅", "先放干净稳的位置", "我发你具体摆法"])
        self.assertIn("调用2次", engine.last_source)

    def test_managed_model_source_keeps_model_rank_instead_of_keyword_reranking(self):
        import main

        context = "[对方] 我想把售后查单核销这套流程自动化"
        replies = [
            "先把查单和核销状态打通，再让机器人按意图触发对应动作",
            "可以先确认流程",
            "收到，我看下",
        ]

        selected = main._select_managed_reply(context, replies, prefer_model_order=True)

        self.assertEqual(selected, replies[0])

    def test_managed_send_replaces_pending_only_candidates_with_final_no_answer_text(self):
        import main

        selected = main._select_managed_reply(
            "[对方] 往年年度工作报告如何查看？",
            ["可以，我确认下", "我先看下，晚点回你"],
        )

        self.assertEqual(selected, "暂时未查到对应答案。")

    def test_managed_send_rejects_long_look_again_transition_candidate(self):
        import main

        selected = main._select_managed_reply(
            "[对方] 年度工作报告在哪里找？",
            ["这个我得再看一眼"],
        )

        self.assertEqual(selected, "暂时未查到对应答案。")

    def test_engine_infers_unreplied_turn_for_manual_generation(self):
        settings = AppSettings()
        settings.reply_source_mode = "model"
        settings.api_key = "configured"
        engine = ReplyEngine(settings, StyleMemory())
        context = "[对方] 发给我看一下\n[我] 这个啊\n[对方] 山海镇收到了\n[对方] 应该怎么放"

        with patch.object(engine, "_build_prompt", wraps=engine._build_prompt) as build_prompt:
            with patch("reply_engine.requests.post") as post:
                post.return_value.status_code = 200
                post.return_value.raise_for_status.return_value = None
                post.return_value.json.return_value = {
                    "choices": [{"message": {"content": "1. 我把放法发你\n2. 先别乱放\n3. 我确认下具体位置"}}]
                }
                engine.generate(context, "好友")

        _conversation, _partner = build_prompt.call_args.args[:2]
        self.assertEqual(build_prompt.call_args.kwargs["target_turn"], ["山海镇收到了", "应该怎么放"])

    def test_memory_only_mode_uses_training_when_relevant_without_api_call(self):
        settings = AppSettings()
        settings.reply_source_mode = "memory"
        settings.api_key = "configured"
        memory = StyleMemory()
        memory.learn_from_training_examples(
            [{"cue": "[对方] 福宝放哪里", "reply": "这个放干净稳妥的位置就行"}]
        )
        engine = ReplyEngine(settings, memory)

        with patch("reply_engine.requests.post") as post:
            result = engine.generate("[对方] 福宝放哪里", "好友")

        post.assert_not_called()
        self.assertIn("话术库", engine.last_source)
        self.assertIn("干净稳妥", " ".join(result))

    def test_memory_mode_falls_back_to_model_when_no_training_match(self):
        settings = AppSettings()
        settings.reply_source_mode = "memory"
        settings.api_key = "configured"
        memory = StyleMemory()
        memory.learn_from_training_examples(
            [{"cue": "[对方] 福宝放哪里", "reply": "这个放干净稳妥的位置就行"}]
        )
        engine = ReplyEngine(settings, memory)

        with patch("reply_engine.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = {
                "choices": [{"message": {"content": "1. 确实有点意思\n2. 这个可以继续看\n3. 我也觉得还行"}}]
            }
            result = engine.generate("[对方] 这事还挺有意思", "好友")

        self.assertGreaterEqual(post.call_count, 1)
        self.assertIn("API", engine.last_source)
        self.assertIn("有点意思", " ".join(result))

    def test_phrasebook_runtime_uses_same_shared_ranking_as_test_ui(self):
        settings = AppSettings()
        settings.reply_source_mode = "model_memory"
        settings.api_key = ""
        memory = StyleMemory()
        memory.learn_from_training_examples(
            [
                {"cue": "[对方] 想查一下之前的年报", "reply": "年报可以在年度报告公示系统查询"},
                {"cue": "[对方] 之前的报告怎么看", "reply": "报告在信息公开栏查看"},
                {"cue": "[对方] 今天天气怎么样", "reply": "今天晴天"},
            ]
        )
        engine = ReplyEngine(settings, memory)
        result = engine.generate("[对方] 之前的报告怎么看", "好友")
        top_ui = memory.scored_examples("之前的报告怎么看", limit=1)[0]
        self.assertTrue(result)
        self.assertEqual(result[0], top_ui["reply"])
        self.assertIn("话术库", engine.last_source)

    def test_phrasebook_high_score_returns_top1_without_api_call(self):
        settings = AppSettings()
        settings.reply_source_mode = "model_memory"
        settings.api_key = "configured"
        memory = StyleMemory()
        memory.learn_from_training_examples(
            [
                {"cue": "[对方] 福宝放哪里", "reply": "这个放客厅正南方就行"},
                {"cue": "[对方] 年报在哪里找", "reply": "在年度报告公示系统查询"},
            ]
        )
        engine = ReplyEngine(settings, memory)
        with patch("reply_engine.requests.post") as post:
            result = engine.generate("[对方] 福宝放哪里", "好友")
        post.assert_not_called()
        self.assertIn("话术库", engine.last_source)
        self.assertIn("正南方", " ".join(result))

    def test_phrasebook_below_threshold_returns_honest_no_answer(self):
        settings = AppSettings()
        settings.reply_source_mode = "model_memory"
        settings.api_key = ""
        memory = StyleMemory()
        memory.learn_from_training_examples(
            [{"cue": "[对方] 福宝放哪里", "reply": "这个放客厅正南方就行"}]
        )
        engine = ReplyEngine(settings, memory)
        result = engine.generate("[对方] 今天天气怎么样", "好友")
        self.assertEqual(result, ["暂时未查到对应答案。"])
        self.assertEqual(engine.last_source, "话术库未命中")

    def test_phrasebook_low_score_does_not_fall_back_to_model_in_model_memory(self):
        # model_memory 模式：大模型只做并列裁决，低分问法不再自由生成兜底
        settings = AppSettings()
        settings.reply_source_mode = "model_memory"
        settings.api_key = "configured"
        settings.enable_web_search = False
        memory = StyleMemory()
        memory.learn_from_training_examples(
            [{"cue": "[对方] 福宝放哪里", "reply": "这个放客厅正南方就行"}]
        )
        engine = ReplyEngine(settings, memory)
        with patch("reply_engine.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = {
                "choices": [{"message": {"content": "1. 今天天气不错\n2. 适合出门\n3. 注意防晒"}}]
            }
            result = engine.generate("[对方] 今天天气怎么样", "好友")
        post.assert_not_called()
        self.assertEqual(result, ["暂时未查到对应答案。"])
        self.assertEqual(engine.last_source, "话术库未命中")

    def test_reply_engine_reports_api_failure_instead_of_silent_fallback(self):
        settings = AppSettings()
        settings.api_provider = "deepseek"
        settings.api_key = "configured"
        settings.base_url = "https://api.deepseek.com"
        settings.model = "deepseek-chat"
        settings.reply_source_mode = "memory"
        engine = ReplyEngine(settings, StyleMemory())

        with patch("reply_engine.requests.post", side_effect=RuntimeError("bad key")):
            result = engine.generate("[对方] 这事还挺有意思的", "friend")

        self.assertTrue(result)
        self.assertNotIn("API", engine.last_source)
        self.assertIn("DeepSeek", engine.last_error)
        self.assertIn("bad key", engine.last_error)

    def test_reply_engine_reports_http_error_body_from_text_api(self):
        settings = AppSettings()
        settings.api_provider = "zhipu"
        settings.api_key = "configured"
        settings.base_url = "https://open.bigmodel.cn/api/paas/v4"
        settings.model = "glm-4.7-flash"
        settings.reply_source_mode = "memory"
        engine = ReplyEngine(settings, StyleMemory())

        with patch("reply_engine.requests.post") as post:
            post.return_value.status_code = 429
            post.return_value.text = '{"error":{"message":"rate limit or quota exceeded"}}'
            result = engine.generate("[对方] 这事还挺有意思的", "friend")

        self.assertTrue(result)
        self.assertNotIn("API", engine.last_source)
        self.assertIn("429", engine.last_error)
        self.assertIn("rate limit", engine.last_error)

    def test_zhipu_generation_falls_back_to_available_flash_model(self):
        settings = AppSettings()
        settings.api_provider = "zhipu"
        settings.api_key = "configured"
        settings.base_url = "https://open.bigmodel.cn/api/paas/v4"
        settings.model = "glm-4.7-flash"
        settings.reply_source_mode = "model"
        engine = ReplyEngine(settings, StyleMemory())

        with patch("reply_engine.requests.post") as post:
            first = unittest.mock.Mock()
            first.status_code = 429
            first.text = '{"error":{"code":"1305","message":"busy"}}'
            second = unittest.mock.Mock()
            second.status_code = 200
            second.raise_for_status.return_value = None
            second.json.return_value = {"choices": [{"message": {"content": "1. 确实挺有意思\n2. 这个可以看看\n3. 我也觉得还行"}}]}
            post.side_effect = [first, second]
            result = engine.generate("[对方] 这事还挺有意思的", "friend")

        self.assertEqual(post.call_args_list[0].kwargs["json"]["model"], "glm-4.7-flash")
        self.assertEqual(post.call_args_list[1].kwargs["json"]["model"], "glm-4-flash-250414")
        self.assertIn("Zhipu API", engine.last_source)
        self.assertFalse(engine.last_error)
        self.assertIn("确实挺有意思", " ".join(result))

    def test_zhipu_default_model_uses_stable_free_text_flash(self):
        settings = AppSettings()

        self.assertEqual(settings.model, "glm-4-flash-250414")

    def test_reply_engine_reports_empty_api_response_text(self):
        settings = AppSettings()
        settings.api_provider = "custom_http"
        settings.api_key = "configured"
        settings.base_url = "https://example.com/text"
        settings.model = "m1"
        settings.custom_response_path = "data.answer"
        settings.reply_source_mode = "memory"
        engine = ReplyEngine(settings, StyleMemory())

        with patch("reply_engine.requests.post") as post:
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = {"data": {"wrong": ""}}
            result = engine.generate("[对方] 这事还挺有意思的", "friend")

        self.assertTrue(result)
        self.assertNotIn("API", engine.last_source)
        self.assertIn("没有识别到文本", engine.last_error)

    def test_managed_auto_send_is_blocked_when_configured_api_fails(self):
        settings = AppSettings()
        settings.api_key = "configured"
        engine = ReplyEngine(settings, StyleMemory())
        engine.last_source = "本地兜底规则"
        engine.last_error = "Zhipu API 调用失败：HTTP 429"

        self.assertTrue(_managed_api_failure_blocks_send(settings, engine))

    def test_single_wechat_message_keeps_full_content(self):
        # 企业年报知识库官方答案可达数百字，必须完整发送，不能截断到第一句；
        # 只合并换行避免拆成多条消息刷屏。
        text = "情形一：填报人员填写错误。整改方式：填写情况说明。\n佐证材料：①情况说明、②会议纪要等。（以上材料需加盖单位公章）"
        result = _single_wechat_message(text)
        self.assertIn("情形一：填报人员填写错误。", result)
        self.assertIn("佐证材料：①情况说明、②会议纪要等", result)
        self.assertIn("加盖单位公章", result)
        self.assertNotIn("\n", result)

    def test_phrasebook_long_answer_is_not_truncated(self):
        settings = AppSettings()
        settings.reply_source_mode = "memory"
        settings.api_key = ""
        long_reply = "情形一：填报人员填写错误。整改方式：填写情况说明。佐证材料：①情况说明、②最近一次理事会会议签到表及会议纪要、③会员花名册、④负责人（变动）备案材料等。（以上材料需加盖单位公章）情形二：确实存在负责人超过理事数的1/3问题，则需依规整改，召开理事会会议进行表决，批准辞职或罢免X名负责人，并到省民政厅政务服务中心备案。"
        memory = StyleMemory()
        memory.learn_from_training_examples(
            [{"cue": "[对方] 负责人超理事数1/3怎么整改", "reply": long_reply}]
        )
        engine = ReplyEngine(settings, memory)
        result = engine.generate("[对方] 负责人超理事数1/3怎么整改", "好友")
        self.assertTrue(result)
        self.assertGreater(len(result[0]), 150)
        self.assertIn("情形二", result[0])
        self.assertIn("省民政厅政务服务中心备案", result[0])

    def test_auto_poll_does_not_clear_existing_replies_before_change_is_known(self):
        import main

        self.assertFalse(main._should_clear_replies_before_capture(force=False, training_only=False))
        self.assertFalse(main._should_clear_replies_before_capture(force=False, training_only=True))
        self.assertTrue(main._should_clear_replies_before_capture(force=True, training_only=False))
        self.assertFalse(_should_clear_replies_for_skip(force=False))
        self.assertTrue(_should_clear_replies_for_skip(force=True))

    def test_managed_unhandled_turn_is_not_skipped_just_because_context_is_unchanged(self):
        other = "[" + chr(0x5BF9) + chr(0x65B9) + "] "
        context = other + (chr(0x8FD9) + chr(0x4E2A) + chr(0x600E) + chr(0x4E48) + chr(0x653E))

        self.assertFalse(
            _should_skip_unchanged_context(
                force=False,
                managed=True,
                current_context=context,
                new_context=context,
                current_hash="same",
                digest="same",
                handled_messages=[],
            )
        )

    def test_non_managed_unchanged_context_is_still_skipped(self):
        other = "[" + chr(0x5BF9) + chr(0x65B9) + "] "
        context = other + (chr(0x8FD9) + chr(0x4E2A) + chr(0x600E) + chr(0x4E48) + chr(0x653E))

        self.assertTrue(
            _should_skip_unchanged_context(
                force=False,
                managed=False,
                current_context=context,
                new_context=context,
                current_hash="same",
                digest="same",
                handled_messages=[],
            )
        )

    def test_visible_skip_does_not_block_latest_customer_text(self):
        context = "\n".join(
            [
                "[" + chr(0x6211) + "] " + "这个工具看起来挺全的，试试看",
                "[" + chr(0x5BF9) + chr(0x65B9) + "] " + "不发了，手下留情",
            ]
        )
        visible = VisibleContent(
            message_type="image",
            summary="图片内容无法可靠识别，建议人工查看",
            confidence="low",
            source="zhipu_vision",
            should_skip_reply=True,
        )

        self.assertFalse(_visible_skip_should_block_reply(context, visible))

    def test_visible_skip_still_blocks_image_only_turn(self):
        context = "[" + chr(0x5BF9) + chr(0x65B9) + "] [" + chr(0x56FE) + chr(0x7247) + "]"
        visible = VisibleContent(
            message_type="image",
            summary="图片内容无法可靠识别，建议人工查看",
            confidence="low",
            source="zhipu_vision",
            should_skip_reply=True,
        )

        self.assertTrue(_visible_skip_should_block_reply(context, visible))

    def test_context_similarity_treats_ocr_noise_as_unchanged(self):
        old = "\n".join(
            [
                "[我] 售后查单用模板回复可以，但遇到复杂纠纷还是得人工介入",
                "[对方] 我买的餐食到了吗?",
                "[对方] 这个七粒米戒指怎么佩戴的",
            ]
        )
        new = "\n".join(
            [
                "[我] 售后查单用模板回复可以，但遇到复杂纠纷还是得人工介入，",
                "[对方] 我买的零食到了吗?",
                "[对方] 这个七粒米戒指怎么佩戴的",
            ]
        )

        self.assertTrue(_contexts_are_same_or_similar(old, new))

    def test_context_similarity_detects_new_customer_message(self):
        old = "[我] 放书房就行\n[对方] 收到了"
        new = "[我] 放书房就行\n[对方] 收到了\n[对方] 还需要每天动它吗"

        self.assertFalse(_contexts_are_same_or_similar(old, new))

    def test_target_window_prefers_discovered_wechat_over_stale_current_window(self):
        stale = types.SimpleNamespace(hwnd=1, title="旧聊天")
        discovered = types.SimpleNamespace(hwnd=2, title="新聊天")

        self.assertIs(_choose_target_wechat_window(None, discovered, stale), discovered)
        self.assertIs(_choose_target_wechat_window(discovered, stale, None), discovered)

    def test_managed_generation_guard_blocks_when_latest_is_own_even_when_forced(self):
        context = "[对方] 这个放哪里\n[我] 放书房就行"

        self.assertFalse(_should_auto_generate_for_context(context, force=True, managed=True, handled_messages=[]))

    def test_auto_generation_guard_allows_forced_manual_recognize(self):
        context = "[对方] 这个放哪里\n[我] 放书房就行"

        self.assertTrue(_should_auto_generate_for_context(context, force=True, managed=False, handled_messages=[]))

    def test_snap_geometry_skips_tiny_or_same_changes(self):
        class Root:
            def __init__(self):
                self.calls = []

            def geometry(self, value):
                self.calls.append(value)

        root = Root()
        last = ""
        last = _snap_geometry_if_changed(root, "440x700+100+100", last)
        last = _snap_geometry_if_changed(root, "440x700+100+100", last)
        last = _snap_geometry_if_changed(root, "440x700+101+101", last)
        last = _snap_geometry_if_changed(root, "440x700+120+130", last)

        self.assertEqual(root.calls, ["440x700+100+100", "440x700+120+130"])

    def test_capture_chat_image_uses_direct_window_capture_without_flashing(self):
        calls = []

        class Queue:
            def put(self, item):
                calls.append(item[0])

        class OCR:
            def capture(self, window, allow_screen_fallback=True):
                calls.append(("capture", allow_screen_fallback))
                return object()

        class Sender:
            @staticmethod
            def _activate(window):
                calls.append("activate")

        image = _capture_chat_image_without_overlay(OCR(), Queue(), Sender(), object(), wait_seconds=0.01)

        self.assertIsNotNone(image)
        self.assertEqual(calls, [("capture", False)])

    def test_capture_chat_image_hides_assistant_only_for_screen_fallback(self):
        calls = []

        class Queue:
            def put(self, item):
                calls.append(item[0])
                if item[0] == "hide_for_capture":
                    item[1].set()

        class OCR:
            def __init__(self):
                self.calls = 0

            def capture(self, window, allow_screen_fallback=True):
                self.calls += 1
                calls.append(("capture", allow_screen_fallback))
                return object() if allow_screen_fallback else None

        class Sender:
            @staticmethod
            def _activate(window):
                calls.append("activate")

        image = _capture_chat_image_without_overlay(OCR(), Queue(), Sender(), object(), wait_seconds=0.01)

        self.assertIsNotNone(image)
        self.assertEqual(
            calls,
            [("capture", False), "hide_for_capture", "activate", ("capture", True), "show_after_capture"],
        )

    def test_managed_turn_signature_changes_only_when_customer_turn_changes(self):
        first = _managed_turn_signature("客户A", ["收到"])
        same = _managed_turn_signature("客户A", ["收到"])
        changed = _managed_turn_signature("客户A", ["收到", "放在哪里"])

        self.assertEqual(first, same)
        self.assertNotEqual(first, changed)

    def test_managed_context_allows_current_unreplied_turn_on_enable(self):
        context = "\n".join(
            [
                "[对方] 放家里的哪个位置比较合适",
                "[我] 我看下，晚点回你",
                "[对方] 看好了吗？放在哪里合适",
            ]
        )

        self.assertTrue(_managed_context_allows_generation(context))

    def test_managed_context_rejects_when_latest_message_is_own(self):
        context = "\n".join(
            [
                "[对方] 放家里的哪个位置比较合适",
                "[我] 我看下，晚点回你",
            ]
        )

        self.assertFalse(_managed_context_allows_generation(context))

    def test_current_unreplied_turn_uses_only_messages_after_latest_own_reply(self):
        context = "\n".join(
            [
                "[对方] 挂件放哪里",
                "[我] 放客厅或者书房都行",
                "[对方] 还有一个小的配饰是挂件吗",
                "[对方] 挂件需要单独放哪里吗",
            ]
        )

        self.assertEqual(
            _current_unreplied_turn(context),
            ["还有一个小的配饰是挂件吗", "挂件需要单独放哪里吗"],
        )

    def test_current_unreplied_turn_includes_latest_visible_image_understanding(self):
        context = "\n".join(
            [
                "[对方] [图片]",
                "[可见内容理解] 类型：image；置信度：high；来源：zhipu_vision；一张花色衣服照片",
            ]
        )

        self.assertEqual(
            _current_unreplied_turn(context),
            ["[图片] 一张花色衣服照片"],
        )

    def test_managed_auto_generate_allows_image_with_visible_understanding(self):
        context = "\n".join(
            [
                "[对方] [图片]",
                "[可见内容理解] 类型：image；置信度：high；来源：zhipu_vision；一张白色上衣的图片，衣服上有花卉刺绣图案",
            ]
        )

        self.assertTrue(_managed_context_allows_generation(context))
        self.assertTrue(
            _should_auto_generate_for_context(
                context,
                force=False,
                managed=True,
                handled_messages=[],
            )
        )

    def test_context_similarity_detects_changed_visible_image_understanding(self):
        old_context = "\n".join(
            [
                "[对方] [图片]",
                "[可见内容理解] 类型：image；置信度：high；来源：zhipu_vision；一张表格截图",
            ]
        )
        new_context = "\n".join(
            [
                "[对方] [图片]",
                "[可见内容理解] 类型：image；置信度：high；来源：zhipu_vision；一张花色衣服照片",
            ]
        )

        self.assertFalse(_contexts_are_same_or_similar(old_context, new_context))

    def test_turn_similarity_treats_ocr_noise_as_same_turn(self):
        old = ["还有一个小的配饰是挂件吗", "挂件需要单独放哪里吗"]
        noisy = ["还有一个小配饰是挂件吗", "挂件需要单独放哪儿吗"]

        self.assertEqual(_turn_key(old), _turn_key(old))
        self.assertTrue(_turns_are_same_or_similar(old, noisy))
        self.assertFalse(_turn_has_real_new_message(old, noisy))

    def test_turn_new_message_detects_appended_customer_message(self):
        old = ["还有一个小的配饰是挂件吗", "挂件需要单独放哪里吗"]
        new = old + ["我的都已经到家了，怎么加持"]

        self.assertFalse(_turns_are_same_or_similar(old, new))
        self.assertTrue(_turn_has_real_new_message(old, new))

    def test_managed_context_rejects_already_handled_turn_with_ocr_noise(self):
        context = "\n".join(
            [
                "[对方] 还有一个小配饰是挂件吗",
                "[对方] 挂件需要单独放哪儿吗",
            ]
        )
        handled = ["还有一个小的配饰是挂件吗", "挂件需要单独放哪里吗"]

        self.assertFalse(_managed_context_allows_generation(context, handled))

    def test_managed_context_allows_turn_with_appended_new_message(self):
        context = "\n".join(
            [
                "[对方] 还有一个小配饰是挂件吗",
                "[对方] 挂件需要单独放哪儿吗",
                "[对方] 我的都已经到家了，怎么加持",
            ]
        )
        handled = ["还有一个小的配饰是挂件吗", "挂件需要单独放哪里吗"]

        self.assertTrue(_managed_context_allows_generation(context, handled))

    def test_managed_gate_waits_until_own_sent_message_is_seen(self):
        context = "\n".join(
            [
                "[瀵规柟] 鏀跺埌浜?",
                "[瀵规柟] 鏀惧湪鎴块棿鍝釜浣嶇疆",
            ]
        )

        allow, waiting = _managed_reply_gate_allows_generation(context, True, "鎴戝彂浣犵湅涓?", ["鏀跺埌浜?"])

        self.assertFalse(allow)
        self.assertTrue(waiting)

    def test_managed_gate_allows_new_customer_message_even_without_own_echo(self):
        context = "\n".join(
            [
                "[瀵规柟] 收到了",
                "[瀵规柟] 那我放哪里合适",
            ]
        )

        allow, waiting = _managed_reply_gate_allows_generation(
            context,
            True,
            "我发你看下",
            ["收到了"],
            allow_new_without_own_echo=True,
        )

        self.assertTrue(allow)
        self.assertFalse(waiting)

    def test_managed_gate_allows_new_other_after_own_sent_message_is_seen(self):
        context = "\n".join(
            [
                "[对方] 收到了",
                "[我] 我发你看下",
                "[对方] 那我放哪个位置",
            ]
        )

        allow, waiting = _managed_reply_gate_allows_generation(context, True, "我发你看下", ["收到了"])

        self.assertTrue(allow)
        self.assertFalse(waiting)

    def test_managed_gate_allows_new_other_even_when_own_echo_text_is_ocr_noisy(self):
        context = "\n".join(
            [
                "[对方] 这个放哪里",
                "[我] 我晚点给你看下位置",
                "[对方] 那我等你",
            ]
        )

        allow, waiting = _managed_reply_gate_allows_generation(context, True, "我发你看下", ["这个放哪里"])

        self.assertTrue(allow)
        self.assertFalse(waiting)

    def test_managed_allows_repeated_customer_text_after_own_reply_boundary(self):
        repeated = "\u653e\u54ea\u91cc\u5408\u9002"
        own = "\u653e\u4e66\u623f\u5c31\u884c"
        context = "\n".join(
            [
                f"[瀵规柟] {repeated}",
                f"[鎴慮 {own}",
                f"[瀵规柟] {repeated}",
            ]
        )
        handled = [repeated]

        self.assertTrue(_managed_context_allows_generation(context, handled))
        allow, waiting = _managed_reply_gate_allows_generation(
            context,
            True,
            own,
            handled,
            allow_new_without_own_echo=True,
        )
        self.assertTrue(allow)
        self.assertFalse(waiting)

    def test_managed_gate_keeps_waiting_for_same_handled_turn_with_ocr_noise(self):
        context = "\n".join(
            [
                "[对方] 还有一个小配饰是挂件吗",
                "[对方] 挂件需要单独放哪儿吗",
            ]
        )
        handled = ["还有一个小的配饰是挂件吗", "挂件需要单独放哪里吗"]

        allow, waiting = _managed_reply_gate_allows_generation(context, True, "通常放书房就行", handled)

        self.assertFalse(allow)
        self.assertTrue(waiting)

    def test_managed_gate_clears_waiting_when_latest_message_is_own_even_if_text_noisy(self):
        context = "\n".join(
            [
                "[对方] 挂件需要单独放哪里吗",
                "[我] 通常放书房或者客厅都可以",
            ]
        )

        allow, waiting = _managed_reply_gate_allows_generation(context, True, "放书房就行", ["挂件需要单独放哪里吗"])

        self.assertFalse(allow)
        self.assertFalse(waiting)

    def test_default_settings_use_manual_mode_and_right_chat_area(self):
        settings = AppSettings()

        self.assertFalse(settings.auto_recognize)
        self.assertFalse(settings.managed_auto_reply)
        self.assertFalse(settings.training_mode)
        self.assertGreaterEqual(settings.chat_area.x_left, 0.30)
        self.assertLessEqual(settings.chat_area.x_right, 0.98)

    def test_settings_roundtrip_keeps_api_key_out_of_source_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            settings = AppSettings(config_path=path)
            settings.api_key = "test-key"
            settings.model = "glm-4.7-flash"
            settings.auto_recognize = True
            settings.managed_auto_reply = True
            settings.training_mode = True
            settings.save()

            loaded = AppSettings(config_path=path)
            loaded.load()

            self.assertEqual(loaded.api_key, "test-key")
            self.assertEqual(loaded.model, "glm-4-flash-250414")
            self.assertTrue(loaded.auto_recognize)
            self.assertTrue(loaded.managed_auto_reply)
            self.assertTrue(loaded.training_mode)

    def test_settings_roundtrip_keeps_dedicated_vision_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            settings = AppSettings(config_path=path)
            settings.api_provider = "deepseek"
            settings.api_key = "text-key"
            settings.base_url = "https://api.deepseek.com"
            settings.model = "deepseek-chat"
            settings.vision_provider = "zhipu"
            settings.vision_api_key = "vision-key"
            settings.vision_base_url = "https://open.bigmodel.cn/api/paas/v4"
            settings.vision_model = "glm-4v-flash"
            settings.save()

            loaded = AppSettings(config_path=path).load()

            self.assertEqual(loaded.api_provider, "deepseek")
            self.assertEqual(loaded.api_key, "text-key")
            self.assertEqual(loaded.model, "deepseek-v4-flash")
            self.assertEqual(loaded.vision_provider, "zhipu")
            self.assertEqual(loaded.vision_api_key, "vision-key")
            self.assertEqual(loaded.vision_base_url, "https://open.bigmodel.cn/api/paas/v4")
            self.assertEqual(loaded.vision_model, "glm-4v-flash")

    def test_provider_switch_keeps_separate_text_api_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            settings = AppSettings(config_path=path)
            settings.api_provider = "deepseek"
            settings.set_provider_api_key("deepseek", "deepseek-key")
            settings.save()

            settings.api_provider = "zhipu"
            settings.set_provider_api_key("zhipu", "zhipu-key")
            settings.save()

            loaded = AppSettings(config_path=path).load()
            self.assertEqual(loaded.api_provider, "zhipu")
            self.assertEqual(loaded.api_key, "zhipu-key")
            self.assertEqual(loaded.get_provider_api_key("deepseek"), "deepseek-key")
            self.assertEqual(loaded.get_provider_api_key("zhipu"), "zhipu-key")

            loaded.api_provider = "deepseek"
            loaded.api_key = loaded.get_provider_api_key("deepseek")
            self.assertEqual(loaded.api_key, "deepseek-key")

    def test_legacy_single_text_api_key_is_migrated_to_active_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(
                json.dumps(
                    {
                        "api_provider": "deepseek",
                        "api_key": "deepseek-key",
                    }
                ),
                encoding="utf-8",
            )

            loaded = AppSettings(config_path=path).load()

            self.assertEqual(loaded.api_key, "deepseek-key")
            self.assertEqual(loaded.get_provider_api_key("deepseek"), "deepseek-key")

    def test_frozen_defaults_store_data_in_appdata(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe_path = Path(tmp) / "聊小智.exe"
            with patch.dict(
                os.environ,
                {"APPDATA": tmp, "WECHAT_ASSISTANT_DATA_DIR": ""},
                clear=False,
            ), patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(exe_path)):
                config_path = settings_module.default_config_path()
                memory_path = memory_module.default_memory_path()

            self.assertEqual(config_path, Path(tmp) / "WeChatStyleReplyAssistant" / "settings.json")
            self.assertEqual(memory_path, Path(tmp) / "WeChatStyleReplyAssistant" / "memory.json")


class BuildReleaseTests(unittest.TestCase):
    def test_build_release_collects_dynamic_training_import_dependencies(self):
        import build_release

        required = set(build_release.REQUIRED_IMPORTS) | set(build_release.COLLECT_ALL_PACKAGES)

        self.assertIn("xlrd", required)
        self.assertIn("enterprise_kb", required)

    def test_build_release_includes_self_check_dynamic_import(self):
        import build_release

        self.assertIn("self_check", build_release.HIDDEN_IMPORTS)


class MultiPlatformChatWindowTests(unittest.TestCase):
    def test_platform_profiles_include_wechat_wecom_and_qq(self):
        self.assertEqual(ChatPlatform.for_key("wechat").label, "微信")
        self.assertEqual(ChatPlatform.for_key("wecom").label, "企业微信")
        self.assertEqual(ChatPlatform.for_key("qq").label, "QQ")

    def test_qq_profile_crops_out_left_conversation_list(self):
        qq = ChatPlatform.for_key("qq")

        self.assertGreaterEqual(qq.chat_area.x_left, 0.36)
        self.assertLessEqual(qq.chat_area.x_right, 0.99)
        self.assertLessEqual(qq.chat_area.y_bottom, 0.70)

    def test_wecom_profile_excludes_right_business_sidebar(self):
        wecom = ChatPlatform.for_key("wecom")

        # 企业微信 4.x 实测：聊天区从 ~29% 宽度一直延伸到窗口右缘（“我”的气泡靠右显示，
        # 无右侧业务面板），裁剪必须覆盖到 0.99 才能识别我方消息。
        self.assertGreaterEqual(wecom.chat_area.x_left, 0.27)
        self.assertLessEqual(wecom.chat_area.x_left, 0.31)
        self.assertGreaterEqual(wecom.chat_area.x_right, 0.95)
        self.assertLessEqual(wecom.chat_area.x_right, 1.00)

    def test_chrome_widget_codex_window_is_not_detected_as_qq(self):
        detector = WeChatWindowDetector()

        with patch.object(detector, "_process_name", return_value=r"C:\Users\PF\AppData\Local\Programs\Codex\Codex.exe"), patch.object(
            detector, "_class_name", return_value="Chrome_WidgetWin_1"
        ):
            platform = detector._match_platform(100, "Codex")

        self.assertIsNone(platform)

    def test_browser_page_containing_wechat_word_is_not_detected_as_wechat(self):
        detector = WeChatWindowDetector()

        with patch.object(detector, "_process_name", return_value=r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"), patch.object(
            detector, "_class_name", return_value="Chrome_WidgetWin_1"
        ):
            platform = detector._match_platform(
                100,
                "CLI+Agent：社媒数据采集 微信公众号 - 抖音 - 个人 - Microsoft Edge",
            )

        self.assertIsNone(platform)

    def test_browser_page_containing_wecom_word_is_not_detected_as_wecom(self):
        detector = WeChatWindowDetector()

        with patch.object(detector, "_process_name", return_value=r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"), patch.object(
            detector, "_class_name", return_value="Chrome_WidgetWin_1"
        ):
            platform = detector._match_platform(101, "企业微信管理后台 - Microsoft Edge")

        self.assertIsNone(platform)

    def test_title_fallback_keeps_only_wecom_titles(self):
        # 本应用只针对企业微信：微信/QQ 标题不再被识别
        detector = WeChatWindowDetector()
        cases = (
            ("企业微信", "wecom"),
            ("WeCom contact", "wecom"),
            ("微信", None),
            ("WeChat contact", None),
        )

        for title, expected_key in cases:
            with self.subTest(title=title), patch.object(detector, "_process_name", return_value="unknown.exe"), patch.object(
                detector, "_class_name", return_value="UnknownWindow"
            ):
                platform = detector._match_platform(102, title)
            if expected_key is None:
                self.assertIsNone(platform)
            else:
                self.assertIsNotNone(platform)
                self.assertEqual(platform.key, expected_key)

    def test_qq_and_wechat_processes_are_no_longer_detected(self):
        # 本应用只针对企业微信：微信/QQ 进程不再被识别
        detector = WeChatWindowDetector()

        with patch.object(detector, "_process_name", return_value=r"C:\Program Files\Tencent\QQNT\QQ.exe"), patch.object(
            detector, "_class_name", return_value="Chrome_WidgetWin_1"
        ):
            platform = detector._match_platform(101, "Codex")
        self.assertIsNone(platform)

        with patch.object(detector, "_process_name", return_value=r"C:\Program Files\Tencent\WeChat\Weixin.exe"), patch.object(
            detector, "_class_name", return_value="WeChatMainWndForPC"
        ):
            platform = detector._match_platform(103, "微信")
        self.assertIsNone(platform)

        with patch.object(detector, "_process_name", return_value=r"C:\Program Files\WXWork\WXWork.exe"), patch.object(
            detector, "_class_name", return_value="WXWorkMainWindowForPC"
        ):
            platform = detector._match_platform(104, "企业微信")
        self.assertIsNotNone(platform)
        self.assertEqual(platform.key, "wecom")

    def test_ocr_capture_uses_window_platform_chat_area(self):
        from PIL import Image

        window = ChatWindow(
            hwnd=8,
            title="QQ contact",
            rect=WindowRect(0, 0, 960, 640),
            platform=ChatPlatform.for_key("qq"),
        )
        full = Image.new("RGB", (960, 640), "white")
        fake_pyautogui = types.SimpleNamespace(calls=[], screenshot=lambda region: fake_pyautogui.calls.append(region) or full)
        ocr = ChatOCR(AppSettings())

        with patch("capture_ocr.pyautogui", fake_pyautogui):
            image = ocr.capture(window)

        self.assertIsNotNone(image)
        self.assertEqual(fake_pyautogui.calls, [(0, 0, 960, 640)])
        self.assertEqual(image.size, (595, 359))

    def test_wechat_capture_keeps_left_bubbles_visible_in_fullscreen(self):
        from PIL import Image

        window = ChatWindow(
            hwnd=8,
            title="WeChat contact",
            rect=WindowRect(0, 0, 1920, 1030),
            platform=ChatPlatform.for_key("wechat"),
        )
        full = Image.new("RGB", (1920, 1030), "white")
        for x in range(0, 300):
            for y in range(0, 1030):
                full.putpixel((x, y), (232, 234, 237))
        fake_pyautogui = types.SimpleNamespace(calls=[], screenshot=lambda region: fake_pyautogui.calls.append(region) or full)
        ocr = ChatOCR(AppSettings())

        with patch("capture_ocr.pyautogui", fake_pyautogui):
            image = ocr.capture(window)

        self.assertIsNotNone(image)
        self.assertEqual(fake_pyautogui.calls, [(0, 0, 1920, 1030)])
        self.assertLessEqual(image.width, 1595)
        self.assertGreaterEqual(image.width, 1560)

    def test_wechat_capture_still_crops_sidebar_in_half_window(self):
        from PIL import Image

        window = ChatWindow(
            hwnd=8,
            title="WeChat contact",
            rect=WindowRect(0, 0, 960, 640),
            platform=ChatPlatform.for_key("wechat"),
        )
        full = Image.new("RGB", (960, 640), "white")
        for x in range(0, 300):
            for y in range(0, 640):
                full.putpixel((x, y), (232, 234, 237))
        fake_pyautogui = types.SimpleNamespace(calls=[], screenshot=lambda region: fake_pyautogui.calls.append(region) or full)
        ocr = ChatOCR(AppSettings())

        with patch("capture_ocr.pyautogui", fake_pyautogui):
            image = ocr.capture(window)

        self.assertIsNotNone(image)
        self.assertEqual(fake_pyautogui.calls, [(0, 0, 960, 640)])
        self.assertLessEqual(image.width, 650)
        self.assertGreaterEqual(image.width, 620)

    def test_wecom_capture_excludes_right_business_sidebar(self):
        from PIL import Image

        window = ChatWindow(
            hwnd=8,
            title="WeCom contact",
            rect=WindowRect(0, 0, 1000, 700),
            platform=ChatPlatform.for_key("wecom"),
        )
        full = Image.new("RGB", (1000, 700), "white")
        for x in range(0, 280):
            for y in range(0, 700):
                full.putpixel((x, y), (232, 234, 237))
        fake_pyautogui = types.SimpleNamespace(calls=[], screenshot=lambda region: fake_pyautogui.calls.append(region) or full)
        ocr = ChatOCR(AppSettings())

        with patch("capture_ocr.pyautogui", fake_pyautogui):
            image = ocr.capture(window)

        self.assertIsNotNone(image)
        self.assertEqual(fake_pyautogui.calls, [(0, 0, 1000, 700)])
        # 企业微信 4.x：聊天区从 ~29% 一直延伸到窗口右缘（“我”的气泡靠右）。
        # 左侧灰色会话列表(280px)被排除后，裁剪宽度约 700px。
        self.assertLessEqual(image.width, 740)
        self.assertGreaterEqual(image.width, 660)

    def test_wechat_capture_excludes_bottom_input_box(self):
        from PIL import Image, ImageDraw

        window = ChatWindow(
            hwnd=8,
            title="WeChat contact",
            rect=WindowRect(0, 0, 960, 640),
            platform=ChatPlatform.for_key("wechat"),
        )
        full = Image.new("RGB", (960, 640), "white")
        draw = ImageDraw.Draw(full)
        draw.rectangle((0, 0, 300, 640), fill=(232, 234, 237))
        draw.line((300, 540, 940, 540), fill=(225, 225, 225), width=2)
        fake_pyautogui = types.SimpleNamespace(calls=[], screenshot=lambda region: fake_pyautogui.calls.append(region) or full)
        ocr = ChatOCR(AppSettings())

        with patch("capture_ocr.pyautogui", fake_pyautogui):
            image = ocr.capture(window)

        self.assertIsNotNone(image)
        self.assertLessEqual(image.height, 490)

    def test_sender_click_input_uses_window_platform_input_point(self):
        window = ChatWindow(
            hwnd=9,
            title="QQ contact",
            rect=WindowRect(10, 20, 970, 660),
            platform=ChatPlatform.for_key("qq"),
        )
        fake_pyautogui = types.SimpleNamespace(calls=[], click=lambda x, y: fake_pyautogui.calls.append((x, y)))

        with patch("sender.pyautogui", fake_pyautogui):
            WeChatSender._click_input(window)

        self.assertEqual(fake_pyautogui.calls, [(662, 621)])

    def test_sender_click_input_uses_wecom_chat_input_not_right_sidebar(self):
        window = ChatWindow(
            hwnd=9,
            title="WeCom contact",
            rect=WindowRect(10, 20, 1010, 720),
            platform=ChatPlatform.for_key("wecom"),
        )
        fake_pyautogui = types.SimpleNamespace(calls=[], click=lambda x, y: fake_pyautogui.calls.append((x, y)))

        with patch("sender.pyautogui", fake_pyautogui):
            WeChatSender._click_input(window)

        self.assertLessEqual(fake_pyautogui.calls[0][0], 560)

    def test_sender_stops_when_target_window_did_not_become_foreground(self):
        window = ChatWindow(
            hwnd=9,
            title="QQ contact",
            rect=WindowRect(10, 20, 970, 660),
            platform=ChatPlatform.for_key("qq"),
        )
        fake_pyautogui = types.SimpleNamespace(
            calls=[],
            click=lambda *args: fake_pyautogui.calls.append(("click", args)),
            hotkey=lambda *args: fake_pyautogui.calls.append(("hotkey", args)),
            press=lambda *args: fake_pyautogui.calls.append(("press", args)),
        )
        fake_pyperclip = types.SimpleNamespace(copy=lambda text: fake_pyautogui.calls.append(("copy", text)))
        fake_win32gui = types.SimpleNamespace(
            IsIconic=lambda hwnd: False,
            SetForegroundWindow=lambda hwnd: None,
            GetForegroundWindow=lambda: 88,
        )

        with patch("sender.pyautogui", fake_pyautogui), patch("sender.pyperclip", fake_pyperclip), patch(
            "sender.win32gui", fake_win32gui
        ):
            ok, message = WeChatSender().send(window, "hello")

        self.assertFalse(ok)
        self.assertIn("目标聊天窗口", message)
        self.assertEqual(fake_pyautogui.calls, [])

    def test_sender_restores_clipboard_after_send(self):
        window = ChatWindow(
            hwnd=9,
            title="QQ contact",
            rect=WindowRect(10, 20, 970, 660),
            platform=ChatPlatform.for_key("qq"),
        )
        fake_pyautogui = types.SimpleNamespace(
            calls=[],
            click=lambda *args: fake_pyautogui.calls.append(("click", args)),
            hotkey=lambda *args: fake_pyautogui.calls.append(("hotkey", args)),
            press=lambda *args: fake_pyautogui.calls.append(("press", args)),
        )
        clipboard = {"value": "old clipboard"}

        def copy(text):
            clipboard["value"] = text
            fake_pyautogui.calls.append(("copy", text))

        fake_pyperclip = types.SimpleNamespace(paste=lambda: clipboard["value"], copy=copy)
        fake_win32gui = types.SimpleNamespace(
            IsIconic=lambda hwnd: False,
            SetForegroundWindow=lambda hwnd: None,
            GetForegroundWindow=lambda: 9,
        )

        with patch("sender.pyautogui", fake_pyautogui), patch("sender.pyperclip", fake_pyperclip), patch(
            "sender.win32gui", fake_win32gui
        ):
            ok, _message = WeChatSender().send(window, "new reply")

        self.assertTrue(ok)
        self.assertEqual(clipboard["value"], "old clipboard")
        self.assertIn(("copy", "new reply"), fake_pyautogui.calls)
        self.assertEqual(fake_pyautogui.calls[-1], ("copy", "old clipboard"))

if __name__ == "__main__":
    unittest.main()
