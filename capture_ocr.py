from __future__ import annotations

import ctypes
import re
import unicodedata
from dataclasses import dataclass

from settings import AppSettings
from wechat_window import WeChatWindow


try:
    import pyautogui
except Exception:  # pragma: no cover
    pyautogui = None

try:
    import win32con
    import win32gui
    import win32ui
    from PIL import Image
except Exception:  # pragma: no cover
    win32con = None
    win32gui = None
    win32ui = None
    Image = None


@dataclass
class ChatLine:
    role: str
    text: str
    y: float


@dataclass
class VoiceBubble:
    role: str
    seconds: int
    left: float
    right: float
    y: float


class ChatOCR:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self._engine = None
        self._engine_error = ""
        self.last_capture_origin = (0, 0)

    @property
    def engine_error(self) -> str:
        return self._engine_error

    def capture_and_read(self, wechat: WeChatWindow) -> tuple[str, object | None]:
        image = self.capture(wechat)
        if image is None:
            return "", None
        return self.read_image(image), image

    def capture(self, wechat: WeChatWindow, allow_screen_fallback: bool = True):
        platform = getattr(wechat, "platform", None)
        try:
            full = _capture_window_bitmap(wechat)
            if full is None:
                if not allow_screen_fallback:
                    self._engine_error = "窗口直接捕获失败，等待屏幕截图兜底。"
                    return None
                if pyautogui is None:
                    self._engine_error = "缺少 pyautogui，无法截图。"
                    return None
                full = pyautogui.screenshot(region=(wechat.rect.left, wechat.rect.top, wechat.rect.width, wechat.rect.height))
            cropped, origin = _crop_detected_chat_area_with_origin(
                full,
                getattr(platform, "key", ""),
                getattr(platform, "chat_area", self.settings.chat_area),
            )
            self.last_capture_origin = (int(wechat.rect.left) + int(origin[0]), int(wechat.rect.top) + int(origin[1]))
            return cropped
        except Exception as exc:
            self._engine_error = f"截图失败：{exc}"
            return None

    def read_image(self, image) -> str:
        results = self._recognize(image)
        if not results:
            return ""
        lines = self._to_chat_lines(results, image.width, image)
        recent = lines[-10:]
        return "\n".join(f"[{item.role}] {item.text}" for item in recent if item.text.strip())

    def read_visible_text(self, image, limit: int = 8) -> str:
        results = self._recognize(image)
        if not results:
            return ""
        rows = []
        for item in sorted(results, key=lambda value: (float(value.get("y", 0)), float(value.get("x", 0)))):
            text = re.sub(r"\s+", " ", str(item.get("text", "")).strip())
            if text and not _is_noise_text(text):
                rows.append(text)
            if len(rows) >= limit:
                break
        return "\n".join(rows)

    def read_chat_title(self, image, limit: int = 3) -> str:
        """读取会话身份标题，排除最右侧操作按钮并恢复可见的 ``- @微信``。"""
        results = self._recognize(image)
        if not results:
            return ""
        try:
            image_width = max(1.0, float(image.size[0]))
        except (AttributeError, IndexError, TypeError, ValueError):
            return ""
        rows: list[str] = []
        for item in sorted(
            results,
            key=lambda value: (float(value.get("y", 0)), float(value.get("x", 0))),
        ):
            left = float(item.get("left", item.get("x", 0)))
            if left >= image_width * 0.90:
                continue
            text = re.sub(r"\s+", " ", str(item.get("text", "")).strip())
            if not text or _is_noise_text(text):
                continue
            restored = _restore_visible_dash_sender(
                item,
                text,
                image,
                leading_search_px=16,
            )
            if restored != text and restored.startswith("-@"):
                restored = f"- {restored[1:]}"
            rows.append(restored)
            if len(rows) >= limit:
                break
        return "\n".join(rows)

    def latest_voice_bubble(self, image) -> VoiceBubble | None:
        results = self._recognize(image, keep_voice=True)
        voices: list[VoiceBubble] = []
        for item in sorted(results, key=lambda value: float(value.get("y", 0))):
            seconds = _voice_duration_seconds(str(item.get("text", "")))
            if seconds is None:
                continue
            role = _infer_chat_role(item, image.width, image)
            voices.append(
                VoiceBubble(
                    role=role,
                    seconds=seconds,
                    left=float(item.get("left", item.get("x", 0))),
                    right=float(item.get("right", item.get("x", 0))),
                    y=float(item.get("y", 0)),
                )
            )
        return voices[-1] if voices else None

    def _recognize(self, image, keep_voice: bool = False) -> list[dict]:
        engine = self._get_engine()
        if engine is None:
            return []
        try:
            raw = engine(self._prepare_image(image))
        except Exception as exc:
            self._engine_error = f"OCR 识别失败：{exc}"
            return []
        return self._normalize_ocr_result(raw, keep_voice=keep_voice)

    @staticmethod
    def _prepare_image(image):
        try:
            import numpy as np

            return np.array(image.convert("RGB"))
        except Exception:
            return image

    def _get_engine(self):
        if self._engine is not None:
            return self._engine
        try:
            from rapidocr_onnxruntime import RapidOCR

            ocr = RapidOCR()
            self._engine = lambda image: ocr(image)
            return self._engine
        except Exception:
            pass
        try:
            from rapidocr import RapidOCR

            ocr = RapidOCR()
            self._engine = lambda image: ocr(image)
            return self._engine
        except Exception as exc:
            self._engine_error = (
                "未安装 RapidOCR。请运行 pip install rapidocr-onnxruntime，"
                f"或查看 requirements.txt。详细：{exc}"
            )
            return None

    @staticmethod
    def _normalize_ocr_result(raw, keep_voice: bool = False) -> list[dict]:
        if isinstance(raw, tuple):
            raw = raw[0]
        items: list[dict] = []
        for entry in raw or []:
            try:
                box, text = entry[0], str(entry[1]).strip()
                score = float(entry[2]) if len(entry) > 2 else 1.0
            except Exception:
                continue
            if not text or score < 0.25:
                continue
            is_recall = _is_recall_message_text(text)
            if (
                _is_noise_text(text)
                and not is_recall
                and not (keep_voice and _voice_duration_seconds(text) is not None)
            ):
                continue
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
            items.append(
                {
                    "text": text,
                    "x": sum(xs) / len(xs),
                    "left": min(xs),
                    "right": max(xs),
                    "y": sum(ys) / len(ys),
                }
            )
        return items

    @staticmethod
    def _to_chat_lines(items: list[dict], image_width: int, image=None) -> list[ChatLine]:
        items = sorted(items, key=lambda item: item["y"])
        merged: list[dict] = []
        for item in items:
            text = item["text"].strip()
            is_recall = _is_recall_message_text(text)
            if _is_noise_text(text) and not is_recall:
                continue
            if (
                merged
                and abs(item["y"] - merged[-1]["y"]) < 18
                and not is_recall
                and not _is_recall_message_text(str(merged[-1].get("text", "")))
                and not _separate_group_sender_from_message(
                    str(merged[-1].get("text", "")),
                    text,
                )
            ):
                if item["x"] < merged[-1]["x"]:
                    merged[-1]["text"] = f"{text} {merged[-1]['text']}"
                else:
                    merged[-1]["text"] = f"{merged[-1]['text']} {text}"
                merged[-1]["y"] = (merged[-1]["y"] + item["y"]) / 2
                merged[-1]["x"] = (merged[-1]["x"] + item["x"]) / 2
                merged[-1]["left"] = min(float(merged[-1].get("left", merged[-1]["x"])), float(item.get("left", item["x"])))
                merged[-1]["right"] = max(float(merged[-1].get("right", merged[-1]["x"])), float(item.get("right", item["x"])))
            else:
                merged.append(dict(item))

        merged = _merge_wrapped_ocr_items(merged, image_width)

        lines: list[ChatLine] = []
        for item in merged:
            is_recall = _is_recall_message_text(str(item.get("text", "")))
            if not is_recall and image is not None and _is_embedded_visual_text(item, image):
                continue
            text = re.sub(r"\s+", " ", item["text"]).strip()
            if text and (is_recall or not _is_noise_text(text)):
                text = _restore_visible_dash_sender(item, text, image)
                if is_recall:
                    role = "系统"
                else:
                    role = "发送者" if _is_group_sender_header(text) else _infer_chat_role(item, image_width, image)
                lines.append(ChatLine(role=role, text=text, y=item["y"]))
        return _merge_wrapped_chat_lines(lines)


