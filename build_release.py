from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


APP_NAME = "聊小智"
ROOT = Path(__file__).resolve().parent
DIST_ROOT = ROOT / "dist"
PACKAGE_DIR = DIST_ROOT / APP_NAME
ZIP_PATH = DIST_ROOT / f"{APP_NAME}_便携版.zip"
REQUIREMENTS = ROOT / "requirements.txt"
ICON_PATH = ROOT / "assets" / "app_icon_clean.ico"

REQUIRED_IMPORTS = [
    "requests",
    "pyautogui",
    "pyperclip",
    "win32gui",
    "PIL",
    "rapidocr_onnxruntime",
    "onnxruntime",
    "cv2",
    "xlrd",
    "enterprise_kb",
]

COLLECT_ALL_PACKAGES = [
    "rapidocr_onnxruntime",
    "onnxruntime",
    "cv2",
    "pyautogui",
    "pyscreeze",
    "pyperclip",
    "PIL",
    "pygetwindow",
    "mouseinfo",
    "pymsgbox",
    "pyclipper",
    "shapely",
    "xlrd",
]

# main.py 在运行时按命令行参数导入这些模块；PyInstaller 无法稳定推断此类延迟导入。
HIDDEN_IMPORTS = [
    "win32timezone",
    "self_check",
]

README_TEXT = """聊小智 - 便携版使用说明

一、启动方式
1. 先解压整个“聊小智”文件夹，不要只把 exe 单独拖出来。
2. 双击“聊小智.exe”即可打开；也可以双击“启动.bat”。
3. 打开微信、企业微信或 QQ，点进一个具体聊天窗口，再点“识别当前聊天”。

二、首次配置 API Key
1. 点右上角“设置”。
2. 在“文本接口配置”里选择模型公司，例如 DeepSeek、Zhipu、通义千问、豆包、Kimi、硅基流动等。
3. 普通用户只需要填写自己的 API Key，Base URL 和模型名会自动带出默认值。
4. 点“保存”后即可使用。

三、基本使用
1. 手动模式：点“识别当前聊天”，程序会截图、OCR，并生成 3 条候选回复。
2. 点候选右侧“发送”，会把该条回复发送到当前聊天。
3. 半自动会自动识别并生成候选，但不会自动发送；全自动会自动识别、生成并发送。
4. 只有手动确认发送的候选回复，才会写入长期记忆；全自动发送不会写入记忆。

四、回复来源
1. 大模型：只调用模型接口，根据当前上下文生成。
2. 话术库：优先检索本地导入或积累的话术；没有合适数据时会用大模型兜底。
3. 大模型 + 话术库：先检索相似话术，再交给大模型理解当前语境生成。

五、导入话术库
1. 支持 zip、txt、md、csv、tsv、json、docx、xlsx、xls、html 等格式。
2. 支持 A/B 对话格式：
   A: 对方说的话
   B: 你希望程序学习的回复
3. 也支持客服宝类 Excel 话术格式：
   一级分类 | 二级分类 | 话术标题 | 话术内容
4. 点主界面右上角“管理话术”，选择导入文件或 zip。

六、企业年报知识库
1. 上传包含“模块”、机构类型列和“问题：…答：…”单元格的年报问答 Excel 时，程序会自动识别为企业年报知识库。
2. 程序会保留模块、机构类型和原单元格来源；“测试命中”和实际回复使用同一套本地候选排序。
3. 第一名有足够匹配证据、明显领先第二名且没有时间或操作冲突时，原样给出官方答案；并列或不可靠时只回复未命中说明。

七、常见问题
1. OCR 不需要 API Key，API Key 只用于生成回复。
2. 如果 OCR 识别失败，请确认聊天窗口没有最小化，且聊天区域没有被遮挡。
3. 如果安全软件提示，请允许程序截图、剪贴板和键鼠自动化权限。
4. 便携包内置运行环境，客户电脑不需要安装 Python、OCR 或表格依赖。
"""


