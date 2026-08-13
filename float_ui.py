from __future__ import annotations

import threading
import tkinter as tk
import webbrowser
import re
from tkinter import filedialog, messagebox, scrolledtext, ttk
from pathlib import Path

from llm_clients import provider_defaults, provider_labels, supported_provider_help
from settings import AppSettings


SOURCE_MODE_OPTIONS = (("knowledge_only", "\u4ec5\u4f7f\u7528\u77e5\u8bc6\u5e93"),)
MANAGED_MODE_OPTIONS = (
    ("off", "\u5173\u95ed"),
    ("observe", "\u89c2\u5bdf"),
    ("controlled", "\u53d7\u63a7"),
    ("full", "\u5168\u6258\u7ba1"),
)


def parse_managed_allowed_chats(value: str) -> list[str]:
    chats: list[str] = []
    for item in re.split(r"[,;\n\uff0c\uff1b]+", value):
        chat = item.strip()
        if chat and chat not in chats:
            chats.append(chat)
    return chats

TEXT = {
    "title": "聊小智",
    "waiting": "等待聊天窗口",
    "starting": "\u542f\u52a8\u4e2d...",
    "settings": "\u8bbe\u7f6e",
    "context": "\u5f53\u524d\u804a\u5929\u4e0a\u4e0b\u6587",
    "progress": "\u8bc6\u522b\u8fdb\u5ea6",
    "recognize": "\u8bc6\u522b\u5f53\u524d\u804a\u5929",
    "training": "\u8bad\u7ec3\u6a21\u5f0f  i",
    "training_tip": "\u8bad\u7ec3\u6a21\u5f0f\uff1a\u6301\u7eed\u8bc6\u522b\u5f53\u524d\u804a\u5929\uff0c\u53ea\u5b66\u4e60\u4e0a\u4e0b\u6587\u548c\u4f60\u5b9e\u9645\u53d1\u51fa\u7684\u56de\u590d\uff0c\u7528\u6765\u589e\u52a0\u573a\u666f\u8bdd\u672f\u5e93\u3002\u4e0d\u4f1a\u81ea\u52a8\u53d1\u9001\u6d88\u606f\u3002",
    "auto": "\u81ea\u52a8  i",
    "managed": "\u5168\u6258\u7ba1  i",
    "auto_tip": "自动识别：当你切换微信、企业微信或 QQ 聊天时，程序会自动截图 OCR 并生成候选回复。适合 OCR 稳定后再开。",
    "managed_tip": "\u5168\u6258\u7ba1\uff1a\u4ec5\u4f7f\u7528\u77e5\u8bc6\u5e93\u4e2d\u5df2\u6279\u51c6\u7684\u7b54\u6848\uff0c\u6a21\u578b\u4ec5\u7528\u4e8e\u5019\u9009\u6392\u5e8f\u4e0e\u4e00\u81f4\u6027\u5224\u65ad\uff0c\u4e0d\u4f1a\u751f\u6210\u6216\u6539\u5199\u7b54\u6848\u3002\u65e0\u53ef\u9760\u547d\u4e2d\u65f6\u56fa\u5b9a\u56de\u590d\uff1a\u6682\u672a\u67e5\u5230\u5bf9\u5e94\u7b54\u6848\u3002\u5168\u6258\u7ba1\u53d1\u9001\u4e0d\u5199\u5165\u8bb0\u5fc6\uff0c\u5efa\u8bae OCR \u7a33\u5b9a\u540e\u518d\u5f00\u3002",
    "replies": "\u5019\u9009\u56de\u590d",
    "reply_source_empty": "来源：未生成",
    "regen": "\u91cd\u65b0\u751f\u6210\u56de\u590d",
    "copy": "\u590d\u5236\u56de\u590d",
    "send": "发送到当前聊天",
    "send_short": "\u53d1",
    "edit_short": "\u6539",
    "manual_reply": "\u624b\u52a8\u8f93\u5165 / \u7f16\u8f91\u540e\u53d1\u9001",
    "empty_title": "\u6ca1\u6709\u5185\u5bb9",
    "empty_body": "\u8bf7\u5148\u9009\u62e9\u6216\u586b\u5199\u4e00\u6761\u56de\u590d\u3002",
    "copied": "\u5df2\u590d\u5236\u5230\u526a\u8d34\u677f",
    "api_provider": "文本生成接口",
    "api_config": "文本接口配置",
    "api_key": "接口 API Key",
    "vision_config": "\u56fe\u7247\u8bc6\u522b\u63a5\u53e3\u914d\u7f6e",
    "vision_provider": "\u56fe\u7247\u8bc6\u522b\u5382\u5bb6",
    "vision_key": "\u56fe\u7247\u8bc6\u522b API Key",
    "supported_vision": "\u76ee\u524d\u652f\u6301\uff1aZhipu glm-4v-flash\u3002\u6587\u672c\u751f\u6210\u53ef\u4ee5\u7ee7\u7eed\u7528 DeepSeek\uff0c\u56fe\u7247\u3001\u5361\u7247\u3001\u53ef\u89c1\u5185\u5bb9\u7406\u89e3\u5355\u72ec\u8d70\u8fd9\u91cc\u7684\u89c6\u89c9\u6a21\u578b\u3002\u5982\u672a\u5355\u72ec\u586b Key\uff0c\u4e14\u6587\u672c\u63a5\u53e3\u4e5f\u9009 Zhipu\uff0c\u4f1a\u590d\u7528\u6587\u672c Key\u3002",
    "supported_api": supported_provider_help(),
    "reply_source_mode": "回复来源",
    "reply_source_tip": (
        "仅使用知识库：回复只能来自已批准的知识记录。\n"
        "模型仅用于候选排序与一致性判断，不会生成或改写答案。\n"
        "无可靠命中时固定回复：暂未查到对应答案"
    ),
    "source_model": "大模型",
    "source_memory": "话术库",
    "source_model_memory": "大模型+话术库",
    "source_model_short": "模型",
    "source_memory_short": "话术库",
    "source_model_memory_short": "混合",
    "custom_headers": "自定义 Header JSON",
    "custom_body": "自定义 Body JSON",
    "custom_response_path": "返回文本路径",
    "web_search": "实时搜索增强",
    "web_search_tip": (
        "开启后，对方问到需要最新事实的问题（最新电影、新闻、价格、发布动态等），"
        "会先联网搜索（DuckDuckGo/Bing），把摘要给大模型参考，避免用过时训练知识回答。"
        "纯闲聊不受影响。"
    ),
    "model": "\u6a21\u578b",
    "save": "\u4fdd\u5b58",
    "saved": "\u8bbe\u7f6e\u5df2\u4fdd\u5b58",
    "usage_title": "使用说明 / 训练聊天记录",
    "import_training": "导入话术",
    "analyze_sop": "话术流程分析",
    "import_done": "训练包导入完成",
    "import_failed": "训练包导入失败",
    "phrasebook_manager": "管理话术",
    "phrasebook_empty": "暂无话术库，请先导入聊天记录训练包。",
    "phrasebook_saved": "话术已保存",
    "usage_body": (
        "基础使用：\n"
        "1. 先打开微信并点进一个具体聊天。\n"
        "2. 点“识别当前聊天”，程序会截图、OCR、结合长期记忆生成 3 条回复。\n"
        "3. 可以点候选右侧“发”直接发送，或点“改”后在下方编辑再发送。\n"
        "4. 只有成功发送的候选/手动输入内容，才会写入长期记忆。\n\n"
        "训练聊天记录：\n"
        "1. 准备 txt 文本，按 A/B 对话格式整理：\n"
        "   A: 对方说的话\n"
        "   B: 你希望程序学习的回复\n"
        "   A: 对方下一句\n"
        "   B: 你的下一句\n"
        "2. 一个 txt 可以放一段或多段聊天；多个 txt 放进同一个文件夹。\n"
        "3. 右键该文件夹，选择“发送到 > 压缩(zipped)文件夹”，得到 zip。\n"
        "4. 点下面“导入聊天记录训练包”，选择 zip。\n"
        "5. 程序会保存 B 方回复前 5-8 轮完整上下文和真实回复，作为长期记忆。\n\n"
        "发给别人使用：\n"
        "1. 对方第一次打开后，在设置里选择文本生成接口，并填写自己的 API Key、Base URL 和模型名。\n"
        "2. 对方也可以按上面的 A/B 格式导入自己的聊天记录训练包。\n"
        "3. 记忆保存在对方电脑的 %APPDATA%\\WeChatStyleReplyAssistant\\memory.json。"
    ),
}