def _voice_duration_seconds(text: str) -> int | None:
    cleaned = re.sub(r"\s+", "", str(text or "").strip())
    if not cleaned:
        return None
    cleaned = (
        cleaned.replace("＂", '"')
        .replace("”", '"')
        .replace("“", '"')
        .replace("″", '"')
        .replace("′", "'")
        .replace("’", "'")
        .replace("‘", "'")
    )
    match = re.search(r"(\d{1,3})(?:秒|s|S|\"|'|''|`)", cleaned)
    if not match:
        return None
    seconds = int(match.group(1))
    if 1 <= seconds <= 180:
        return seconds
    return None


def _merge_wrapped_ocr_items(items: list[dict], image_width: int) -> list[dict]:
    merged: list[dict] = []
    for item in items:
        if merged and _looks_like_wrapped_ocr_continuation(merged[-1], item, image_width):
            previous = merged[-1]
            previous["text"] = f"{previous['text']}{item['text']}"
            previous["y"] = item["y"]
            previous["wrap_right"] = max(float(previous.get("wrap_right", previous.get("right", previous["x"]))), float(item.get("right", item["x"])))
            previous["wrap_left"] = min(float(previous.get("wrap_left", previous.get("left", previous["x"]))), float(item.get("left", item["x"])))
            continue
        copy = dict(item)
        copy["wrap_left"] = float(copy.get("left", copy.get("x", 0)))
        copy["wrap_right"] = float(copy.get("right", copy.get("x", 0)))
        merged.append(copy)
    return merged


