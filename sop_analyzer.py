from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import json
import re
from typing import Iterable

from chat_training import ChatTurn, _iter_export_files, _parse_export_file
from llm_clients import build_text_generation_request, extract_text_from_response, provider_source_label
from memory import StyleMemory
from settings import AppSettings

try:
    import requests
except Exception:
    requests = None


SOP_SOURCE = "sop_analysis_training"
SOP_LIBRARY_VERSION = 1
FLOW_LIBRARY_VERSION = 3
FLOW_STAGE_PATTERNS: list[tuple[int, str, tuple[str, ...]]] = [
    (10, "客户进线/开场", ("刚添加", "开始聊", "你好", "您好", "了解", "咨询", "在吗", "可以开始")),
    (20, "需求判断", ("想做", "需要", "需求", "哪方面", "财运", "健康", "姻缘", "活动类型", "什么活动")),
    (30, "产品/方案说明", ("怎么用", "怎么放", "放哪里", "摆放", "佩戴", "资料", "方案", "介绍", "说明")),
    (40, "信任建立/异议处理", ("真假", "靠谱", "骗人", "假的", "担心", "怕", "不确定", "有没有用")),
    (50, "资料收集", ("姓名", "生日", "生辰", "地址", "门店", "电话", "资料", "订单号", "截图")),
    (60, "报价/付款推进", ("多少钱", "价格", "费用", "付款", "支付", "转账", "收款", "下单", "随喜")),
    (70, "成交确认/交付安排", ("已付款", "付款截图", "收到了", "登记", "安排", "发货", "视频发您", "后面怎么安排")),
    (80, "售后/复购承接", ("售后", "查单", "核销", "到期", "怎么处理", "没收到", "退款", "复购")),
]

FLOW_STAGE_PATTERNS_ZH: list[tuple[int, str, tuple[str, ...]]] = [
    (10, "客户进线/开场", ("刚添加", "开始聊", "你好", "您好", "了解", "咨询", "在吗", "可以开始")),
    (20, "需求判断", ("想做", "需要", "需求", "哪方面", "财运", "健康", "姻缘", "活动类型", "什么活动")),
    (30, "产品/方案说明", ("怎么用", "怎么放", "放哪里", "摆放", "佩戴", "钱母", "聚宝盆", "福宝", "资料", "方案", "介绍", "说明")),
    (40, "信任建立/异议处理", ("真假", "靠谱", "骗人", "假的", "担心", "不确定", "有没有用")),
    (50, "资料收集", ("姓名", "生日", "生辰", "出生", "年月日", "地址", "门店", "电话", "资料", "订单号", "截图")),
    (60, "报价/付款推进", ("多少钱", "价格", "费用", "付款", "支付", "转账", "收款", "下单", "随喜")),
        (70, "成交确认/交付安排", ("已付款", "付款", "付款成功", "付款截图", "收到了", "收到", "登记", "安排", "发货", "视频发您", "后面怎么安排")),
    (80, "售后/复购承接", ("售后", "查单", "核销", "到期", "怎么处理", "没收到", "退款", "复购")),
]

FLOW_STAGE_EXTRA_TERMS: list[tuple[int, str, tuple[str, ...]]] = [
    (10, "客户进线/开场", ("添加", "开始聊", "娣诲姞", "寮€濮嬭亰")),
    (30, "产品/方案说明", ("怎么使用", "怎么放", "放置", "钱母", "手机后面", "鎬庝箞浣跨敤", "鎬庝箞鏀", "鏀剧疆", "閽辨瘝", "鎵嬫満")),
    (50, "资料收集", ("生日", "生辰", "年月日", "名字", "鐢熸棩", "鐢熻景", "骞", "鏈", "鏃")),
        (70, "成交确认/交付安排", ("已经付款", "已付款", "付款成功", "付款截图", "后面怎么安排", "浠樻", "鍚庨潰", "瀹夋帓")),
]


def analyze_chat_turns(turns: Iterable[ChatTurn]) -> dict[str, object]:
    items = list(turns)
    scene_stats: dict[str, dict[str, object]] = {}
    open_customer_texts: list[str] = []
    total_customer_turns = 0

    for turn in items:
        text = _clean_text(turn.text)
        if not text:
            continue
        if turn.speaker == "A":
            total_customer_turns += 1
            open_customer_texts.append(text)
            continue
        if turn.speaker != "B" or not open_customer_texts:
            continue
        customer_text = "\n".join(open_customer_texts)
        scene = classify_sop_scene(customer_text)
        reply = _clean_reply(text)
        if not reply:
            open_customer_texts = []
            continue
        stat = scene_stats.setdefault(
            scene,
            {
                "scene": scene,
                "count": 0,
                "customer_examples": [],
                "reply_counts": Counter(),
            },
        )
        stat["count"] = int(stat["count"]) + len(open_customer_texts)
        examples = stat["customer_examples"]
        if isinstance(examples, list):
            examples.append(customer_text)
        reply_counts = stat["reply_counts"]
        if isinstance(reply_counts, Counter):
            reply_counts[reply] += 1
        open_customer_texts = []

    scenes = _sort_scenes_for_customer_journey([_format_scene_stat(stat) for stat in scene_stats.values()])
    return {
        "total_customer_turns": total_customer_turns,
        "scene_count": len(scenes),
        "scenes": scenes,
        "sop_steps": _build_sop_steps(scenes),
    }


def analyze_training_source_to_memory(
    source_path: Path | str,
    memory: StyleMemory,
    max_examples: int = 3000,
    settings: AppSettings | None = None,
    post=None,
) -> dict[str, object]:
    all_turns: list[ChatTurn] = []
    files = 0
    for name, raw in _iter_export_files(Path(source_path)):
        files += 1
        turns = _parse_export_file(name, raw, own_names=("我", "客服", "老师", "顾问", "运营", "销售", "店长"))
        all_turns.extend(turns)

    report = _analyze_turns_prefer_llm(all_turns, settings, post=post)
    examples = _report_to_memory_examples(report)[:max_examples]
    memory.load()
    memory.examples = [item for item in memory.examples if item.get("source") != SOP_SOURCE]
    memory.learn_from_training_examples(examples)
    result = dict(report)
    result.update({"files": files, "turns": len(all_turns), "examples": len(examples)})
    return result