TEXT["supported_api"] = (
    "常规使用：选择模型公司后只需要填写自己的 API Key，Base URL 和模型会自动带出默认值。\n"
    "高级用法：只有接入自定义 HTTP 或模型公司临时改接口时，才需要展开修改 Base URL、模型名、Header、Body、返回路径。"
)
TEXT["usage_body"] = (
    "基础使用：\n"
    "1. 打开微信电脑版，并点进一个具体聊天。\n"
    "2. 第一次使用先点右上角“设置”，在“文本接口配置”里选择模型公司，例如 DeepSeek 或 Zhipu。\n"
    "3. 只需要填写自己的 API Key；Base URL 和模型名会随模型公司自动填好，普通用户不用改。\n"
    "4. 保存后回到主界面，点击“识别当前聊天”，程序会截图、OCR，并生成 3 条候选回复。\n"
    "5. 可以点候选右侧“发”直接发送，或点“改”后在下方编辑再发送。\n"
    "6. 只有手动确认发送的候选/手动输入内容，才会写入长期记忆；全托管发送不会写入记忆。\n\n"
    "回复来源：\n"
    "1. 仅使用知识库：回复只能来自已批准的知识记录。\n"
    "2. 模型仅用于候选排序与一致性判断，不会生成或改写答案。\n"
    "3. 无可靠命中时固定回复：暂未查到对应答案。\n\n"
    "本地语料库 / 检索说明：\n"
    "1. 导入训练包后，程序会在本机生成长期记忆语料库，保存完整上下文、真实回复、来源和去重标识。\n"
    "2. 当前版本的语料库保存在 memory.json，主要用于本地相似历史检索和大模型提示词参考。\n"
    "3. 当前检索不是 ChromaDB 向量语义检索，而是本地文本相似度检索；它不会联网，也不需要额外模型。\n"
    "4. 如果后续启用真正向量语义检索，需要额外生成 embedding 向量索引，例如本地向量库或 ChromaDB。\n"
    "5. 语义向量模式适合大量训练语料；普通少量话术包用当前本地语料库即可工作。\n\n"
    "训练聊天记录：\n"
    "1. 推荐按 A/B 对话格式整理：\n"
    "   A: 对方说的话\n"
    "   B: 你希望程序学习的回复\n"
    "   A: 对方下一句\n"
    "   B: 你的下一句\n"
    "2. 支持 zip、txt、md、csv、tsv、json、docx、xlsx、xls、html 等格式。\n"
    "3. 多个文件可以放进一个文件夹后压缩成 zip，再点击“导入聊天记录训练包”。\n"
    "4. 程序会保存 B 方回复前 5-8 轮完整上下文和真实回复，作为长期记忆。\n\n"
    "企业年报知识库：\n"
    "1. 上传含“模块”、机构类型列以及“问题：…答：…”单元格的年报问答 Excel，程序会自动启用企业年报知识库解析。\n"
    "2. 程序会保留模块、机构类型和原始单元格来源；遇到相似问法时，大模型只在候选问题中排序。\n"
    "3. 高置信命中后将原样显示官方答案，不按日常聊天的短回复规则截断；低置信时不会冒充知识库答案。\n\n"
    "发给别人使用：\n"
    "1. 对方解压完整便携包后，双击“启动.bat”即可打开。\n"
    "2. 对方只需要在设置里选择模型公司并填写自己的 API Key。\n"
    "3. 便携版的设置和记忆保存在程序同级 user_data 文件夹，不会读取打包者的本机数据。\n"
    "4. 如果需要学习自己的话术，对方可以在设置里导入自己的聊天记录训练包。\n"
)
COLORS = {
    "page": "#f2f3f5",
    "panel": "#ffffff",
    "text": "#1f2329",
    "muted": "#6b7280",
    "line": "#e5e7eb",
    "green": "#07c160",
    "green_dark": "#06ad56",
    "green_soft": "#e8f8ef",
    "bubble": "#f7f8fa",
    "selected": "#dff6e9",
}


