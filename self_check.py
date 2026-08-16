from __future__ import annotations

import os
import platform
import sys
import traceback
from datetime import datetime
from pathlib import Path


def run_self_check() -> Path:
    report_path = _report_path()
    lines: list[str] = []
    lines.append("聊小智便携版自检报告")
    lines.append(f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"系统：{platform.platform()}")
    lines.append(f"Python/打包运行时：{sys.version.split()[0]}")
    lines.append(f"程序目录：{_app_dir()}")
    _check_import(lines, "xlrd", "xls 表格训练导入")
    lines.append("")

    _check_import(lines, "requests", "API 请求库")
    _check_import(lines, "pyautogui", "截图和键鼠自动化")
    _check_import(lines, "pyperclip", "剪贴板发送")
    _check_import(lines, "win32gui", "微信窗口检测")
    _check_import(lines, "PIL", "图片处理")
    _check_import(lines, "cv2", "OpenCV 图像处理")
    _check_import(lines, "onnxruntime", "ONNX OCR 推理运行库")
    _check_import(lines, "rapidocr_onnxruntime", "RapidOCR 本地中文 OCR")
    lines.append("")

    _check_rapidocr_assets(lines)
    _check_rapidocr_init(lines)
    _check_screenshot(lines)
    _check_config_paths(lines)
    _check_wechat_window(lines)

    report_path.write_text("\n".join(lines), encoding="utf-8-sig")
    return report_path


def run_api_check() -> Path:
    report_path = _app_dir() / "接口检测报告.txt"
    lines: list[str] = []
    lines.append("聊小智 API 接口检测报告")
    lines.append(f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    try:
        from memory import StyleMemory
        from reply_engine import ReplyEngine
        from settings import AppSettings

        settings = AppSettings().load()
        lines.append(f"文本生成接口：{settings.api_provider}")
        lines.append(f"Base URL：{settings.base_url}")
        lines.append(f"模型：{settings.model}")
        lines.append(f"API Key 状态：{'已填写' if settings.api_key.strip() else '未填写'}")
        engine = ReplyEngine(settings, StyleMemory().load())
        replies = engine.generate("[对方] 这事还挺有意思的\n[对方] 你觉得呢", "接口检测")
        lines.append(f"生成来源：{engine.last_source}")
        lines.append(f"接口错误：{engine.last_error or '无'}")
        lines.append("候选回复：")
        for index, reply in enumerate(replies, 1):
            lines.append(f"{index}. {reply}")
    except Exception:
        lines.append("检测异常：")
        lines.append(traceback.format_exc())
    report_path.write_text("\n".join(lines), encoding="utf-8-sig")
    return report_path


def _report_path() -> Path:
    return _app_dir() / "自检报告.txt"


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _check_import(lines: list[str], module_name: str, label: str) -> None:
    try:
        module = __import__(module_name)
        version = getattr(module, "__version__", "")
        suffix = f" {version}" if version else ""
        lines.append(f"[通过] {label}：{module_name}{suffix}")
    except Exception as exc:
        lines.append(f"[失败] {label}：无法导入 {module_name}，{exc}")


def _check_rapidocr_assets(lines: list[str]) -> None:
    try:
        import rapidocr_onnxruntime

        root = Path(rapidocr_onnxruntime.__file__).resolve().parent
        models = sorted((root / "models").glob("*.onnx"))
        if len(models) >= 3:
            lines.append("[通过] OCR 模型文件：已找到")
            for model in models:
                lines.append(f"       - {model.name} ({model.stat().st_size} bytes)")
        else:
            lines.append(f"[失败] OCR 模型文件：只找到 {len(models)} 个 .onnx，请重新打包")
    except Exception as exc:
        lines.append(f"[失败] OCR 模型文件检查异常：{exc}")


def _check_rapidocr_init(lines: list[str]) -> None:
    try:
        from rapidocr_onnxruntime import RapidOCR

        RapidOCR()
        lines.append("[通过] OCR 引擎初始化：RapidOCR 可以启动")
    except Exception:
        lines.append("[失败] OCR 引擎初始化：RapidOCR 启动失败")
        lines.append(traceback.format_exc())


def _check_screenshot(lines: list[str]) -> None:
    try:
        import pyautogui

        image = pyautogui.screenshot(region=(0, 0, 40, 40))
        lines.append(f"[通过] 屏幕截图权限：成功截取 {image.width}x{image.height}")
    except Exception:
        lines.append("[失败] 屏幕截图权限：无法截图，可能被安全软件/远程桌面/系统权限限制")
        lines.append(traceback.format_exc())


def _check_config_paths(lines: list[str]) -> None:
    try:
        from memory import default_memory_path
        from settings import AppSettings

        settings = AppSettings().load()
        config_path = settings.config_path
        memory_path = default_memory_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        memory_path.parent.mkdir(parents=True, exist_ok=True)
        test_file = config_path.parent / ".write_test"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink(missing_ok=True)
        key_state = "已填写" if settings.api_key else "未填写"
        lines.append(f"[通过] 设置目录可写：{config_path.parent}")
        lines.append(f"[信息] 文本生成接口：{settings.api_provider}")
        lines.append(f"[信息] Base URL：{settings.base_url or '未填写'}")
        lines.append(f"[信息] 模型：{settings.model or '未填写'}")
        lines.append(f"[信息] API Key 状态：{key_state}")
        lines.append(f"[信息] 记忆文件位置：{memory_path}")
    except Exception:
        lines.append("[失败] 设置/记忆目录：无法写入")
        lines.append(traceback.format_exc())


def _check_wechat_window(lines: list[str]) -> None:
    try:
        from wechat_window import WeChatWindowDetector

        detector = WeChatWindowDetector()
        window = detector.foreground_chat() or detector.any_chat_window()
        if window:
            lines.append(f"[通过] 微信窗口检测：{window.title}，{window.rect.width}x{window.rect.height}")
        else:
            lines.append("[提示] 微信窗口检测：没有找到微信聊天窗口。请先打开 PC 微信并点进一个聊天。")
    except Exception:
        lines.append("[失败] 微信窗口检测异常")
        lines.append(traceback.format_exc())


if __name__ == "__main__":
    path = run_self_check()
    os.startfile(path)
