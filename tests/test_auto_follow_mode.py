from __future__ import annotations

import inspect
from types import SimpleNamespace

import build_release
import float_ui
import main
import pytest
from float_ui import ReplyAssistantUI
from settings import AppSettings
from wechat_window import WindowRect, snap_geometry_for_work_area


class _Detector:
    def __init__(self, *, foreground=None, refreshed=None, discovered=None) -> None:
        self.foreground = foreground
        self.refreshed = refreshed
        self.discovered = discovered
        self.refreshed_hwnds: list[int] = []
        self.any_calls = 0

    def foreground_chat(self):
        return self.foreground

    def window_from_hwnd(self, hwnd: int):
        self.refreshed_hwnds.append(hwnd)
        return self.refreshed

    def any_chat_window(self):
        self.any_calls += 1
        return self.discovered


def test_dock_follow_refreshes_same_wecom_hwnd_after_foreground_is_lost() -> None:
    moved = SimpleNamespace(hwnd=19, title="企业微信", rect=SimpleNamespace(left=520))
    detector = _Detector(refreshed=moved)

    selected = main._resolve_dock_follow_window(detector, 19)

    assert selected is moved
    assert detector.refreshed_hwnds == [19]
    assert detector.any_calls == 0


def test_dock_follow_prefers_new_foreground_wecom_window() -> None:
    foreground = SimpleNamespace(hwnd=23, title="企业微信")
    stale = SimpleNamespace(hwnd=19, title="企业微信")
    detector = _Detector(foreground=foreground, refreshed=stale)

    selected = main._resolve_dock_follow_window(detector, 19)

    assert selected is foreground
    assert detector.refreshed_hwnds == []


def test_dock_follow_once_posts_fresh_window_and_remembers_hwnd() -> None:
    moved = SimpleNamespace(
        hwnd=19,
        title="企业微信",
        rect=SimpleNamespace(left=520, top=80, right=1520, bottom=780),
    )
    app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
    app.detector = _Detector(refreshed=moved)
    app.dock_target_hwnd = 19
    posted: list[tuple[str, object]] = []
    app.queue = SimpleNamespace(put=posted.append)

    app._dock_follow_once()

    assert app.dock_target_hwnd == 19
    assert posted == [("dock_window", moved)]


def test_dock_follow_does_not_flood_queue_until_window_moves() -> None:
    first = SimpleNamespace(
        hwnd=19,
        title="企业微信",
        rect=SimpleNamespace(left=520, top=80, right=1520, bottom=780),
    )
    moved = SimpleNamespace(
        hwnd=19,
        title="企业微信",
        rect=SimpleNamespace(left=620, top=120, right=1620, bottom=820),
    )
    detector = _Detector(refreshed=first)
    app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
    app.detector = detector
    app.dock_target_hwnd = 19
    posted: list[tuple[str, object]] = []
    app.queue = SimpleNamespace(put=posted.append)

    app._dock_follow_once()
    app._dock_follow_once()
    detector.refreshed = moved
    app._dock_follow_once()

    assert posted == [("dock_window", first), ("dock_window", moved)]


def test_snap_geometry_supports_a_monitor_left_of_the_primary_screen() -> None:
    geometry = snap_geometry_for_work_area(
        WindowRect(-1200, 100, -500, 900),
        width=440,
        height=700,
        work_area=(-1920, 0, 0, 1080),
    )

    assert geometry == "440x700-492+100"
    assert main._parse_geometry(geometry) == (440, 700, -492, 100)


def test_dock_follow_refresh_interval_is_fifty_milliseconds() -> None:
    assert main.DOCK_FOLLOW_INTERVAL_SECONDS == 0.05


def test_automation_mode_matches_the_two_legacy_checkbox_states() -> None:
    from automation_mode import (
        FULL_AUTO,
        HALF_AUTO,
        automation_flags,
        automation_mode,
    )

    assert automation_flags(HALF_AUTO) == (True, False)
    assert automation_flags(FULL_AUTO) == (True, True)
    assert automation_mode(True, False) == HALF_AUTO
    assert automation_mode(True, True) == FULL_AUTO


def test_selecting_current_automation_mode_again_turns_it_off() -> None:
    from automation_mode import FULL_AUTO, HALF_AUTO, OFF, next_automation_mode

    assert next_automation_mode(OFF, HALF_AUTO) == HALF_AUTO
    assert next_automation_mode(HALF_AUTO, FULL_AUTO) == FULL_AUTO
    assert next_automation_mode(FULL_AUTO, FULL_AUTO) == OFF


def test_main_ui_replaces_legacy_auto_and_managed_checkboxes_with_one_menu() -> None:
    source = inspect.getsource(ReplyAssistantUI._build)

    assert "automation_button = tk.Menubutton" in source
    assert "auto = tk.Checkbutton" not in source
    assert "managed = tk.Checkbutton" not in source


def test_automation_help_describes_current_functions_without_legacy_wording() -> None:
    tip = float_ui.TEXT["automation_tip"]

    assert "半自动：自动识别并生成候选回复，不会自动发送" in tip
    assert "全自动：自动识别、生成并自动发送回复" in tip
    assert all(word not in tip for word in ("原来", "之前", "勾选"))