def build_sop_library_from_memory(memory: StyleMemory, limit: int = 500) -> dict[str, object]:
    memory.load()
    source_examples = [
        item
        for item in memory.examples[-limit:]
        if str(item.get("cue", "")).strip() and str(item.get("reply", "")).strip()
    ]
    scene_stats: dict[str, dict[str, object]] = {}
    for item in source_examples:
        cue = str(item.get("cue", "")).strip()
        reply = _clean_reply(str(item.get("reply", "")))
        if not reply:
            continue
        scene = str(item.get("scenario_title", "")).strip() or _derive_scene_title(cue)
        stat = scene_stats.setdefault(
            scene,
            {
                "scene": scene,
                "count": 0,
                "customer_examples": [],
                "reply_counts": Counter(),
            },
        )
        stat["count"] = int(stat["count"]) + 1
        examples = stat["customer_examples"]
        if isinstance(examples, list):
            for line in _customer_lines_from_cue(cue):
                if line and line not in examples:
                    examples.append(line)
        reply_counts = stat["reply_counts"]
        if isinstance(reply_counts, Counter):
            reply_counts[reply] += 1

    scenes = _sort_scenes_for_customer_journey([_format_scene_stat(stat) for stat in scene_stats.values()])
    flow_nodes = _build_flow_nodes_from_examples(source_examples)
    return {
        "version": SOP_LIBRARY_VERSION,
        "source": "current_phrasebook",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_examples": len(source_examples),
        "scene_count": len(scenes),
        "scenes": scenes,
        "flow_nodes": flow_nodes,
        "flow_version": FLOW_LIBRARY_VERSION,
        "sop_steps": _build_flow_steps(flow_nodes) if flow_nodes else _build_sop_steps(scenes),
    }


def sop_library_from_report(report: dict[str, object], source: str = "sop_analysis") -> dict[str, object]:
    scenes = report.get("scenes", [])
    if not isinstance(scenes, list):
        scenes = []
    scenes = _sort_scenes_for_customer_journey([scene for scene in scenes if isinstance(scene, dict)])
    steps = report.get("sop_steps", [])
    if not isinstance(steps, list):
        steps = []
    flow_nodes = report.get("flow_nodes", [])
    if not isinstance(flow_nodes, list):
        flow_nodes = []
    library = {
        "version": SOP_LIBRARY_VERSION,
        "source": source,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "analysis_source": report.get("analysis_source", ""),
        "total_examples": report.get("examples", report.get("total_customer_turns", 0)),
        "scene_count": len(scenes),
        "scenes": scenes,
        "flow_nodes": flow_nodes,
        "sop_steps": [str(item).strip() for item in steps if str(item).strip()]
        or (_build_flow_steps(flow_nodes) if flow_nodes else _build_sop_steps(scenes)),
        "sales_skills": report.get("sales_skills", []),
    }
    return ensure_sop_library_flow(library)


def save_sop_library(library: dict[str, object], path: Path | str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(library, ensure_ascii=False, indent=2), encoding="utf-8")


