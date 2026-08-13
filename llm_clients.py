from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from settings import AppSettings


@dataclass
class TextGenerationRequest:
    url: str
    headers: dict[str, str]
    payload: dict[str, Any]


PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "zhipu": {
        "label": "Zhipu",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash-250414",
        "path": "/chat/completions",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "path": "/chat/completions",
    },
    "openai": {
        "label": "OpenAI format",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4.1-mini",
        "path": "/chat/completions",
    },
    "qwen": {
        "label": "Qwen / DashScope",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "path": "/chat/completions",
    },
    "doubao": {
        "label": "Doubao / Volcengine Ark",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-1-6-flash-250615",
        "path": "/chat/completions",
    },
    "moonshot": {
        "label": "Moonshot / Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "path": "/chat/completions",
    },
    "siliconflow": {
        "label": "SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "Qwen/Qwen2.5-7B-Instruct",
        "path": "/chat/completions",
    },
}


def supported_provider_help() -> str:
    return (
        "支持的文本生成接口：Zhipu、DeepSeek、Qwen/通义千问百炼、Doubao/火山方舟、"
        "Moonshot/Kimi、SiliconFlow、OpenAI 格式接口，以及 Custom HTTP 自定义接口。"
        "只要接口能通过 HTTP 返回文本，就可以通过自定义 URL、Header、Body 和返回路径接入。"
    )


def provider_labels() -> list[tuple[str, str]]:
    labels = [(key, value["label"]) for key, value in PROVIDER_PRESETS.items()]
    labels.append(("custom_http", "Custom HTTP"))
    return labels


def provider_defaults(provider: str) -> dict[str, str]:
    return PROVIDER_PRESETS.get(provider, {})


def build_text_generation_request(settings: AppSettings, messages: list[dict[str, str]]) -> TextGenerationRequest:
    if settings.api_provider == "custom_http":
        return _build_custom_request(settings, messages)
    preset = provider_defaults(settings.api_provider)
    path = preset.get("path", "/chat/completions")
    url = _join_url(settings.base_url or preset.get("base_url", ""), path)
    headers = {
        "Authorization": f"Bearer {settings.api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.model or preset.get("model", ""),
        "messages": messages,
        "temperature": 0.62,
        "max_tokens": 600,
    }
    if settings.api_provider in {"zhipu", "deepseek"}:
        # 微信回复建议器不需要深度推理：禁用思考模式，避免推理内容挤占输出配额
        # 导致 content 为空（DeepSeek v4-flash 默认思考模式，max_tokens 小的时候尤其容易触发）
        payload["thinking"] = {"type": "disabled"}
    return TextGenerationRequest(url=url, headers=headers, payload=payload)


def extract_text_from_response(data: Any, settings: AppSettings) -> str:
    if settings.api_provider == "custom_http" and settings.custom_response_path.strip():
        value = _get_path(data, settings.custom_response_path.strip())
        return "" if value is None else str(value)
    value = _get_path(data, "choices.0.message.content")
    if value is None:
        value = _get_path(data, "choices.0.text")
    if value is None:
        value = _get_path(data, "output.text")
    if value is None:
        value = _get_path(data, "data.text")
    return "" if value is None else str(value)


def provider_source_label(settings: AppSettings) -> str:
    if settings.api_provider == "custom_http":
        return "Custom HTTP API"
    return f"{provider_defaults(settings.api_provider).get('label', settings.api_provider)} API"


def _build_custom_request(settings: AppSettings, messages: list[dict[str, str]]) -> TextGenerationRequest:
    prompt = _messages_to_prompt(messages)
    headers_template = settings.custom_headers.strip() or '{"Authorization":"Bearer {api_key}","Content-Type":"application/json"}'
    body_template = settings.custom_body.strip() or '{"model":"{model}","messages":{messages_json}}'
    headers = _loads_template(headers_template, settings, messages, prompt, string_values=True)
    payload = _loads_template(body_template, settings, messages, prompt, string_values=False)
    return TextGenerationRequest(url=settings.base_url.strip(), headers=headers, payload=payload)


def _loads_template(
    template: str,
    settings: AppSettings,
    messages: list[dict[str, str]],
    prompt: str,
    string_values: bool,
) -> dict[str, Any]:
    rendered = _render_template(template, settings, messages, prompt, string_values=string_values)
    data = json.loads(rendered)
    if not isinstance(data, dict):
        raise ValueError("template must render to a JSON object")
    return data


def _render_template(
    template: str,
    settings: AppSettings,
    messages: list[dict[str, str]],
    prompt: str,
    string_values: bool,
) -> str:
    replacements = {
        "{api_key}": settings.api_key,
        "{model}": settings.model,
        "{base_url}": settings.base_url,
        "{prompt}": prompt,
    }
    rendered = template
    for key, value in replacements.items():
        if string_values:
            rendered = rendered.replace(key, str(value))
        else:
            rendered = rendered.replace(key, _json_string_fragment(str(value)))
    rendered = rendered.replace("{messages_json}", json.dumps(messages, ensure_ascii=False))
    return rendered


def _messages_to_prompt(messages: list[dict[str, str]]) -> str:
    return "\n".join(str(item.get("content", "")) for item in messages if item.get("role") == "user").strip()


def _json_string_fragment(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)[1:-1]


def _join_url(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions") or base.endswith("/responses"):
        return base
    return f"{base}{path}"


def _get_path(data: Any, path: str) -> Any:
    current = data
    for part in path.split("."):
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except Exception:
                return None
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current
