from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from io import BytesIO

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

from settings import AppSettings


@dataclass
class VisibleContent:
    message_type: str = "text"
    summary: str = ""
    confidence: str = "low"
    source: str = "screen_ocr"
    should_skip_reply: bool = False


def infer_visible_content_from_context(context: str) -> VisibleContent:
    last = _last_other_text(context)
    if not last:
        return VisibleContent(message_type="unknown", summary="", confidence="low", should_skip_reply=False)
    if _looks_like_sticker(last):
        return VisibleContent(
            message_type="sticker",
            summary="识别为表情包或贴图，没有实际文字问题，默认不回复。",
            confidence="medium",
            should_skip_reply=True,
        )
    if _has_url(last):
        return VisibleContent(
            message_type="link",
            summary=f"链接消息：{_strip_url_tail(last)}。只根据聊天窗口可见文字理解，不打开链接。",
            confidence="medium",
            should_skip_reply=False,
        )
    if _looks_like_card(context):
        return VisibleContent(
            message_type="card",
            summary=f"卡片/公众号预览可见内容：{last}",
            confidence="medium",
            should_skip_reply=False,
        )
    if _looks_like_image_placeholder(last):
        return VisibleContent(
            message_type="image",
            summary="疑似图片消息，OCR 未读到足够文字；需要视觉模型或人工查看。",
            confidence="low",
            should_skip_reply=True,
        )
    return VisibleContent(message_type="text", summary="", confidence="high", should_skip_reply=False)