class ReplyAssistantUI:
    def __init__(self, settings: AppSettings, callbacks: dict) -> None:
        self.settings = settings
        self.callbacks = callbacks
        self.root = tk.Tk()
        self.root.title(TEXT["title"])
        assets_dir = Path(__file__).resolve().parent / "assets"
        png_icon_path = assets_dir / "app_icon_clean.png"
        ico_icon_path = assets_dir / "app_icon_clean.ico"
        self._window_icon = None
        if png_icon_path.exists():
            try:
                self._window_icon = tk.PhotoImage(file=str(png_icon_path))
                self.root.iconphoto(True, self._window_icon)
            except Exception:
                self._window_icon = None
        if ico_icon_path.exists():
            try:
                self.root.iconbitmap(str(ico_icon_path))
            except Exception:
                pass
        self.root.geometry("440x700+120+120")
        self.root.minsize(410, 560)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=COLORS["page"])

        self.partner_var = tk.StringVar(value=TEXT["waiting"])
        self.status_var = tk.StringVar(value=TEXT["starting"])
        self.progress_var = tk.StringVar(value="")
        self.training_var = tk.BooleanVar(value=self.settings.training_mode)
        self.auto_var = tk.BooleanVar(value=self.settings.auto_recognize)
        self.managed_var = tk.StringVar(value=self.settings.managed_mode)
        self.source_mode_var = tk.StringVar(value=self.settings.reply_source_mode)
        self.source_mode_label_var = tk.StringVar(value=self._source_mode_short_label(self.settings.reply_source_mode))
        self.selected_reply = ""
        self.reply_cards: list[tk.Frame] = []

        self._build()
        self.root.bind("<Control-Return>", lambda _event: self._send_editor())
        self.root.protocol("WM_DELETE_WINDOW", self._close_requested)

    def _build(self) -> None:
        outer = tk.Frame(self.root, bg=COLORS["page"], padx=11, pady=6)
        outer.pack(fill="both", expand=True)

        meta = tk.Frame(outer, bg=COLORS["page"])
        meta.pack(fill="x", pady=(0, 4))
        top_line = tk.Frame(meta, bg=COLORS["page"])
        top_line.pack(fill="x")
        tk.Label(
            top_line,
            textvariable=self.partner_var,
            bg=COLORS["page"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 9, "bold"),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        # 管理话术入口移到主界面右上角（设置按钮左边）
        self._ghost_button(
            top_line,
            TEXT["phrasebook_manager"],
            lambda: self._open_phrasebook_manager(self.root),
            width=9,
        ).pack(side="right", padx=(0, 0))
        self._ghost_button(top_line, TEXT["settings"], self.callbacks["settings"]).pack(side="right", padx=(8, 0))
        activity_row = tk.Frame(
            meta,
            bg=COLORS["green_soft"],
        )
        activity_row.pack(fill="x", pady=(6, 0), ipady=4)
        status = tk.Label(
            activity_row,
            textvariable=self.status_var,
            bg=COLORS["green_soft"],
            fg="#18864b",
            font=("Microsoft YaHei UI", 9),
            padx=8,
            anchor="w",
        )
        status.pack(side="left", fill="x", expand=True)
        self.progress_label = tk.Label(
            activity_row,
            text="",
            bg=COLORS["green_soft"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 8),
            anchor="e",
            justify="right",
            padx=8,
        )
        self.progress_label.pack(side="right", fill="x", expand=True)

        action_row = tk.Frame(outer, bg=COLORS["page"])
        action_row.pack(fill="x", pady=(0, 8))
        self._primary_button(action_row, TEXT["recognize"], self.callbacks["recognize"]).pack(
            side="left", fill="x", expand=True
        )
        self._ghost_button(action_row, TEXT["regen"], self.callbacks["regenerate"]).pack(
            side="left", padx=(8, 0)
        )
        mode_row = tk.Frame(outer, bg=COLORS["page"])
        mode_row.pack(fill="x", pady=(0, 8))
        training_group = tk.Frame(mode_row, bg=COLORS["page"])
        training_group.pack(side="left")
        training = tk.Checkbutton(
            training_group,
            text=TEXT["training"],
            variable=self.training_var,
            command=self._toggle_training,
            bg=COLORS["page"],
            fg=COLORS["muted"],
            activebackground=COLORS["page"],
            selectcolor=COLORS["panel"],
            font=("Microsoft YaHei UI", 10),
            padx=4,
        )
        training.pack(side="left")
        Tooltip(training, TEXT["training_tip"])
        source_group = tk.Frame(mode_row, bg=COLORS["page"])
        source_group.pack(side="left", padx=(12, 0))
        source_button = tk.Menubutton(
            source_group,
            text=f"{TEXT['reply_source_mode']}  ▾",
            bg=COLORS["panel"],
            fg=COLORS["text"],
            activebackground=COLORS["bubble"],
            relief="flat",
            borderwidth=0,
            font=("Microsoft YaHei UI", 9),
            highlightthickness=0,
            padx=8,
            pady=4,
        )
        self.source_menu = tk.Menu(source_button, tearoff=0)
        self._refresh_source_menu()
        source_button.configure(menu=self.source_menu)
        source_button.pack(side="left")
        Tooltip(source_button, TEXT["reply_source_tip"])
        auto_group = tk.Frame(mode_row, bg=COLORS["page"])
        auto_group.pack(side="right", padx=(8, 0))
        auto = tk.Checkbutton(
            auto_group,
            text=TEXT["auto"],
            variable=self.auto_var,
            command=self._toggle_auto,
            bg=COLORS["page"],
            fg=COLORS["muted"],
            activebackground=COLORS["page"],
            selectcolor=COLORS["panel"],
            font=("Microsoft YaHei UI", 10),
            padx=4,
        )
        auto.pack(side="left")
        Tooltip(auto, TEXT["auto_tip"])
        managed_group = tk.Frame(mode_row, bg=COLORS["page"])
        managed_group.pack(side="right", padx=(4, 0))
        managed = tk.OptionMenu(
            managed_group,
            self.managed_var,
            *[mode for mode, _label in MANAGED_MODE_OPTIONS],
            command=self._set_managed_mode,
        )
        managed.configure(
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            activebackground=COLORS["page"],
            font=("Microsoft YaHei UI", 10),
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
        )
        managed.pack(side="left")
        Tooltip(managed, TEXT["managed_tip"])

        bottom = tk.Frame(outer, bg=COLORS["page"])
        bottom.pack(side="bottom", fill="x", pady=(4, 0))
        self._ghost_button(bottom, TEXT["copy"], self._copy_editor).pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._primary_button(bottom, TEXT["send"], self._send_editor).pack(side="right", fill="x", expand=True)

        # 当前账号用户名输入行（群聊 @ 识别用）：模式行下方、上下文面板上方
        username_row = tk.Frame(outer, bg=COLORS["page"])
        username_row.pack(fill="x", pady=(0, 6))
        tk.Label(
            username_row,
            text="当前账号用户名",
            bg=COLORS["page"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left")
        self.account_name_var = tk.StringVar(value=self.settings.wecom_account_name)
        self.account_name_entry = tk.Entry(
            username_row,
            textvariable=self.account_name_var,
            relief="flat",
            bg="white",
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 10),
        )
        self.account_name_entry.pack(side="left", fill="x", expand=True, padx=(10, 0), ipady=4)
        self.account_name_entry.bind("<FocusOut>", self._save_account_name)
        self.account_name_entry.bind("<Return>", lambda _e: self._save_account_name())

        context_panel = self._panel(outer)
        context_panel.pack(fill="both", expand=False, pady=(0, 8))
        self._section_title(context_panel, TEXT["context"]).pack(anchor="w", padx=12, pady=(8, 3))
        context_wrap = tk.Frame(
            context_panel,
            bg=COLORS["bubble"],
            highlightthickness=1,
            highlightbackground=COLORS["line"],
        )
        context_wrap.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.context_canvas = tk.Canvas(
            context_wrap,
            height=112,
            bg=COLORS["bubble"],
            highlightthickness=0,
            borderwidth=0,
        )
        context_scroll = tk.Scrollbar(context_wrap, orient="vertical", command=self.context_canvas.yview)
        self.context_canvas.configure(yscrollcommand=context_scroll.set)
        context_scroll.pack(side="right", fill="y")
        self.context_canvas.pack(side="left", fill="both", expand=True)
        self.context_bubbles = tk.Frame(self.context_canvas, bg=COLORS["bubble"])
        self.context_canvas_window = self.context_canvas.create_window((0, 0), window=self.context_bubbles, anchor="nw")
        self.context_bubbles.bind(
            "<Configure>",
            lambda _event: self.context_canvas.configure(scrollregion=self.context_canvas.bbox("all")),
        )
        self.context_canvas.bind(
            "<Configure>",
            lambda event: self.context_canvas.itemconfigure(self.context_canvas_window, width=event.width),
        )
        self.context_canvas.bind("<MouseWheel>", self._scroll_context)
        self._bind_wheel_recursive(self.context_bubbles, self._scroll_context)

        replies_panel = self._panel(outer)
        replies_panel.pack(fill="both", expand=True, pady=(0, 8))
        replies_header = tk.Frame(replies_panel, bg=COLORS["panel"])
        replies_header.pack(fill="x", padx=12, pady=(8, 5))
        self._section_title(replies_header, TEXT["replies"]).pack(side="left")
        self.reply_source_var = tk.StringVar(value=TEXT["reply_source_empty"])
        tk.Label(
            replies_header,
            textvariable=self.reply_source_var,
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 8),
            anchor="e",
        ).pack(side="right")
        # 候选回复板块：Canvas + 滚动条，支持滚轮查看被窗口遮住的更多候选
        reply_scroll_wrap = tk.Frame(replies_panel, bg=COLORS["panel"])
        reply_scroll_wrap.pack(fill="x", padx=12)
        self.reply_scroll_canvas = tk.Canvas(
            reply_scroll_wrap,
            height=170,
            bg=COLORS["panel"],
            highlightthickness=0,
            borderwidth=0,
        )
        reply_scroll = tk.Scrollbar(reply_scroll_wrap, orient="vertical", command=self.reply_scroll_canvas.yview)
        self.reply_scroll_canvas.configure(yscrollcommand=reply_scroll.set)
        reply_scroll.pack(side="right", fill="y")
        self.reply_scroll_canvas.pack(side="left", fill="both", expand=True)
        self.reply_area = tk.Frame(self.reply_scroll_canvas, bg=COLORS["panel"])
        self.reply_canvas_window = self.reply_scroll_canvas.create_window((0, 0), window=self.reply_area, anchor="nw")
        self.reply_area.bind(
            "<Configure>",
            lambda _event: self.reply_scroll_canvas.configure(scrollregion=self.reply_scroll_canvas.bbox("all")),
        )
        self.reply_scroll_canvas.bind(
            "<Configure>",
            lambda event: self.reply_scroll_canvas.itemconfigure(self.reply_canvas_window, width=event.width),
        )
        self.reply_scroll_canvas.bind("<MouseWheel>", self._scroll_replies)

        self._section_title(replies_panel, TEXT["manual_reply"]).pack(anchor="w", padx=12, pady=(5, 5))
        editor_wrap = tk.Frame(replies_panel, bg=COLORS["bubble"], highlightthickness=1, highlightbackground=COLORS["line"])
        editor_wrap.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.editor = scrolledtext.ScrolledText(
            editor_wrap,
            height=6,
            wrap="word",
            font=("Microsoft YaHei UI", 11),
            bg=COLORS["bubble"],
            fg=COLORS["text"],
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=6,
        )
        self.editor.pack(fill="both", expand=True)

    def _panel(self, parent: tk.Widget) -> tk.Frame:
        return tk.Frame(parent, bg=COLORS["panel"], highlightthickness=1, highlightbackground=COLORS["line"])

    def _section_title(self, parent: tk.Widget, text: str) -> tk.Label:
        return tk.Label(
            parent,
            text=text,
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9, "bold"),
        )

    def _primary_button(self, parent: tk.Widget, text: str, command, width: int | None = None) -> tk.Button:
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=COLORS["green"],
            fg="white",
            activebackground=COLORS["green_dark"],
            activeforeground="white",
            relief="flat",
            borderwidth=0,
            padx=14,
            pady=9,
            width=width or 0,
            cursor="hand2",
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        button.bind("<Enter>", lambda _e: button.configure(bg=COLORS["green_dark"]))
        button.bind("<Leave>", lambda _e: button.configure(bg=COLORS["green"]))
        return button

    def _ghost_button(self, parent: tk.Widget, text: str, command, width: int | None = None) -> tk.Button:
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=COLORS["panel"],
            fg=COLORS["text"],
            activebackground=COLORS["bubble"],
            activeforeground=COLORS["text"],
            relief="flat",
            borderwidth=0,
            padx=12,
            pady=8,
            width=width or 0,
            cursor="hand2",
            font=("Microsoft YaHei UI", 10),
        )
        button.bind("<Enter>", lambda _e: button.configure(bg=COLORS["bubble"]))
        button.bind("<Leave>", lambda _e: button.configure(bg=COLORS["panel"]))
        return button

    def _mini_button(self, parent: tk.Widget, text: str, command, kind: str = "ghost") -> tk.Button:
        is_primary = kind == "primary"
        normal_bg = COLORS["green"] if is_primary else COLORS["panel"]
        hover_bg = COLORS["green_dark"] if is_primary else COLORS["bubble"]
        fg = "white" if is_primary else COLORS["text"]
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=normal_bg,
            fg=fg,
            activebackground=hover_bg,
            activeforeground=fg,
            relief="flat",
            borderwidth=0,
            padx=8,
            pady=4,
            cursor="hand2",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        button.bind("<Enter>", lambda _e: button.configure(bg=hover_bg))
        button.bind("<Leave>", lambda _e: button.configure(bg=normal_bg))
        return button

    def _info_icon(self, parent: tk.Widget, tip: str) -> tk.Label:
        label = tk.Label(
            parent,
            text="i",
            bg=COLORS["page"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9, "bold"),
            width=2,
            cursor="question_arrow",
        )
        Tooltip(label, tip)
        return label

    def _toggle_auto(self) -> None:
        enabled = bool(self.auto_var.get())
        self.settings.auto_recognize = enabled
        self.settings.save()
        self.callbacks["auto_changed"](enabled)

    def _toggle_training(self) -> None:
        enabled = bool(self.training_var.get())
        self.settings.training_mode = enabled
        self.settings.save()
        self.callbacks["training_changed"](enabled)

    def _toggle_managed(self) -> None:
        value = self.managed_var.get()
        self._set_managed_mode("full" if value is True else "off" if value is False else str(value))

    def _set_managed_mode(self, mode: str, *, persist: bool = True) -> None:
        label_to_mode = {label: value for value, label in MANAGED_MODE_OPTIONS}
        selected = label_to_mode.get(mode, mode)
        if selected not in {value for value, _label in MANAGED_MODE_OPTIONS}:
            selected = "off"
        self.managed_var.set(selected)
        self.settings.managed_mode = selected
        if selected != "off":
            self.settings.auto_recognize = True
            self.auto_var.set(True)
        if "managed_mode_changed" in self.callbacks:
            self.callbacks["managed_mode_changed"](selected)
        elif "managed_changed" in self.callbacks:
            self.callbacks["managed_changed"](selected == "full")
        self.settings.managed_mode = selected
        if selected != "off":
            self.settings.auto_recognize = True
            self.auto_var.set(True)
        if persist:
            self.settings.save()

    def _toggle_source_mode(self) -> None:
        mode = self._source_mode_value(self.source_mode_label_var.get())
        self._set_source_mode(mode)

    def _set_source_mode(self, mode: str) -> None:
        del mode
        self.source_mode_var.set("knowledge_only")
        self.source_mode_label_var.set(self._source_mode_short_label("knowledge_only"))
        self.settings.reply_source_mode = "knowledge_only"
        self.settings.save()
        self._refresh_source_menu()

    def _refresh_source_menu(self) -> None:
        if not hasattr(self, "source_menu"):
            return
        current = "knowledge_only"
        items = SOURCE_MODE_OPTIONS
        self.source_menu.delete(0, "end")
        for value, label in items:
            prefix = "✓ " if value == current else "   "
            self.source_menu.add_command(label=f"{prefix}{label}", command=lambda selected=value: self._set_source_mode(selected))

    def _source_mode_label(self, value: str) -> str:
        return dict(SOURCE_MODE_OPTIONS).get(value, dict(SOURCE_MODE_OPTIONS)["knowledge_only"])

    def _source_mode_short_label(self, value: str) -> str:
        return self._source_mode_label(value)

    def _source_mode_value(self, label: str) -> str:
        return {option_label: value for value, option_label in SOURCE_MODE_OPTIONS}.get(label, "knowledge_only")

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _save_account_name(self, _event=None) -> None:
        """保存当前账号用户名（修改即存，不覆盖已填值）。"""
        try:
            name = self.account_name_var.get().strip()
            if name != self.settings.wecom_account_name:
                self.settings.wecom_account_name = name
                self.settings.save()
                if name:
                    self.set_status(f"已保存当前账号用户名：{name}")
        except Exception:
            pass

    def _run_llm_import_with_progress(self, zip_path: str, parent_window: tk.Toplevel, on_success=None) -> None:
        """导入知识库：结构化直读，非结构化整篇提取，后台完成暂存校验。

        进度条按已完成文件推进，到达 100% 时暂存和激活完成；
        成功显示"训练包已导入：..."，失败弹错误提示。
        """
        win = tk.Toplevel(parent_window)
        win.title(TEXT["import_training"])
        win.resizable(False, False)
        win.attributes("-topmost", True)
        tk.Label(win, text="正在校验并暂存知识文档，请稍候...").pack(padx=24, pady=(16, 4))
        bar = ttk.Progressbar(win, length=320, mode="determinate", maximum=100)
        bar.pack(padx=24, pady=8)
        pct_label = tk.Label(win, text="0%")
        pct_label.pack(pady=(0, 12))
        # 居中于父窗口（话术管理对话框）正中间
        win.update_idletasks()
        try:
            parent_x = parent_window.winfo_rootx()
            parent_y = parent_window.winfo_rooty()
            parent_w = parent_window.winfo_width()
            parent_h = parent_window.winfo_height()
            win_w = win.winfo_reqwidth()
            win_h = win.winfo_reqheight()
            win.geometry(f"+{parent_x + max(0, (parent_w - win_w) // 2)}+{parent_y + max(0, (parent_h - win_h) // 3)}")
        except Exception:
            pass

        def on_progress(done: int, total: int) -> None:
            pct = int(done / total * 100) if total else 0
            try:
                win.after(0, lambda: (bar.configure(value=pct), pct_label.configure(text=f"{pct}%")))
            except Exception:
                pass

        def run() -> None:
            result = ""
            error = ""
            try:
                result = self.callbacks["import_training_zip"](zip_path, progress=on_progress)
            except Exception as exc:
                error = str(exc)
            try:
                win.after(0, win.destroy)
            except Exception:
                pass
            if error:
                try:
                    win.after(0, lambda: messagebox.showerror(TEXT["import_failed"], error, parent=parent_window))
                except Exception:
                    pass
            else:
                def finish() -> None:
                    self.set_status(result)
                    messagebox.showinfo(TEXT["import_done"], result, parent=parent_window)
                    if on_success:
                        on_success()
                try:
                    win.after(0, finish)
                except Exception:
                    pass

        threading.Thread(target=run, daemon=True).start()

    def set_progress(self, lines: list[str]) -> None:
        self.progress_var.set("\n".join(lines))
        self.progress_label.configure(text=lines[-1] if lines else "")

    def clear_progress(self) -> None:
        self.progress_var.set("")
        self.progress_label.configure(text="")

    def set_partner(self, text: str) -> None:
        self.partner_var.set(text)

    def set_context(self, text: str) -> None:
        for child in self.context_bubbles.winfo_children():
            child.destroy()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            self._add_context_note("")
        for line in lines:
            role, body = self._parse_context_line(line)
            if role == "other":
                self._add_context_bubble(body, own=False)
            elif role == "own":
                self._add_context_bubble(body, own=True)
            else:
                self._add_context_note(body or line)
        self.context_canvas.update_idletasks()
        self.context_canvas.configure(scrollregion=self.context_canvas.bbox("all"))
        self.context_canvas.yview_moveto(1.0)
        # 气泡是动态创建的，每次设置上下文后重新递归绑定滚轮（Label 上也能滚动）
        self._bind_wheel_recursive(self.context_bubbles, self._scroll_context)

    def _parse_context_line(self, line: str) -> tuple[str, str]:
        if line.startswith("[") and "]" in line:
            role_text, body = line[1:].split("]", 1)
            return self._context_role(role_text), body.strip()
        return "", line.strip()

    def _context_role(self, role_text: str) -> str:
        role = role_text.strip()
        if role in {"对方", "瀵规柟"} or "瀵规柟" in role:
            return "other"
        if role in {"我", "鎴"} or role.startswith("鎴") or role.startswith("閹"):
            return "own"
        return ""

    def _add_context_bubble(self, text: str, own: bool) -> None:
        row = tk.Frame(self.context_bubbles, bg=COLORS["bubble"])
        row.pack(fill="x", padx=7, pady=(1, 4))
        bubble = tk.Label(
            row,
            text=text,
            bg="#95ec69" if own else COLORS["panel"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 8),
            justify="left",
            anchor="w",
            wraplength=232,
            padx=8,
            pady=5,
        )
        if own:
            bubble.pack(side="right", padx=(62, 3))
        else:
            bubble.pack(side="left", padx=(3, 62))

    def _add_context_note(self, text: str) -> None:
        note = tk.Label(
            self.context_bubbles,
            text=text,
            bg=COLORS["bubble"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 7),
            wraplength=300,
            justify="center",
        )
        note.pack(fill="x", padx=10, pady=2)

    def _scroll_context(self, event) -> None:
        self.context_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _scroll_replies(self, event) -> None:
        self.reply_scroll_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    @staticmethod
    def _bind_wheel_recursive(widget: tk.Widget, handler) -> None:
        """把滚轮事件递归绑定到控件及其所有子控件（Label/Frame 上的滚轮才能滑动）。"""
        widget.bind("<MouseWheel>", handler)
        for child in widget.winfo_children():
            ReplyAssistantUI._bind_wheel_recursive(child, handler)

    def set_replies(self, replies: list[str]) -> None:
        self.clear_replies()
        if not replies:
            return
        for index, reply in enumerate(replies, 1):
            self._add_reply_card(index, reply, selected=False)
        self.set_editor("")

    def set_reply_source(self, text: str) -> None:
        self.reply_source_var.set(text or TEXT["reply_source_empty"])

    def clear_replies(self) -> None:
        for child in self.reply_area.winfo_children():
            child.destroy()
        self.reply_cards.clear()
        self.selected_reply = ""
        self.set_editor("")
        self.set_reply_source(TEXT["reply_source_empty"])

    def _add_reply_card(self, index: int, reply: str, selected: bool) -> None:
        card = tk.Frame(
            self.reply_area,
            bg=COLORS["selected"] if selected else COLORS["bubble"],
            highlightthickness=1,
            highlightbackground=COLORS["green"] if selected else COLORS["line"],
            cursor="hand2",
        )
        card.pack(fill="x", pady=(0, 7))
        number = tk.Label(
            card,
            text=str(index),
            bg=card["bg"],
            fg=COLORS["green"],
            font=("Microsoft YaHei UI", 10, "bold"),
            width=3,
        )
        number.pack(side="left", padx=(8, 4), pady=9)
        edit_button = self._mini_button(
            card,
            TEXT["edit_short"],
            lambda value=reply, frame=card: self._edit_reply(value, frame),
            kind="ghost",
        )
        edit_button.pack(side="left", padx=(0, 8), pady=7)
        label = tk.Label(
            card,
            text=reply,
            bg=card["bg"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 10),
            justify="left",
            anchor="w",
            wraplength=270,
        )
        label.pack(side="left", fill="x", expand=True, padx=(0, 8), pady=9)
        send_button = self._mini_button(
            card,
            TEXT["send_short"],
            lambda value=reply: self._send_text(value, "selected_candidate"),
            kind="primary",
        )
        send_button.pack(side="right", padx=(0, 8), pady=7)
        for widget in (card, number, label):
            widget.bind("<Button-1>", lambda _e, value=reply, frame=card: self._select_reply(value, frame))
            widget.bind("<Enter>", lambda _e, frame=card: frame.configure(bg=COLORS["selected"]))
            widget.bind("<Leave>", lambda _e, frame=card: self._refresh_card_colors())
        self._bind_wheel_recursive(card, self._scroll_replies)
        self.reply_cards.append(card)

    def _select_reply(self, text: str, card: tk.Frame) -> None:
        self.selected_reply = text
        self.set_editor(text)
        self.editor.focus_set()
        for item in self.reply_cards:
            item.configure(
                bg=COLORS["selected"] if item is card else COLORS["bubble"],
                highlightbackground=COLORS["green"] if item is card else COLORS["line"],
            )
            for child in item.winfo_children():
                if not isinstance(child, tk.Button):
                    child.configure(bg=item["bg"])

    def _edit_reply(self, text: str, card: tk.Frame) -> None:
        self._select_reply(text, card)
        self.editor.mark_set("insert", "end-1c")

    def _refresh_card_colors(self) -> None:
        for card in self.reply_cards:
            selected = any(
                isinstance(child, tk.Label) and child.cget("text") == self.selected_reply
                for child in card.winfo_children()
            )
            card.configure(bg=COLORS["selected"] if selected else COLORS["bubble"])
            for child in card.winfo_children():
                if not isinstance(child, tk.Button):
                    child.configure(bg=card["bg"])

    def set_editor(self, text: str) -> None:
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", text)

    def editor_text(self) -> str:
        return self.editor.get("1.0", "end").strip()

    def _send_editor(self) -> None:
        text = self.editor_text()
        if not text:
            messagebox.showwarning(TEXT["empty_title"], TEXT["empty_body"])
            return
        source = "selected_candidate" if text == self.selected_reply else "manual_edit"
        self._send_text(text, source)

    def _send_text(self, text: str, source: str) -> None:
        cleaned = text.strip()
        if not cleaned:
            messagebox.showwarning(TEXT["empty_title"], TEXT["empty_body"])
            return
        self.callbacks["send"](cleaned, source)

    def _copy_editor(self) -> None:
        text = self.editor_text()
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.set_status(TEXT["copied"])

    def _close_requested(self) -> None:
        callback = self.callbacks.get("exit")
        if callback:
            callback()
            return
        self.close()

    def close(self) -> None:
        self.root.destroy()

    def show_settings_dialog(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title(TEXT["settings"])
        screen_h = max(640, self.root.winfo_screenheight())
        dialog.geometry(f"620x{min(760, screen_h - 80)}")
        dialog.minsize(560, 560)
        dialog.configure(bg=COLORS["page"])
        dialog.attributes("-topmost", True)
        dialog.transient(self.root)

        button_row = tk.Frame(dialog, bg=COLORS["page"], padx=16, pady=12)
        button_row.pack(side="bottom", fill="x")
        frame = tk.Frame(dialog, bg=COLORS["page"], padx=16, pady=14)
        frame.pack(side="top", fill="both", expand=True)

        provider_items = provider_labels()
        provider_label_by_key = {key: label for key, label in provider_items}
        provider_key_by_label = {label: key for key, label in provider_items}
        provider_var = tk.StringVar(value=provider_label_by_key.get(self.settings.api_provider, self.settings.api_provider))
        api_var = tk.StringVar(value=self.settings.get_provider_api_key(self.settings.api_provider))
        active_provider_key = {"value": self.settings.api_provider}
        base_var = tk.StringVar(value=self.settings.base_url)
        model_var = tk.StringVar(value=self.settings.model)
        headers_var = tk.StringVar(value=self.settings.custom_headers)
        response_path_var = tk.StringVar(value=self.settings.custom_response_path)
        vision_items = [("zhipu", "Zhipu / glm-4v-flash"), ("off", "关闭图片识别")]
        vision_label_by_key = {key: label for key, label in vision_items}
        vision_key_by_label = {label: key for key, label in vision_items}
        vision_provider_var = tk.StringVar(
            value=vision_label_by_key.get(self.settings.vision_provider, self.settings.vision_provider)
        )
        vision_api_var = tk.StringVar(value=self.settings.vision_api_key)
        vision_base_var = tk.StringVar(value=self.settings.vision_base_url)
        vision_model_var = tk.StringVar(value=self.settings.vision_model)

        api_detail_visible = tk.BooleanVar(value=False)
        api_detail_frame = tk.Frame(frame, bg=COLORS["page"])

        def api_summary() -> str:
            key_state = "Key已填" if api_var.get().strip() else "Key未填"
            provider = provider_var.get() or "未选择"
            model = model_var.get().strip() or "未填模型"
            return f"{provider} / {model} / {key_state}"

        def refresh_api_summary() -> None:
            api_summary_var.set(api_summary())
            api_arrow_var.set("▲" if api_detail_visible.get() else "▼")

        def toggle_api_detail() -> None:
            if api_detail_visible.get():
                api_detail_frame.pack_forget()
                api_detail_visible.set(False)
            else:
                api_detail_visible.set(True)
                api_detail_frame.pack(fill="x", pady=(0, 10), after=api_toggle)
            refresh_api_summary()

        api_toggle = tk.Frame(
            frame,
            bg=COLORS["panel"],
            highlightthickness=1,
            highlightbackground=COLORS["line"],
            cursor="hand2",
        )
        api_toggle.pack(fill="x", pady=(0, 12), ipady=6)
        api_summary_var = tk.StringVar(value=api_summary())
        api_arrow_var = tk.StringVar(value="▼")
        tk.Label(
            api_toggle,
            text=TEXT["api_config"],
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 10, "bold"),
            cursor="hand2",
        ).pack(side="left", padx=(14, 10), pady=8)
        tk.Label(
            api_toggle,
            textvariable=api_summary_var,
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 10),
            cursor="hand2",
        ).pack(side="left", fill="x", expand=True, pady=8)
        tk.Label(
            api_toggle,
            textvariable=api_arrow_var,
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 10, "bold"),
            width=3,
            cursor="hand2",
        ).pack(side="right", padx=(6, 12), pady=8)

        def set_api_toggle_bg(color: str) -> None:
            api_toggle.configure(bg=color)
            for child in api_toggle.winfo_children():
                child.configure(bg=color)

        for widget in [api_toggle, *api_toggle.winfo_children()]:
            widget.bind("<Button-1>", lambda _event: toggle_api_detail())
            widget.bind("<Enter>", lambda _event: set_api_toggle_bg(COLORS["bubble"]))
            widget.bind("<Leave>", lambda _event: set_api_toggle_bg(COLORS["panel"]))

        tk.Label(api_detail_frame, text=TEXT["api_provider"], bg=COLORS["page"], fg=COLORS["muted"], anchor="w").pack(fill="x")
        provider_menu = tk.OptionMenu(api_detail_frame, provider_var, *[label for _key, label in provider_items])
        provider_menu.configure(
            bg=COLORS["panel"],
            fg=COLORS["text"],
            activebackground=COLORS["bubble"],
            relief="flat",
            borderwidth=0,
            font=("Microsoft YaHei UI", 10),
            highlightthickness=0,
        )
        provider_menu.pack(fill="x", ipady=3, pady=(3, 6))
        tk.Label(
            api_detail_frame,
            text=TEXT["supported_api"],
            bg=COLORS["page"],
            fg=COLORS["muted"],
            justify="left",
            wraplength=560,
            anchor="w",
        ).pack(fill="x", pady=(0, 10))

        def apply_provider_defaults(*_args) -> None:
            key = provider_key_by_label.get(provider_var.get(), self.settings.api_provider)
            previous_key = active_provider_key["value"]
            if key != previous_key:
                self.settings.set_provider_api_key(previous_key, api_var.get().strip())
                api_var.set(self.settings.get_provider_api_key(key))
                active_provider_key["value"] = key
            defaults = provider_defaults(key)
            if defaults:
                base_var.set(defaults.get("base_url", base_var.get()))
                model_var.set(defaults.get("model", model_var.get()))

        provider_var.trace_add("write", apply_provider_defaults)
        provider_var.trace_add("write", lambda *_args: refresh_api_summary())
        api_var.trace_add("write", lambda *_args: refresh_api_summary())
        model_var.trace_add("write", lambda *_args: refresh_api_summary())

        tk.Label(api_detail_frame, text=TEXT["api_key"], bg=COLORS["page"], fg=COLORS["muted"], anchor="w").pack(fill="x")
        key_row = tk.Frame(api_detail_frame, bg=COLORS["page"])
        key_row.pack(fill="x", pady=(3, 10))
        api_entry = tk.Entry(
            key_row,
            textvariable=api_var,
            show="*",
            relief="flat",
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 10),
        )
        api_entry.pack(side="left", fill="x", expand=True, ipady=7)

        key_visible = tk.BooleanVar(value=False)

        def toggle_api_key_visible() -> None:
            visible = not key_visible.get()
            key_visible.set(visible)
            api_entry.configure(show="" if visible else "*")
            key_button.configure(text="隐藏" if visible else "显示")

        key_button = self._ghost_button(key_row, "显示", toggle_api_key_visible, width=6)
        key_button.pack(side="right", padx=(8, 0))

        for label, var in [
            ("Base URL", base_var),
            (TEXT["model"], model_var),
        ]:
            tk.Label(api_detail_frame, text=label, bg=COLORS["page"], fg=COLORS["muted"], anchor="w").pack(fill="x")
            tk.Entry(
                api_detail_frame,
                textvariable=var,
                relief="flat",
                bg=COLORS["panel"],
                fg=COLORS["text"],
                font=("Microsoft YaHei UI", 10),
            ).pack(fill="x", ipady=7, pady=(3, 10))

        for label, var in [
            (TEXT["custom_headers"], headers_var),
            (TEXT["custom_response_path"], response_path_var),
        ]:
            tk.Label(api_detail_frame, text=label, bg=COLORS["page"], fg=COLORS["muted"], anchor="w").pack(fill="x")
            tk.Entry(
                api_detail_frame,
                textvariable=var,
                relief="flat",
                bg=COLORS["panel"],
                fg=COLORS["text"],
                font=("Microsoft YaHei UI", 10),
            ).pack(fill="x", ipady=7, pady=(3, 10))

        tk.Label(api_detail_frame, text=TEXT["custom_body"], bg=COLORS["page"], fg=COLORS["muted"], anchor="w").pack(fill="x")
        body_editor = scrolledtext.ScrolledText(
            api_detail_frame,
            height=4,
            wrap="word",
            relief="flat",
            borderwidth=0,
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 9),
            padx=8,
            pady=6,
        )
        body_editor.insert("1.0", self.settings.custom_body)
        body_editor.pack(fill="x", pady=(3, 10))

        web_search_var = tk.BooleanVar(value=False)
        tk.Label(
            api_detail_frame,
            text="\u6b63\u5f0f\u6a21\u5f0f\u7981\u7528\u8054\u7f51\u641c\u7d22\uff0c\u56de\u590d\u4ec5\u6765\u81ea\u672c\u5730\u77e5\u8bc6\u5e93\u3002",
            bg=COLORS["page"],
            fg=COLORS["muted"],
            justify="left",
            wraplength=560,
            anchor="w",
        ).pack(fill="x", pady=(0, 10))

        managed_mode_var = tk.StringVar(value=self.settings.managed_mode)
        tk.Label(
            frame,
            text="\u6258\u7ba1\u6a21\u5f0f",
            bg=COLORS["page"],
            fg=COLORS["muted"],
            anchor="w",
        ).pack(fill="x")
        managed_mode_menu = tk.OptionMenu(
            frame,
            managed_mode_var,
            *[mode for mode, _label in MANAGED_MODE_OPTIONS],
        )
        managed_mode_menu.configure(
            bg=COLORS["panel"],
            fg=COLORS["text"],
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
        )
        managed_mode_menu.pack(fill="x", ipady=3, pady=(3, 8))
        tk.Label(
            frame,
            text="\u53d7\u63a7\u6a21\u5f0f\u5141\u8bb8\u7684\u804a\u5929\uff08\u6bcf\u884c\u6216\u9017\u53f7\u5206\u9694\uff09",
            bg=COLORS["page"],
            fg=COLORS["muted"],
            anchor="w",
        ).pack(fill="x")
        managed_allowlist_editor = scrolledtext.ScrolledText(
            frame,
            height=3,
            wrap="word",
            relief="flat",
            borderwidth=0,
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 9),
            padx=8,
            pady=6,
        )
        managed_allowlist_editor.insert("1.0", "\n".join(self.settings.managed_allowed_chats))
        managed_allowlist_editor.pack(fill="x", pady=(3, 10))

        vision_detail_visible = tk.BooleanVar(value=False)
        vision_detail_frame = tk.Frame(frame, bg=COLORS["page"])

        def vision_summary() -> str:
            provider = vision_provider_var.get() or "未选择"
            model = vision_model_var.get().strip() or "未填模型"
            key_state = "Key已填" if vision_api_var.get().strip() else "Key未填"
            return f"{provider} / {model} / {key_state}"

        def refresh_vision_summary() -> None:
            vision_summary_var.set(vision_summary())
            vision_arrow_var.set("▲" if vision_detail_visible.get() else "▼")

        def toggle_vision_detail() -> None:
            if vision_detail_visible.get():
                vision_detail_frame.pack_forget()
                vision_detail_visible.set(False)
            else:
                vision_detail_visible.set(True)
                vision_detail_frame.pack(fill="x", pady=(0, 10), after=vision_toggle)
            refresh_vision_summary()

        vision_toggle = tk.Frame(
            frame,
            bg=COLORS["panel"],
            highlightthickness=1,
            highlightbackground=COLORS["line"],
            cursor="hand2",
        )
        vision_toggle.pack(fill="x", pady=(0, 12), ipady=6)
        vision_summary_var = tk.StringVar(value=vision_summary())
        vision_arrow_var = tk.StringVar(value="▼")
        tk.Label(
            vision_toggle,
            text=TEXT["vision_config"],
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 10, "bold"),
            cursor="hand2",
        ).pack(side="left", padx=(14, 10), pady=8)
        tk.Label(
            vision_toggle,
            textvariable=vision_summary_var,
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 10),
            cursor="hand2",
        ).pack(side="left", fill="x", expand=True, pady=8)
        tk.Label(
            vision_toggle,
            textvariable=vision_arrow_var,
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 10, "bold"),
            width=3,
            cursor="hand2",
        ).pack(side="right", padx=(6, 12), pady=8)

        def set_vision_toggle_bg(color: str) -> None:
            vision_toggle.configure(bg=color)
            for child in vision_toggle.winfo_children():
                child.configure(bg=color)

        for widget in [vision_toggle, *vision_toggle.winfo_children()]:
            widget.bind("<Button-1>", lambda _event: toggle_vision_detail())
            widget.bind("<Enter>", lambda _event: set_vision_toggle_bg(COLORS["bubble"]))
            widget.bind("<Leave>", lambda _event: set_vision_toggle_bg(COLORS["panel"]))

        tk.Label(
            vision_detail_frame,
            text=TEXT["vision_provider"],
            bg=COLORS["page"],
            fg=COLORS["muted"],
            anchor="w",
        ).pack(fill="x")
        vision_provider_menu = tk.OptionMenu(
            vision_detail_frame,
            vision_provider_var,
            *[label for _key, label in vision_items],
        )
        vision_provider_menu.configure(
            bg=COLORS["panel"],
            fg=COLORS["text"],
            activebackground=COLORS["bubble"],
            relief="flat",
            borderwidth=0,
            font=("Microsoft YaHei UI", 10),
            highlightthickness=0,
        )
        vision_provider_menu.pack(fill="x", ipady=3, pady=(3, 6))
        tk.Label(
            vision_detail_frame,
            text=TEXT["supported_vision"],
            bg=COLORS["page"],
            fg=COLORS["muted"],
            justify="left",
            wraplength=560,
            anchor="w",
        ).pack(fill="x", pady=(0, 10))

        def apply_vision_defaults(*_args) -> None:
            key = vision_key_by_label.get(vision_provider_var.get(), self.settings.vision_provider)
            if key == "zhipu":
                vision_base_var.set(vision_base_var.get().strip() or "https://open.bigmodel.cn/api/paas/v4")
                vision_model_var.set(vision_model_var.get().strip() or "glm-4v-flash")
            refresh_vision_summary()

        vision_provider_var.trace_add("write", apply_vision_defaults)
        vision_api_var.trace_add("write", lambda *_args: refresh_vision_summary())
        vision_model_var.trace_add("write", lambda *_args: refresh_vision_summary())

        tk.Label(vision_detail_frame, text=TEXT["vision_key"], bg=COLORS["page"], fg=COLORS["muted"], anchor="w").pack(fill="x")
        vision_key_row = tk.Frame(vision_detail_frame, bg=COLORS["page"])
        vision_key_row.pack(fill="x", pady=(3, 10))
        vision_api_entry = tk.Entry(
            vision_key_row,
            textvariable=vision_api_var,
            show="*",
            relief="flat",
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 10),
        )
        vision_api_entry.pack(side="left", fill="x", expand=True, ipady=7)

        vision_key_visible = tk.BooleanVar(value=False)

        def toggle_vision_key_visible() -> None:
            visible = not vision_key_visible.get()
            vision_key_visible.set(visible)
            vision_api_entry.configure(show="" if visible else "*")
            vision_key_button.configure(text="隐藏" if visible else "显示")

        vision_key_button = self._ghost_button(vision_key_row, "显示", toggle_vision_key_visible, width=6)
        vision_key_button.pack(side="right", padx=(8, 0))

        for label, var in [
            ("Base URL", vision_base_var),
            (TEXT["model"], vision_model_var),
        ]:
            tk.Label(vision_detail_frame, text=label, bg=COLORS["page"], fg=COLORS["muted"], anchor="w").pack(fill="x")
            tk.Entry(
                vision_detail_frame,
                textvariable=var,
                relief="flat",
                bg=COLORS["panel"],
                fg=COLORS["text"],
                font=("Microsoft YaHei UI", 10),
            ).pack(fill="x", ipady=7, pady=(3, 10))

        usage_visible = tk.BooleanVar(value=False)
        usage_frame = tk.Frame(frame, bg=COLORS["page"])

        def toggle_usage() -> None:
            if usage_visible.get():
                usage_frame.pack_forget()
                usage_visible.set(False)
                usage_toggle.configure(text=f"{TEXT['usage_title']}  ▼")
            else:
                usage_visible.set(True)
                usage_frame.pack(fill="both", expand=True, pady=(0, 10))
                usage_toggle.configure(text=f"{TEXT['usage_title']}  ▲")

        usage_toggle = self._ghost_button(frame, f"{TEXT['usage_title']}  ▼", toggle_usage)
        usage_toggle.pack(fill="x", pady=(2, 10))
        usage = scrolledtext.ScrolledText(
            usage_frame,
            height=14,
            wrap="word",
            relief="flat",
            borderwidth=0,
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 9),
            padx=10,
            pady=8,
        )
        usage.insert("1.0", TEXT["usage_body"])
        usage.configure(state="disabled")
        usage.pack(fill="both", expand=True, pady=(0, 10))

        def import_training_zip() -> None:
            zip_path = filedialog.askopenfilename(
                parent=dialog,
                title=TEXT["import_training"],
                filetypes=[
                    ("All files", "*.*"),
                    ("训练文件", "*.zip *.pdf *.txt *.md *.csv *.tsv *.json *.docx *.xlsx *.xls *.html *.htm"),
                    ("Zip files", "*.zip"),
                    ("PDF files", "*.pdf"),
                    ("Office files", "*.docx *.xlsx *.xls"),
                    ("Text files", "*.txt *.md *.csv *.tsv *.json *.html *.htm"),
                ],
            )
            if not zip_path:
                return
            self._run_llm_import_with_progress(zip_path, dialog)

        def choose_and_analyze_sop_training(parent_window: tk.Toplevel | None = None, body_widget: scrolledtext.ScrolledText | None = None) -> None:
            source_path = filedialog.askopenfilename(
                parent=parent_window or dialog,
                title=TEXT["analyze_sop"],
                filetypes=[
                    ("All files", "*.*"),
                    ("聊天记录文件", "*.zip *.txt *.md *.csv *.tsv *.json *.docx *.xlsx *.xls *.html *.htm"),
                    ("Zip files", "*.zip"),
                    ("Office files", "*.docx *.xlsx *.xls"),
                    ("Text files", "*.txt *.md *.csv *.tsv *.json *.html *.htm"),
                ],
            )
            if not source_path:
                return
            try:
                selected_provider = provider_key_by_label.get(provider_var.get(), self.settings.api_provider)
                self.settings.api_provider = selected_provider
                self.settings.set_provider_api_key(selected_provider, api_var.get().strip())
                self.settings.base_url = base_var.get().strip()
                self.settings.model = model_var.get().strip() or self.settings.model
                self.settings.custom_headers = headers_var.get().strip()
                self.settings.custom_body = body_editor.get("1.0", "end").strip()
                self.settings.custom_response_path = response_path_var.get().strip()
                self.settings.enable_web_search = False
                self.settings.save()
                result = self.callbacks["analyze_sop_training"](source_path)
            except Exception as exc:
                messagebox.showerror(TEXT["import_failed"], str(exc), parent=parent_window or dialog)
                return
            self.set_status(result.splitlines()[0] if result else TEXT["phrasebook_saved"])
            if self._open_sop_html_report(parent_window or dialog):
                if body_widget is not None:
                    body_widget.configure(state="normal")
                    body_widget.delete("1.0", "end")
                    body_widget.insert("1.0", "已生成话术流程分析 HTML 报告，并在浏览器中打开。")
                    body_widget.configure(state="disabled")
                return
            if body_widget is not None:
                body_widget.configure(state="normal")
                body_widget.delete("1.0", "end")
                body_widget.insert("1.0", result or "暂无 SOP 分析结果。")
                body_widget.configure(state="disabled")
            else:
                self._show_sop_document_dialog(dialog, result, analyze_current_sop_library, choose_and_analyze_sop_training)

        def analyze_current_sop_library(parent_window: tk.Toplevel | None = None, body_widget: scrolledtext.ScrolledText | None = None) -> None:
            try:
                result = self.callbacks["analyze_current_sop_library"]()
            except Exception as exc:
                messagebox.showerror(TEXT["import_failed"], str(exc), parent=parent_window or dialog)
                return
            self.set_status(result.splitlines()[0] if result else TEXT["phrasebook_saved"])
            if self._open_sop_html_report(parent_window or dialog):
                if body_widget is not None:
                    body_widget.configure(state="normal")
                    body_widget.delete("1.0", "end")
                    body_widget.insert("1.0", "已生成话术流程分析 HTML 报告，并在浏览器中打开。")
                    body_widget.configure(state="disabled")
                return
            if body_widget is not None:
                body_widget.configure(state="normal")
                body_widget.delete("1.0", "end")
                body_widget.insert("1.0", result or "暂无 SOP 分析结果。")
                body_widget.configure(state="disabled")
            else:
                self._show_sop_document_dialog(dialog, result, analyze_current_sop_library, choose_and_analyze_sop_training)

        def open_sop_document() -> None:
            if self._open_sop_html_report(dialog):
                return
            getter = self.callbacks.get("get_sop_document")
            document = getter() if getter else "当前版本缺少 SOP 文档接口。"
            self._show_sop_document_dialog(dialog, document, analyze_current_sop_library, choose_and_analyze_sop_training)

        def save() -> None:
            selected_provider = provider_key_by_label.get(provider_var.get(), self.settings.api_provider)
            self.settings.api_provider = selected_provider
            self.settings.set_provider_api_key(selected_provider, api_var.get().strip())
            self.settings.base_url = base_var.get().strip()
            self.settings.model = model_var.get().strip() or self.settings.model
            self.settings.vision_provider = vision_key_by_label.get(
                vision_provider_var.get(),
                self.settings.vision_provider,
            )
            self.settings.vision_api_key = vision_api_var.get().strip()
            self.settings.vision_base_url = vision_base_var.get().strip()
            self.settings.vision_model = vision_model_var.get().strip() or self.settings.vision_model
            self.settings.custom_headers = headers_var.get().strip()
            self.settings.custom_body = body_editor.get("1.0", "end").strip()
            self.settings.custom_response_path = response_path_var.get().strip()
            self.settings.enable_web_search = False
            self.settings.managed_allowed_chats = parse_managed_allowed_chats(
                managed_allowlist_editor.get("1.0", "end")
            )
            self._set_managed_mode(managed_mode_var.get(), persist=False)
            self.settings.save()
            self.set_status(TEXT["saved"])
            dialog.destroy()

        self._primary_button(button_row, TEXT["save"], save, width=8).pack(side="right")

    def _show_sop_document_dialog(self, parent: tk.Toplevel, document: str, analyze_current_callback=None, import_analyze_callback=None) -> None:
        dialog = tk.Toplevel(parent)
        dialog.title(TEXT["analyze_sop"])
        dialog.geometry("760x620")
        dialog.minsize(620, 460)
        dialog.configure(bg=COLORS["page"])
        dialog.attributes("-topmost", True)
        dialog.transient(parent)

        header = tk.Frame(dialog, bg=COLORS["page"], padx=16, pady=12)
        header.pack(fill="x")
        tk.Label(
            header,
            text="SOP 分析文档",
            bg=COLORS["page"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 13, "bold"),
        ).pack(side="left")
        if import_analyze_callback:
            self._ghost_button(
                header,
                "导入记录重新分析",
                lambda: import_analyze_callback(dialog, body),
                width=16,
            ).pack(side="right", padx=(8, 0))
        if analyze_current_callback:
            self._ghost_button(
                header,
                "分析当前话术库",
                lambda: analyze_current_callback(dialog, body),
                width=16,
            ).pack(side="right", padx=(8, 0))
        self._ghost_button(header, "关闭", dialog.destroy, width=8).pack(side="right")

        body = scrolledtext.ScrolledText(
            dialog,
            wrap="word",
            relief="flat",
            borderwidth=0,
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 10),
            padx=14,
            pady=12,
        )
        body.insert("1.0", document or "暂无 SOP 分析结果。")
        body.configure(state="disabled")
        body.pack(fill="both", expand=True, padx=16, pady=(0, 16))

    def _open_sop_html_report(self, parent: tk.Toplevel | None = None) -> bool:
        getter = self.callbacks.get("get_sop_html_report")
        if not getter:
            return False
        try:
            report_path = getter()
            if not report_path:
                return False
            path = Path(report_path).resolve()
            if not path.exists():
                return False
            webbrowser.open(path.as_uri())
            return True
        except Exception as exc:
            messagebox.showerror(TEXT["import_failed"], f"打开话术流程 HTML 报告失败：{exc}", parent=parent)
            return False

    def _open_phrasebook_manager(self, parent: tk.Toplevel) -> None:
        getter = self.callbacks.get("get_phrasebook_libraries")
        example_getter = self.callbacks.get("get_phrasebook_examples")
        saver = self.callbacks.get("save_phrasebook_example")
        deleter = self.callbacks.get("delete_phrasebook_examples")
        if not getter or not example_getter or not saver or not deleter:
            messagebox.showerror(TEXT["settings"], "当前版本缺少话术库管理接口。", parent=parent)
            return

        dialog = tk.Toplevel(parent)
        dialog.title(TEXT["phrasebook_manager"])
        dialog.geometry("980x640")
        dialog.minsize(860, 560)
        dialog.configure(bg=COLORS["page"])
        dialog.attributes("-topmost", True)
        dialog.transient(parent)

        libraries: list[dict[str, object]] = []
        examples: list[dict[str, object]] = []
        current_hash = tk.StringVar(value="")
        library_var = tk.StringVar(value="")
        query_var = tk.StringVar(value="")
        summary_var = tk.StringVar(value="")

        top = tk.Frame(dialog, bg=COLORS["page"], padx=14, pady=12)
        top.pack(fill="x")
        toolbar = tk.Frame(dialog, bg=COLORS["page"], padx=14)
        toolbar.pack(fill="x", pady=(0, 8))

        def import_training_from_manager() -> None:
            zip_path = filedialog.askopenfilename(
                parent=dialog,
                title=TEXT["import_training"],
                filetypes=[
                    ("All files", "*.*"),
                    ("训练文件", "*.zip *.pdf *.txt *.md *.csv *.tsv *.json *.docx *.xlsx *.xls *.html *.htm"),
                    ("Zip files", "*.zip"),
                    ("PDF files", "*.pdf"),
                    ("Office files", "*.docx *.xlsx *.xls"),
                    ("Text files", "*.txt *.md *.csv *.tsv *.json *.html *.htm"),
                ],
            )
            if not zip_path:
                return
            self._run_llm_import_with_progress(zip_path, dialog, on_success=refresh_libraries)

        self._ghost_button(toolbar, TEXT["import_training"], import_training_from_manager, width=12).pack(side="left")

        tk.Label(top, text="话术库", bg=COLORS["page"], fg=COLORS["muted"]).pack(side="left")
        library_menu = tk.OptionMenu(top, library_var, "")
        library_menu.configure(
            bg="white",
            fg=COLORS["text"],
            relief="flat",
            highlightthickness=0,
            activebackground=COLORS["green_soft"],
            width=18,
        )
        library_menu.pack(side="left", padx=(8, 16))
        tk.Label(top, text="测试客户说法", bg=COLORS["page"], fg=COLORS["muted"]).pack(side="left")
        query_entry = tk.Entry(top, textvariable=query_var, relief="flat", bg="white", fg=COLORS["text"])
        query_entry.pack(side="left", fill="x", expand=True, padx=(8, 8), ipady=7)

        body = tk.Frame(dialog, bg=COLORS["page"], padx=14, pady=4)
        body.pack(fill="both", expand=True)
        left = tk.Frame(body, bg=COLORS["panel"], highlightbackground=COLORS["line"], highlightthickness=1)
        left.pack(side="left", fill="both", expand=False, ipadx=0, ipady=0)
        right = tk.Frame(body, bg=COLORS["page"])
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))

        tk.Label(left, text="命中结果 / 规则列表", bg=COLORS["panel"], fg=COLORS["text"], anchor="w").pack(fill="x", padx=10, pady=(10, 6))
        list_frame = tk.Frame(left, bg=COLORS["panel"])
        list_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        item_list = tk.Listbox(
            list_frame,
            width=36,
            relief="flat",
            bg="white",
            fg=COLORS["text"],
            selectbackground=COLORS["green"],
            selectforeground="white",
            activestyle="none",
            exportselection=False,
            selectmode="single",
        )
        item_list.pack(side="left", fill="both", expand=True)
        list_scroll = tk.Scrollbar(list_frame, orient="vertical", command=item_list.yview)
        list_scroll.pack(side="right", fill="y")
        item_list.configure(yscrollcommand=list_scroll.set)

        summary = tk.Label(right, textvariable=summary_var, bg=COLORS["page"], fg=COLORS["muted"], anchor="w", justify="left")
        summary.pack(fill="x", pady=(0, 8))
        tk.Label(right, text="匹配上下文 / 客户说法", bg=COLORS["page"], fg=COLORS["text"], anchor="w").pack(fill="x")
        cue_editor = scrolledtext.ScrolledText(right, height=9, relief="flat", bg="white", fg=COLORS["text"], wrap="word")
        cue_editor.pack(fill="both", expand=True, pady=(6, 12))
        tk.Label(right, text="推荐回复", bg=COLORS["page"], fg=COLORS["text"], anchor="w").pack(fill="x")
        reply_editor = scrolledtext.ScrolledText(right, height=7, relief="flat", bg="white", fg=COLORS["text"], wrap="word")
        reply_editor.pack(fill="both", expand=True, pady=(6, 10))

        bottom = tk.Frame(dialog, bg=COLORS["page"], padx=14, pady=12)
        bottom.pack(fill="x")

        def selected_library_key() -> str:
            label = library_var.get()
            for library in libraries:
                if str(library.get("label", "")) == label:
                    return str(library.get("key", ""))
            return ""

        def display_text(item: dict[str, object]) -> str:
            score = int(item.get("score", 0) or 0)
            reply = str(item.get("reply", "")).replace("\n", " ")
            decision = str(item.get("enterprise_decision_label", "")).strip()
            decision_prefix = f"[{decision}] " if decision else ""
            prefix = f"{score}% " if score else ""
            return (decision_prefix + prefix + reply)[:42]

        def clear_editor(message: str = "") -> None:
            current_hash.set("")
            cue_editor.delete("1.0", "end")
            reply_editor.delete("1.0", "end")
            summary_var.set(message)

        def populate_item(index: int) -> None:
            if index < 0 or index >= len(examples):
                clear_editor()
                return
            item = examples[index]
            current_hash.set(str(item.get("conversation_hash", "")))
            cue_editor.delete("1.0", "end")
            cue_editor.insert("1.0", str(item.get("cue", "")))
            reply_editor.delete("1.0", "end")
            reply_editor.insert("1.0", str(item.get("reply", "")))
            reasons = "、".join(str(reason) for reason in item.get("reasons", []) or [])
            score = int(item.get("score", 0) or 0)
            source = str(item.get("source_file") or item.get("library_name") or item.get("source") or "")
            decision = str(item.get("enterprise_decision_label", "")).strip()
            decision_reason = str(item.get("enterprise_decision_reason", "")).strip()
            decision_text = f"决策：{decision}    {decision_reason}" if decision else ""
            summary_var.set(
                f"{decision_text + '    ' if decision_text else ''}来源：{source}    命中：{score}%    原因：{reasons or '最近记录'}"
            )

        def refresh_examples() -> None:
            nonlocal examples
            try:
                examples = list(example_getter(selected_library_key(), query_var.get()))
            except Exception as exc:
                messagebox.showerror(TEXT["phrasebook_manager"], str(exc), parent=dialog)
                return
            item_list.delete(0, "end")
            for index, item in enumerate(examples):
                text = display_text(item)
                if edit_state["on"]:
                    text = ("☑ " if index in checked_indices else "☐ ") + text
                item_list.insert("end", text)
            if examples and not edit_state["on"]:
                item_list.selection_set(0)
                item_list.activate(0)
                populate_item(0)
            else:
                clear_editor("当前话术库没有可显示的话术。")

        def refresh_libraries() -> None:
            nonlocal libraries
            try:
                libraries = list(getter())
            except Exception as exc:
                messagebox.showerror(TEXT["phrasebook_manager"], str(exc), parent=dialog)
                return
            menu = library_menu["menu"]
            menu.delete(0, "end")
            if not libraries:
                library_var.set(TEXT["phrasebook_empty"])
                clear_editor(TEXT["phrasebook_empty"])
                return
            for library in libraries:
                label = f"{library.get('name', library.get('key', '话术库'))} ({library.get('count', 0)})"
                library["label"] = label
                menu.add_command(label=label, command=lambda value=label: (library_var.set(value), refresh_examples()))
            library_var.set(str(libraries[0].get("label", "")))
            refresh_examples()

        def on_select(_event: object = None) -> None:
            selection = item_list.curselection()
            if not selection:
                return
            index = int(selection[0])
            if edit_state["on"]:
                # 编辑模式：点击 = 勾选/取消勾选（方形选项框），不进入详情
                if index in checked_indices:
                    checked_indices.discard(index)
                    item_list.selection_clear(index)
                else:
                    checked_indices.add(index)
                    item_list.selection_set(index)
                refresh_item_text(index)
                return
            populate_item(index)

        def save_current() -> None:
            target = current_hash.get()
            cue = cue_editor.get("1.0", "end").strip()
            reply = reply_editor.get("1.0", "end").strip()
            if not target or not reply:
                messagebox.showwarning(TEXT["empty_title"], "请选择一条话术并填写回复。", parent=dialog)
                return
            try:
                result = saver(target, cue, reply)
            except Exception as exc:
                messagebox.showerror(TEXT["phrasebook_manager"], str(exc), parent=dialog)
                return
            self.set_status(str(result))
            messagebox.showinfo(TEXT["phrasebook_saved"], str(result), parent=dialog)
            refresh_libraries()

        item_list.bind("<<ListboxSelect>>", on_select)
        query_entry.bind("<Return>", lambda _event: refresh_examples())
        self._ghost_button(top, "测试命中", refresh_examples, width=10).pack(side="left")

        # 编辑模式：点"编辑"启用（按钮变绿显示"取消编辑"），列表项前出现方形勾选框 ☐/☑ 多选
        edit_state = {"on": False}
        checked_indices: set[int] = set()

        def refresh_item_text(index: int) -> None:
            if index < 0 or index >= len(examples):
                return
            item = examples[index]
            text = display_text(item)
            if edit_state["on"]:
                item_list.delete(index)
                item_list.insert(index, ("☑ " if index in checked_indices else "☐ ") + text)
                if index in checked_indices:
                    item_list.selection_set(index)
            else:
                item_list.delete(index)
                item_list.insert(index, text)

        def toggle_edit() -> None:
            edit_state["on"] = not edit_state["on"]
            checked_indices.clear()
            item_list.selection_clear(0, "end")
            if edit_state["on"]:
                edit_button.configure(
                    bg=COLORS["green"],
                    fg="white",
                    activebackground=COLORS["green_dark"],
                    activeforeground="white",
                    text="取消编辑",
                )
                edit_button.unbind("<Enter>")
                edit_button.unbind("<Leave>")
            else:
                edit_button.configure(
                    bg=COLORS["panel"],
                    fg=COLORS["text"],
                    activebackground=COLORS["bubble"],
                    activeforeground=COLORS["text"],
                    text="编辑",
                )
                edit_button.bind("<Enter>", lambda _e: edit_button.configure(bg=COLORS["bubble"]))
                edit_button.bind("<Leave>", lambda _e: edit_button.configure(bg=COLORS["panel"]))
            refresh_examples()

        def delete_checked() -> None:
            if not edit_state["on"]:
                messagebox.showwarning(TEXT["empty_title"], "请先点击“编辑”，进入编辑模式后勾选要删除的问答。", parent=dialog)
                return
            if not checked_indices:
                messagebox.showwarning(TEXT["empty_title"], "请先勾选要删除的问答（点击问答开头的方框）。", parent=dialog)
                return
            if not messagebox.askyesno(
                TEXT["phrasebook_manager"],
                f"确定删除勾选的 {len(checked_indices)} 条问答吗？删除后不可恢复。",
                parent=dialog,
            ):
                return
            hashes = [str(examples[index].get("conversation_hash", "")) for index in sorted(checked_indices)]
            try:
                result = deleter(hashes)
            except Exception as exc:
                messagebox.showerror(TEXT["phrasebook_manager"], str(exc), parent=dialog)
                return
            self.set_status(str(result))
            checked_indices.clear()
            refresh_libraries()

        edit_button = self._ghost_button(toolbar, "编辑", toggle_edit, width=10)
        edit_button.pack(side="left", padx=(8, 0))
        self._ghost_button(bottom, "刷新", refresh_libraries, width=10).pack(side="left")
        self._primary_button(bottom, "删除", delete_checked, width=10).pack(side="left", padx=(8, 0))
        self._primary_button(bottom, "保存当前话术", save_current, width=14).pack(side="right")
        refresh_libraries()

    def mainloop(self) -> None:
        self.root.mainloop()


class Tooltip:
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.tip_window: tk.Toplevel | None = None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def show(self, _event=None) -> None:
        if self.tip_window is not None:
            return
        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + 22
        self.tip_window = tk.Toplevel(self.widget)
        self.tip_window.wm_overrideredirect(True)
        self.tip_window.attributes("-topmost", True)
        self.tip_window.geometry(f"+{x}+{y}")
        label = tk.Label(
            self.tip_window,
            text=self.text,
            justify="left",
            bg="#111827",
            fg="white",
            relief="flat",
            padx=10,
            pady=7,
            wraplength=260,
            font=("Microsoft YaHei UI", 9),
        )
        label.pack()

    def hide(self, _event=None) -> None:
        if self.tip_window is not None:
            self.tip_window.destroy()
            self.tip_window = None
