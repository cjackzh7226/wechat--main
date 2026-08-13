from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata


ROOT = Path(SPECPATH)
datas = [
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "wecom_5_calibration.json"), "."),
]
binaries = []
hiddenimports = [
    "release_manifest",
    "self_check",
    "version",
    "win32timezone",
]

for package in (
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
    "openpyxl",
    "pypdf",
):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

for distribution in (
    "requests",
    "PyAutoGUI",
    "pyperclip",
    "pywin32",
    "Pillow",
    "rapidocr-onnxruntime",
    "onnxruntime",
    "xlrd",
    "openpyxl",
    "pypdf",
    "opencv-python",
):
    datas += copy_metadata(distribution)

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="聊小智",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(ROOT / "assets" / "app_icon_clean.ico"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="聊小智",
)