def main() -> None:
    if not _pyinstaller_available():
        print("未检测到 PyInstaller，正在安装...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    _install_runtime_requirements()
    _verify_runtime_requirements()

    if PACKAGE_DIR.exists():
        shutil.rmtree(PACKAGE_DIR)
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--name",
        APP_NAME,
        "--icon",
        str(ICON_PATH),
        "--add-data",
        f"{ROOT / 'assets'};assets",
    ]
    for module in HIDDEN_IMPORTS:
        cmd.extend(["--hidden-import", module])
    for package in COLLECT_ALL_PACKAGES:
        cmd.extend(["--collect-all", package])
    cmd.append(str(ROOT / "main.py"))

    subprocess.check_call(cmd, cwd=ROOT)

    _write_release_files()
    _remove_private_data()
    shutil.make_archive(str(ZIP_PATH.with_suffix("")), "zip", DIST_ROOT, APP_NAME)
    print(f"打包完成：{PACKAGE_DIR}")
    print(f"便携压缩包：{ZIP_PATH}")


def _write_release_files() -> None:
    (PACKAGE_DIR / "使用说明.txt").write_text(README_TEXT, encoding="utf-8-sig")
    (PACKAGE_DIR / "启动.bat").write_text(
        f'@echo off\r\ncd /d "%~dp0"\r\nstart "" "{APP_NAME}.exe"\r\n',
        encoding="gbk",
    )
    (PACKAGE_DIR / "便携包自检.bat").write_text(
        "@echo off\r\n"
        'cd /d "%~dp0"\r\n'
        f'"{APP_NAME}.exe" --self-check\r\n'
        'start "" "自检报告.txt"\r\n',
        encoding="gbk",
    )
    (PACKAGE_DIR / "接口检测.bat").write_text(
        "@echo off\r\n"
        'cd /d "%~dp0"\r\n'
        f'"{APP_NAME}.exe" --api-check\r\n'
        'start "" "接口检测报告.txt"\r\n',
        encoding="gbk",
    )
    _write_ocr_check_note()
    _write_clean_marker()


def _remove_private_data() -> None:
    for name in ("user_data", "memory.json", "settings.json", "vector_memory.json"):
        target = PACKAGE_DIR / name
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()


def _write_clean_marker() -> None:
    marker = PACKAGE_DIR / "重要说明_首次打开先填APIKey.txt"
    marker.write_text(
        "这个便携包不包含打包者本机的 API Key、设置、聊天记忆或话术库。\n"
        "首次打开后，请在右上角“设置”里选择模型公司，并填写自己的 API Key。\n"
        "Base URL 和模型名会自动填入默认值，普通用户不用修改。\n"
        "如需风格训练或话术库，请在设置中导入自己的聊天记录或话术表。\n",
        encoding="utf-8-sig",
    )


def _write_ocr_check_note() -> None:
    note = PACKAGE_DIR / "OCR识别失败排查.txt"
    note.write_text(
        "OCR 不需要 API Key，API Key 只用于生成回复。\n"
        "如果换电脑后 OCR 识别失败，请优先检查：\n"
        "1. 必须解压整个便携版文件夹后运行，不要只单独复制 exe。\n"
        "2. 聊天窗口不能最小化，聊天区域要露出来。\n"
        "3. Windows 安全软件不要拦截图权限、剪贴板权限或键鼠自动化权限。\n"
        "4. 程序目录下的 _internal 文件夹不能删除；里面包含 RapidOCR、ONNXRuntime、OpenCV 和 OCR 模型。\n"
        "5. 如果状态栏提示缺少 pyautogui、RapidOCR、cv2 或 ONNXRuntime，说明便携包不完整，请重新打包。\n",
        encoding="utf-8-sig",
    )


def _install_runtime_requirements() -> None:
    if REQUIREMENTS.exists():
        print("正在安装/补齐运行依赖...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS)])


def _verify_runtime_requirements() -> None:
    missing: list[str] = []
    for module_name in REQUIRED_IMPORTS:
        try:
            __import__(module_name)
        except Exception as exc:
            missing.append(f"{module_name}: {exc}")
    if missing:
        joined = "\n".join(missing)
        raise RuntimeError(f"打包前依赖校验失败，便携包会不可用：\n{joined}")


def _pyinstaller_available() -> bool:
    try:
        subprocess.check_call(
            [sys.executable, "-m", "PyInstaller", "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


if __name__ == "__main__":
    main()
