# -*- coding: utf-8 -*-
"""会话列表自动切换 + 会话类型四分类（企业微信 5.0.9.6060 旁路）。

模块划分（按 docs/会话列表自动切换_功能交接.md）：
- ConversationListMonitor：左侧会话列表扫描 + 红点像素检测
- ChatIdentityTracker：当前会话标识（顶部标题文字）
- ChatStateGuard：触发/打断状态机
- ConversationTypeDetector：system / group / personal / unknown 四分类

标定：加载 wecom_5_calibration.json（不存在时用内置默认值）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None

ChatType = Literal["personal", "group", "system", "unknown"]

_DEFAULT_CALIBRATION: dict = {
    # 最左窄列是消息/邮件/文档工具栏（含红点角标，必须排除），其右侧才是会话列表
    "conversation_list_area": {"x_left": 0.08, "x_right": 0.30, "y_top": 0.12, "y_bottom": 1.0},
    "top_title_area": {"x_left": 0.29, "x_right": 1.0, "y_top": 0.0, "y_bottom": 0.10},
    "avatar_area": {"x_left": 0.30, "x_right": 0.44, "y_top": 0.012, "y_bottom": 0.085},
    "red_dot_rgb": {"r_min": 230, "g_max": 90, "b_max": 90, "min_diameter_px": 5, "max_diameter_px": 16},
    "avatar_grid": {"grid": 3, "boundary_jump_min": 18},
    "click_target": {"avatar_x_ratio": 0.18},
    "timing": {
        "self_stable_seconds": 1.2,
        "scan_interval_seconds": 0.8,
        "switch_wait_seconds": 1.2,
        "user_typing_seconds": 1.5,
        "fail_pause_seconds": 300,
        "max_consecutive_failures": 3,
        "retry_per_failure": 1,
    },
    "system_whitelist": [
        "客户联系", "可能的商务伙伴", "一周小结", "学习园地", "行业资讯",
        "接收微信中的工作消息", "客户咨询", "微信客服", "邮件提醒", "管理企业",
        "腾讯云客服", "企业微信团队", "腾讯云企业服务", "程明现的机器人",
    ],
    "system_keywords": ["团队", "客服", "服务", "提醒", "管理", "联系"],
}


def load_calibration(path: str | Path | None = None) -> dict:
    """加载标定 JSON；失败时回退内置默认值。"""
    if path is None:
        path = Path(__file__).resolve().parent / "wecom_5_calibration.json"
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        merged = dict(_DEFAULT_CALIBRATION)
        for key, value in data.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
        return merged
    except Exception:
        return dict(_DEFAULT_CALIBRATION)


@dataclass(frozen=True)
class RedDotHit:
    """红点命中：所在行 y 坐标 + 该行中心 x（点击用）。"""

    y: float
    x_center: float


class ConversationListMonitor:
    """左侧会话列表扫描 + 红点检测（像素颜色，不用 OCR）。"""

    def __init__(self, calibration: dict | None = None) -> None:
        self.cal = calibration or load_calibration()
        area = self.cal["conversation_list_area"]
        self.area = (area["x_left"], area["y_top"], area["x_right"], area["y_bottom"])
        rgb = self.cal["red_dot_rgb"]
        self.rgb = (
            int(rgb["r_min"]),
            int(rgb["g_max"]),
            int(rgb["b_max"]),
            int(rgb["min_diameter_px"]),
            int(rgb["max_diameter_px"]),
        )
        self.click_x_ratio = float(self.cal.get("click_target", {}).get("avatar_x_ratio", 0.10))

    def scan(self, full_window_image) -> list[RedDotHit]:
        """扫描左侧列表区，返回红点命中（按 y 从上到下排序）。

        红点检测：像素颜色 (R>=r_min, G<=g_max, B<=b_max) 的连通块，
        直径落在 [min_diameter, max_diameter] 视为红点。
        """
        if Image is None or full_window_image is None:
            return []
        left_f, top_f, right_f, bottom_f = self.area
        width, height = full_window_image.size
        left = int(width * left_f)
        top = int(height * top_f)
        right = int(width * right_f)
        bottom = int(height * bottom_f)
        if right <= left or bottom <= top:
            return []
        r_min, g_max, b_max, min_d, max_d = self.rgb
        region = full_window_image.crop((left, top, right, bottom))
        pixels = region.load()
        w, h = region.size
        visited = [[False] * w for _ in range(h)]
        hits: list[RedDotHit] = []
        min_area = max(2, int(3.14 * (min_d / 2) ** 2))
        max_area = int(3.14 * (max_d / 2) ** 2) * 2
        for y in range(h):
            for x in range(w):
                if visited[y][x]:
                    continue
                r, g, b = pixels[x, y][:3]
                if not (r >= r_min and g <= g_max and b <= b_max):
                    continue
                # BFS 连通块
                stack = [(x, y)]
                visited[y][x] = True
                blob: list[tuple[int, int]] = []
                min_x = max_x = x
                min_y = max_y = y
                while stack:
                    cx, cy = stack.pop()
                    blob.append((cx, cy))
                    min_x, max_x = min(min_x, cx), max(max_x, cx)
                    min_y, max_y = min(min_y, cy), max(max_y, cy)
                    for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                        if 0 <= nx < w and 0 <= ny < h and not visited[ny][nx]:
                            nr, ng, nb = pixels[nx, ny][:3]
                            if nr >= r_min and ng <= g_max and nb <= b_max:
                                visited[ny][nx] = True
                                stack.append((nx, ny))
                area = len(blob)
                if not (min_area <= area <= max_area):
                    continue
                blob_w = max_x - min_x + 1
                blob_h = max_y - min_y + 1
                if blob_w > max_d * 2 or blob_h > max_d * 2:
                    continue
                # 只保留"红点多于非红点"的紧凑块（过滤孤立噪点行）
                if area < blob_w * blob_h * 0.5:
                    continue
                cy = (min_y + max_y) // 2
                cx = (min_x + max_x) // 2
                hits.append(RedDotHit(y=top + cy, x_center=left + cx))
        # 去重：相近 y 只保留最上面的（红点边缘可能被分成两块）
        hits.sort(key=lambda item: (item.y, item.x_center))
        deduped: list[RedDotHit] = []
        for hit in hits:
            if deduped and abs(hit.y - deduped[-1].y) <= 3:
                continue
            deduped.append(hit)
        return deduped

    def click_point_for(self, hit: RedDotHit, window_size: tuple[int, int]) -> tuple[int, int]:
        """返回点击坐标：x=列表区 10% 处（头像区），y=红点行 y。"""
        width, height = window_size
        left = int(width * self.area[0])
        right = int(width * self.area[2])
        x = left + int((right - left) * self.click_x_ratio)
        return x, int(hit.y)

    def extract_row_avatar(self, full_window_image, hit: RedDotHit):
        """提取红点所在会话行的头像区域（列表行头像，用于群聊/个人判定）。

        标定（5.0.9.6060 实测）：会话行头像在窗口 x 7.6%~11.7%（约 40px 宽）、
        行中心 y ±22px。
        """
        if Image is None or full_window_image is None:
            return None
        width, height = full_window_image.size
        avatar_left = int(width * 0.075)
        avatar_right = min(width, avatar_left + 45)
        top = max(0, int(hit.y) - 22)
        bottom = min(height, int(hit.y) + 22)
        if avatar_right <= avatar_left or bottom <= top:
            return None
        return full_window_image.crop((avatar_left, top, avatar_right, bottom))

    def extract_row_title(self, full_window_image, hit: RedDotHit):
        """提取红点所在会话行的标题，排除头像和下一行消息摘要。"""
        if Image is None or full_window_image is None:
            return None
        width, height = full_window_image.size
        left = max(0, int(width * 0.12))
        right = min(width, int(width * 0.295))
        top = max(0, int(hit.y) - 15)
        bottom = min(height, int(hit.y) + 15)
        if right <= left or bottom <= top:
            return None
        return full_window_image.crop((left, top, right, bottom))


class ChatIdentityTracker:
    """当前会话标识：顶部标题文字（切换会话 hwnd 不变，必须用标题文字）。"""

    def __init__(self) -> None:
        self.current_chat_key = ""
        self._last_top_title = ""

    def update_from_top_title(self, top_title_text: str) -> None:
        text = (top_title_text or "").strip()
        if text:
            self.current_chat_key = text
            self._last_top_title = text

    def matches(self, top_title_text: str) -> bool:
        """顶部标题文字是否仍指向当前会话。"""
        text = (top_title_text or "").strip()
        if not text or not self.current_chat_key:
            return False
        return text == self.current_chat_key


class ChatStateGuard:
    """触发/打断状态机。

    触发：托管模式开启 且 最新消息为我方发出 且 持续 stable_seconds 秒。
    打断：对方又发消息 / 用户正在输入 / 主路发送中 / 主路处理中 / 企业微信不在前台 /
          切换失败重试中。
    """

    def __init__(self, calibration: dict | None = None) -> None:
        cal = calibration or load_calibration()
        self.cal = cal
        timing = cal.get("timing", {})
        self.self_stable_seconds = float(timing.get("self_stable_seconds", 1.2))
        self.scan_interval_seconds = float(timing.get("scan_interval_seconds", 0.8))
        self.user_typing_seconds = float(timing.get("user_typing_seconds", 1.5))

    def should_start_scan(
        self,
        managed_enabled: bool,
        latest_role: str,
        seconds_as_self: float,
        last_scan_at: float,
        now: float,
        paused_until: float,
        chat_type: str = "personal",
        group_idle: bool = False,
    ) -> bool:
        """触发扫描列表的条件。

        个人/其他：最新消息为我方发出或撤回系统提示，且持续 1.2 秒。
        群聊：我方发出 1.2 秒；或对方消息没 @ 我（聊天内容无变化）1.2 秒——都视为可切走。
        @ 我（group_idle=False）不触发（要走回复流程）。
        """
        if not managed_enabled:
            return False
        if now < paused_until:
            return False
        if latest_role in {"我", "系统"}:
            if seconds_as_self < self.self_stable_seconds:
                return False
        elif chat_type == "group" and latest_role == "对方" and group_idle:
            if seconds_as_self < self.self_stable_seconds:
                return False
        else:
            return False
        if now - last_scan_at + 1e-9 < self.scan_interval_seconds:
            return False
        return True

    def is_interrupted(
        self,
        latest_role: str,
        user_typing: bool,
        managed_send_inflight: bool,
        processing: bool,
        wecom_foreground: bool,
        retrying: bool,
    ) -> bool:
        if latest_role == "对方":
            return True
        if user_typing:
            return True
        if managed_send_inflight:
            return True
        if processing:
            return True
        if not wecom_foreground:
            return True
        if retrying:
            return True
        return False


class ConversationTypeDetector:
    """会话类型四分类：system → group → personal → unknown（逐级判定）。"""

    def __init__(self, calibration: dict | None = None) -> None:
        cal = calibration or load_calibration()
        self.cal = cal
        self.system_whitelist = [str(item) for item in cal.get("system_whitelist", [])]
        self.system_keywords = [str(item) for item in cal.get("system_keywords", [])]
        avatar = cal.get("avatar_area", {})
        self.avatar_area = (
            float(avatar.get("x_left", 0.30)),
            float(avatar.get("x_right", 0.44)),
            float(avatar.get("y_top", 0.012)),
            float(avatar.get("y_bottom", 0.085)),
        )
        grid = cal.get("avatar_grid", {})
        self.grid = int(grid.get("grid", 3))
        self.boundary_jump_min = float(grid.get("boundary_jump_min", 18))

    def detect(self, top_title_text: str, full_window_image=None, list_avatar_type: str = "") -> ChatType:
        """四分类：system（标题白名单）→ group（列表行头像九宫格，优先；顶部头像兜底）
        → personal（列表行头像单块，优先；顶部头像兜底）→ unknown。

        list_avatar_type：切换前从左侧列表行头像判定的类型（"group"/"personal"/""），
        比顶部标题栏头像更可靠（5.0.9 顶部无大头像，列表行头像始终存在）。
        """
        title_type = self.detect_title(top_title_text)
        if title_type != "unknown":
            return title_type
        if not str(top_title_text or "").strip():
            return "unknown"
        if list_avatar_type == "group":
            return "group"
        if list_avatar_type == "personal":
            return "personal"
        if full_window_image is not None:
            avatar = self._crop_avatar(full_window_image)
            if avatar is not None:
                if self._is_grid_avatar(avatar):
                    return "group"
                if self._is_single_avatar(avatar):
                    return "personal"
        # 标题无文字且头像无法判定 → unknown（绝不猜成 personal）
        return "unknown"

    def detect_title(self, top_title_text: str) -> ChatType:
        """只使用标题的强证据分类，不让易误判的头像覆盖明确标题。"""
        if self._is_system(top_title_text):
            return "system"
        if self._is_explicit_group_title(top_title_text):
            return "group"
        if self._is_explicit_personal_title(top_title_text):
            return "personal"
        return "unknown"

    def detect_row_avatar(self, avatar_image) -> str:
        """判定列表行头像类型：九宫格 → group；有内容（非纯色/空白）→ personal；其他 → ""。"""
        if avatar_image is None:
            return ""
        if self._is_grid_avatar(avatar_image):
            return "group"
        if self._has_avatar_content(avatar_image):
            return "personal"
        return ""

    def _has_avatar_content(self, avatar) -> bool:
        """头像区域是否有实际内容（非纯白/纯灰）：至少 4/64 采样像素有饱和度。"""
        if Image is None or avatar is None:
            return False
        small = avatar.resize((8, 8))
        pixels = list(small.getdata())
        colorful = sum(1 for r, g, b in pixels if max(r, g, b) - min(r, g, b) > 25)
        return colorful >= 4

    def _is_system(self, top_title_text: str) -> bool:
        text = self._normalize_system_title(top_title_text)
        if not text:
            return False
        for name in self.system_whitelist:
            normalized_name = self._normalize_system_title(name)
            if text == normalized_name:
                return True
            if text.startswith(normalized_name) and text[len(normalized_name):len(normalized_name) + 1] in {
                "@", "·", "：", ":", "-", "—", "（", "(",
            }:
                return True
        return False

    @staticmethod
    def _normalize_system_title(value: str) -> str:
        text = re.sub(r"\s+", "", str(value or "")).casefold()
        # 企业微信浅色列表的小字号 OCR 偶尔把“联系”的“系”读成“糸”。
        return text.replace("客户联糸", "客户联系")

    @staticmethod
    def _is_explicit_personal_title(top_title_text: str) -> bool:
        text = re.sub(r"\s+", "", str(top_title_text or "")).casefold()
        return "@微信" in text or "@wecom" in text

    @staticmethod
    def _is_explicit_group_title(top_title_text: str) -> bool:
        text = re.sub(r"\s+", "", str(top_title_text or "")).casefold()
        if not text:
            return False
        if any(marker in text for marker in ("外部群", "群聊", "交流群", "群主")):
            return True
        return sum(text.count(separator) for separator in ("、", "，", ",")) >= 2

    def _crop_avatar(self, full_window_image):
        if Image is None or full_window_image is None:
            return None
        x_left, x_right, y_top, y_bottom = self.avatar_area
        width, height = full_window_image.size
        left = int(width * x_left)
        right = max(left + 1, int(width * x_right))
        top = int(height * y_top)
        bottom = max(top + 1, int(height * y_bottom))
        return full_window_image.crop((left, top, right, bottom))

    def _is_grid_avatar(self, avatar) -> bool:
        """九宫格判定：3×3 子块边界主色跳变。

        关键：用"子块边界颜色跳变"而非"整体色彩丰富度"——
        合影头像色彩丰富但渐变连续，九宫格在网格边界有明显跳变。
        """
        grid = self.grid
        w, h = avatar.size
        block_w = max(1, w // grid)
        block_h = max(1, h // grid)
        block_colors: list[list[tuple[int, int, int]]] = []
        for row in range(grid):
            colors_row: list[tuple[int, int, int]] = []
            for col in range(grid):
                bx = col * block_w
                by = row * block_h
                block = avatar.crop((bx, by, min(w, bx + block_w), min(h, by + block_h)))
                colors_row.append(self._dominant_color(block))
            block_colors.append(colors_row)
        jumps = 0
        comparisons = 0
        for row in range(grid):
            for col in range(grid):
                current = block_colors[row][col]
                if col + 1 < grid:
                    jumps += self._color_distance(current, block_colors[row][col + 1])
                    comparisons += 1
                if row + 1 < grid:
                    jumps += self._color_distance(current, block_colors[row + 1][col])
                    comparisons += 1
        if comparisons == 0:
            return False
        avg_jump = jumps / comparisons
        return avg_jump >= self.boundary_jump_min

    def _is_single_avatar(self, avatar) -> bool:
        """单方块头像：整体色彩收敛（子块间差异小）且非纯色。"""
        grid = self.grid
        w, h = avatar.size
        block_w = max(1, w // grid)
        block_h = max(1, h // grid)
        block_colors = []
        for row in range(grid):
            for col in range(grid):
                bx = col * block_w
                by = row * block_h
                block = avatar.crop((bx, by, min(w, bx + block_w), min(h, by + block_h)))
                block_colors.append(self._dominant_color(block))
        distances = []
        for i in range(len(block_colors)):
            for j in range(i + 1, len(block_colors)):
                distances.append(self._color_distance(block_colors[i], block_colors[j]))
        avg = sum(distances) / len(distances) if distances else 0
        return avg < self.boundary_jump_min

    @staticmethod
    def _dominant_color(block):
        """子块主色：中位色（对噪点不敏感，比均值稳）。"""
        if Image is None:
            return (0, 0, 0)
        small = block.resize((8, 8))
        pixels = list(small.getdata())
        r = sorted(p[0] for p in pixels)[len(pixels) // 2]
        g = sorted(p[1] for p in pixels)[len(pixels) // 2]
        b = sorted(p[2] for p in pixels)[len(pixels) // 2]
        return (r, g, b)

    @staticmethod
    def _color_distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
        return (abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])) / 3.0