def _looks_like_wrapped_ocr_continuation(previous: dict, current: dict, image_width: int) -> bool:
    gap = float(current.get("y", 0)) - float(previous.get("y", 0))
    if gap <= 0 or gap > 34:
        return False
    previous_text = str(previous.get("text", "")).strip()
    current_text = str(current.get("text", "")).strip()
    if _is_recall_message_text(previous_text) or _is_recall_message_text(current_text):
        return False
    if _separate_group_sender_from_message(previous_text, current_text):
        return False
    if len(previous_text) < 18 and len(current_text) < 8:
        return False
    width = max(1.0, float(image_width or 1))
    previous_left = float(previous.get("wrap_left", previous.get("left", previous.get("x", 0))))
    previous_right = float(previous.get("wrap_right", previous.get("right", previous.get("x", previous_left))))
    current_left = float(current.get("left", current.get("x", 0)))
    current_right = float(current.get("right", current.get("x", current_left)))
    overlap = min(previous_right, current_right) - max(previous_left, current_left)
    if overlap >= min(50.0, max(0.0, current_right - current_left) * 0.35):
        return True
    return current_left >= previous_left - width * 0.06 and current_right <= previous_right + width * 0.18


def _capture_window_bitmap(window):
    if win32gui is None or win32ui is None or Image is None:
        return None
    hwnd = int(getattr(window, "hwnd", 0) or 0)
    width = int(getattr(getattr(window, "rect", None), "width", 0) or 0)
    height = int(getattr(getattr(window, "rect", None), "height", 0) or 0)
    if not hwnd or width <= 0 or height <= 0:
        return None

    hwnd_dc = mfc_dc = save_dc = bitmap = None
    try:
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
        save_dc.SelectObject(bitmap)
        result = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 2)
        if not result:
            result = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 0)
        if not result:
            return None
        bmp_info = bitmap.GetInfo()
        bmp_bytes = bitmap.GetBitmapBits(True)
        image = Image.frombuffer(
            "RGB",
            (bmp_info["bmWidth"], bmp_info["bmHeight"]),
            bmp_bytes,
            "raw",
            "BGRX",
            0,
            1,
        )
        return image.copy()
    except Exception:
        return None
    finally:
        try:
            if bitmap is not None:
                win32gui.DeleteObject(bitmap.GetHandle())
        except Exception:
            pass
        try:
            if save_dc is not None:
                save_dc.DeleteDC()
            if mfc_dc is not None:
                mfc_dc.DeleteDC()
            if hwnd_dc is not None:
                win32gui.ReleaseDC(hwnd, hwnd_dc)
        except Exception:
            pass


def _crop_detected_chat_area(image, platform_key: str, fallback_area) -> object:
    left, top, right, bottom = _detected_chat_area_box(image, platform_key, fallback_area)
    return image.crop((left, top, right, bottom))


def _crop_detected_chat_area_with_origin(image, platform_key: str, fallback_area) -> tuple[object, tuple[int, int]]:
    left, top, right, bottom = _detected_chat_area_box(image, platform_key, fallback_area)
    return image.crop((left, top, right, bottom)), (left, top)


