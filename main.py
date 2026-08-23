from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
import threading
import time
from difflib import SequenceMatcher
from pathlib import Path
from queue import Empty, Queue

from automation_mode import FULL_AUTO, HALF_AUTO, OFF, automation_flags
from chat_training import import_chat_zip_to_memory, import_chat_zip_to_memory_with_llm
from capture_ocr import ChatOCR
from float_ui import ReplyAssistantUI
from memory import ENTERPRISE_ANNUAL_REPORT_SOURCE, StyleMemory
from reply_engine import (
    NO_DIRECT_ANSWER_REPLY,
    ReplyEngine,
    _is_question,
    _is_pending_reply,
    last_meaningful_other_message,
    latest_meaningful_message,
    select_best_reply_for_context,
    unreplied_other_messages,
)
from sender import WeChatSender
from settings import AppSettings
from sop_analyzer import (
    analyze_training_source_to_memory,
    build_sop_library_from_memory,
    ensure_sop_library_flow,
    FLOW_LIBRARY_VERSION,
    format_sop_document,
    format_sop_library_document,
    load_sop_library,
    save_sop_library,
    sop_document_from_memory,
    sop_library_from_report,
    write_sop_library_html,
)
from visible_content import VisibleContentAnalyzer, build_augmented_context, visible_from_ocr_text
from wechat_window import WeChatWindow, WeChatWindowDetector


try:
    import pyautogui
except Exception:  # pragma: no cover
    pyautogui = None


