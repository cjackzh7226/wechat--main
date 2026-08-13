from __future__ import annotations

import re
import time
import urllib.parse

try:
    import requests
except Exception:  # pragma: no cover
    requests = None


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

SEARCH_TIMEOUT = 6.0

# 简单内存缓存：同一问题 5 分钟内不重复搜索
_CACHE: dict[str, tuple[float, list[dict[str, str]]]] = {}
_CACHE_TTL = 300.0

# 事实性问题触发词：命中任一且非闲聊跳过词才触发搜索
_FACTUAL_MARKERS = (
    "是什么", "什么是", "是谁", "哪个", "哪些", "多少", "几个", "几部", "第几",
    "什么时候", "何时", "哪里", "在哪", "为什么", "为啥", "什么原因", "怎么回事",
    "多少钱", "价格", "多少钱啊", "贵不贵", "值不值",
    "最新", "现在", "今年", "最近", "今天", "目前", "现在出到", "出到",
    "上映", "发布", "上市", "开售", "开卖", "更新", "上线",
    "新闻", "消息", "传闻", "进展", "结果", "排名", "排行榜",
    "百科", "介绍", "资料", "背景", "区别", "对比",
)

# 闲聊/个人话题：不搜索
_SKIP_MARKERS = (
    "在吗", "在不在", "收到没", "睡了没", "忙吗", "吃了没", "有空吗",
    "干嘛呢", "干啥呢", "在干嘛", "约吗", "出来玩", "到家没",
    "你叫什么", "你是谁", "你是机器人吗",
)

# 搜索失败次数过多时，短暂熔断，避免每次回复都被搜索拖慢
_FAIL_COUNT = 0
_FAIL_RESET_AT = 0.0
_CIRCUIT_BREAK_SECONDS = 60.0


def should_search(text: str) -> bool:
    """判断对方消息是否需要实时搜索（事实性问题启发式）。

    命中事实触发词、且不在闲聊跳过词里、且像在问别人/别的事，才返回 True。
    """
    text = (text or "").strip()
    if not text or len(text) < 4:
        return False
    if any(marker in text for marker in _SKIP_MARKERS):
        return False
    return any(marker in text for marker in _FACTUAL_MARKERS)


def search_web(query: str, num_results: int = 3) -> list[dict[str, str]]:
    """搜索并返回 [{title, snippet, url}]。失败或超时返回空列表，绝不抛异常。

    依次尝试 DuckDuckGo、Bing，带 5 分钟缓存和 1 分钟熔断。
    """
    global _FAIL_COUNT, _FAIL_RESET_AT
    if requests is None:
        return []
    query = (query or "").strip()
    if not query:
        return []
    now = time.time()
    if _FAIL_COUNT >= 2 and now - _FAIL_RESET_AT < _CIRCUIT_BREAK_SECONDS:
        return []

    cache_key = f"{query}|{num_results}"
    cached = _CACHE.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    results = _search_ddg(query, num_results)
    if not results:
        results = _search_bing(query, num_results)

    if results:
        _CACHE[cache_key] = (now, results)
        _FAIL_COUNT = 0
    else:
        _FAIL_COUNT += 1
        _FAIL_RESET_AT = now
    return results


def format_search_context(results: list[dict[str, str]], max_items: int = 3) -> str:
    """把搜索结果格式化成注入 prompt 的参考块；没有结果返回空字符串。"""
    items = results[:max_items]
    if not items:
        return ""
    lines = ["实时参考信息（网络搜索摘要，回答事实性问题时优先参考，不要照抄）："]
    for index, item in enumerate(items, 1):
        title = _clean_text(item.get("title", ""))
        snippet = _clean_text(item.get("snippet", ""))
        url = (item.get("url") or "").strip()
        line = f"{index}. {title}"
        if snippet:
            line += f"：{snippet}"
        if url:
            line += f"（{url}）"
        lines.append(line)
    return "\n".join(lines)


# ---------- 搜索引擎后端 ----------


def _search_ddg(query: str, num_results: int) -> list[dict[str, str]]:
    try:
        url = "https://html.duckduckgo.com/html/"
        resp = requests.post(url, data={"q": query}, headers=_HEADERS, timeout=SEARCH_TIMEOUT)
        if resp.status_code != 200:
            return []
        html = resp.text
    except Exception:
        return []

    results: list[dict[str, str]] = []
    blocks = re.split(r'class="result results_links', html)[1:]
    for block in blocks:
        title_match = re.search(r'class="result__a"[^>]*>(.*?)</a>', block, re.S)
        snippet_match = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.S)
        url_match = re.search(r'class="result__url"[^>]*>(.*?)</a>', block, re.S)
        if not title_match:
            continue
        title = _strip_html(title_match.group(1))
        snippet = _strip_html(snippet_match.group(1)) if snippet_match else ""
        raw_url = url_match.group(1) if url_match else ""
        url = _decode_ddg_url(raw_url)
        if not title:
            continue
        results.append({"title": title, "snippet": snippet, "url": url})
        if len(results) >= num_results:
            break
    return results


def _decode_ddg_url(raw: str) -> str:
    url = _strip_html(raw).strip()
    if "duckduckgo.com/l/?uddg=" in url:
        try:
            url = urllib.parse.unquote(url.split("uddg=", 1)[1].split("&", 1)[0])
        except Exception:
            pass
    return url


def _search_bing(query: str, num_results: int) -> list[dict[str, str]]:
    try:
        url = "https://www.bing.com/search?q=" + urllib.parse.quote(query) + "&setlang=zh-hans&count=" + str(num_results)
        resp = requests.get(url, headers=_HEADERS, timeout=SEARCH_TIMEOUT)
        if resp.status_code != 200:
            return []
        html = resp.text
    except Exception:
        return []

    results: list[dict[str, str]] = []
    blocks = re.split(r'<li class="b_algo"', html)[1:]
    for block in blocks:
        link_match = re.search(r'<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a></h2>', block, re.S)
        snippet_match = re.search(r'<p[^>]*>(.*?)</p>', block, re.S)
        if not link_match:
            continue
        title = _strip_html(link_match.group(2))
        url = link_match.group(1)
        snippet = _strip_html(snippet_match.group(1)) if snippet_match else ""
        if not title:
            continue
        results.append({"title": title, "snippet": snippet, "url": url})
        if len(results) >= num_results:
            break
    return results


# ---------- 文本清洗 ----------


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _clean_text(text: str) -> str:
    text = _strip_html(text)
    text = re.sub(r"\s+", " ", text).strip(" .,，。;；")
    if len(text) > 120:
        text = text[:120].rstrip() + "…"
    return text