def _detected_chat_area_box(image, platform_key: str, fallback_area) -> tuple[int, int, int, int]:
    width, height = image.size
    left = _detect_chat_left_boundary(image, platform_key)
    if left is None:
        left = int(width * fallback_area.x_left)
    top = int(height * fallback_area.y_top)
    right = int(width * fallback_area.x_right)
    detected_right = _detect_chat_right_boundary(image, left, right, platform_key)
    if detected_right is not None:
        right = detected_right
    bottom = _detect_chat_bottom_boundary(image, left, right, platform_key)
    fallback_bottom = int(height * fallback_area.y_bottom)
    if bottom is None:
        bottom = fallback_bottom
    elif platform_key == "wecom" and bottom > fallback_bottom:
        # 企业微信 4.x 的“输入框分隔线”下方还有工具栏（@全员、表情等按钮），
        # 检测到的线会把工具栏文字包进来，收紧到裁剪比例以内。
        bottom = fallback_bottom
    left = max(0, min(left, width - 1))
    top = max(0, min(top, height - 1))
    right = max(left + 1, min(right, width))
    bottom = max(top + 1, min(bottom, height))
    return left, top, right, bottom


def _detect_chat_right_boundary(
    image,
    left: int,
    fallback_right: int,
    platform_key: str,
) -> int | None:
    """Detect the durable divider before an expanded WeCom member sidebar."""
    if platform_key != "wecom":
        return None
    width, height = image.size
    scan_left = max(int(width * 0.60), int(left) + int(width * 0.30))
    scan_right = min(int(fallback_right), int(width * 0.97))
    if scan_right - scan_left < 2:
        return None
    hits: list[int] = []
    strong_single_pixel_hits: list[int] = []
    for x in range(scan_left, scan_right):
        total = 0
        divider = 0
        for y in range(int(height * 0.12), int(height * 0.96), 4):
            r, g, b = image.getpixel((x, y))[:3]
            total += 1
            if (
                210 <= r <= 242
                and 210 <= g <= 242
                and 210 <= b <= 242
                and max(r, g, b) - min(r, g, b) <= 8
            ):
                divider += 1
        if not total:
            continue
        divider_ratio = divider / total
        if divider_ratio >= 0.82:
            hits.append(x)
        # 企业微信 4.x 的群公告/群成员侧栏在部分 DPI 与主题下只有一列
        # 物理像素宽。此时不能继续要求相邻两列同时命中；但单列必须几乎
        # 贯穿整个消息区域，避免把气泡边缘或普通文字竖线误判成侧栏边界。
        if divider_ratio >= 0.96:
            strong_single_pixel_hits.append(x)
    candidates = list(strong_single_pixel_hits)
    for index in range(1, len(hits)):
        if hits[index] == hits[index - 1] + 1:
            candidates.append(hits[index - 1])
    return min(candidates) if candidates else None


def _detect_chat_left_boundary(image, platform_key: str) -> int | None:
    if platform_key not in {"wechat", "wecom", "qq"}:
        return None
    width, height = image.size
    min_x = max(80, int(width * 0.08))
    max_x = min(int(width * 0.55), width - 80)
    if max_x <= min_x:
        return None

    best_x: int | None = None
    best_score = 0.0
    for x in range(min_x, max_x, 4):
        right_score = _white_background_score(image, x + 8, min(width - 1, x + 88))
        left_score = _white_background_score(image, max(0, x - 88), max(1, x - 8))
        transition_score = right_score - left_score
        if right_score >= 0.68 and transition_score > best_score:
            best_score = transition_score
            best_x = x
    if best_x is not None and best_score >= 0.12:
        return best_x

    for x in range(min_x, max_x, 4):
        left_score = _white_background_score(image, max(0, x - 88), max(1, x - 8))
        if left_score < 0.50 and _white_background_score(image, x, min(width - 1, x + 90)) >= 0.78:
            return x
    return None


def _white_background_score(image, x1: int, x2: int) -> float:
    width, height = image.size
    x1 = max(0, min(x1, width - 1))
    x2 = max(x1 + 1, min(x2, width))
    y1 = int(height * 0.14)
    y2 = int(height * 0.86)
    total = 0
    white = 0
    for x in range(x1, x2, 8):
        for y in range(y1, y2, 12):
            pixel = image.getpixel((x, y))
            r, g, b = pixel[:3]
            total += 1
            if r >= 248 and g >= 248 and b >= 248:
                white += 1
    return white / total if total else 0.0