class VisibleContentAnalyzer:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.last_error = ""

    def analyze(self, context: str, image=None) -> VisibleContent:
        self.last_error = ""
        visible = infer_visible_content_from_context(context)
        if visible.message_type in {"card", "link"}:
            return visible
        if visible.message_type == "sticker":
            return visible
        if image is None or self.settings.vision_provider != "zhipu" or not _vision_api_key(self.settings):
            return visible
        if not _should_call_vision(context, visible, image):
            return visible
        vision = self._analyze_image_with_zhipu(image, context)
        if vision.summary:
            return vision
        return visible

    def _analyze_image_with_zhipu(self, image, context: str) -> VisibleContent:
        if requests is None:
            self.last_error = "requests 组件不可用，无法调用视觉模型"
            return VisibleContent()
        try:
            image_url = _image_to_data_url(image)
            payload = {
                "model": self.settings.vision_model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "你在帮助微信回复助手理解当前聊天截图。只看截图中可见内容，不要假装打开链接或读过全文。\n"
                                    "判断最后一条对方消息类型：text/card/link/image/sticker/unknown。\n"
                                    "如果是表情包、纯贴图、无实际问题，should_skip_reply=true。\n"
                                    "如果 OCR 上下文里最后一句文字和图片同时出现，把它们当成同一轮消息，说明图片如何补充这句话。\n"
                                    "如果是公众号/卡片/图片，必须描述屏幕可见主体、场景、标题、摘要、文字、图片与最后一句文字的关系和可能回复重点；不要只说“对方发了一张图片”。\n"
                                    "输出严格 JSON："
                                    '{"message_type":"card","summary":"...","confidence":"high|medium|low","should_skip_reply":false}\n'
                                    f"OCR上下文：\n{context[-1200:]}"
                                ),
                            },
                            {"type": "image_url", "image_url": {"url": image_url}},
                        ],
                    }
                ],
                "temperature": 0.2,
                "max_tokens": 220,
            }
            response = requests.post(
                self.settings.vision_base_url.rstrip("/") + "/chat/completions",
                headers={
                    "Authorization": f"Bearer {_vision_api_key(self.settings)}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=20,
            )
            if int(getattr(response, "status_code", 200) or 200) >= 400:
                self.last_error = f"视觉模型 HTTP {response.status_code}: {str(getattr(response, 'text', ''))[:200]}"
                return VisibleContent()
            response.raise_for_status()
            content = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            return _validate_vision_result(context, _parse_vision_json(str(content)))
        except Exception as exc:
            self.last_error = f"视觉模型识别失败：{exc}"
            return VisibleContent()


def _vision_api_key(settings: AppSettings) -> str:
    key = (settings.vision_api_key or "").strip()
    if key:
        return key
    if settings.vision_provider == "zhipu" and settings.api_provider == "zhipu":
        return (settings.api_key or "").strip()
    return ""


def build_augmented_context(context: str, visible: VisibleContent) -> str:
    base_context = _strip_visible_understanding_lines(context)
    if not visible.summary:
        return base_context
    return (
        f"{base_context.rstrip()}\n"
        f"[可见内容理解] 类型：{visible.message_type}；置信度：{visible.confidence}；"
        f"来源：{visible.source}；{visible.summary}"
    )


def _strip_visible_understanding_lines(context: str) -> str:
    lines = []
    for line in (context or "").splitlines():
        if line.strip().startswith("[可见内容理解]"):
            continue
        lines.append(line)
    return "\n".join(lines).rstrip()


def visible_from_ocr_text(text: str) -> VisibleContent:
    cleaned = "\n".join(line.strip() for line in (text or "").splitlines() if line.strip())
    if not cleaned:
        return VisibleContent()
    return VisibleContent(
        message_type="image",
        summary=f"图片可见文字：{cleaned}",
        confidence="medium",
        source="local_image_ocr",
        should_skip_reply=False,
    )


def _parse_vision_json(content: str) -> VisibleContent:
    import json

    match = re.search(r"\{.*\}", content, re.S)
    if not match:
        return VisibleContent()
    try:
        data = json.loads(match.group(0))
    except Exception:
        return VisibleContent()
    message_type = str(data.get("message_type", "unknown")).strip() or "unknown"
    summary = str(data.get("summary", "")).strip()
    confidence = str(data.get("confidence", "low")).strip() or "low"
    should_skip = bool(data.get("should_skip_reply", False))
    return VisibleContent(
        message_type=message_type,
        summary=summary,
        confidence=confidence,
        source="zhipu_vision",
        should_skip_reply=should_skip,
    )


def _validate_vision_result(context: str, visible: VisibleContent) -> VisibleContent:
    last = _last_other_text(context)
    if _looks_like_image_placeholder(last) and visible.message_type == "text":
        return _unreliable_vision_result(visible.source)
    if _looks_like_image_placeholder(last) and re.search(r"(客户|对方|用户).{0,4}(说|问|表示)", visible.summary):
        return _unreliable_vision_result(visible.source)
    return visible


def _unreliable_vision_result(source: str) -> VisibleContent:
    return VisibleContent(
        message_type="image",
        summary=chr(0x56FE) + chr(0x7247) + chr(0x5185) + chr(0x5BB9) + chr(0x65E0) + chr(0x6CD5)
        + chr(0x53EF) + chr(0x9760) + chr(0x8BC6) + chr(0x522B) + chr(0xFF0C) + chr(0x5EFA)
        + chr(0x8BAE) + chr(0x4EBA) + chr(0x5DE5) + chr(0x67E5) + chr(0x770B),
        confidence="low",
        source=source,
        should_skip_reply=True,
    )


def _image_to_data_url(image) -> str:
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=80)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _should_call_vision(context: str, visible: VisibleContent, image=None) -> bool:
    if visible.message_type in {"image", "unknown"}:
        return True
    text = _last_other_text(context)
    if len(text) <= 4 and not _has_url(text):
        return True
    if image is not None and _has_large_visible_image_region(image) and _effective_context_line_count(context) <= 3:
        return True
    return any(marker in context for marker in ["[图片]", "[动画表情]", "公众号", "小程序", "卡片"])


def _effective_context_line_count(context: str) -> int:
    return sum(1 for line in (context or "").splitlines() if line.strip().startswith("["))


def _has_large_visible_image_region(image) -> bool:
    try:
        small = image.convert("RGB").resize((64, 64))
    except Exception:
        return False
    width, height = small.size
    non_background = 0
    pixels = small.load()
    for y in range(height):
        for x in range(width):
            r, g, b = pixels[x, y]
            if max(r, g, b) - min(r, g, b) > 18 or max(r, g, b) < 215:
                non_background += 1
    return non_background / max(width * height, 1) >= 0.08