def load_sop_library(path: Path | str) -> dict[str, object]:
    target = Path(path)
    if not target.exists():
        return {}
    data = json.loads(target.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def ensure_sop_library_flow(library: dict[str, object]) -> dict[str, object]:
    data = dict(library or {})
    flow_nodes = data.get("flow_nodes")
    if (
        isinstance(flow_nodes, list)
        and flow_nodes
        and _safe_int(data.get("flow_version", 0)) >= FLOW_LIBRARY_VERSION
    ):
        return data
    scenes = data.get("scenes", [])
    if not isinstance(scenes, list):
        scenes = []
    nodes = _build_flow_nodes_from_scenes([scene for scene in scenes if isinstance(scene, dict)])
    data["flow_nodes"] = nodes
    data["flow_version"] = FLOW_LIBRARY_VERSION
    if nodes:
        data["scene_count"] = len(nodes)
        data["sop_steps"] = _build_flow_steps(nodes)
    elif scenes:
        data["sop_steps"] = _build_sop_steps(scenes)
    return data


def format_sop_library_document(library: dict[str, object]) -> str:
    library = ensure_sop_library_flow(library)
    scenes = library.get("scenes", [])
    if not isinstance(scenes, list):
        scenes = []
    scenes = _sort_scenes_for_customer_journey([scene for scene in scenes if isinstance(scene, dict)])
    flow_nodes = library.get("flow_nodes", [])
    if isinstance(flow_nodes, list):
        flow_nodes = _sort_scenes_for_customer_journey([node for node in flow_nodes if isinstance(node, dict)])
    else:
        flow_nodes = []
    report = {
        "files": 0,
        "turns": library.get("total_examples", 0),
        "total_customer_turns": library.get("total_examples", 0),
        "scene_count": len(flow_nodes) if flow_nodes else len(scenes),
        "examples": library.get("total_examples", 0),
        "analysis_source": "当前话术库",
        "scenes": flow_nodes or scenes,
        "sop_steps": _build_flow_steps(flow_nodes) if flow_nodes else (_build_sop_steps(scenes) if scenes else library.get("sop_steps", [])),
        "sales_skills": library.get("sales_skills", []),
    }
    document = format_sop_document(report)
    return document.replace("命中率", "推荐置信度")


def format_sop_library_html(library: dict[str, object]) -> str:
    library = ensure_sop_library_flow(library)
    scenes = library.get("scenes", [])
    if not isinstance(scenes, list):
        scenes = []
    scenes = _sort_scenes_for_customer_journey([scene for scene in scenes if isinstance(scene, dict)])
    flow_nodes = library.get("flow_nodes", [])
    if isinstance(flow_nodes, list):
        flow_nodes = _sort_scenes_for_customer_journey([node for node in flow_nodes if isinstance(node, dict)])
    else:
        flow_nodes = []
    display_nodes = flow_nodes or scenes
    data = dict(library)
    data["scenes"] = scenes
    data["flow_nodes"] = display_nodes
    data["sop_steps"] = _build_flow_steps(flow_nodes) if flow_nodes else (_build_sop_steps(scenes) if scenes else list(data.get("sop_steps", []) or []))
    data["stats"] = {
        "records": data.get("total_examples", data.get("examples", 0)),
        "scenes": len(display_nodes),
        "rules": sum(len(item.get("rules", [])) for item in display_nodes if isinstance(item.get("rules", []), list)),
        "steps": len(data.get("sop_steps", [])),
    }
    payload = json.dumps(data, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>话术流程分析演示</title>
  <style>
    :root {{
      --bg: #eef5f8; --panel: #ffffff; --line: #cfe0ea; --muted: #5f7485;
      --text: #0b2233; --accent: #0b938d; --soft: #e9f7f5;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
      background: var(--bg); color: var(--text); font-size: 14px;
    }}
    .app {{ display: grid; grid-template-columns: 270px 1fr; min-height: 100vh; }}
    .side {{ border-right: 1px solid var(--line); background: #f7fbfd; padding: 18px 14px; overflow-y: auto; }}
    .brand {{ display: flex; gap: 10px; align-items: center; margin-bottom: 14px; }}
    .logo {{ width: 38px; height: 38px; border-radius: 10px; background: var(--accent); color: white; display: grid; place-items: center; font-weight: 800; }}
    h1 {{ font-size: 17px; margin: 0 0 4px; }}
    h2 {{ font-size: 19px; margin: 0; }}
    .sub, .note, .meta, label {{ color: var(--muted); font-size: 12px; }}
    .stats {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 14px 0; }}
    .stat {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 10px; }}
    .stat b {{ display: block; color: var(--accent); font-size: 20px; margin-bottom: 2px; }}
    .scene-btn {{ width: 100%; border: 0; border-radius: 7px; padding: 10px 9px; margin: 6px 0; text-align: left; background: #e8f0f4; cursor: pointer; font-weight: 600; }}
    .scene-btn.on {{ background: var(--accent); color: white; }}
    .main {{ padding: 16px 18px; overflow: auto; }}
    .card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; margin-bottom: 14px; }}
    .top {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
    select {{ width: 100%; height: 38px; border: 1px solid #bcd2df; border-radius: 6px; padding: 0 10px; background: white; font-weight: 600; }}
    .head {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }}
    .tag {{ background: var(--soft); color: var(--accent); border-radius: 999px; padding: 5px 10px; font-weight: 700; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
    .box {{ border: 1px solid var(--line); border-radius: 8px; background: #f8fbfd; min-height: 235px; }}
    .boxt {{ background: #e6f0f6; padding: 10px 12px; font-weight: 700; border-radius: 8px 8px 0 0; }}
    .bubble {{ display: inline-block; margin: 14px; padding: 10px 13px; background: white; border: 1px solid var(--line); border-radius: 7px; max-width: 88%; }}
    .reply {{ margin: 10px 12px; padding: 12px; border: 1px solid var(--line); border-radius: 7px; display: grid; grid-template-columns: 28px 1fr auto; gap: 8px; align-items: start; background: white; }}
    .reply:first-child {{ border-color: var(--accent); background: var(--soft); }}
    .rank {{ color: var(--accent); font-weight: 800; }}
    .step {{ display: grid; grid-template-columns: 30px 1fr; gap: 10px; padding: 10px 0; border-bottom: 1px solid #e2edf3; }}
    .num {{ width: 22px; height: 22px; border-radius: 50%; background: var(--accent); color: white; display: grid; place-items: center; font-weight: 700; font-size: 12px; }}
  </style>
</head>
<body>
  <div class="app">
    <aside class="side">
      <div class="brand"><div class="logo">智</div><div><h1>话术流程分析演示</h1><div class="sub">基于当前话术库自动汇总</div></div></div>
      <div class="stats">
        <div class="stat"><b id="records">0</b><span>话术记录</span></div>
        <div class="stat"><b id="scenes">0</b><span>流程节点</span></div>
        <div class="stat"><b id="rules">0</b><span>推荐回复</span></div>
        <div class="stat"><b id="stepsCount">0</b><span>SOP步骤</span></div>
      </div>
      <p class="note">说明：左侧按客户从进线到成交/售后的流程排序；场景名来自话术库数据，不是固定分类。</p>
      <b>客户流程</b>
      <div id="sceneList"></div>
    </aside>
    <main class="main">
      <div class="card top">
        <div><label>当前客户说法</label><select id="examples"></select></div>
        <div><label>产品 / 资料线索</label><select><option>自动判断</option><option>产品咨询</option><option>资料领取</option><option>售后查询</option></select></div>
      </div>
      <section class="card">
        <div class="head"><h2 id="sceneName"></h2><span class="tag" id="count"></span></div>
        <div class="grid">
          <div class="box"><div class="boxt">模拟当前聊天</div><div class="bubble" id="bubble"></div></div>
          <div class="box"><div class="boxt">推荐候选回复</div><div id="replies"></div></div>
        </div>
      </section>
      <section class="card"><h2>从进线到成交的主流程</h2><div id="steps"></div></section>
    </main>
  </div>
  <script>
    const DATA = {payload};
    let current = 0;
    const esc = (t) => String(t || "").replace(/[&<>"']/g, (s) => ({{"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}}[s]));
    function draw() {{
      const stats = DATA.stats || {{}};
      records.textContent = stats.records || 0;
      scenes.textContent = stats.scenes || 0;
      rules.textContent = stats.rules || 0;
      stepsCount.textContent = stats.steps || 0;
      const sceneData = DATA.flow_nodes || DATA.scenes || [];
      sceneList.innerHTML = "";
      sceneData.forEach((item, index) => {{
        const btn = document.createElement("button");
        btn.className = "scene-btn " + (index === current ? "on" : "");
        btn.textContent = `${{index + 1}}. ${{item.title || item.scene}}  ${{item.count || 0}}条`;
        btn.onclick = () => {{ current = index; draw(); }};
        sceneList.appendChild(btn);
      }});
      const scene = sceneData[current] || {{ scene: "暂无场景", title: "暂无场景", count: 0, examples: [], customer_examples: [], rules: [] }};
      sceneName.textContent = scene.title || scene.scene;
      count.textContent = `${{scene.flow_stage || "流程节点"}} · ${{scene.count || 0}} 条记录`;
      const ex = (scene.examples && scene.examples.length ? scene.examples : scene.customer_examples) || ["暂无客户说法"];
      examples.innerHTML = "";
      ex.forEach((item) => {{
        const option = document.createElement("option");
        option.textContent = item;
        option.value = item;
        examples.appendChild(option);
      }});
      bubble.textContent = "客户：" + ex[0];
      examples.onchange = () => {{ bubble.textContent = "客户：" + examples.value; }};
      replies.innerHTML = "";
      const replyList = scene.rules && scene.rules.length ? scene.rules : [{{ reply: "暂无推荐回复", hit_rate: 0, count: 0 }}];
      replyList.slice(0, 3).forEach((item, index) => {{
        const div = document.createElement("div");
        div.className = "reply";
        div.innerHTML = `<div class="rank">${{index + 1}}.</div><div><b>推荐命中率 ${{Number(item.hit_rate || 0).toFixed(1)}}%</b><br>${{esc(item.reply)}}</div><div class="meta">${{item.count || 0}} 条记录</div>`;
        replies.appendChild(div);
      }});
      steps.innerHTML = "";
      (DATA.sop_steps || DATA.steps || []).forEach((item, index) => {{
        const div = document.createElement("div");
        div.className = "step";
        div.innerHTML = `<div class="num">${{index + 1}}</div><div>${{esc(item)}}</div>`;
        steps.appendChild(div);
      }});
    }}
    draw();
  </script>
</body>
</html>
"""


def write_sop_library_html(library: dict[str, object], path: Path | str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(format_sop_library_html(library), encoding="utf-8")
    return target


def analyze_chat_turns_with_llm(
    turns: Iterable[ChatTurn],
    settings: AppSettings,
    post=None,
) -> dict[str, object]:
    if not settings.api_key:
        raise RuntimeError("未配置文本模型 API Key")
    poster = post or (requests.post if requests is not None else None)
    if poster is None:
        raise RuntimeError("requests 组件不可用，无法调用文本模型")
    items = list(turns)
    request = build_text_generation_request(settings, _build_sop_messages(items))
    response = poster(request.url, headers=request.headers, json=request.payload, timeout=60)
    if int(getattr(response, "status_code", 200) or 200) >= 400:
        raise RuntimeError(f"HTTP {getattr(response, 'status_code', '')}")
    response.raise_for_status()
    content = extract_text_from_response(response.json(), settings)
    report = _parse_llm_report(content)
    report["analysis_source"] = provider_source_label(settings)
    report.setdefault("total_customer_turns", sum(1 for item in items if item.speaker == "A"))
    report.setdefault("scene_count", len(report.get("scenes", [])))
    return report


def _analyze_turns_prefer_llm(turns: list[ChatTurn], settings: AppSettings | None, post=None) -> dict[str, object]:
    if settings and settings.api_key:
        try:
            return analyze_chat_turns_with_llm(turns, settings, post=post)
        except Exception as exc:
            report = analyze_chat_turns(turns)
            report["analysis_source"] = "本地规则兜底"
            report["analysis_error"] = str(exc)
            return report
    report = analyze_chat_turns(turns)
    report["analysis_source"] = "本地规则"
    return report


def _build_sop_messages(turns: list[ChatTurn]) -> list[dict[str, str]]:
    transcript = _turns_to_transcript(turns, limit=18000)
    return [
        {
            "role": "system",
            "content": (
                "你是销售话术与客服 SOP 分析师。只根据用户提供的聊天记录分析，不要编造记录中没有的数据。"
                "输出必须是严格 JSON，不要 Markdown。"
            ),
        },
        {
            "role": "user",
            "content": (
                "请分析下面客户与我的聊天记录，目标是提炼一套完整的客户进线到成交完成的 SOP。"
                "必须输出 JSON，字段如下："
                "{"
                "\"scenes\":[{\"scene\":\"根据聊天记录提炼的具体场景名\",\"flow_order\":流程顺序数字,\"count\":次数,\"customer_examples\":[\"客户常见说法\"],"
                "\"rules\":[{\"reply\":\"我方推荐回复\",\"count\":命中次数,\"hit_rate\":命中率数字}]}],"
                "\"sop_steps\":[\"1. 客户进线：...\",\"2. 需求判断：...\",\"3. 资料收集：...\",\"4. 成交推进：...\",\"5. 售后承接：...\"],"
                "\"sales_skills\":[\"销冠 skill\"]"
                "}。"
                "要求：1. 场景名必须从聊天数据里总结，类似“客户刚添加后问能否开始聊”“客户付款后问后续安排”，不要直接套用固定分类名；"
                "2. 命中率只能基于聊天记录里的真实出现次数计算；"
                "3. 每条推荐回复必须来自或贴近聊天记录中的真实我方回复；"
                "4. scenes 必须按客户从进线到成交/售后的顺序排列，并给每个场景填写 flow_order，数值越小越靠前；"
                "5. SOP 必须覆盖客户进线、需求判断、资料收集、成交推进、售后/复购承接。\n\n"
                f"聊天记录：\n{transcript}"
            ),
        },
    ]


def _turns_to_transcript(turns: list[ChatTurn], limit: int) -> str:
    lines = []
    for turn in turns:
        role = "客户" if turn.speaker == "A" else "我"
        text = _clean_text(turn.text)
        if text:
            lines.append(f"{role}：{text}")
    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    return text[-limit:]


def _parse_llm_report(content: str) -> dict[str, object]:
    data = _load_json_object(content)
    scenes = data.get("scenes", [])
    if not isinstance(scenes, list):
        scenes = []
    normalized_scenes = []
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        normalized_scenes.append(
            {
                "scene": str(scene.get("scene", "未命名场景")).strip() or "未命名场景",
                "count": _safe_int(scene.get("count", 0)),
                "customer_examples": [str(item).strip() for item in scene.get("customer_examples", []) if str(item).strip()]
                if isinstance(scene.get("customer_examples", []), list)
                else [],
                "rules": _normalize_llm_rules(scene.get("rules", [])),
                "flow_order": _safe_int(scene.get("flow_order", 0)),
            }
        )
    normalized_scenes = _sort_scenes_for_customer_journey(normalized_scenes)
    sop_steps = data.get("sop_steps", [])
    if not isinstance(sop_steps, list):
        sop_steps = []
    sales_skills = data.get("sales_skills", [])
    if not isinstance(sales_skills, list):
        sales_skills = []
    return {
        "scenes": normalized_scenes,
        "scene_count": len(normalized_scenes),
        "sop_steps": [str(item).strip() for item in sop_steps if str(item).strip()] or _build_sop_steps(normalized_scenes),
        "sales_skills": [str(item).strip() for item in sales_skills if str(item).strip()],
    }


def _load_json_object(content: str) -> dict[str, object]:
    text = (content or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("模型未返回 JSON 对象")
    return data


def _normalize_llm_rules(raw_rules: object) -> list[dict[str, object]]:
    if not isinstance(raw_rules, list):
        return []
    rules = []
    for rule in raw_rules:
        if not isinstance(rule, dict):
            continue
        reply = str(rule.get("reply", "")).strip()
        if not reply:
            continue
        rules.append(
            {
                "reply": reply,
                "count": _safe_int(rule.get("count", 0)),
                "hit_rate": _safe_float(rule.get("hit_rate", 0)),
            }
        )
    return rules


def _safe_int(value: object) -> int:
    try:
        return max(0, int(float(str(value))))
    except Exception:
        return 0


def _safe_float(value: object) -> float:
    try:
        return round(max(0.0, min(100.0, float(str(value).replace("%", "")))), 1)
    except Exception:
        return 0.0


def _sort_scenes_for_customer_journey(scenes: list[dict[str, object]]) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    for index, scene in enumerate(scenes):
        item = dict(scene)
        item["_original_index"] = index
        fields = _infer_flow_fields(
            str(item.get("scene", "")),
            item.get("customer_examples", []),
            item.get("rules", []),
        )
        if _safe_int(item.get("flow_order", 0)) <= 0:
            item["flow_order"] = fields["flow_order"]
        if not str(item.get("flow_stage", "")).strip():
            item["flow_stage"] = fields["flow_stage"]
        normalized.append(item)
    normalized.sort(
        key=lambda item: (
            _safe_int(item.get("flow_order", 999)),
            _safe_int(item.get("_original_index", 0)),
            -_safe_int(item.get("count", 0)),
        )
    )
    for item in normalized:
        item.pop("_original_index", None)
    return normalized


def _build_flow_nodes_from_examples(examples: list[dict[str, object]], max_nodes: int = 10) -> list[dict[str, object]]:
    buckets: dict[int, dict[str, object]] = {}
    for item in examples:
        cue = str(item.get("cue", "")).strip()
        reply = _clean_reply(str(item.get("reply", "")))
        if not cue or not reply:
            continue
        customer_lines = _customer_lines_from_cue(cue)
        if not customer_lines:
            continue
        latest_customer = customer_lines[-1]
        fields = _infer_flow_fields("", [latest_customer], [{"reply": reply}])
        order = _safe_int(fields.get("flow_order", 90))
        bucket = buckets.setdefault(
            order,
            {
                "flow_order": order,
                "flow_stage": fields.get("flow_stage", "后续承接"),
                "count": 0,
                "customer_examples": [],
                "reply_counts": Counter(),
                "first_seen_index": len(buckets),
            },
        )
        bucket["count"] = int(bucket["count"]) + 1
        stored_examples = bucket["customer_examples"]
        if isinstance(stored_examples, list):
            if latest_customer and latest_customer not in stored_examples:
                stored_examples.append(latest_customer)
        reply_counts = bucket["reply_counts"]
        if isinstance(reply_counts, Counter):
            reply_counts[reply] += 1

    nodes = []
    for bucket in sorted(buckets.values(), key=lambda item: (_safe_int(item.get("flow_order", 90)), _safe_int(item.get("first_seen_index", 0))))[:max_nodes]:
        count = max(1, int(bucket.get("count", 0)))
        examples_for_node = list(bucket.get("customer_examples", []) if isinstance(bucket.get("customer_examples"), list) else [])[:8]
        reply_counts = bucket.get("reply_counts")
        if not isinstance(reply_counts, Counter):
            reply_counts = Counter()
        rules = [
            {"reply": reply, "count": hit_count, "hit_rate": round(hit_count / count * 100, 1)}
            for reply, hit_count in reply_counts.most_common(5)
        ]
        stage = str(bucket.get("flow_stage", "后续承接"))
        title = _flow_node_title(stage, examples_for_node)
        nodes.append(
            {
                "step": len(nodes) + 1,
                "title": title,
                "scene": title,
                "flow_stage": stage,
                "flow_order": _safe_int(bucket.get("flow_order", 90)),
                "count": count,
                "customer_examples": examples_for_node,
                "rules": rules,
            }
        )
    return nodes


def _build_flow_nodes_from_scenes(scenes: list[dict[str, object]], max_nodes: int = 12) -> list[dict[str, object]]:
    buckets: dict[int, dict[str, object]] = {}
    for scene in scenes:
        raw_rules = scene.get("rules", [])
        rules = raw_rules if isinstance(raw_rules, list) else []
        raw_examples = scene.get("customer_examples", [])
        examples = raw_examples if isinstance(raw_examples, list) else []
        if not examples:
            scene_text = str(scene.get("scene", "")).strip()
            examples = [scene_text] if scene_text else []
        for sample in examples:
            customer_text = _clean_text(str(sample))
            if not customer_text:
                continue
            if _is_ack_only_flow_sample(customer_text):
                continue
            fields = _infer_flow_fields("", [customer_text], [])
            if _safe_int(fields.get("flow_order", 90)) >= 90:
                fields = _infer_flow_fields("", [customer_text], rules)
            if _safe_int(fields.get("flow_order", 90)) >= 90 and _is_weak_flow_title_sample(customer_text):
                continue
            order = _safe_int(fields.get("flow_order", 90))
            bucket = buckets.setdefault(
                order,
                {
                    "flow_order": order,
                    "flow_stage": fields.get("flow_stage", "后续承接"),
                    "count": 0,
                    "customer_examples": [],
                    "reply_counts": Counter(),
                    "first_seen_index": len(buckets),
                },
            )
            bucket["count"] = int(bucket["count"]) + max(1, _safe_int(scene.get("count", 1)) // max(1, len(examples)))
            stored_examples = bucket["customer_examples"]
            if isinstance(stored_examples, list) and customer_text not in stored_examples:
                stored_examples.append(customer_text)
            reply_counts = bucket["reply_counts"]
            if isinstance(reply_counts, Counter):
                for rule in rules[:5]:
                    if not isinstance(rule, dict):
                        continue
                    reply = _clean_reply(str(rule.get("reply", "")))
                    if not reply:
                        continue
                    reply_counts[reply] += max(1, _safe_int(rule.get("count", 1)))

    nodes = []
    for bucket in sorted(buckets.values(), key=lambda item: (_safe_int(item.get("flow_order", 90)), _safe_int(item.get("first_seen_index", 0))))[:max_nodes]:
        count = max(1, int(bucket.get("count", 0)))
        examples_for_node = list(bucket.get("customer_examples", []) if isinstance(bucket.get("customer_examples"), list) else [])[:8]
        reply_counts = bucket.get("reply_counts")
        if not isinstance(reply_counts, Counter):
            reply_counts = Counter()
        rules = [
            {"reply": reply, "count": hit_count, "hit_rate": round(hit_count / max(1, sum(reply_counts.values())) * 100, 1)}
            for reply, hit_count in reply_counts.most_common(5)
        ]
        stage = str(bucket.get("flow_stage", "后续承接"))
        title = _flow_node_title(stage, examples_for_node)
        nodes.append(
            {
                "step": len(nodes) + 1,
                "title": title,
                "scene": title,
                "flow_stage": stage,
                "flow_order": _safe_int(bucket.get("flow_order", 90)),
                "count": count,
                "customer_examples": examples_for_node,
                "rules": rules,
            }
        )
    return nodes


def _flow_node_title(stage: str, examples: list[str]) -> str:
    sample = _best_flow_title_sample_for_stage(stage, examples) or _best_flow_title_sample(examples)
    sample = _clean_text(sample)
    if len(sample) > 18:
        sample = sample[:18].rstrip("，。！？,.!? ") + "…"
    return f"{stage}：{sample}" if sample else stage


def _best_flow_title_sample_for_stage(stage: str, examples: list[str]) -> str:
    stage_text = _compact(stage)
    stage_terms = [
        ("进线开场客户", ("添加", "开始聊", "你好", "您好", "娣诲姞", "寮€濮嬭亰")),
        ("需求判断", ("想做", "需求", "财运", "事业", "健康", "姻缘", "闇€姹", "璐㈣繍")),
        ("产品方案说明", ("怎么用", "怎么使用", "怎么放", "放置", "钱母", "聚宝盆", "福宝", "佩戴", "鎬庝箞", "閽辨瘝", "绂忓疂")),
        ("信任异议处理", ("真假", "假的", "骗人", "担心", "靠谱吗", "鐪熷亣")),
        ("资料收集", ("姓名", "名字", "生日", "生辰", "年月日", "地址", "电话", "鐢熸棩", "鐢熻景")),
        ("报价付款推进", ("多少钱", "价格", "费用", "随喜", "付款", "支付", "澶氬皯", "浠樻")),
        ("成交确认交付安排", ("已付款", "付款成功", "付款截图", "后面怎么安排", "收到", "登记", "安排", "浠樻")),
        ("售后复购承接", ("到期", "售后", "没收到", "退款", "复购", "怎么处理", "鍒版湡")),
    ]
    for stage_key, terms in stage_terms:
        if not any(key in stage_text for key in stage_key):
            continue
        ranked = []
        for index, sample in enumerate(examples):
            text = _clean_text(sample)
            if _is_ack_only_flow_sample(text):
                continue
            compact = _compact(text)
            score = sum(1 for term in terms if _compact(term) in compact)
            if any(key in stage_text for key in "进线开场客户") and any(term in compact for term in ("添加", "加好", "娣诲姞")):
                score += 5
            if score:
                ranked.append((score, len(text), -index, text))
        if ranked:
            ranked.sort(reverse=True)
            return ranked[0][3]
    return ""


def _is_ack_only_flow_sample(text: str) -> bool:
    compact = re.sub(r"[，。！？!?.、,~～\s🙏🤝您师傅福主兄]+", "", _compact(text))
    if not compact:
        return True
    ack_words = {
        "好",
        "好的",
        "好的谢谢",
        "谢谢",
        "谢谢师傅",
        "收到",
        "收到了",
        "嗯",
        "是的",
        "对",
        "知道了",
        "可以",
    }
    return compact in ack_words


def _best_flow_title_sample(examples: list[str]) -> str:
    for sample in examples:
        text = _clean_text(sample)
        if _is_weak_flow_title_sample(text):
            continue
        return text
    return ""


def _is_weak_flow_title_sample(text: str) -> bool:
    compact = _compact(text)
    if not compact:
        return True
    if compact in {"对", "好的", "好", "嗯", "是的", "知道了", "收到", "谢谢", "感谢"}:
        return True
    if len(compact) <= 3:
        return True
    if re.search(r"(生日|生辰|阴历|农历|阳历|姓名|名字|电话|手机|地址)[:：]?", text):
        return True
    if re.search(r"\d{2,4}[年./-]\d{1,2}[月./-]\d{0,2}", text):
        return True
    return False


def _build_flow_steps(flow_nodes: list[dict[str, object]]) -> list[str]:
    steps = []
    for index, node in enumerate(flow_nodes, 1):
        rules = node.get("rules")
        top_reply = ""
        if isinstance(rules, list) and rules:
            top_reply = str(rules[0].get("reply", ""))
        title = str(node.get("title") or node.get("scene") or node.get("flow_stage") or f"流程节点{index}")
        if top_reply:
            steps.append(f"{index}. {title}：推荐承接话术：{top_reply}")
        else:
            steps.append(f"{index}. {title}：确认客户意图后推进下一步。")
    return steps


def _infer_flow_fields(scene: str, examples: object, rules: object) -> dict[str, object]:
    texts = [scene]
    if isinstance(examples, list):
        texts.extend(str(item) for item in examples[:8])
    if isinstance(rules, list):
        for rule in rules[:5]:
            if isinstance(rule, dict):
                texts.append(str(rule.get("reply", "")))
    compacted = _compact("\n".join(texts))
    direct = _direct_flow_fields(compacted)
    if direct:
        return direct
    best_order = 90
    best_stage = "后续承接"
    best_score = 0
    for order, stage, terms in [*FLOW_STAGE_EXTRA_TERMS, *FLOW_STAGE_PATTERNS_ZH, *FLOW_STAGE_PATTERNS]:
        score = sum(1 for term in terms if _compact(term) in compacted)
        if score > best_score or (score == best_score and score > 0 and order < best_order):
            best_order = order
            best_stage = stage
            best_score = score
    return {"flow_order": best_order, "flow_stage": best_stage}


def _direct_flow_fields(compacted: str) -> dict[str, object] | None:
    checks = [
        (70, "成交确认/交付安排", ("已经付款", "已付款", "付款截图", "后面怎么安排", "浠樻", "鍚庨潰鎬庝箞瀹夋帓")),
        (50, "资料收集", ("生日", "生辰", "年月日", "出生", "名字", "鐢熸棩", "鐢熻景", "骞", "鏈", "鏃")),
        (30, "产品/方案说明", ("怎么使用", "怎么用", "怎么放", "放置", "钱母", "聚宝盆", "福宝", "手机后面", "鎬庝箞浣跨敤", "鎬庝箞鏀", "鏀剧疆", "閽辨瘝", "绂忓疂", "鎵嬫満")),
        (10, "客户进线/开场", ("刚添加", "添加了你", "开始聊天", "娣诲姞", "寮€濮嬭亰")),
    ]
    for order, stage, terms in checks:
        if any(_compact(term) in compacted for term in terms):
            return {"flow_order": order, "flow_stage": stage}
    return None


def _derive_scene_title(cue: str) -> str:
    for line in _customer_lines_from_cue(cue):
        text = _clean_text(line)
        if not text:
            continue
        if len(text) > 24:
            text = text[:24].rstrip("，。！？,.!? ") + "…"
        return f"客户说：{text}"
    fallback = _clean_text(cue)
    if len(fallback) > 24:
        fallback = fallback[:24].rstrip("，。！？,.!? ") + "…"
    return f"客户说：{fallback or '未命名场景'}"


def sop_document_from_memory(memory: StyleMemory) -> str:
    items = [item for item in memory.examples if item.get("source") == SOP_SOURCE]
    if not items:
        return (
            "聊小智 SOP 分析报告\n\n"
            "当前还没有 SOP 分析结果。\n\n"
            "请点击“导入聊天记录并重新分析”，上传客户与你的聊天记录后，"
            "系统会自动提炼场景、推荐回复、命中率和 SOP 流程。"
        )
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for item in items:
        scene = str(item.get("scenario_title", "") or "未命名场景")
        grouped[scene].append(item)
    scenes = []
    for scene, rows in grouped.items():
        customer_examples: list[str] = []
        for cue in (str(row.get("cue", "")) for row in rows):
            for line in cue.splitlines():
                line = line.strip()
                if line and line not in customer_examples:
                    customer_examples.append(line)
        rules = []
        for row in rows:
            why = str(row.get("why", ""))
            count = _extract_int(why, r"命中\s*(\d+)\s*次")
            hit_rate = _extract_float(why, r"命中率\s*([0-9.]+)%")
            rules.append(
                {
                    "reply": str(row.get("reply", "")),
                    "count": count,
                    "hit_rate": hit_rate,
                }
            )
        scenes.append(
            {
                "scene": scene,
                "count": max(_extract_int(" ".join(str(row.get("why", "")) for row in rows), r"出现\s*(\d+)\s*次"), len(rows)),
                "customer_examples": customer_examples[:8],
                "rules": rules,
            }
        )
    scenes = _sort_scenes_for_customer_journey(scenes)
    return format_sop_document(
        {
            "files": 0,
            "turns": 0,
            "total_customer_turns": sum(int(scene.get("count", 0)) for scene in scenes),
            "scene_count": len(scenes),
            "examples": len(items),
            "scenes": scenes,
            "sop_steps": _build_sop_steps(scenes),
        }
    )


def format_sop_document(report: dict[str, object]) -> str:
    lines: list[str] = []
    lines.append("聊小智 SOP 分析报告")
    lines.append("")
    lines.append("一、分析概览")
    lines.append(f"- 文件数：{report.get('files', 0)}")
    lines.append(f"- 消息数：{report.get('turns', 0)}")
    lines.append(f"- 客户轮次：{report.get('total_customer_turns', 0)}")
    lines.append(f"- 识别场景：{report.get('scene_count', 0)}")
    lines.append(f"- 写入话术：{report.get('examples', 0)}")
    if report.get("analysis_source"):
        lines.append(f"- 分析来源：{report.get('analysis_source')}")
    lines.append("")
    lines.append("二、场景与命中话术")
    scenes = report.get("scenes", [])
    if not isinstance(scenes, list) or not scenes:
        lines.append("暂无可分析场景。请确认聊天记录能区分客户和我的回复。")
    else:
        for scene_index, scene in enumerate(scenes, 1):
            if not isinstance(scene, dict):
                continue
            lines.append(f"{scene_index}. {scene.get('scene', '未命名场景')}｜出现 {scene.get('count', 0)} 次")
            examples = scene.get("customer_examples", [])
            if isinstance(examples, list) and examples:
                lines.append("   客户常见说法：")
                for sample in examples[:3]:
                    lines.append(f"   - {sample}")
            rules = scene.get("rules", [])
            if isinstance(rules, list) and rules:
                lines.append("   推荐回复：")
                for rule_index, rule in enumerate(rules[:5], 1):
                    if not isinstance(rule, dict):
                        continue
                    lines.append(
                        f"   {rule_index}) {rule.get('reply', '')} "
                        f"｜命中 {rule.get('count', 0)} 次｜命中率 {rule.get('hit_rate', 0)}%"
                    )
            lines.append("")
    lines.append("三、建议 SOP 流程")
    steps = report.get("sop_steps", [])
    if isinstance(steps, list) and steps:
        for step in steps:
            lines.append(str(step))
    else:
        lines.append("暂无 SOP 流程。")
    skills = report.get("sales_skills", [])
    if isinstance(skills, list) and skills:
        lines.append("")
        lines.append("四、销冠 skill 提炼")
        for index, skill in enumerate(skills, 1):
            lines.append(f"{index}. {skill}")
    return "\n".join(lines).strip()


def classify_sop_scene(text: str) -> str:
    normalized = _compact(text)
    checks = [
        ("放置/使用方式", ("放哪", "放哪里", "摆放", "怎么放", "放在", "位置", "佩戴", "使用")),
        ("售后/查单核销", ("查单", "核销", "订单", "售后", "没收到", "怎么处理", "退款")),
        ("活动/上架对接", ("活动", "上架", "霸王餐", "商家", "套餐", "团购", "审核")),
        ("领取/资料发送", ("领取", "资料", "发我", "发给我", "链接", "文件", "怎么看", "在哪看")),
        ("价格/费用咨询", ("多少钱", "价格", "费用", "贵", "便宜", "收费", "预算")),
        ("真假/信任质疑", ("真假", "靠谱吗", "骗人", "假的", "可信吗", "靠谱吗", "有没有用")),
        ("成交/付款推进", ("付款", "支付", "转账", "下单", "收款", "定金")),
        ("客户犹豫/考虑", ("考虑", "想想", "再说", "纠结", "担心", "怕", "不确定")),
        ("感谢/确认收到", ("谢谢", "感谢", "收到", "好的", "明白", "知道了")),
    ]
    for scene, terms in checks:
        if any(term in normalized for term in terms):
            return scene
    if "?" in text or "？" in text or any(word in normalized for word in ("怎么", "为什么", "可以吗", "行吗")):
        return "通用问题咨询"
    return "日常承接"


def _format_scene_stat(stat: dict[str, object]) -> dict[str, object]:
    scene_count = max(1, int(stat.get("count", 0)))
    reply_counts = stat.get("reply_counts")
    if not isinstance(reply_counts, Counter):
        reply_counts = Counter()
    rules = []
    for reply, count in reply_counts.most_common(8):
        rules.append(
            {
                "reply": reply,
                "count": count,
                "hit_rate": round(count / scene_count * 100, 1),
            }
        )
    examples = stat.get("customer_examples")
    scene = str(stat.get("scene", ""))
    result = {
        "scene": str(stat.get("scene", "")),
        "count": scene_count,
        "customer_examples": list(examples if isinstance(examples, list) else [])[:8],
        "rules": rules,
    }
    result.update(_infer_flow_fields(scene, result.get("customer_examples", []), rules))
    return result


def _build_sop_steps(scenes: list[dict[str, object]]) -> list[str]:
    steps = []
    for index, scene in enumerate(_sort_scenes_for_customer_journey(scenes)[:12], 1):
        rules = scene.get("rules")
        top_reply = ""
        if isinstance(rules, list) and rules:
            top_reply = str(rules[0].get("reply", ""))
        if top_reply:
            stage = str(scene.get("flow_stage", "")).strip()
            prefix = f"{stage}｜" if stage else ""
            steps.append(f"{index}. {prefix}{scene['scene']}：优先按客户问题承接，可参考话术：{top_reply}")
        else:
            steps.append(f"{index}. {scene['scene']}：先确认客户具体需求，再给下一步。")
    return steps


def _report_to_memory_examples(report: dict[str, object]) -> list[dict[str, str]]:
    examples: list[dict[str, str]] = []
    scenes = report.get("scenes")
    if not isinstance(scenes, list):
        return examples
    priority_base = 100
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        scene_name = str(scene.get("scene", "")).strip()
        customer_examples = scene.get("customer_examples")
        rules = scene.get("rules")
        if not isinstance(customer_examples, list) or not isinstance(rules, list):
            continue
        cue = "\n".join(str(item).strip() for item in customer_examples[:6] if str(item).strip())
        if not cue:
            continue
        for rank, rule in enumerate(rules[:3], 1):
            if not isinstance(rule, dict):
                continue
            reply = str(rule.get("reply", "")).strip()
            if not reply:
                continue
            hit_rate = float(rule.get("hit_rate", 0) or 0)
            count = int(rule.get("count", 0) or 0)
            examples.append(
                {
                    "partner": "SOP话术分析",
                    "cue": cue,
                    "reply": reply,
                    "source": SOP_SOURCE,
                    "scenario_title": scene_name,
                    "why": f"场景 {scene_name}，出现 {scene.get('count', 0)} 次；该回复命中 {count} 次，命中率 {hit_rate}%。",
                    "priority": str(max(1, priority_base - rank * 5)),
                }
            )
    return examples


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _clean_reply(text: str) -> str:
    text = _clean_text(text)
    if not text or text in {"[图片]", "[表情]", "图片", "表情"}:
        return ""
    return text


def _customer_lines_from_cue(cue: str) -> list[str]:
    lines: list[str] = []
    for raw_line in str(cue or "").splitlines():
        line = _clean_text(raw_line)
        if not line:
            continue
        if line.startswith("[对方]"):
            line = line.split("]", 1)[-1].strip()
        elif line.startswith("[我]"):
            continue
        lines.append(line)
    return lines or [_clean_text(cue)]


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").lower())


def _extract_int(text: str, pattern: str) -> int:
    match = re.search(pattern, text)
    if not match:
        return 0
    try:
        return int(match.group(1))
    except Exception:
        return 0


def _extract_float(text: str, pattern: str) -> float:
    match = re.search(pattern, text)
    if not match:
        return 0.0
    try:
        return float(match.group(1))
    except Exception:
        return 0.0