def _detect_chat_bottom_boundary(image, left: int, right: int, platform_key: str) -> int | None:
    if platform_key not in {"wechat", "wecom", "qq"}:
        return None
    width, height = image.size
    left = max(0, min(int(left), width - 2))
    right = max(left + 1, min(int(right), width))
    scan_top = int(height * 0.56)
    scan_bottom = int(height * 0.95)
    hits: list[int] = []
    for y in range(scan_top, scan_bottom, 3):
        score = _horizontal_input_line_score(image, left, right, y)
        if score >= 0.50:
            hits.append(y)
    if hits:
        clusters: list[list[int]] = []
        for y in hits:
            if clusters and y - clusters[-1][-1] <= 9:
                clusters[-1].append(y)
            else:
                clusters.append([y])
        input_cluster = clusters[-1]
        return max(int(height * 0.42), input_cluster[0] - 2)
    return None


def _horizontal_input_line_score(image, left: int, right: int, y: int) -> float:
    total = 0
    line = 0
    for x in range(left + 20, max(left + 21, right - 20), 8):
        r, g, b = image.getpixel((x, y))[:3]
        total += 1
        if 214 <= r <= 238 and 214 <= g <= 238 and 214 <= b <= 238 and max(r, g, b) - min(r, g, b) <= 8:
            line += 1
    return line / total if total else 0.0


def _infer_chat_role(item: dict, image_width: int, image=None) -> str:
    avatar_role = _infer_role_from_avatar_proximity(item, image)
    if avatar_role:
        return avatar_role
    visual_role = _infer_role_from_bubble_color(item, image)
    if visual_role:
        return visual_role
    left = float(item.get("left", item.get("x", 0)))
    right = float(item.get("right", item.get("x", left)))
    if image_width <= 0:
        return "对方"

    width = float(image_width)
    center = (left + right) / 2
    right_margin = max(0.0, width - right)

    if left <= width * 0.18 and right_margin >= width * 0.12:
        return "对方"
    if right_margin <= width * 0.19 and left >= width * 0.20:
        return "我"
    if center >= width * 0.58:
        return "我"
    if center <= width * 0.42:
        return "对方"
    if right_margin + width * 0.04 < left:
        return "我"
    return "对方"


def _infer_role_from_avatar_proximity(item: dict, image) -> str:
    if image is None:
        return ""
    try:
        width, height = image.size
        text_left = float(item.get("left", item.get("x", 0)))
        text_right = float(item.get("right", item.get("x", text_left)))
        y = float(item.get("y", 0))
        search_distance = max(96.0, min(160.0, width * 0.18))
        left_candidate = _best_avatar_candidate(
            image,
            int(max(0, text_left - search_distance)),
            int(max(1, text_left - 10)),
            y,
        )
        right_candidate = _best_avatar_candidate(
            image,
            int(min(width - 1, text_right + 10)),
            int(min(width, text_right + search_distance)),
            y,
        )
        if not left_candidate and not right_candidate:
            return ""
        if right_candidate and not left_candidate:
            return "我"
        if left_candidate and not right_candidate:
            return "对方"

        left_score, left_center_x = left_candidate
        right_score, right_center_x = right_candidate
        left_distance = max(1.0, text_left - left_center_x)
        right_distance = max(1.0, right_center_x - text_right)
        if right_score >= left_score + 0.10 and right_distance <= left_distance * 1.5:
            return "我"
        if left_score >= right_score + 0.10 and left_distance <= right_distance * 1.5:
            return "对方"
        if right_distance < left_distance * 0.85:
            return "我"
        if left_distance < right_distance * 0.85:
            return "对方"
    except Exception:
        return ""
    return ""


def _best_avatar_candidate(image, x1: int, x2: int, y: float) -> tuple[float, float] | None:
    width, height = image.size
    x1 = max(0, min(x1, width - 1))
    x2 = max(x1 + 1, min(x2, width))
    if x2 - x1 < 24:
        return None
    best_score = 0.0
    best_center = 0.0
    for size in (32, 40, 48):
        y_top = int(max(0, min(height - size, y - size / 2)))
        for x in range(x1, max(x1 + 1, x2 - size + 1), 6):
            score = _avatar_patch_score(image, x, y_top, size)
            if score > best_score:
                best_score = score
                best_center = x + size / 2
    if best_score >= 0.34:
        return best_score, best_center
    return None