def _last_other_text(context: str) -> str:
    own_markers = {chr(0x6211), chr(0x93B4)}
    for line in reversed((context or "").splitlines()):
        stripped = line.strip()
        if not stripped.startswith("[") or "]" not in stripped:
            continue
        label = stripped.split("]", 1)[0] + "]"
        if any(label.startswith("[" + marker) for marker in own_markers):
            continue
        return stripped.split("]", 1)[-1].strip()
    return ""


def _has_url(text: str) -> bool:
    return bool(re.search(r"https?://|www\.", text, re.I))


def _strip_url_tail(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _looks_like_sticker(text: str) -> bool:
    cleaned = re.sub(r"\s+", "", text)
    sticker_words = {
        "[" + chr(0x8868) + chr(0x60C5) + "]",
        "[" + chr(0x52A8) + chr(0x753B) + chr(0x8868) + chr(0x60C5) + "]",
        "[" + chr(0x8D34) + chr(0x56FE) + "]",
        "[" + chr(0x56FE) + chr(0x7247) + chr(0x8868) + chr(0x60C5) + "]",
        chr(0x8868) + chr(0x60C5),
        chr(0x52A8) + chr(0x753B) + chr(0x8868) + chr(0x60C5),
        "[???]", "[??????]", "[??]", "???", "??????",
    }
    if cleaned in sticker_words:
        return True
    if len(cleaned) <= 6 and any(mark in cleaned for mark in ["\U0001f600", "\U0001f602", "\U0001f604", "\U0001f44d", "\U0001f64f"]):
        return True
    return False


def _looks_like_image_placeholder(text: str) -> bool:
    cleaned = re.sub(r"\s+", "", text)
    image_word = chr(0x56FE) + chr(0x7247)
    photo_word = chr(0x7167) + chr(0x7247)
    image_words = {
        "[" + image_word + "]", image_word, "[" + photo_word + "]", photo_word,
        "[??]", "??", "[???]", "???",
    }
    return cleaned in image_words or (chr(0x56FE) in cleaned and chr(0x7247) in cleaned and len(cleaned) <= 8)


def _looks_like_card(context: str) -> bool:
    markers = ["公众号", "小程序", "发布", "阅读", "招聘", "岗位", "五险", "一金", "链接卡片"]
    return sum(1 for marker in markers if marker in context) >= 2


def _validate_vision_result(context: str, visible: VisibleContent) -> VisibleContent:
    last = _last_other_text(context)
    if not (context or "").strip() and visible.message_type == "text":
        return _unreliable_vision_result(visible.source)
    if _looks_like_image_placeholder(last) and visible.message_type == "text":
        return _unreliable_vision_result(visible.source)
    if _looks_like_image_placeholder(last) and _looks_like_unsupported_vision_summary(visible.summary):
        return _unreliable_vision_result(visible.source)
    if re.search(r"(客户|对方|用户).{0,4}(说|问|表示)", visible.summary or ""):
        return _unreliable_vision_result(visible.source)
    if _looks_like_unsupported_vision_summary(visible.summary):
        return _unreliable_vision_result(visible.source)
    return visible


def _looks_like_unsupported_vision_summary(summary: str) -> bool:
    text = re.sub(r"\s+", "", summary or "")
    if not text:
        return False
    # Narrative interpretations are not concrete text visible in the chat.
    narrative_phrases = [
        "\u5206\u4eab\u4e86\u4e00\u6bb5\u5173\u4e8e",
        "\u4e00\u6bb5\u5173\u4e8e",
        "\u8bb2\u8ff0\u4e86",
        "\u63cf\u8ff0\u4e86",
        "\u8868\u8fbe\u4e86",
        "\u611f\u6fc0\u4e4b\u60c5",
        "\u6df1\u6df1\u7684\u611f\u6fc0",
    ]
    return any(phrase in text for phrase in narrative_phrases)