def _setup_diag_log() -> logging.Logger:
    """全托管发送链路诊断日志：记录每次发送决策与原因，便于排查“候选刷新但不发送”。"""
    logger = logging.getLogger("managed_send_diag")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        base = os.environ.get("APPDATA", "")
        log_dir = Path(base) / "WeChatStyleReplyAssistant" if base else Path.home() / ".wechat_style_reply_assistant"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(log_dir / "managed_send.log", encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(handler)
            logger.propagate = False
        except Exception:
            pass
    return logger


DIAG_LOG = _setup_diag_log()

DOCK_FOLLOW_INTERVAL_SECONDS = 0.05


def _diag(*args: object) -> None:
    try:
        DIAG_LOG.info(" ".join(str(arg) for arg in args))
    except Exception:
        pass



MSG = {
    "waiting": "等待微信、企业微信或 QQ 聊天窗口...",
    "no_key": "\u5df2\u542f\u52a8\u3002\u672a\u914d\u7f6e API Key \u65f6\u4f1a\u4f7f\u7528\u672c\u5730\u5907\u7528\u56de\u590d\u3002",
    "idle_partner": "等待聊天窗口",
    "idle_status": "请打开微信、企业微信或 QQ，并点进一个聊天",
    "current": "当前聊天：{title}",
    "manual_ready": "已定位聊天窗口。点“识别当前聊天”生成回复。",
    "recognizing": "\u6b63\u5728\u8bc6\u522b\u5f53\u524d\u804a\u5929...",
    "step_located": "1/5 已定位聊天窗口：{title}",
    "step_capture": "2/5 \u6b63\u5728\u622a\u53d6\u5f53\u524d\u804a\u5929\u533a\u57df...",
    "step_ocr": "3/5 \u6b63\u5728\u8c03\u7528\u672c\u5730 OCR \u8bc6\u522b\u6c14\u6ce1\u6587\u5b57...",
    "step_voice": "正在检测语音气泡并尝试转文字...",
    "step_clean": "4/5 \u6b63\u5728\u6e05\u7406\u65f6\u95f4\u6233\u548c\u56fe\u6807\u566a\u58f0...",
    "step_generate": "6/6 正在结合记忆和上下文生成回复...",
    "step_visible": "5/6 正在理解图片、卡片和可见标题...",
    "no_text": "\u6ca1\u6709\u8bc6\u522b\u5230\u804a\u5929\u6587\u5b57",
    "unchanged": "\u804a\u5929\u5185\u5bb9\u65e0\u53d8\u5316",
    "generating": "\u6b63\u5728\u751f\u6210\u56de\u590d...",
    "generated": "\u5df2\u751f\u6210 {count} \u6761\u56de\u590d｜来源：{source}",
    "visible_skip": "识别为表情包/纯图片且没有实际问题，已跳过回复。",
    "wait_other": "\u6700\u65b0\u6709\u6548\u6d88\u606f\u662f\u6211\u65b9\u53d1\u51fa\uff0c\u5df2\u505c\u6b62\u81ea\u52a8\u56de\u590d\uff0c\u7b49\u5f85\u5bf9\u65b9\u4e0b\u4e00\u6761\u6d88\u606f\u3002",
    "monitor_error": "\u76d1\u63a7\u5f02\u5e38\uff1a{error}",
    "no_window": "请先点进一个微信、企业微信或 QQ 聊天",
    "no_context": "\u8fd8\u6ca1\u6709\u8bc6\u522b\u5230\u804a\u5929\u5185\u5bb9",
    "regenerating": "\u6b63\u5728\u91cd\u65b0\u751f\u6210...",
    "training_on": "\u5df2\u5f00\u542f\u8bad\u7ec3\u6a21\u5f0f\uff1a\u5c06\u6301\u7eed\u5b66\u4e60\u4e0a\u4e0b\u6587\u548c\u4f60\u53d1\u51fa\u7684\u56de\u590d\uff0c\u4e0d\u4f1a\u81ea\u52a8\u53d1\u9001\u3002",
    "training_off": "\u5df2\u5173\u95ed\u8bad\u7ec3\u6a21\u5f0f\u3002",
    "training_learned": "\u8bad\u7ec3\u6a21\u5f0f\u5df2\u5b66\u4e60\u5f53\u524d\u804a\u5929\u4e0a\u4e0b\u6587\u548c\u6211\u65b9\u56de\u590d\u3002",
    "half_auto_on": "已开启半自动：自动识别并生成候选回复，不会自动发送。",
    "full_auto_on": "已开启全自动：自动识别、生成并发送回复。",
    "automation_off": "已关闭自动功能，请手动点击识别按钮。",
    "auto_sending": "全自动审核后选中：{text}",
    "managed_preflight_blocked": "全自动发送前复核未通过：{reason}",
    "api_failed_stop_send": "接口未生效，已停止全自动发送。请先检查 API Key、额度、模型名或 Base URL。",
    "no_send_window": "没有可发送的聊天窗口",
    "training_imported": "训练包已导入：{files} 个文件，{turns} 轮对话，写入 {examples} 条长期记忆样例。",
}


class WeChatStyleReplyAssistant:
    def __init__(self) -> None:
        self.settings = AppSettings().load()
        self.memory = StyleMemory().load()
        self.detector = WeChatWindowDetector()
        self.ocr = ChatOCR(self.settings)
        self.visible_analyzer = VisibleContentAnalyzer(self.settings)
        self.engine = ReplyEngine(self.settings, self.memory)
        self.sender = WeChatSender()
        self.queue: Queue[tuple[str, object]] = Queue()
        self.current_window: WeChatWindow | None = None
        self.current_context = ""
        self.current_hash = ""
        self.last_capture = 0.0
        self.processing = False
        self.running = True
        self.progress_steps: list[str] = []
        self.last_auto_reply_signature = ""
        self.pending_auto_reply_signature = ""
        self.handled_auto_reply_signatures: set[str] = set()
        self.managed_send_inflight = False
        self.managed_waiting_for_own_echo = False
        self.last_managed_sent_text = ""
        self.managed_last_handled_turn_messages: list[str] = []
        self.managed_last_handled_turn_key = ""
        self.managed_last_sent_at = 0.0
        self.managed_last_handled_partner = ""
        self.last_window_key = ""
        self.last_snap_geometry = ""
        self.dock_target_hwnd = 0
        self.dock_last_rect_key: tuple[int, int, int, int, int] | None = None
        self.generation_inflight = False
        self.pending_generation: tuple[str, WeChatWindow, list[str]] | None = None
        self.generation_active_turn: list[str] = []
        self.generation_lock = threading.Lock()
        self.engine_call_lock = threading.Lock()
        # ---- 会话列表自动切换旁路（conversation_list 模块）----
        from conversation_list import (
            ChatIdentityTracker,
            ChatStateGuard,
            ConversationListMonitor,
            ConversationTypeDetector,
        )

        self.chat_identity = ChatIdentityTracker()
        self.chat_state_guard = ChatStateGuard()
        self.chat_list_monitor = ConversationListMonitor()
        self.chat_type_detector = ConversationTypeDetector()
        # 群聊客服（group_chat_handler 模块）
        from group_chat_handler import GroupChatHandler

        self.group_handler = GroupChatHandler(self.settings)
        self.current_chat_type = "personal"
        self.group_last_handled_msg = ""
        self.group_generation_inflight = False
        self.pending_group_generation: tuple[WeChatWindow, str, str, str, str, str] | None = None
        self.group_generation_lock = threading.Lock()
        self.bypass_last_scan_at = 0.0
        self.bypass_self_since = 0.0
        self.bypass_fail_count = 0
        self.bypass_paused_until = 0.0
        self.bypass_retrying = False
        self.bypass_switch_in_progress = False
        self.force_refresh = False
        self.ui = ReplyAssistantUI(
            self.settings,
            {
                "settings": self._open_settings,
                "recognize": self._recognize_current,
                "training_changed": self._training_changed,
                "automation_mode_changed": self._automation_mode_changed,
                "source_mode_changed": self._source_mode_changed,
                "regenerate": self._regenerate,
                "send": self._send,
                "import_training_zip": self._import_training_zip,
                "analyze_sop_training": self._analyze_sop_training,
                "analyze_current_sop_library": self._analyze_current_sop_library,
                "get_sop_document": self._get_sop_document,
                "get_sop_html_report": self._get_sop_html_report,
                "get_phrasebook_libraries": self._get_phrasebook_libraries,
                "get_phrasebook_examples": self._get_phrasebook_examples,
                "save_phrasebook_example": self._save_phrasebook_example,
                "delete_phrasebook_examples": self._delete_phrasebook_examples,
                "exit": self._exit,
            },
        )

    def start(self) -> None:
        self.ui.set_status(MSG["waiting"] if self.settings.api_key else MSG["no_key"])
        threading.Thread(
            target=self._dock_follow_loop,
            daemon=True,
            name="wecom-window-follow",
        ).start()
        threading.Thread(target=self._monitor_loop, daemon=True).start()
        self.ui.root.after(250, self._ui_loop)
        # 首次使用群聊功能：用户名未填写 → 弹提示（UI 线程）
        if not self.settings.wecom_account_name.strip():
            self.ui.root.after(900, self._prompt_account_name)
        self.ui.mainloop()
        self.running = False

    def _prompt_account_name(self) -> None:
        """首次弹提示让用户填写当前账号用户名；填写后保存并回填输入框。"""
        try:
            import tkinter.simpledialog as simpledialog

            name = simpledialog.askstring(
                "填写当前账号用户名",
                "检测到您可能使用群聊客服功能。\n请输入当前企业微信账号的用户名（如：程正恒）：",
                parent=self.ui.root,
            )
            if name and name.strip():
                self.settings.wecom_account_name = name.strip()
                self.settings.save()
                try:
                    self.ui.account_name_var.set(name.strip())
                except Exception:
                    pass
                self.ui.set_status(f"已保存当前账号用户名：{name.strip()}")
        except Exception:
            pass

    def _poll_interval(self) -> float:
        """轮询间隔：群聊用 group_poll_interval_ms（更快响应 @），其他用默认。"""
        if getattr(self, "current_chat_type", "personal") == "group":
            return float(self.settings.group_poll_interval_ms)
        return float(self.settings.poll_interval_ms)

    def _dock_follow_once(self) -> None:
        window = _resolve_dock_follow_window(self.detector, self.dock_target_hwnd)
        if window is None:
            return
        self.dock_target_hwnd = int(window.hwnd)
        rect_key = _dock_window_rect_key(window)
        if rect_key == getattr(self, "dock_last_rect_key", None):
            return
        self.dock_last_rect_key = rect_key
        self.queue.put(("dock_window", window))

    def _dock_follow_loop(self) -> None:
        while self.running:
            try:
                self._dock_follow_once()
            except Exception:
                pass
            time.sleep(DOCK_FOLLOW_INTERVAL_SECONDS)

    def _monitor_loop(self) -> None:
        while self.running:
            try:
                wechat = self.detector.foreground_chat()
                if not wechat and (
                    self.settings.auto_recognize or self.settings.training_mode or self.settings.managed_auto_reply
                ):
                    wechat = self.detector.any_chat_window()
                if not wechat:
                    self.queue.put(("idle", None))
                    time.sleep(self._poll_interval() / 1000)
                    continue

                self.current_window = wechat
                window_key = _window_key(wechat)
                if window_key != self.last_window_key:
                    self.last_window_key = window_key
                    self.current_hash = ""
                    self.last_capture = 0.0
                self.queue.put(("window", wechat))
                if self.settings.managed_auto_reply and self.managed_send_inflight:
                    time.sleep(self._poll_interval() / 1000)
                    continue
                # 先刷新群聊/个人类型，再进入主识别流程。否则手动切进群聊的
                # 第一轮仍会按旧的个人会话执行图片理解，最长阻塞一次视觉请求。
                try:
                    self._conversation_list_bypass(wechat)
                except Exception as exc:
                    self.queue.put(("status", f"会话切换旁路异常：{exc}"))
                if self.settings.auto_recognize or self.settings.training_mode:
                    now = time.time()
                    if now - self.last_capture >= self.settings.capture_cooldown_seconds:
                        self.last_capture = now
                        self._process_window(wechat, force=self.force_refresh)
                        self.force_refresh = False
            except Exception as exc:
                self.queue.put(("status", MSG["monitor_error"].format(error=exc)))
            time.sleep(self._poll_interval() / 1000)

    def _conversation_list_bypass(self, wechat: WeChatWindow) -> None:
        """会话列表自动切换旁路（托管模式）。

        触发：当前会话最新消息为我方发出且持续 1.2 秒稳定 → 最短每 0.8 秒扫描左侧列表红点 →
        点击 y 最小且非当前会话的红点行 → 确认切换（顶部标题变化 + 红点消失）→
        判定会话类型分流。6 种打断条件任一命中即跳过。连续 3 次失败 UI 提示 + 暂停 5 分钟。

        最新消息角色由旁路自己截图 OCR 判定（不依赖主路 current_context，
        托管模式单独开启时主路不识别，current_context 恒为空会导致永不触发）。
        """
        if not self.settings.managed_auto_reply or self.bypass_switch_in_progress:
            return
        # 一次全窗截图三用：聊天区 OCR 判角色 / 列表区红点 / 顶部标题区
        from capture_ocr import _capture_window_bitmap, _crop_detected_chat_area_with_origin

        full = _capture_window_bitmap(wechat)
        if full is None:
            return
        chat_crop = None
        try:
            chat_crop, _origin = _crop_detected_chat_area_with_origin(full, "wecom", self.settings.chat_area)
        except Exception:
            pass
        context = ""
        if chat_crop is not None:
            try:
                context = self.ocr.read_image(chat_crop)
            except Exception:
                context = ""
        self._refresh_current_chat_type(self._read_top_title(full))
        latest_role = "对方"
        if context:
            latest_role, _latest_text = latest_meaningful_message(context)
        now = time.time()
        # 群聊：最新消息没 @ 我 = 聊天内容无变化 → 等同"我方发出"，也能触发扫描
        chat_type = getattr(self, "current_chat_type", "personal")
        group_idle = False
        if chat_type == "group":
            # 群聊角色用发送者名字判定（位置规则对长消息会误判"阳光"为"我"）
            role_by_sender = self.group_handler.latest_role_by_sender(
                context, self.settings.wecom_account_name
            )
            if role_by_sender:
                latest_role = role_by_sender
            # 群聊里最新消息 @ 我 → 直接走群聊回复（不扫描列表）
            account_name = self.settings.wecom_account_name.strip()
            if latest_role == "对方" and account_name and self.group_handler.detect_at_mention(
                context, account_name
            ):
                self._handle_group_chat(wechat, context, full)
                return
            group_idle = self.group_handler.is_chat_idle(context, self.settings.wecom_account_name)
        stable_for_scan = latest_role in {"我", "系统"} or (
            chat_type == "group" and latest_role == "对方" and group_idle
        )
        if stable_for_scan:
            if self.bypass_self_since == 0:
                self.bypass_self_since = now
        else:
            self.bypass_self_since = 0.0
        seconds_as_self = (now - self.bypass_self_since) if self.bypass_self_since else 0.0
        if not self.chat_state_guard.should_start_scan(
            True,
            latest_role,
            seconds_as_self,
            self.bypass_last_scan_at,
            now,
            self.bypass_paused_until,
            chat_type=chat_type,
            group_idle=group_idle,
        ):
            return
        # 企业微信必须在最前台（不抢焦点，不在前台就等下一轮）
        if not WeChatSender._is_target_foreground(wechat):
            return
        user_typing = self._user_typing_detected()
        interrupt_role = "我" if (
            latest_role == "系统"
            or (chat_type == "group" and latest_role == "对方" and group_idle)
        ) else latest_role
        if self.chat_state_guard.is_interrupted(
            interrupt_role,
            user_typing,
            self.managed_send_inflight,
            self.processing,
            True,
            self.bypass_retrying,
        ):
            return
        self.bypass_last_scan_at = now
        self._bypass_try_switch(wechat, full)

    def _bypass_try_switch(self, wechat: WeChatWindow, full=None) -> None:
        """执行一次扫描+切换（含重试与失败计数）。full 可复用旁路已截的全窗图。"""
        from capture_ocr import _capture_window_bitmap

        if full is None:
            full = _capture_window_bitmap(wechat)
        if full is None:
            return
        hits = self.chat_list_monitor.scan(full)
        if not hits:
            return
        title_before = self._read_top_title(full)
        attempts = 1 + int(self.chat_state_guard.cal.get("timing", {}).get("retry_per_failure", 1))
        success = False
        attempted_switches = 0
        actionable_seen = False
        title_after = ""
        row_avatar_type = ""
        row_title = ""
        for hit in hits:
            try:
                row_title_crop = self.chat_list_monitor.extract_row_title(full, hit)
                row_title = self.ocr.read_chat_title(row_title_crop, limit=1)
            except Exception:
                row_title = ""
            if not str(row_title or "").strip():
                continue
            if self.chat_type_detector.detect_title(row_title) == "system":
                continue
            actionable_seen = True
            if attempted_switches >= attempts:
                break
            self.bypass_retrying = attempted_switches > 0
            attempted_switches += 1
            if self.chat_state_guard.is_interrupted(
                "我",
                self._user_typing_detected(),
                self.managed_send_inflight,
                self.processing,
                WeChatSender._is_target_foreground(wechat),
                False,
            ):
                self.bypass_retrying = False
                return
            # 切换前：用列表行头像预判会话类型（群聊九宫格 vs 个人单块）
            row_avatar_type = ""
            try:
                row_avatar = self.chat_list_monitor.extract_row_avatar(full, hit)
                row_avatar_type = self.chat_type_detector.detect_row_avatar(row_avatar)
            except Exception:
                row_avatar_type = ""
            x, y = self.chat_list_monitor.click_point_for(hit, (full.width, full.height))
            try:
                import pyautogui

                pyautogui.click(wechat.rect.left + x, wechat.rect.top + y)
            except Exception:
                break
            time.sleep(self.chat_state_guard.cal.get("timing", {}).get("switch_wait_seconds", 1.2))
            full_after = _capture_window_bitmap(wechat)
            if full_after is None:
                continue
            title_after = self._read_top_title(full_after)
            # 切换确认：原红点消失（强信号——切到该会话后红点自动消失）+ 标题变化或已预判类型
            red_dots_after = self.chat_list_monitor.scan(full_after)
            same_row_still = any(abs(item.y - hit.y) <= 6 for item in red_dots_after)
            effective_title = title_after or row_title
            title_changed = bool(title_after) and title_after != title_before
            title_type = self.chat_type_detector.detect_title(effective_title)
            if (not same_row_still) and (
                title_changed or title_type in {"personal", "group"} or row_avatar_type
            ):
                success = True
                title_after = effective_title
                break
            # 红点还在/标题没变 → 点的是当前会话/未生效，换下一个红点重试
        self.bypass_retrying = False
        if success:
            self.bypass_fail_count = 0
            self.chat_identity.update_from_top_title(title_after)
            self._after_switch(wechat, title_after, row_avatar_type)
            return
        if not actionable_seen:
            return
        # 切换失败
        self.bypass_fail_count += 1
        max_failures = int(self.chat_state_guard.cal.get("timing", {}).get("max_consecutive_failures", 3))
        if self.bypass_fail_count >= max_failures:
            self.bypass_fail_count = 0
            pause = float(self.chat_state_guard.cal.get("timing", {}).get("fail_pause_seconds", 300))
            self.bypass_paused_until = time.time() + pause
            self.queue.put(("bypass_alert", "会话切换连续失败，已暂停旁路 5 分钟。"))

    def _after_switch(self, wechat: WeChatWindow, top_title: str, row_avatar_type: str = "") -> None:
        """切换成功后：重置主路会话状态 + 判定会话类型分流。"""
        # 重置主路状态（不破坏主路逻辑，仅通过 force 标志触发重识别）
        self.current_hash = ""
        self.current_context = ""
        self.managed_last_handled_partner = ""
        self.managed_last_handled_turn_messages = []
        self.managed_last_handled_turn_key = ""
        self.group_last_handled_msg = ""
        self.force_refresh = True
        # 四分类分流（system 标题优先；group/personal 优先用列表行头像判定）
        full = None
        from capture_ocr import _capture_window_bitmap

        full = _capture_window_bitmap(wechat)
        chat_type = self.chat_type_detector.detect(top_title, full, list_avatar_type=row_avatar_type)
        self.current_chat_type = chat_type
        if chat_type == "system":
            # 系统号：跳过不回复（红点切过去后自动消失）
            self.queue.put(("status", f"会话切换：系统号「{top_title}」，跳过不回复"))
            return
        if chat_type == "group":
            # 群聊客服分支
            self.queue.put(("status", f"会话切换：群聊「{top_title}」，进入群聊客服"))
            self._handle_group_chat(wechat)
            return
        if chat_type == "unknown":
            self.queue.put(("status", f"会话切换：无法判定会话类型「{top_title}」，留待人工"))
            return
        # personal：下一轮主路 force 重识别并处理
        self.queue.put(("status", f"会话切换：个人会话「{top_title}」，开始处理"))

    def _refresh_current_chat_type(self, top_title: str) -> None:
        """用户手动切换会话时，用标题强证据刷新旧的群聊/个人状态。"""
        detected = self.chat_type_detector.detect_title(top_title)
        if detected not in {"personal", "group", "system"}:
            return
        if detected == getattr(self, "current_chat_type", "personal"):
            return
        self.current_chat_type = detected
        self.current_hash = ""
        self.current_context = ""
        self.force_refresh = detected == "personal"
        labels = {"personal": "个人会话", "group": "群聊", "system": "系统会话"}
        self.queue.put(("status", f"当前会话已识别为{labels[detected]}「{top_title}」"))

    def _handle_group_chat(self, wechat: WeChatWindow, context: str = "", full=None) -> None:
        """群聊客服处理：@ 我 → 提取问题 → 生成回复 → @ 回发送者；没 @ → 不回复。

        用户名为空时：提示填写；填写前群聊消息一律视为"聊天内容无变化"。
        context/full 可复用旁路已截取的上下文与全窗图（避免重复截图）。
        """
        account_name = self.settings.wecom_account_name.strip()
        if not account_name:
            self.queue.put(
                ("bypass_alert", "检测到群聊会话：请先在输入框填写当前企业微信账号的用户名（如：程正恒），才能处理 @ 提问。")
            )
            return
        # 截图 + OCR 群聊上下文（复用传入的，否则自行截取）
        from capture_ocr import _capture_window_bitmap, _crop_detected_chat_area_with_origin

        if full is None:
            full = _capture_window_bitmap(wechat)
        if full is None:
            return
        if not context:
            chat_crop = None
            try:
                chat_crop, _origin = _crop_detected_chat_area_with_origin(full, "wecom", self.settings.chat_area)
            except Exception:
                pass
            if chat_crop is not None:
                try:
                    context = self.ocr.read_image(chat_crop)
                except Exception:
                    context = ""
        if not context.strip():
            return
        # 群聊角色用发送者名字判定（位置规则对长消息误判）
        latest_role = self.group_handler.latest_role_by_sender(context, account_name)
        if latest_role == "":
            latest_role, _latest_text = latest_meaningful_message(context)
        if latest_role != "对方":
            # 我方发出或空 → 无需回复（列表扫描条件已覆盖）
            self.group_last_handled_msg = ""
            return
        if not self.group_handler.detect_at_mention(context, account_name):
            # 没 @ 我 → 聊天内容无变化，不回复
            self.group_last_handled_msg = ""
            return
        # 防重：同一群聊的同一条 @ 消息只进入一次生成队列。
        latest_msg = self.group_handler.latest_other_message(context)
        # @ 我：提取问题 + 发送者名字 → 复用现有决策链生成 → @ 回
        question = self.group_handler.extract_question(
            latest_msg, account_name
        )
        sender_name = self.group_handler.extract_sender_name(context)
        if not question.strip():
            return
        expected_title = self._read_top_title(full)
        message_key = hashlib.sha1(
            f"{expected_title}\n{sender_name}\n{latest_msg}".encode("utf-8")
        ).hexdigest()
        if message_key == self.group_last_handled_msg:
            return
        self.group_last_handled_msg = message_key
        self._schedule_group_generation(
            wechat,
            context,
            latest_msg,
            question,
            sender_name,
            expected_title,
        )

    def _schedule_group_generation(
        self,
        wechat: WeChatWindow,
        context: str,
        latest_msg: str,
        question: str,
        sender_name: str,
        expected_title: str,
    ) -> None:
        """群聊生成在后台执行；新 @ 到来时只保留最后一个待处理任务。"""
        job = (wechat, context, latest_msg, question, sender_name, expected_title)
        lock = getattr(self, "group_generation_lock", None)
        if lock is None:
            lock = self.group_generation_lock = threading.Lock()
        with lock:
            if getattr(self, "group_generation_inflight", False):
                self.pending_group_generation = job
                return
            self.group_generation_inflight = True
        threading.Thread(
            target=lambda: self._group_generation_worker(job),
            daemon=True,
        ).start()

    def _group_generation_worker(
        self,
        job: tuple[WeChatWindow, str, str, str, str, str],
    ) -> None:
        wechat, _context, latest_msg, question, sender_name, expected_title = job
        try:
            engine_lock = getattr(self, "engine_call_lock", None)
            if engine_lock is None:
                replies = self.engine.generate(question, "群聊")
            else:
                with engine_lock:
                    replies = self.engine.generate(question, "群聊")
            lock = getattr(self, "group_generation_lock", None)
            with lock:
                has_newer_pending = self.pending_group_generation is not None
            if has_newer_pending:
                return
            if not replies:
                self.queue.put(("status", f"群聊 @ 回复：未找到合适回答（问题：{question[:30]}）"))
                return
            allowed, reason = self._group_send_preflight_allows(
                wechat,
                latest_msg,
                sender_name,
                expected_title,
            )
            if not allowed:
                self.queue.put(("status", f"群聊回复发送前复核未通过：{reason}"))
                return
            final_reply = self.group_handler.build_reply(sender_name, replies[0])
            self._send_group_reply(wechat, final_reply, question)
        except Exception as exc:
            self.queue.put(("status", f"群聊回复生成异常：{exc}"))
        finally:
            next_job = None
            lock = getattr(self, "group_generation_lock", None)
            with lock:
                if self.pending_group_generation is not None:
                    next_job = self.pending_group_generation
                    self.pending_group_generation = None
                else:
                    self.group_generation_inflight = False
            if next_job is not None:
                threading.Thread(
                    target=lambda: self._group_generation_worker(next_job),
                    daemon=True,
                ).start()

    def _group_send_preflight_allows(
        self,
        wechat: WeChatWindow,
        expected_message: str,
        expected_sender: str,
        expected_title: str,
    ) -> tuple[bool, str]:
        """发送前重新 OCR：会话、最新消息和 @ 任一变化就丢弃旧结果。"""
        from capture_ocr import _capture_window_bitmap, _crop_detected_chat_area_with_origin

        if not WeChatSender._is_target_foreground(wechat):
            return False, "企业微信已不在前台"
        full = _capture_window_bitmap(wechat)
        if full is None:
            return False, "无法重新截取当前群聊"
        live_title = self._read_top_title(full)
        if expected_title and not live_title:
            return False, "无法复核当前群聊标题"
        if expected_title:
            expected_compact = re.sub(r"\s+", "", expected_title)
            live_compact = re.sub(r"\s+", "", live_title)
            if expected_compact != live_compact:
                return False, "当前群聊已切换"
        try:
            chat_crop, _origin = _crop_detected_chat_area_with_origin(
                full, "wecom", self.settings.chat_area
            )
            live_context = self.ocr.read_image(chat_crop)
        except Exception:
            return False, "无法复核群聊最新消息"
        if self.group_handler.latest_role_by_sender(
            live_context, self.settings.wecom_account_name
        ) != "对方":
            return False, "最新消息已不是对方消息"
        live_message = self.group_handler.latest_other_message(live_context)
        if not live_message or not _messages_are_similar(expected_message, live_message):
            return False, "对方最新消息已变化"
        if not self.group_handler.detect_at_mention(
            live_context, self.settings.wecom_account_name
        ):
            return False, "最新消息没有 @ 当前账号"
        live_sender = self.group_handler.extract_sender_name(live_context)
        if expected_sender and live_sender and expected_sender != live_sender:
            return False, "最新消息发送者已变化"
        return True, ""

    def _send_group_reply(self, wechat: WeChatWindow, text: str, question: str) -> None:
        """在群聊生成工作线程内发送，主监控循环不会被网络或发送动作阻塞。"""
        try:
            ok, message = self.sender.send(wechat, text)
            if ok:
                self.queue.put(("status", f"群聊 @ 回复已发送：{text[:40]}"))
            else:
                self.queue.put(("status", f"群聊回复发送失败：{message}"))
        except Exception as exc:
            self.queue.put(("status", f"群聊回复发送异常：{exc}"))

    def _read_top_title(self, full_window_image) -> str:
        """顶部标题栏 OCR（会话标题/系统号名称）。"""
        try:
            area = self.chat_type_detector.cal.get("top_title_area", {})
            width, height = full_window_image.size
            left = int(width * float(area.get("x_left", 0.29)))
            right = int(width * float(area.get("x_right", 1.0)))
            top = int(height * float(area.get("y_top", 0.0)))
            bottom = int(height * float(area.get("y_bottom", 0.10)))
            crop = full_window_image.crop((left, top, right, bottom))
            return self.ocr.read_chat_title(crop, limit=3)
        except Exception:
            return ""

    def _user_typing_detected(self) -> bool:
        """近 1.5 秒内有键盘活动（轮询常用键的 GetAsyncKeyState + 时间戳窗口）。"""
        try:
            import ctypes

            user32 = ctypes.windll.user32
            keys = (
                [0x41 + i for i in range(26)]  # A-Z
                + [0x30 + i for i in range(10)]  # 0-9
                + [0x20, 0x0D, 0x08, 0x1B]  # 空格/回车/退格/Esc
            )
            now = time.time()
            pressed_now = False
            for key in keys:
                state = user32.GetAsyncKeyState(key)
                if state & 0x8000:  # 当前按下
                    pressed_now = True
                elif state & 0x0001:  # 本次轮询前按下过 → 轮询间隔内有按键
                    self._last_key_event_at = now
            window_typing_seconds = float(self.chat_state_guard.cal.get("timing", {}).get("user_typing_seconds", 1.5))
            if pressed_now or (now - getattr(self, "_last_key_event_at", 0.0)) < window_typing_seconds:
                return True
            return False
        except Exception:
            return False

    def _process_window(self, wechat: WeChatWindow, force: bool) -> None:
        if self.processing:
            return
        if getattr(self, "current_chat_type", "personal") == "group":
            # 群聊一律不走个人主路（force 也不行——切换后 force 重识别会重复处理群聊
            # 导致双重回复/状态卡死），群聊由旁路 _handle_group_chat 单独处理
            return
        if self.settings.managed_auto_reply and self.managed_send_inflight and not force:
            return
        self.processing = True
        training_only = self.settings.training_mode and not self.settings.auto_recognize and not self.settings.managed_auto_reply and not force
        try:
            self.queue.put(("progress_clear", None))
            self._progress(MSG["step_located"].format(title=wechat.title))
            if _should_clear_replies_before_capture(force, training_only):
                self.queue.put(("clear_replies", None))
            self._progress(MSG["step_capture"])
            # 截图前滚动聊天窗口到底部，确保识别到最新消息（企业微信/微信若停在旧位置会漏掉新消息）
            if not self.managed_send_inflight:
                try:
                    self.sender.scroll_chat_to_bottom(wechat)
                except Exception:
                    pass
            image = _capture_chat_image_without_overlay(self.ocr, self.queue, self.sender, wechat)
            if image is None:
                self.queue.put(("status", self.ocr.engine_error or MSG["no_text"]))
                return
            self._progress(MSG["step_ocr"])
            context = self.ocr.read_image(image)
            latest_role, _latest_text = latest_meaningful_message(context)
            if not context.strip() or latest_role != "对方":
                voice_context, voice_image = self._try_transcribe_latest_voice(wechat, image, context)
                if voice_context and voice_context != context:
                    context = voice_context
                    image = voice_image or image
            self._progress(MSG["step_clean"])
            if not context.strip():
                self._progress(MSG["step_visible"])
                visible = self.visible_analyzer.analyze("", image)
                if _visible_skip_should_block_reply("[瀵规柟] [鍥剧墖]", visible):
                    context = build_augmented_context("[对方] [图片]", visible)
                    self.current_context = context
                    self.queue.put(("context", context))
                    if _should_clear_replies_for_skip(force):
                        self.queue.put(("clear_replies", None))
                    self.queue.put(("status", MSG["visible_skip"]))
                    return
                if visible.summary:
                    context = build_augmented_context("[对方] [图片]", visible)
                else:
                    image_text = self.ocr.read_visible_text(image)
                    visible = visible_from_ocr_text(image_text)
                    if visible.summary:
                        context = build_augmented_context("[瀵规柟] [鍥剧墖]", visible)
                    else:
                        msg = self.visible_analyzer.last_error or self.ocr.engine_error or MSG["no_text"]
                        self.queue.put(("status", msg))
                        return
            else:
                self._progress(MSG["step_visible"])
                visible = self.visible_analyzer.analyze(context, image)
            if _visible_skip_should_block_reply(context, visible):
                self.current_context = build_augmented_context(context, visible)
                self.queue.put(("context", self.current_context))
                if _should_clear_replies_for_skip(force):
                    self.queue.put(("clear_replies", None))
                self.queue.put(("status", MSG["visible_skip"]))
                return
            context = build_augmented_context(context, visible)
            digest = hashlib.sha1(f"{wechat.title}\n{context}".encode("utf-8")).hexdigest()
            if _should_skip_unchanged_context(
                force=force,
                managed=self.settings.managed_auto_reply,
                current_context=self.current_context,
                new_context=context,
                current_hash=self.current_hash,
                digest=digest,
                handled_messages=self.managed_last_handled_turn_messages,
            ):
                self.current_hash = digest
                self.queue.put(("status", MSG["unchanged"]))
                return
            self.current_hash = digest
            self.current_context = context
            if _should_learn_observed_chat(self.settings.training_mode, self.settings.managed_auto_reply):
                self.memory.learn_from_chat_text(context)
            self.queue.put(("context", context))
            if training_only:
                self.queue.put(("status", MSG["training_learned"]))
                return
            if self.settings.managed_auto_reply and self.managed_last_handled_partner != wechat.title:
                self.managed_last_handled_turn_messages = []
                self.managed_last_handled_turn_key = ""
                self.managed_last_handled_partner = wechat.title
            if self.settings.managed_auto_reply and self.managed_waiting_for_own_echo:
                # 超时保护：等待自己的回声超过 8 秒仍识别不到时强制解除，
                # 避免企业微信“我”气泡识别不出时永久卡死、不响应新消息。
                if time.time() - self.managed_last_sent_at > 8.0:
                    self.managed_waiting_for_own_echo = False
                else:
                    allow_generation, still_waiting = _managed_reply_gate_allows_generation(
                        context,
                        self.managed_waiting_for_own_echo,
                        self.last_managed_sent_text,
                        self.managed_last_handled_turn_messages,
                        allow_new_without_own_echo=True,
                    )
                    self.managed_waiting_for_own_echo = still_waiting
                    if still_waiting:
                        self.queue.put(("status", MSG["wait_other"]))
                        return
                    if not allow_generation:
                        self.queue.put(("status", MSG["wait_other"]))
                        return
            latest_role, _latest_text = latest_meaningful_message(context)
            if not _should_auto_generate_for_context(
                context,
                force=force,
                managed=self.settings.managed_auto_reply,
                handled_messages=self.managed_last_handled_turn_messages,
            ):
                self.queue.put(("status", MSG["wait_other"]))
                return
            self._progress(MSG["step_generate"])
            target_turn = _current_unreplied_turn(context)
            self._schedule_generation(context, wechat, target_turn)
        finally:
            self.processing = False

    def _schedule_generation(self, context: str, wechat: WeChatWindow, target_turn: list[str]) -> None:
        with self.generation_lock:
            if self.generation_inflight:
                if self.settings.managed_auto_reply and _turns_are_same_or_similar(
                    self.generation_active_turn,
                    target_turn,
                ):
                    return
                self.pending_generation = (context, wechat, list(target_turn))
                return
            self.generation_inflight = True
            self.generation_active_turn = list(target_turn)
        threading.Thread(target=lambda: self._generation_worker(context, wechat, list(target_turn)), daemon=True).start()

    def _generation_worker(self, context: str, wechat: WeChatWindow, target_turn: list[str]) -> None:
        try:
            engine_lock = getattr(self, "engine_call_lock", None)
            if engine_lock is None:
                replies = self.engine.generate(context, wechat.title, target_turn=target_turn)
            else:
                with engine_lock:
                    replies = self.engine.generate(context, wechat.title, target_turn=target_turn)
            with self.generation_lock:
                has_newer_pending = self.pending_generation is not None
            if self.settings.managed_auto_reply:
                result_current = _managed_generation_result_still_current(
                    target_turn,
                    wechat,
                    self.current_context,
                    self.current_window,
                )
            else:
                result_current = _generation_result_still_current(
                    context,
                    wechat,
                    self.current_context,
                    self.current_window,
                )
            if not has_newer_pending and result_current:
                self.queue.put(("replies", replies))
                self.queue.put(("reply_source", self._reply_source_label()))
                self.queue.put(("status", self._generated_status(len(replies))))
                self._maybe_managed_send(context, replies)
        finally:
            next_job = None
            with self.generation_lock:
                if self.pending_generation is not None:
                    next_job = self.pending_generation
                    self.pending_generation = None
                    self.generation_active_turn = list(next_job[2])
                else:
                    self.generation_inflight = False
                    self.generation_active_turn = []
            if next_job is not None:
                next_context, next_window, next_turn = next_job
                threading.Thread(
                    target=lambda: self._generation_worker(next_context, next_window, next_turn),
                    daemon=True,
                ).start()

    def _generated_status(self, count: int) -> str:
        status = MSG["generated"].format(count=count, source=self.engine.last_source)
        if getattr(self.engine, "last_search", ""):
            status += f"；{self.engine.last_search}"
        if self.engine.last_error:
            status += f"；接口提示：{self.engine.last_error}"
        return status

    def _reply_source_label(self) -> str:
        label = f"来源：{self.engine.last_source}"
        if self.engine.last_error:
            label += "｜接口未生效"
        return label

    def _ui_loop(self) -> None:
        while True:
            try:
                kind, data = self.queue.get_nowait()
            except Empty:
                break

            if kind == "idle":
                self.ui.set_partner(MSG["idle_partner"])
                self.ui.set_status(MSG["idle_status"])
            elif kind == "window" and isinstance(data, WeChatWindow):
                self.ui.set_partner(MSG["current"].format(title=getattr(data, "display_title", data.title)))
                if not self.settings.auto_recognize:
                    self.ui.set_status(MSG["manual_ready"])
                self._apply_dock_geometry(data)
            elif kind == "dock_window" and isinstance(data, WeChatWindow):
                self._apply_dock_geometry(data)
            elif kind == "hide_for_capture":
                try:
                    self.ui.root.withdraw()
                    self.ui.root.update_idletasks()
                finally:
                    data.set()
            elif kind == "show_after_capture":
                self.ui.root.deiconify()
            elif kind == "status":
                self.ui.set_status(str(data))
            elif kind == "progress":
                self.ui.set_status(str(data))
                self.ui.set_progress(list(self.progress_steps))
            elif kind == "progress_clear":
                self.progress_steps.clear()
                self.ui.clear_progress()
            elif kind == "context":
                self.ui.set_context(str(data))
            elif kind == "replies":
                self.ui.set_replies(list(data))
            elif kind == "reply_source":
                self.ui.set_reply_source(str(data))
            elif kind == "clear_replies":
                self.ui.clear_replies()
            elif kind == "bypass_alert":
                self._show_bypass_alert(str(data))
        if self.running:
            self.ui.root.after(250, self._ui_loop)

    def _apply_dock_geometry(self, window: WeChatWindow) -> None:
        self.last_snap_geometry = _snap_geometry_if_changed(
            self.ui.root,
            self.detector.snap_geometry(
                window,
                self.ui.root.winfo_width(),
                self.ui.root.winfo_height(),
            ),
            self.last_snap_geometry,
        )

    def _show_bypass_alert(self, message: str) -> None:
        """旁路告警：屏幕正中央 + 最上层 + 系统提示音。"""
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = self.ui.root
            root.attributes("-topmost", True)
            root.update_idletasks()
            # 独立置顶提示窗，屏幕正中央
            win = tk.Toplevel(root)
            win.withdraw()
            win.attributes("-topmost", True)
            win.title(MSG.get("bypass_alert_title", "聊小智提示"))
            tk.Label(win, text=message, font=("Microsoft YaHei UI", 11), padx=28, pady=18).pack()
            tk.Button(win, text="知道了", command=win.destroy, padx=18, pady=6).pack(pady=(0, 14))
            win.update_idletasks()
            screen_w = root.winfo_screenwidth()
            screen_h = root.winfo_screenheight()
            win_w = win.winfo_reqwidth()
            win_h = win.winfo_reqheight()
            win.geometry(f"+{max(0, (screen_w - win_w) // 2)}+{max(0, (screen_h - win_h) // 2)}")
            win.deiconify()
            win.grab_set()
            win.focus_force()
            root.after(8000, lambda: win.destroy() if win.winfo_exists() else None)
        except Exception:
            pass
        finally:
            try:
                self.ui.root.attributes("-topmost", False)
            except Exception:
                pass

    def _open_settings(self) -> None:
        self.ui.show_settings_dialog()

    def _import_training_zip(self, zip_path: str, progress=None) -> str:
        # 知识库导入统一走大模型识别（任意格式文档 → 提取问答对，叠加进企业年报总库）；
        # 未配置 API Key 或识别失败会抛错，由 UI 弹窗提示。
        stats = import_chat_zip_to_memory_with_llm(
            Path(zip_path),
            self.memory,
            self.settings,
            max_examples=self.memory.max_examples,
            progress=progress,
        )
        self.memory.load()
        return MSG["training_imported"].format(**stats)

    def _analyze_sop_training(self, source_path: str) -> str:
        report = analyze_training_source_to_memory(
            Path(source_path),
            self.memory,
            max_examples=self.memory.max_examples,
            settings=self.settings,
        )
        self.memory.load()
        self.engine = ReplyEngine(self.settings, self.memory)
        library = sop_library_from_report(report, source="uploaded_training_analysis")
        save_sop_library(library, self.memory.sop_library_path)
        self._write_sop_html_report(library)
        return format_sop_document(report)

    def _analyze_current_sop_library(self) -> str:
        self.memory.load()
        if not self.memory.examples:
            return "聊小智 SOP 分析报告\n\n当前话术库为空。请先导入聊天记录或话术文件。"
        library = build_sop_library_from_memory(self.memory)
        save_sop_library(library, self.memory.sop_library_path)
        self._write_sop_html_report(library)
        return format_sop_library_document(library)

    def _get_sop_document(self) -> str:
        self.memory.load()
        library = load_sop_library(self.memory.sop_library_path)
        if library and (
            not library.get("flow_nodes") or int(library.get("flow_version", 0) or 0) < FLOW_LIBRARY_VERSION
        ):
            if self.memory.examples:
                library = build_sop_library_from_memory(self.memory)
            else:
                library = ensure_sop_library_flow(library)
            save_sop_library(library, self.memory.sop_library_path)
        if library:
            return format_sop_library_document(library)
        if self.memory.examples:
            return format_sop_library_document(build_sop_library_from_memory(self.memory))
        return sop_document_from_memory(self.memory)

    def _get_sop_html_report(self) -> str:
        self.memory.load()
        library = load_sop_library(self.memory.sop_library_path)
        if (
            not library
            or not library.get("flow_nodes")
            or int(library.get("flow_version", 0) or 0) < FLOW_LIBRARY_VERSION
        ):
            if self.memory.examples:
                library = build_sop_library_from_memory(self.memory)
            elif library:
                library = ensure_sop_library_flow(library)
            if library:
                save_sop_library(library, self.memory.sop_library_path)
        if not library:
            return ""
        return str(self._write_sop_html_report(library))

    def _write_sop_html_report(self, library: dict[str, object]) -> Path:
        target = self.memory.sop_library_path.with_name("sop_flow_report.html")
        return write_sop_library_html(library, target)

    def _get_phrasebook_libraries(self) -> list[dict[str, object]]:
        self.memory.load()
        return self.memory.library_summaries()

    def _get_phrasebook_examples(
        self,
        library_key: str = "",
        query: str = "",
        source_file: str | None = None,
    ) -> list[dict[str, object]]:
        self.memory.load()
        if query.strip():
            if library_key == ENTERPRISE_ANNUAL_REPORT_SOURCE:
                rows = self.memory.enterprise_scored_examples(query, limit=self.memory.max_examples)
            else:
                rows = self.memory.scored_examples(
                    query,
                    library_key=library_key,
                    limit=self.memory.max_examples,
                )
            if source_file is not None:
                rows = [
                    row
                    for row in rows
                    if str(row.get("source_file", "")).strip() == source_file
                ]
            return rows
        rows: list[dict[str, object]] = []
        for item in self.memory.examples_for_library(library_key, limit=self.memory.max_examples):
            if source_file is not None and str(item.get("source_file", "")).strip() != source_file:
                continue
            rows.append(
                {
                    **item,
                    "score": 0,
                    "reasons": ["recent"],
                    "library_key": item.get("source", ""),
                    "library_name": item.get("source", ""),
                }
            )
        return rows

    def _save_phrasebook_example(self, conversation_hash: str, cue: str, reply: str) -> str:
        self.memory.load()
        if not self.memory.update_example(conversation_hash, cue=cue, reply=reply):
            raise ValueError("未找到要保存的话术，可能已被更新，请刷新后再试。")
        self.memory.load()
        self.engine = ReplyEngine(self.settings, self.memory)
        return "话术已保存，已重建本地向量库。"

    def _delete_phrasebook_examples(self, conversation_hashes: list[str]) -> str:
        """按 conversation_hash 批量删除话术库条目（单选/多选删除）。"""
        self.memory.load()
        target = set(conversation_hashes)
        before = len(self.memory.examples)
        self.memory.examples = [
            item for item in self.memory.examples if str(item.get("conversation_hash", "")) not in target
        ]
        removed = before - len(self.memory.examples)
        if removed == 0:
            raise ValueError("未找到要删除的话术，可能已被更新，请刷新后再试。")
        self.memory.save()
        self.memory.load()
        self.engine = ReplyEngine(self.settings, self.memory)
        return f"已删除 {removed} 条话术，已重建本地向量库。"

    def _recognize_current(self) -> None:
        wechat = self._target_wechat_window()
        if not wechat:
            self.ui.set_status(MSG["no_window"])
            return
        self.current_window = wechat
        threading.Thread(target=lambda: self._process_window(wechat, force=True), daemon=True).start()

    def _automation_mode_changed(self, mode: str) -> None:
        auto_recognize, managed_auto_reply = automation_flags(mode)
        self.settings.auto_recognize = auto_recognize
        self.settings.managed_auto_reply = managed_auto_reply
        self.current_hash = ""
        self.last_capture = 0.0
        if managed_auto_reply:
            self.managed_waiting_for_own_echo = False
            self.managed_last_handled_turn_messages = []
            self.managed_last_handled_turn_key = ""
            self.managed_last_sent_at = 0.0
            self.managed_last_handled_partner = ""
        self.settings.save()
        self.ui.set_status(
            {
                HALF_AUTO: MSG["half_auto_on"],
                FULL_AUTO: MSG["full_auto_on"],
                OFF: MSG["automation_off"],
            }[mode]
        )
        if not auto_recognize:
            return
        wechat = self._target_wechat_window()
        if not wechat:
            self.ui.set_status(MSG["no_window"])
            return
        self.current_window = wechat
        threading.Thread(
            target=lambda: self._process_window(wechat, force=True),
            daemon=True,
        ).start()

    def _training_changed(self, enabled: bool) -> None:
        self.settings.training_mode = enabled
        self.settings.save()
        self.current_hash = ""
        self.last_capture = 0.0
        self.ui.set_status(MSG["training_on"] if enabled else MSG["training_off"])
        if enabled:
            wechat = self._target_wechat_window()
            if wechat:
                self.current_window = wechat
                threading.Thread(target=lambda: self._process_window(wechat, force=False), daemon=True).start()

    def _source_mode_changed(self, mode: str) -> None:
        self.settings.reply_source_mode = mode if mode in {"model", "memory", "model_memory"} else "model_memory"
        self.settings.save()
        names = {"model": "大模型", "memory": "话术库", "model_memory": "大模型+话术库"}
        self.ui.set_status(f"回复来源已切换为：{names.get(self.settings.reply_source_mode, '大模型+话术库')}")

    def _target_wechat_window(self) -> WeChatWindow | None:
        return _choose_target_wechat_window(
            self.detector.foreground_chat(),
            self.detector.any_chat_window(),
            self.current_window,
        )

    def _maybe_managed_send(self, context: str, replies: list[str]) -> None:
        if not self.settings.managed_auto_reply or not replies or not self.current_window:
            _diag("[send-skip] 前置条件不满足", "managed=", self.settings.managed_auto_reply, "replies=", len(replies), "window=", self.current_window is not None)
            return
        send_window = self.current_window
        if _managed_api_failure_blocks_send(self.settings, self.engine):
            _diag("[send-block] api_failure_blocks_send", "last_error=", self.engine.last_error, "last_source=", self.engine.last_source)
            self.queue.put(("status", MSG["api_failed_stop_send"]))
            return
        unreplied_messages = _current_unreplied_turn(context)
        latest_role, latest_text = latest_meaningful_message(context)
        if latest_role != "对方" and not unreplied_messages:
            _diag("[send-block] 最后消息非对方且无未回复", "latest_role=", latest_role, "unreplied=", unreplied_messages)
            self.queue.put(("status", MSG["wait_other"]))
            return
        if not unreplied_messages:
            _diag("[send-block] 无未回复消息", "context=", context[:120])
            self.queue.put(("status", MSG["wait_other"]))
            return
        signature = _managed_turn_cycle_signature(send_window.title, context, unreplied_messages)
        same_handled_cycle = (
            bool(self.managed_last_handled_turn_key)
            and signature == self.managed_last_handled_turn_key
            and _turns_are_same_or_similar(self.managed_last_handled_turn_messages, unreplied_messages)
        )
        if same_handled_cycle:
            _diag("[send-block] 同一轮已处理过", "signature=", signature[:24])
            self.queue.put(("status", MSG["wait_other"]))
            return
        if (
            signature == self.last_auto_reply_signature
            or signature == self.pending_auto_reply_signature
            or signature in self.handled_auto_reply_signatures
            or self.managed_send_inflight
        ):
            _diag("[send-block] 签名已处理/发送中", "sig=", signature[:24], "inflight=", self.managed_send_inflight)
            self.queue.put(("status", MSG["wait_other"]))
            return
        selection_context = _turn_messages_to_context(unreplied_messages)
        # 企业库命中后的一致性判断已由 generate() 内完成（一致才返回答案，不一致返回未命中），
        # 这里直接选择候选发送。
        text = _select_managed_reply(
            selection_context,
            replies,
            prefer_model_order="API" in self.engine.last_source,
        )
        if not text:
            _diag("[send-skip] 未选中候选文本", "replies=", replies)
            return
        if len(unreplied_messages) == 1:
            text = _single_wechat_message(text)
        self.pending_auto_reply_signature = signature
        self.managed_send_inflight = True
        self.managed_waiting_for_own_echo = False
        _diag("[send-go] 启动发送线程", "sig=", signature[:24], "text=", text[:40])
        self.queue.put(("status", MSG["auto_sending"].format(text=text)))

        def delayed_send() -> None:
            try:
                time.sleep(self.settings.auto_reply_delay_seconds)
                live_window = _managed_live_send_window(self.detector, send_window)
                latest_context = self._managed_preflight_context(live_window)
                _diag("[preflight] 重定位窗口", "live_window=", live_window is not None, "preflight_ctx_len=", len(latest_context or ""))
                latest_context = _managed_preflight_context_or_cached(
                    send_window,
                    live_window,
                    context,
                    latest_context,
                    getattr(self, "current_context", ""),
                )
                allowed, reason = _managed_send_preflight_allows(send_window, live_window, context, latest_context)
                if not allowed:
                    self.pending_auto_reply_signature = ""
                    _diag("[send-block] preflight 未通过:", reason)
                    self.queue.put(("status", MSG["managed_preflight_blocked"].format(reason=reason)))
                    return
                ok, message = self.sender.send(live_window, text)
                _diag("[send-result]", "ok=", ok, "message=", message)
                if ok:
                    self.last_auto_reply_signature = signature
                    self.handled_auto_reply_signatures.add(signature)
                    self.managed_last_handled_turn_messages = list(unreplied_messages)
                    self.managed_last_handled_turn_key = signature
                    self.managed_last_handled_partner = send_window.title
                    self.managed_last_sent_at = time.time()
                    self.last_managed_sent_text = text
                    self.managed_waiting_for_own_echo = True
                else:
                    self.pending_auto_reply_signature = ""
                self.queue.put(("status", message))
            finally:
                self.managed_send_inflight = False
                if self.pending_auto_reply_signature == signature:
                    self.pending_auto_reply_signature = ""

        threading.Thread(target=delayed_send, daemon=True).start()

    def _managed_preflight_context(self, window: WeChatWindow | None) -> str:
        if window is None:
            return ""
        try:
            # 允许屏幕截图兜底：窗口直接捕获失败（被遮挡/硬件加速黑图等）时，
            # 若禁用兜底会导致“发送前无法复核”而永不发送、候选反复刷新。
            # 后续仍有 turn 相似度校验兜底，不会因兜底截图而误发到其他内容。
            image = self.ocr.capture(window, allow_screen_fallback=True)
            if image is None:
                return ""
            context = self.ocr.read_image(image)
            if context.strip():
                visible = self.visible_analyzer.analyze(context, image)
                return build_augmented_context(context, visible)
            visible = self.visible_analyzer.analyze("", image)
            if visible.summary:
                return build_augmented_context("[对方] [图片]", visible)
            return context
        except Exception:
            return ""

    def _try_transcribe_latest_voice(self, wechat: WeChatWindow, image, context: str) -> tuple[str, object | None]:
        if pyautogui is None:
            return context, image
        try:
            bubble = self.ocr.latest_voice_bubble(image)
        except Exception:
            return context, image
        if bubble is None or bubble.role != "对方":
            return context, image
        self._progress(MSG["step_voice"])
        origin_x, origin_y = getattr(self.ocr, "last_capture_origin", (0, 0))
        click_x = int(origin_x + (bubble.left + bubble.right) / 2)
        click_y = int(origin_y + bubble.y)
        hidden = threading.Event()
        self.queue.put(("hide_for_capture", hidden))
        hidden.wait(0.6)
        try:
            try:
                self.sender._activate(wechat)
                time.sleep(0.08)
            except Exception:
                pass
            pyautogui.click(click_x, click_y)
            time.sleep(1.35)
            new_image = _capture_chat_image_without_overlay(self.ocr, self.queue, self.sender, wechat, wait_seconds=0.8)
            if new_image is None:
                return context, image
            new_context = self.ocr.read_image(new_image)
            if new_context.strip() and new_context != context:
                return new_context, new_image
            return context, new_image
        except Exception:
            return context, image
        finally:
            self.queue.put(("show_after_capture", None))

    def _regenerate(self) -> None:
        if not self.current_context:
            self.ui.set_status(MSG["no_context"])
            return
        self.ui.set_status(MSG["regenerating"])
        self.ui.clear_replies()

        def work() -> None:
            partner = self.current_window.title if self.current_window else ""
            replies = self.engine.generate(self.current_context, partner, target_turn=_current_unreplied_turn(self.current_context))
            self.queue.put(("replies", replies))
            self.queue.put(("reply_source", self._reply_source_label()))
            self.queue.put(("status", self._generated_status(len(replies))))

        threading.Thread(target=work, daemon=True).start()

    def _send(self, text: str, source: str = "manual_edit") -> None:
        if not self.current_window:
            self.ui.set_status(MSG["no_send_window"])
            return
        window = self.current_window
        partner = self.current_window.title
        self.ui.root.withdraw()

        def work() -> None:
            ok, message = self.sender.send(window, text)
            if ok and _should_learn_sent_reply(source):
                self.memory.learn_from_sent_reply(partner, self.current_context, text, source=source)
            self.queue.put(("status", message))

        threading.Thread(target=work, daemon=True).start()
        self.ui.root.after(700, self.ui.root.deiconify)

    def _exit(self) -> None:
        self.running = False
        self.ui.close()

    def _progress(self, text: str) -> None:
        self.progress_steps.append(text)
        self.queue.put(("progress", text))


def main() -> None:
    if "--self-check" in sys.argv:
        from self_check import run_self_check

        run_self_check()
        return
    if "--api-check" in sys.argv:
        from self_check import run_api_check

        run_api_check()
        return
    WeChatStyleReplyAssistant().start()


def _current_unreplied_turn(context: str) -> list[str]:
    messages = unreplied_other_messages(context)
    visible_summary = _latest_visible_summary(context)
    if messages:
        return _attach_visible_summary_to_turn(messages, visible_summary)
    if visible_summary and _latest_other_line_is_image_placeholder(context):
        return [f"[图片] {visible_summary}".strip()]
    return messages


def _turn_messages_to_context(messages: list[str]) -> str:
    return "\n".join(f"[对方] {message}" for message in messages if message.strip())


def _attach_visible_summary_to_turn(messages: list[str], visible_summary: str) -> list[str]:
    if not visible_summary:
        return messages
    result = list(messages)
    result[-1] = f"{result[-1]} {visible_summary}".strip()
    return result


def _strip_visible_summary(text: str) -> str:
    """去掉消息尾部附加的“[可见内容理解]…”片段，返回纯消息文本。"""
    cleaned = (text or "").strip()
    if "[可见内容理解]" in cleaned:
        cleaned = cleaned.split("[可见内容理解]", 1)[0].strip()
    return cleaned


def _latest_visible_summary(context: str) -> str:
    for line in reversed((context or "").splitlines()):
        stripped = line.strip()
        if not stripped.startswith("[可见内容理解]"):
            continue
        content = stripped.split("]", 1)[-1].strip()
        parts = [part.strip() for part in content.split("；") if part.strip()]
        if parts:
            return parts[-1]
        return content
    return ""


def _latest_other_line_is_image_placeholder(context: str) -> bool:
    image_word = chr(0x56FE) + chr(0x7247)
    for line in reversed((context or "").splitlines()):
        stripped = line.strip()
        if not stripped or stripped.startswith("[可见内容理解]"):
            continue
        if not (stripped.startswith("[对方]") or stripped.startswith("[瀵规柟]")):
            continue
        text = stripped.split("]", 1)[-1].strip() if "]" in stripped else stripped
        compact = re.sub(r"\s+", "", text)
        return compact in {f"[{image_word}]", image_word, "[??]", "??"}
    return False


def _should_clear_replies_before_capture(force: bool, training_only: bool) -> bool:
    return force and not training_only


def _should_clear_replies_for_skip(force: bool) -> bool:
    return force


def _visible_skip_should_block_reply(context: str, visible) -> bool:
    if not getattr(visible, "should_skip_reply", False):
        return False
    if context.strip() and _current_unreplied_turn(context) and not _latest_other_line_is_image_placeholder(context):
        return False
    # 上下文里存在“对方提问但未被回答”的问句时，即使最新是图片/表情包也不跳过，
    # 让生成流程继续，优先回答前面遗留的问题。
    if context.strip() and _context_has_unanswered_question(context):
        return False
    return True


def _context_has_unanswered_question(context: str) -> bool:
    """检测上下文里是否有对方提出的、尚未被实质回答的问题。
    排除短反馈反问（“是吗？”“真的？”“这样啊”）——它们是即时反馈，不是新问题。"""
    from reply_engine import _is_feedback_question, _looks_like_question

    messages = _context_role_messages(context)
    # 从后往前找：最近一条对方消息若是问句，且其后没有“我”给出实质回答，视为未回答
    for index in range(len(messages) - 1, -1, -1):
        role, text = messages[index]
        if role != "other":
            continue
        if _is_feedback_question(text):
            continue
        if not _looks_like_question(text):
            continue
        # 该问句之后是否有“我”的实质回复
        for later_role, later_text in messages[index + 1 :]:
            if later_role == "own" and _reply_is_substantive(later_text):
                return False
        return True
    return False


def _reply_is_substantive(text: str) -> bool:
    from reply_engine import _is_pending_reply

    cleaned = (text or "").strip()
    if not cleaned:
        return False
    if _is_pending_reply(cleaned):
        return False
    return len(cleaned) >= 6 or cleaned.rstrip().endswith(("。", "！", "？", "～", "~", "!", "?"))


def _should_skip_unchanged_context(
    force: bool,
    managed: bool,
    current_context: str,
    new_context: str,
    current_hash: str,
    digest: str,
    handled_messages: list[str] | None = None,
) -> bool:
    if force:
        return False
    unchanged = bool(current_context and _contexts_are_same_or_similar(current_context, new_context)) or (
        bool(digest) and digest == current_hash
    )
    if not unchanged:
        return False
    if managed:
        # 上下文未变化，但存在尚未实质回答的用户问题 → 不跳过，继续生成
        if _context_has_unanswered_question(new_context):
            return False
        if _managed_context_allows_generation(new_context, handled_messages):
            return False
    return True


def _capture_chat_image_without_overlay(ocr, ui_queue, sender, target, wait_seconds: float = 1.0):
    try:
        direct = ocr.capture(target, allow_screen_fallback=False)
    except TypeError:
        direct = None
    if direct is not None:
        return direct

    hidden = threading.Event()
    ui_queue.put(("hide_for_capture", hidden))
    hidden.wait(wait_seconds)
    try:
        try:
            sender._activate(target)
            time.sleep(0.08)
        except Exception:
            pass
        return ocr.capture(target)
    finally:
        ui_queue.put(("show_after_capture", None))


def _should_learn_sent_reply(source: str) -> bool:
    return source in {"selected_candidate", "manual_edit"}


def _should_learn_observed_chat(training_only: bool, managed: bool) -> bool:
    return bool(training_only) and not managed


def _choose_target_wechat_window(
    foreground: WeChatWindow | None,
    discovered: WeChatWindow | None,
    current: WeChatWindow | None,
) -> WeChatWindow | None:
    return foreground or discovered or current


def _select_managed_reply(context: str, replies: list[str], prefer_model_order: bool = False) -> str:
    candidates = _filter_sendable_replies(context, replies)
    if not candidates:
        return NO_DIRECT_ANSWER_REPLY if _is_question(context) else ""
    if prefer_model_order:
        return candidates[0]
    return select_best_reply_for_context(context, candidates)


def _filter_sendable_replies(context: str, replies: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    context_key = _compact_for_echo_match(context)
    for reply in replies:
        text = str(reply or "").strip()
        key = _compact_for_echo_match(text)
        if not text or not key or key in seen:
            continue
        if _is_pending_reply(text):
            continue
        if key in context_key or context_key in key:
            continue
        result.append(text)
        seen.add(key)
    return result


def _managed_api_failure_blocks_send(settings: AppSettings, engine: ReplyEngine) -> bool:
    return bool(settings.api_key and engine.last_error and "API" not in engine.last_source)


def _generation_result_still_current(
    generated_context: str,
    generated_window: WeChatWindow | None,
    current_context: str,
    current_window: WeChatWindow | None,
) -> bool:
    if generated_window is None or current_window is None:
        return False
    if not _same_chat_session(generated_window, current_window):
        return False
    return _contexts_are_same_or_similar(generated_context, current_context)


def _managed_generation_result_still_current(
    target_turn: list[str],
    generated_window: WeChatWindow | None,
    current_context: str,
    current_window: WeChatWindow | None,
) -> bool:
    if generated_window is None or current_window is None:
        return False
    if not _same_chat_session(generated_window, current_window):
        return False
    current_turn = _current_unreplied_turn(current_context)
    return _turn_matches_target_allowing_noise(target_turn, current_turn)


def _turn_matches_target_allowing_noise(target_turn: list[str], current_turn: list[str]) -> bool:
    target = [_normalize_turn_message(message) for message in target_turn if _normalize_turn_message(message)]
    current = [_normalize_turn_message(message) for message in current_turn if _normalize_turn_message(message)]
    if not target or not current:
        return False
    matched = [False] * len(target)
    extra_substantive = 0
    for message in current:
        matched_existing = False
        for index, target_message in enumerate(target):
            if not matched[index] and _messages_are_similar(target_message, message):
                matched[index] = True
                matched_existing = True
                break
        if matched_existing:
            continue
        if not _is_managed_ocr_noise_message(message):
            extra_substantive += 1
    return all(matched) and extra_substantive == 0


def _is_managed_ocr_noise_message(normalized: str) -> bool:
    if not normalized:
        return True
    image_word = chr(0x56FE) + chr(0x7247)
    sticker_word = chr(0x8868) + chr(0x60C5)
    if normalized in {image_word, sticker_word}:
        return True
    if re.fullmatch(r"\d{1,2}[:：]\d{2}", normalized):
        return True
    if re.fullmatch(r"\d{1,2}", normalized):
        return True
    return False


def _managed_send_preflight_allows(
    expected_window: WeChatWindow | None,
    live_window: WeChatWindow | None,
    generated_context: str,
    latest_context: str,
) -> tuple[bool, str]:
    if expected_window is None or live_window is None:
        return False, "目标聊天窗口已不可用"
    if not _same_chat_session(expected_window, live_window):
        return False, "聊天对象已变化，取消发送"
    if not latest_context.strip():
        return False, "发送前无法复核当前聊天内容"

    generated_turn = _current_unreplied_turn(generated_context)
    latest_turn = _current_unreplied_turn(latest_context)
    latest_role, _latest_text = latest_meaningful_message(latest_context)
    if latest_role != "对方" and not latest_turn:
        return False, "最新消息已不是对方未回复消息"
    if not generated_turn or not latest_turn:
        return False, "未检测到需要回复的对方消息"
    if not _turns_are_same_or_similar(generated_turn, latest_turn):
        return False, "对方最新问题已变化，需要重新生成"
    return True, ""


def _managed_preflight_context_or_cached(
    expected_window: WeChatWindow | None,
    live_window: WeChatWindow | None,
    generated_context: str,
    latest_context: str,
    cached_context: str,
) -> str:
    if not cached_context.strip() or not _same_chat_session(expected_window, live_window):
        return latest_context
    generated_turn = _current_unreplied_turn(generated_context)
    cached_turn = _current_unreplied_turn(cached_context)
    if latest_context.strip():
        latest_turn = _current_unreplied_turn(latest_context)
        latest_role, _latest_text = latest_meaningful_message(latest_context)
        if (
            generated_turn
            and cached_turn
            and _turns_are_same_or_similar(generated_turn, cached_turn)
            and _context_role_messages_are_prefix(latest_context, cached_context)
            and (not latest_turn or latest_role == "我")
        ):
            return cached_context
        return latest_context
    if generated_turn and cached_turn and _turns_are_same_or_similar(generated_turn, cached_turn):
        return cached_context
    return latest_context


def _context_role_messages_are_prefix(short_context: str, full_context: str) -> bool:
    short_messages = [(role, text) for role, text in _context_role_messages(short_context) if role in {"own", "other"}]
    full_messages = [(role, text) for role, text in _context_role_messages(full_context) if role in {"own", "other"}]
    if not short_messages or len(short_messages) > len(full_messages):
        return False
    for (short_role, short_text), (full_role, full_text) in zip(short_messages, full_messages):
        if short_role != full_role:
            return False
        if not _messages_are_similar(_normalize_turn_message(short_text), _normalize_turn_message(full_text)):
            return False
    return True


def _same_chat_session(left: WeChatWindow | None, right: WeChatWindow | None) -> bool:
    if left is None or right is None:
        return False
    left_platform = str(getattr(getattr(left, "platform", None), "key", "") or "")
    right_platform = str(getattr(getattr(right, "platform", None), "key", "") or "")
    if left_platform and right_platform and left_platform != right_platform:
        return False
    left_title = str(getattr(left, "title", "") or "").strip()
    right_title = str(getattr(right, "title", "") or "").strip()
    if left_title and right_title:
        return left_title == right_title
    left_hwnd = int(getattr(left, "hwnd", 0) or 0)
    right_hwnd = int(getattr(right, "hwnd", 0) or 0)
    return bool(left_hwnd and left_hwnd == right_hwnd)


def _current_foreground_chat(detector: object) -> WeChatWindow | None:
    foreground = getattr(detector, "foreground_chat", None)
    if callable(foreground):
        try:
            return foreground()
        except Exception:
            return None
    return None


def _managed_live_send_window(detector: object, expected_window: WeChatWindow | None) -> WeChatWindow | None:
    foreground = _current_foreground_chat(detector)
    if foreground is not None and _same_chat_session(expected_window, foreground):
        return foreground

    discovered = None
    any_chat = getattr(detector, "any_chat_window", None)
    if callable(any_chat):
        try:
            discovered = any_chat()
        except Exception:
            discovered = None
    if discovered is not None and _same_chat_session(expected_window, discovered):
        return discovered

    window_from_hwnd = getattr(detector, "window_from_hwnd", None)
    expected_hwnd = int(getattr(expected_window, "hwnd", 0) or 0)
    if callable(window_from_hwnd) and expected_hwnd:
        try:
            refreshed = window_from_hwnd(expected_hwnd)
        except Exception:
            refreshed = None
        if refreshed is not None:
            return refreshed

    return expected_window


def _managed_context_allows_generation(context: str, handled_messages: list[str] | None = None) -> bool:
    unreplied_messages = _current_unreplied_turn(context)
    latest_role, _latest_text = latest_meaningful_message(context)
    if latest_role != "对方" and not unreplied_messages:
        return False
    if not unreplied_messages:
        return False
    # 未回复消息全是短反馈反问（“是吗？”“真的？”“这样啊”）→ 不是新问题，不触发生成
    from reply_engine import _is_feedback_question

    if unreplied_messages and all(
        _is_feedback_question(_strip_visible_summary(message)) for message in unreplied_messages
    ):
        return False
    handled_latest = _latest_turn_message(handled_messages)
    if handled_latest and _turns_are_same_or_similar(handled_latest, unreplied_messages):
        return _current_turn_has_own_boundary(context)
    return True


def _should_auto_generate_for_context(
    context: str,
    force: bool,
    managed: bool,
    handled_messages: list[str] | None = None,
) -> bool:
    if managed:
        return _managed_context_allows_generation(context, handled_messages)
    if force:
        return True
    latest_role, _latest_text = latest_meaningful_message(context)
    return latest_role == "对方"


def _managed_reply_gate_allows_generation(
    context: str,
    waiting_for_own_echo: bool,
    last_sent_text: str,
    handled_messages: list[str] | None = None,
    allow_new_without_own_echo: bool = False,
) -> tuple[bool, bool]:
    if not waiting_for_own_echo:
        return True, False
    current_turn = _current_unreplied_turn(context)
    latest_role, _latest_text = latest_meaningful_message(context)
    if latest_role == "我":
        return False, False
    handled_latest = _latest_turn_message(handled_messages)
    if allow_new_without_own_echo and handled_latest and _turn_has_real_new_message(handled_latest, current_turn):
        return True, False
    if not _context_has_own_echo(context, last_sent_text):
        return False, True
    if handled_latest and _turns_are_same_or_similar(handled_latest, current_turn):
        if _current_turn_has_own_boundary(context):
            return True, False
        return False, True
    if handled_messages and current_turn:
        return True, False
    return _managed_context_allows_generation(context, handled_messages), False


def _turn_key(messages: list[str]) -> str:
    return "\n".join(_normalize_turn_message(message) for message in messages if _normalize_turn_message(message))


def _latest_turn_message(messages: list[str] | None) -> list[str]:
    """兼容旧运行状态里的多消息列表；新机制只比较最后一条消息。"""
    latest = [str(message or "") for message in (messages or []) if _normalize_turn_message(str(message or ""))]
    return latest[-1:] if latest else []


def _normalize_turn_message(text: str) -> str:
    cleaned = text.strip().lower()
    cleaned = cleaned.replace("哪儿", "哪里").replace("那儿", "那里")
    cleaned = re.sub(r"[\s\W_]+", "", cleaned)
    return cleaned


def _turns_are_same_or_similar(old_messages: list[str] | None, new_messages: list[str] | None) -> bool:
    old_raw = [str(message or "") for message in (old_messages or []) if _normalize_turn_message(str(message or ""))]
    new_raw = [str(message or "") for message in (new_messages or []) if _normalize_turn_message(str(message or ""))]
    old = [_normalize_turn_message(message) for message in old_raw]
    new = [_normalize_turn_message(message) for message in new_raw]
    if not old or not new or len(old) != len(new):
        return False
    for old_text, new_text, old_norm, new_norm in zip(old_raw, new_raw, old, new):
        if _messages_are_similar(old_norm, new_norm):
            continue
        if _image_turns_are_same_or_related(old_text, new_text):
            continue
        return False
    return True


def _turn_has_real_new_message(old_messages: list[str] | None, new_messages: list[str] | None) -> bool:
    old = _latest_turn_message(old_messages)
    new = _latest_turn_message(new_messages)
    if not old or not new:
        return False
    if _turns_are_same_or_similar(old, new):
        return False
    return len(_normalize_turn_message(new[0])) >= 2


def _messages_are_similar(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right or left in right or right in left:
        return True
    return SequenceMatcher(None, left, right).ratio() >= 0.78


def _image_turns_are_same_or_related(left: str, right: str) -> bool:
    if not (_looks_like_image_turn(left) and _looks_like_image_turn(right)):
        return False
    left_tokens = _visual_bigrams(left)
    right_tokens = _visual_bigrams(right)
    if not left_tokens or not right_tokens:
        return False
    return len(left_tokens & right_tokens) >= 3


def _looks_like_image_turn(text: str) -> bool:
    image_word = chr(0x56FE) + chr(0x7247)
    return image_word in str(text or "") or "image" in str(text or "").lower()


def _visual_bigrams(text: str) -> set[str]:
    normalized = _normalize_turn_message(text)
    image_word = chr(0x56FE) + chr(0x7247)
    generic = {
        image_word,
        "片一",
        "一张",
        "张图",
        "截图",
        "照片",
        "展示",
        "内容",
        "上面",
        "一个",
    }
    grams = {normalized[index : index + 2] for index in range(max(len(normalized) - 1, 0))}
    return {gram for gram in grams if gram and gram not in generic}


def _contexts_are_same_or_similar(old_context: str, new_context: str) -> bool:
    old_messages = _context_role_messages(old_context)
    new_messages = _context_role_messages(new_context)
    if not old_messages or not new_messages or len(old_messages) != len(new_messages):
        return False
    for (old_role, old_text), (new_role, new_text) in zip(old_messages, new_messages):
        if old_role != new_role:
            return False
        if not _messages_are_similar(_normalize_turn_message(old_text), _normalize_turn_message(new_text)):
            return False
    return True


def _context_role_messages(context: str) -> list[tuple[str, str]]:
    messages: list[tuple[str, str]] = []
    for line in context.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("[可见内容理解]"):
            summary = _latest_visible_summary(stripped)
            if summary:
                messages.append(("visible", summary))
            continue
        match = re.match(r"^\[([^\]\s]+)\]?\s+(.+)$", stripped)
        if not match:
            continue
        role = _normalize_context_role(match.group(1))
        text = match.group(2).strip()
        if role and text:
            messages.append((role, text))
    return messages


def _normalize_context_role(role: str) -> str:
    cleaned = role.strip()
    if cleaned in {"对方", "瀵规柟"} or "瀵规柟" in cleaned:
        return "other"
    if cleaned in {"我", "鎴"} or cleaned.startswith("鎴") or cleaned.startswith("閹"):
        return "own"
    return ""


def _context_has_own_echo(context: str, last_sent_text: str) -> bool:
    expected = _compact_for_echo_match(last_sent_text)
    for line in context.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("[鎴") or stripped.startswith("[我")):
            continue
        actual = _compact_for_echo_match(stripped.split("]", 1)[-1] if "]" in stripped else stripped)
        if not expected:
            return bool(actual)
        if expected in actual or actual in expected:
            return True
        if len(expected) >= 8 and expected[:8] in actual:
            return True
        if len(actual) >= 8 and actual[:8] in expected:
            return True
        if SequenceMatcher(None, expected, actual).ratio() >= 0.46:
            return True
    return False


def _context_has_other_after_last_own(context: str) -> bool:
    saw_own = False
    saw_other_after_own = False
    for line in context.splitlines():
        stripped = line.strip()
        if stripped.startswith("[鎴") or stripped.startswith("[我"):
            saw_own = True
            saw_other_after_own = False
        elif stripped.startswith("[瀵规柟]") or stripped.startswith("[对方]"):
            if saw_own:
                saw_other_after_own = True
    return saw_other_after_own


def _latest_own_message_before_current_turn(context: str) -> str:
    messages = [(role, text) for role, text in _context_role_messages(context) if role in {"own", "other"}]
    index = len(messages) - 1
    while index >= 0 and messages[index][0] == "other":
        index -= 1
    if index >= 0 and messages[index][0] == "own":
        return messages[index][1]
    return ""


def _current_turn_has_own_boundary(context: str) -> bool:
    return bool(_latest_own_message_before_current_turn(context))


def _compact_for_echo_match(text: str) -> str:
    return "".join(ch for ch in text.strip() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def _window_key(wechat: WeChatWindow) -> str:
    return f"{wechat.hwnd}:{wechat.title}"


def _resolve_dock_follow_window(detector: object, last_hwnd: int) -> WeChatWindow | None:
    """Resolve a fresh window rectangle without requiring WeCom to keep focus."""
    try:
        foreground = detector.foreground_chat()
    except Exception:
        foreground = None
    if foreground is not None:
        return foreground
    if last_hwnd:
        try:
            refreshed = detector.window_from_hwnd(last_hwnd)
        except Exception:
            refreshed = None
        if refreshed is not None:
            return refreshed
    try:
        return detector.any_chat_window()
    except Exception:
        return None


def _dock_window_rect_key(window: object) -> tuple[int, int, int, int, int]:
    rect = window.rect
    return (
        int(window.hwnd),
        int(rect.left),
        int(rect.top),
        int(rect.right),
        int(rect.bottom),
    )


def _snap_geometry_if_changed(root, geometry: str, previous: str) -> str:
    if not geometry:
        return previous
    if _geometry_close_enough(geometry, previous):
        return previous or geometry
    root.geometry(geometry)
    return geometry


def _geometry_close_enough(current: str, previous: str) -> bool:
    if not previous:
        return False
    current_parts = _parse_geometry(current)
    previous_parts = _parse_geometry(previous)
    if not current_parts or not previous_parts:
        return current == previous
    cw, ch, cx, cy = current_parts
    pw, ph, px, py = previous_parts
    return cw == pw and ch == ph and abs(cx - px) <= 2 and abs(cy - py) <= 2


def _parse_geometry(value: str) -> tuple[int, int, int, int] | None:
    match = re.match(r"^(\d+)x(\d+)([+-]\d+)([+-]\d+)$", value or "")
    if not match:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _single_wechat_message(text: str) -> str:
    """把多行回复合并成一条微信消息发送。

    保留完整内容（企业年报知识库等官方答案可达数百字），不再截断到第一句；
    只去掉换行以避免拆成多条消息刷屏。单条消息上限按 4000 字兜底。
    """
    cleaned = " ".join(part.strip() for part in (text or "").splitlines() if part.strip())
    return cleaned[:4000].strip()


def _managed_turn_signature(title: str, unreplied_messages: list[str]) -> str:
    turn_text = "\n".join(message.strip() for message in unreplied_messages if message.strip())
    return hashlib.sha1(f"{title}\n{turn_text}".encode("utf-8")).hexdigest()


def _managed_turn_cycle_signature(title: str, context: str, unreplied_messages: list[str]) -> str:
    boundary = _latest_own_message_before_current_turn(context)
    turn_text = "\n".join(message.strip() for message in unreplied_messages if message.strip())
    return hashlib.sha1(f"{title}\n{boundary}\n{turn_text}".encode("utf-8")).hexdigest()


def _build_completion_context(context: str, sent_text: str) -> str:
    """把已发送的未完成回复（如“我查查”）作为“[我]”消息追加到上下文，
    并加指令让模型在此基础上补全实质内容。"""
    context = (context or "").strip()
    own_line = f"[我] {sent_text}".strip()
    if not context:
        base = own_line
    else:
        base = f"{context}\n{own_line}"
    # 追加补全指令：明确要求接着上一条回复给出实质答案
    return f"{base}\n[系统] 你刚才只说了“{sent_text}”，请接着这句话把实际结果/答案补充完整，直接给出内容，不要重复“{sent_text}”。"

if __name__ == "__main__":
    main()