def _avatar_patch_score(image, x: int, y: int, size: int) -> float:
    samples: list[tuple[int, int, int]] = []
    for px in range(x, x + size, 4):
        for py in range(y, y + size, 4):
            r, g, b = image.getpixel((px, py))[:3]
            samples.append((r, g, b))
    if not samples:
        return 0.0
    non_background = [
        (r, g, b)
        for r, g, b in samples
        if not _is_chat_background_color(r, g, b)
    ]
    non_background_ratio = len(non_background) / len(samples)
    if non_background_ratio < 0.15:
        return 0.0
    means = [sum(pixel[i] for pixel in samples) / len(samples) for i in range(3)]
    variance = sum(
        (r - means[0]) ** 2 + (g - means[1]) ** 2 + (b - means[2]) ** 2
        for r, g, b in samples
    ) / len(samples)
    colorfulness = sum(abs(r - g) + abs(g - b) + abs(r - b) for r, g, b in samples) / (len(samples) * 510)
    # 真实头像应有一定色彩或纹理；纯浅色背景即使误入，得分也应极低
    if variance < 900 and colorfulness < 0.05:
        return 0.0
    return non_background_ratio + min(variance / 9000, 1.0) * 0.35 + colorfulness * 0.25


def _is_chat_background_color(r: int, g: int, b: int) -> bool:
    """判断是否为聊天界面背景色（微信/企业微信通用）。
    微信背景纯白(255,255,255)；企业微信背景为浅灰蓝(245,247,250)等；
    浅色背景统一视为背景，避免被误判为头像内容。"""
    if r >= 240 and g >= 240 and b >= 240:
        return True  # 接近白色/极浅色（含企业微信 (245,247,250)）
    if 228 <= r <= 244 and 228 <= g <= 244 and 228 <= b <= 244:
        return True  # 灰色气泡底色
    return False


def _infer_role_from_bubble_color(item: dict, image) -> str:
    if image is None:
        return ""
    try:
        width, height = image.size
        left = max(0, int(float(item.get("left", item.get("x", 0))) - 28))
        right = min(width, int(float(item.get("right", item.get("x", left))) + 28))
        y = int(float(item.get("y", 0)))
        top = max(0, y - 20)
        bottom = min(height, y + 20)
        green = 0
        blue = 0
        grey = 0
        total = 0
        for x in range(left, right, 5):
            for sample_y in range(top, bottom, 5):
                r, g, b = image.getpixel((x, sample_y))[:3]
                total += 1
                if _is_wechat_green((r, g, b)):
                    green += 1
                elif _is_wecom_blue((r, g, b)):
                    blue += 1
                elif 220 <= r <= 248 and 220 <= g <= 248 and 220 <= b <= 248 and abs(r - g) <= 8 and abs(g - b) <= 8:
                    grey += 1
        if total and (green + blue) / total >= 0.12:
            return "我"
        if total and grey / total >= 0.18:
            return "对方"
    except Exception:
        return ""
    return ""


def _is_embedded_visual_text(item: dict, image) -> bool:
    if image is None:
        return False
    if _infer_role_from_avatar_proximity(item, image):
        return False
    region = _chat_color_region_size(item, image)
    if region is None:
        return False
    _color, width, height = region
    text_width = max(1.0, float(item.get("right", item.get("x", 0))) - float(item.get("left", item.get("x", 0))))
    return height <= 26 and width >= min(80.0, text_width * 0.45)


def _chat_color_region_size(item: dict, image) -> tuple[str, int, int] | None:
    try:
        width, height = image.size
        x = int(float(item.get("x", 0)))
        y = int(float(item.get("y", 0)))
        x = max(0, min(width - 1, x))
        y = max(0, min(height - 1, y))
        pixel = image.getpixel((x, y))[:3]
        if _is_wechat_green(pixel):
            color = "green"
            predicate = _is_wechat_green
        elif _is_wecom_blue(pixel):
            color = "blue"
            predicate = _is_wecom_blue
        elif _is_wechat_gray(pixel):
            color = "gray"
            predicate = _is_wechat_gray
        else:
            return None

        left = x
        while left > 0 and predicate(image.getpixel((left - 1, y))[:3]):
            left -= 1
        right = x
        while right < width - 1 and predicate(image.getpixel((right + 1, y))[:3]):
            right += 1
        top = y
        while top > 0 and predicate(image.getpixel((x, top - 1))[:3]):
            top -= 1
        bottom = y
        while bottom < height - 1 and predicate(image.getpixel((x, bottom + 1))[:3]):
            bottom += 1
        return color, right - left + 1, bottom - top + 1
    except Exception:
        return None


def _is_wechat_green(pixel: tuple[int, int, int]) -> bool:
    r, g, b = pixel
    return 120 <= r <= 180 and 210 <= g <= 250 and 80 <= b <= 150