def test_default_window_is_wider_and_username_has_hover_help() -> None:
    source = inspect.getsource(ReplyAssistantUI._build)

    assert float_ui.DEFAULT_WINDOW_WIDTH == 500
    assert "Tooltip(account_name_label, TEXT[\"account_name_tip\"])" in source
    assert "为什么" in float_ui.TEXT["account_name_tip"]


def test_automation_menu_exposes_exactly_half_and_full_auto(tmp_path) -> None:
    import tkinter as tk
    from tkinter import scrolledtext

    events: list[str] = []
    sent: list[tuple[str, str]] = []
    callbacks = {
        name: (lambda *_args, **_kwargs: None)
        for name in (
            "settings",
            "recognize",
            "training_changed",
            "source_mode_changed",
            "regenerate",
            "send",
            "import_training_zip",
            "analyze_sop_training",
            "analyze_current_sop_library",
            "exit",
        )
    }
    callbacks["automation_mode_changed"] = events.append
    callbacks["send"] = lambda text, source: sent.append((text, source))
    settings = AppSettings(config_path=tmp_path / "settings.json")
    try:
        ui = ReplyAssistantUI(settings, callbacks)
    except tk.TclError as error:
        pytest.skip(f"Tk unavailable: {error}")
    ui.root.withdraw()
    try:
        buttons = [
            child
            for child in _descendants(ui.root)
            if isinstance(child, tk.Menubutton)
        ]
        automation_button = next(
            button for button in buttons if str(button.cget("textvariable")) == str(ui.automation_mode_label_var)
        )
        menu = automation_button.nametowidget(automation_button.cget("menu"))

        assert automation_button.cget("background") == float_ui.COLORS["green"]
        assert automation_button.cget("foreground") == "white"
        assert automation_button.cget("cursor") == "hand2"
        assert ui.automation_mode_label_var.get().endswith("▾")
        assert [menu.entrycget(index, "label").strip() for index in range(menu.index("end") + 1)] == [
            "半自动",
            "全自动",
        ]
        menu.invoke(0)
        assert (settings.auto_recognize, settings.managed_auto_reply) == (True, False)
        assert events == ["half_auto"]
        menu.invoke(1)
        assert (settings.auto_recognize, settings.managed_auto_reply) == (True, True)
        assert events == ["half_auto", "full_auto"]

        ui.set_replies(["这是第一条候选回复"])
        candidate_buttons = [
            widget
            for widget in _descendants(ui.reply_area)
            if isinstance(widget, tk.Button)
        ]
        assert not hasattr(ui, "editor")
        assert not any(isinstance(widget, scrolledtext.ScrolledText) for widget in _descendants(ui.root))
        assert [button.cget("text") for button in candidate_buttons] == ["发送"]
        candidate_buttons[0].invoke()
        assert sent == [("这是第一条候选回复", "selected_candidate")]
    finally:
        ui.root.destroy()


def test_approved_ui_uses_neutral_palette_and_compact_main_sections() -> None:
    assert float_ui.COLORS == {
        "page": "#f2f2f1",
        "panel": "#fafaf9",
        "input": "#ffffff",
        "text": "#202321",
        "muted": "#666b66",
        "line": "#d5d7d3",
        "green": "#2f6f52",
        "green_dark": "#285f46",
        "green_soft": "#e6eee9",
        "bubble": "#eeeeec",
        "selected": "#dfe9e3",
    }
    assert float_ui.MODE_CONTROL_FONT_SIZE == 9
    assert float_ui.MODE_CONTROL_PAD_Y == 3
    assert float_ui.CONTEXT_CANVAS_HEIGHT == 100
    assert float_ui.REPLY_CANVAS_HEIGHT >= 280


def test_main_ui_keeps_chat_context_and_gives_freed_space_to_candidates() -> None:
    source = inspect.getsource(ReplyAssistantUI._build)

    assert "self.context_canvas" in source
    assert "height=CONTEXT_CANVAS_HEIGHT" in source
    assert "height=REPLY_CANVAS_HEIGHT" in source
    assert "TEXT[\"manual_reply\"]" not in source
    assert "self._send_editor" not in source


def test_usage_help_matches_direct_candidate_send_without_manual_editor() -> None:
    usage = float_ui.TEXT["usage_body"]

    assert "候选右侧“发送”" in usage
    assert all(text not in usage for text in ("点“改”", "手动输入", "下方编辑"))


def test_portable_help_matches_approved_candidate_send_and_automation_names() -> None:
    help_text = build_release.README_TEXT

    assert "候选右侧“发送”" in help_text
    assert "半自动会自动识别并生成候选" in help_text
    assert "全自动会自动识别、生成并发送" in help_text
    assert "主界面右上角“管理话术”" in help_text
    assert all(text not in help_text for text in ("候选右侧“改”", "下方手动输入框", "全托管"))


def _descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from _descendants(child)