def _is_wecom_blue(pixel: tuple[int, int, int]) -> bool:
    """企业微信“我”的消息气泡是蓝色（实测 RGB 约 (56,135,244)），
    与微信的绿色气泡区分开，用于识别我方消息。"""
    r, g, b = pixel
    return (
        30 <= r <= 110
        and 100 <= g <= 190
        and 210 <= b <= 255
        and b > r + 80
        and b > g + 40
        and abs(g - r) <= 70
    )


def _is_wechat_gray(pixel: tuple[int, int, int]) -> bool:
    r, g, b = pixel
    return 220 <= r <= 248 and 220 <= g <= 248 and 220 <= b <= 248 and abs(r - g) <= 8 and abs(g - b) <= 8


_SYSTEM_MESSAGE_KEYWORDS = (
    "以上是打招呼内容",
    "你已添加了",
    "现在可以开始聊天了",
    "刚刚添加",
    "已成为好友",
    "现在可以开始聊天",
    "你已通过验证",
    "对方已通过你的好友验证",
    "你添加了",
    "消息已发出，但被对方拒收了",
    "被对方拉黑",
    "无法接收消息",
    "现在可以开始聊天",
    "开启了好友验证",
    "对方开启了朋友验证",
)


def _is_recall_message_text(text: str) -> bool:
    """识别个人/群聊撤回提示，并容忍常见 OCR 的“撤/撒”和“一/1”误判。"""
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or "")))
    if not compact or len(compact) > 60:
        return False
    return bool(
        re.fullmatch(
            r"(?:来自微信的)?[^，。！？!?：:]{0,32}(?:撤|撒)回了(?:一|1)?条消息",
            compact,
        )
    )


def _is_system_message_text(compact: str) -> bool:
    """企业微信/微信的居中系统消息（非对话内容），按关键词识别。"""
    if _is_recall_message_text(compact):
        return True
    if len(compact) > 60:
        return False
    for keyword in _SYSTEM_MESSAGE_KEYWORDS:
        if keyword in compact:
            return True
    return False


def _is_noise_text(text: str) -> bool:
    lowered_text = text.lower()
    if "win" in lowered_text and "alt" in lowered_text:
        return True
    if _looks_like_visual_ocr_artifact(text):
        return True
    normalized = re.sub(r"\s+", "", text.strip())
    # 企业微信/微信的系统消息（居中灰字提示），不是聊天内容
    if _is_system_message_text(normalized):
        return True
    if re.fullmatch(r"(今天|昨天|前天|星期[一二三四五六日天]|周[一二三四五六日天])(\d{1,2}[:：]\d{2})?", normalized):
        return True
    # 相对时间戳：刚刚、X分钟前、X小时前、X天前、昨天、前天
    # 注意：OCR 可能裁掉数字只留“分钟前”，所以也接受无数字前缀的形式
    if re.fullmatch(r"(刚刚|刚才|\d{1,3}\s*(分钟|小时|天|秒)?\s*前|分钟前|小时前|天前|昨天|前天)", normalized):
        return True
    cleaned = text.strip().replace("”", '"').replace("“", '"')
    if re.fullmatch(r"[\d:：/\-\s]+", cleaned):
        return True
    if re.fullmatch(r"(今天|昨天|前天)?\s*\d{1,2}[:：]\d{2}", cleaned):
        return True
    if re.fullmatch(r"(上午|下午|晚上|凌晨)?\s*\d{1,2}[:：]\d{2}", cleaned):
        return True
    if re.fullmatch(r"\d{1,3}\s*['\"]", cleaned):
        return True
    if re.fullmatch(r"[\(\[]?\d{1,3}\s*秒[\)\]]?", cleaned):
        return True
    return False


def _looks_like_visual_ocr_artifact(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    lowered = stripped.lower()
    if "http://" in lowered or "https://" in lowered:
        return False

    compact = re.sub(r"\s+", "", stripped)
    chinese_count = len(re.findall(r"[\u4e00-\u9fff]", compact))
    digit_count = len(re.findall(r"\d", compact))
    ascii_letter_count = len(re.findall(r"[a-zA-Z]", compact))
    symbol_count = len(re.findall(r"[^0-9a-zA-Z\u4e00-\u9fff]", compact))
    total = len(compact)
    if chinese_count == 0 and symbol_count == 0 and digit_count == 0 and 2 <= ascii_letter_count <= 4:
        return True
    if chinese_count == 0 and symbol_count == 0 and 3 <= total <= 4 and ascii_letter_count >= 1 and digit_count >= 1:
        return True
    if total < 5:
        return False

    has_measurement_unit = bool(re.search(r"(?i)(m2|㎡|cm|mm|kg|g|ml|mb|gb)$", compact))
    has_math_noise = bool(re.search(r"[±≈=×*/]{2,}", compact))
    has_parameter_punctuation = bool(re.search(r"[:：+=*/\\-]", compact))
    looks_like_file_name = bool(re.search(r"(?i)\.(mp4|mov|jpg|jpeg|png|gif|doc|docx|xls|xlsx|zip|pdf|txt)$", compact))
    digit_symbol_ratio = (digit_count + symbol_count) / max(total, 1)

    if (
        chinese_count == 0
        and 6 <= total <= 18
        and digit_count >= 1
        and ascii_letter_count >= 1
        and symbol_count >= 1
        and has_parameter_punctuation
        and not looks_like_file_name
    ):
        return True
    if chinese_count == 0 and digit_count >= 4 and (digit_symbol_ratio >= 0.55 or has_measurement_unit or has_math_noise):
        return True
    if chinese_count <= 1 and digit_count >= 5 and symbol_count >= 2 and ascii_letter_count <= 3:
        return True
    return False


def _merge_wrapped_chat_lines(lines: list[ChatLine]) -> list[ChatLine]:
    merged: list[ChatLine] = []
    for line in lines:
        if merged and _looks_like_wrapped_continuation(merged[-1], line):
            merged[-1] = ChatLine(
                role=merged[-1].role,
                text=f"{merged[-1].text}{line.text}",
                y=line.y,
            )
            continue
        merged.append(line)
    return merged


def _looks_like_wrapped_continuation(previous: ChatLine, current: ChatLine) -> bool:
    if previous.role != current.role:
        return False
    if _is_recall_message_text(previous.text) or _is_recall_message_text(current.text):
        return False
    if _separate_group_sender_from_message(previous.text, current.text):
        return False
    gap = current.y - previous.y
    if gap <= 0 or gap > 34:
        return False
    if previous.text.endswith(("，", "、", "；", "：", ",")):
        return True
    return len(previous.text) >= 18 or len(current.text) >= 18


def _separate_group_sender_from_message(previous: str, current: str) -> bool:
    return _is_group_sender_header(previous) and bool(str(current or "").strip())


def _is_group_sender_header(text: str) -> bool:
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))
    if not compact or len(compact) > 64:
        return False
    markers = (
        "@-@微信",
        "@-@wecom",
        "@企业微信",
        "@微信",
        "@wecom",
        "(微信)",
        "(企业微信)",
        "(wechat)",
        "(wecom)",
        "·微信",
        "·企业微信",
    )
    return compact.casefold().endswith(markers)


def _restore_visible_dash_sender(
    item: dict,
    text: str,
    image,
    *,
    leading_search_px: int = 2,
) -> str:
    """仅在 OCR 框前沿确有短横线像素时恢复昵称 ``-``。"""
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))
    if compact.casefold() not in {"@微信", "@wecom"} or image is None:
        return text
    try:
        width, height = image.size
        left = int(round(float(item.get("left", item.get("x", 0)))))
        center_y = int(round(float(item.get("y", 0))))
        x_start = max(0, left - max(0, int(leading_search_px)))
        x_end = min(int(width) - 1, left + 6)
        y_start = max(0, center_y - 4)
        y_end = min(int(height) - 1, center_y + 4)
        pixels: set[tuple[int, int]] = set()
        for y in range(y_start, y_end + 1):
            for x in range(x_start, x_end + 1):
                value = image.getpixel((x, y))
                rgb = tuple(int(channel) for channel in value[:3])
                if max(rgb) < 210 and max(rgb) - min(rgb) <= 28:
                    pixels.add((x, y))
    except (AttributeError, IndexError, TypeError, ValueError):
        return text

    while pixels:
        seed = pixels.pop()
        component = [seed]
        stack = [seed]
        while stack:
            x, y = stack.pop()
            for neighbor_x in range(x - 1, x + 2):
                for neighbor_y in range(y - 1, y + 2):
                    neighbor = (neighbor_x, neighbor_y)
                    if neighbor in pixels:
                        pixels.remove(neighbor)
                        stack.append(neighbor)
                        component.append(neighbor)
        component_x = [point[0] for point in component]
        component_y = [point[1] for point in component]
        component_width = max(component_x) - min(component_x) + 1
        component_height = max(component_y) - min(component_y) + 1
        if 2 <= component_width <= 8 and component_height <= 2:
            return f"-{text}"
    return text
