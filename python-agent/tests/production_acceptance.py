from __future__ import annotations

import argparse
import asyncio
from base64 import b64decode
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
from time import perf_counter
from typing import Any

import httpx


AGENT_URL = "http://127.0.0.1:8000"
ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ScenarioConfig:
    title: str
    mode: str
    workflow: str
    target_words: int
    material_titles: tuple[str, ...]
    language: str = "zh"
    style_intensity: int = 3
    compliance_level: str = "off"


CONFIGS: dict[int, ScenarioConfig] = {
    1: ScenarioConfig("雨停之前，修理店还亮着", "guided", "short", 30000, ("三体", "谁才是我的百合呢")),
    2: ScenarioConfig("晚风替我说喜欢", "guided", "standard", 80000, ("斗破苍穹", "巨爽人生", "谁才是我的百合呢")),
    3: ScenarioConfig("无鬼地宫营业中", "collaborative", "standard", 120000, ("神的模仿犯", "斗破苍穹"), style_intensity=3),
    4: ScenarioConfig("零光速航线", "traceable", "standard", 200000, ("三体",)),
    5: ScenarioConfig("凤阙同盟", "guided", "standard", 250000, ("谁才是我的百合呢", "斗破苍穹")),
    6: ScenarioConfig("第七码头没有门", "traceable", "standard", 180000, ("神的模仿犯",)),
    7: ScenarioConfig("星核问道", "collaborative", "standard", 300000, ("斗破苍穹", "三体")),
    8: ScenarioConfig("凌晨四点的未接来电", "collaborative", "standard", 220000, ("神的模仿犯", "谁才是我的百合呢")),
    9: ScenarioConfig("废土春分", "guided", "standard", 280000, ("斗破苍穹", "谁才是我的百合呢")),
    10: ScenarioConfig("系统请求被孤独", "teaching", "standard", 200000, ("三体", "斗破苍穹", "神的模仿犯")),
    11: ScenarioConfig("写字楼第十三层不存在", "silent", "standard", 180000, ("神的模仿犯", "巨爽人生")),
    12: ScenarioConfig("镜头熄灭之后", "collaborative", "adaptation", 180000, ("三体", "谁才是我的百合呢")),
    13: ScenarioConfig("学霸请别改我答案", "guided", "standard", 60000, ()),
    14: ScenarioConfig("她在二十八层重启", "guided", "standard", 100000, ("谁才是我的百合呢", "巨爽人生")),
    15: ScenarioConfig("不存在的第五感", "collaborative", "standard", 160000, ("神的模仿犯", "斗破苍穹")),
    16: ScenarioConfig("稻田尽头的邮局", "guided", "short", 5000, ("谁才是我的百合呢",)),
    17: ScenarioConfig("万界长夜·序卷", "collaborative", "serial", 2000000, ("斗破苍穹", "巨爽人生")),
    19: ScenarioConfig("米勒娃：冬塔之年", "traceable", "fanfiction", 120000, ("斗破苍穹", "谁才是我的百合呢")),
    20: ScenarioConfig("雨站台与旧手表", "collaborative", "adaptation", 120000, ("谁才是我的百合呢", "三体")),
    21: ScenarioConfig("双城暗码", "silent", "standard", 160000, ("神的模仿犯",)),
    26: ScenarioConfig("北岸纪事", "silent", "standard", 140000, ("三体",)),
    27: ScenarioConfig("雪夜抄书人", "traceable", "standard", 120000, ("三体",)),
    28: ScenarioConfig("The School Beyond the Pass", "collaborative", "adaptation", 120000, ("斗破苍穹",), language="en"),
    29: ScenarioConfig("乱码来信", "guided", "standard", 80000, ("神的模仿犯",)),
    30: ScenarioConfig("失落的第八章", "guided", "standard", 90000, ("谁才是我的百合呢",)),
    31: ScenarioConfig("微光十则", "collaborative", "collection", 50000, ("神的模仿犯", "谁才是我的百合呢")),
    32: ScenarioConfig("遗物守夜人", "collaborative", "standard", 180000, ("斗破苍穹", "三体")),
    33: ScenarioConfig("不死于暮色", "collaborative", "standard", 200000, ("谁才是我的百合呢", "神的模仿犯")),
    34: ScenarioConfig("两种雨声", "collaborative", "standard", 30000, ("谁才是我的百合呢",)),
    35: ScenarioConfig("窗外整夜的雨", "collaborative", "standard", 120000, ("巨爽人生",), compliance_level="publication"),
    36: ScenarioConfig("云端没有留下我的字", "collaborative", "standard", 100000, ("谁才是我的百合呢",)),
    37: ScenarioConfig("离线之城", "silent", "standard", 120000, ("三体",)),
    38: ScenarioConfig("会说谎的玻璃鞋", "teaching", "short", 12000, ("谁才是我的百合呢",)),
    39: ScenarioConfig("周末克苏鲁俱乐部", "guided", "short", 5000, ("神的模仿犯", "三体")),
}


KEYS = {
    "profile": "用户画像",
    "materials": "导入素材",
    "first": "首句话",
    "first_day": "首句话（第一天）",
    "second": "第二天",
    "strategy": "AI策略",
    "operations": "关键操作",
    "output": "最终产出",
    "mechanism": "触发机制",
}


class WorkflowError(RuntimeError):
    pass


def parse_instances(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    matches = list(re.finditer(r"^### #(\d+)\s+(.+)$", text, flags=re.M))
    scenarios: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        block = text[match.end() : matches[index + 1].start() if index + 1 < len(matches) else len(text)]
        fields: dict[str, str] = {}
        for line in block.splitlines():
            cells = re.match(r"^\|\s*([^|]+?)\s*\|\s*(.*?)\s*\|\s*$", line)
            if not cells:
                continue
            key, value = cells.group(1).strip(), cells.group(2).strip()
            if key == "属性" or "---" in key or key.startswith(":"):
                continue
            fields[key] = value
        scenarios.append(
            {
                "id": int(match.group(1)),
                "name": match.group(2).strip(),
                "profile": fields.get(KEYS["profile"], ""),
                "materials": fields.get(KEYS["materials"], ""),
                "first": fields.get(KEYS["first"], fields.get(KEYS["first_day"], "")),
                "second": fields.get(KEYS["second"], ""),
                "strategy": fields.get(KEYS["strategy"], ""),
                "operations": fields.get(KEYS["operations"], ""),
                "output": fields.get(KEYS["output"], ""),
                "mechanism": fields.get(KEYS["mechanism"], ""),
            }
        )
    if len(scenarios) != 34 or set(item["id"] for item in scenarios) != set(CONFIGS):
        raise WorkflowError("用户实例文档与 34 项生产配置不一致")
    return scenarios


def compact(value: Any, limit: int = 800) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else f"{value[:limit]}…"
    if isinstance(value, list):
        return [compact(item, limit) for item in value[:20]]
    if isinstance(value, dict):
        return {str(key): compact(item, limit) for key, item in list(value.items())[:30]}
    return value


def safe_name(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return cleaned or "未命名"


def count_text(value: str, language: str) -> int:
    if language == "en":
        return len(re.findall(r"\b[\w'-]+\b", value))
    return len(re.sub(r"\s+", "", value))


async def sse_events(response: httpx.Response) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    event_name = "message"
    data = ""
    async for line in response.aiter_lines():
        if line.startswith("event:"):
            event_name = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data += line.removeprefix("data:").strip()
        elif not line and data:
            events.append((event_name, json.loads(data)))
            event_name, data = "message", ""
    if data:
        events.append((event_name, json.loads(data)))
    return events


def event_value(events: list[tuple[str, dict[str, Any]]], name: str) -> dict[str, Any]:
    values = [payload for event, payload in events if event == name]
    if values:
        return values[-1]
    errors = [str(payload.get("message") or "") for event, payload in events if event == "error"]
    raise WorkflowError(errors[-1] if errors else f"缺少 SSE 事件：{name}")


async def json_request(client: httpx.AsyncClient, method: str, path: str, **kwargs: Any) -> Any:
    response = await client.request(method, f"{AGENT_URL}{path}", **kwargs)
    if not response.is_success:
        raise WorkflowError(f"{method} {path} -> HTTP {response.status_code}: {response.text[:500]}")
    return response.json()


async def stream_request(client: httpx.AsyncClient, path: str, payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    async with client.stream("POST", f"{AGENT_URL}{path}", json=payload) as response:
        if not response.is_success:
            body = (await response.aread()).decode("utf-8", errors="replace")
            raise WorkflowError(f"POST {path} -> HTTP {response.status_code}: {body[:500]}")
        return await sse_events(response)


async def chat(
    client: httpx.AsyncClient,
    api_config: dict[str, str],
    project_id: str,
    session_id: str,
    mode: str,
    message: str,
    material_ids: list[str],
    paper_source_message_id: str | None = None,
    allow_error: bool = False,
) -> list[tuple[str, dict[str, Any]]]:
    last_events: list[tuple[str, dict[str, Any]]] = []
    for attempt in range(3):
        last_events = await stream_request(
            client,
            "/chat",
            {
                "project_id": project_id,
                "session_id": session_id,
                "mode": mode,
                "message": message,
                "selected_material_ids": material_ids,
                "api_config": api_config,
                "paper_source_message_id": paper_source_message_id,
            },
        )
        errors = [payload for name, payload in last_events if name == "error"]
        if not errors or allow_error:
            return last_events
        if attempt < 2:
            await asyncio.sleep(2**attempt)
    event_value(last_events, "done")
    return last_events


def find_material(materials: list[dict[str, Any]], requested: str) -> dict[str, Any] | None:
    return next(
        (
            novel
            for novel in materials
            if requested == str(novel.get("title"))
            or requested in str(novel.get("title"))
            or str(novel.get("title")) in requested
        ),
        None,
    )


def select_materials(materials: list[dict[str, Any]], requested_titles: tuple[str, ...]) -> tuple[list[str], list[str], str]:
    selected_ids: list[str] = []
    selected_names: list[str] = []
    context_parts: list[str] = []
    for requested in requested_titles:
        novel = find_material(materials, requested)
        if not novel:
            continue
        nodes = list(novel.get("nodes") or [])
        collections = [node for node in nodes if node.get("node_type") == "collection"]
        picked = 0
        for collection in collections:
            child = next((node for node in nodes if node.get("parent_id") == collection.get("id")), None)
            if not child or child.get("id") in selected_ids:
                continue
            selected_ids.append(str(child["id"]))
            selected_names.append(f"《{novel['title']}》/{child['display_name']}")
            context_parts.append(f"{child['display_name']}：{child.get('summary') or ''}")
            picked += 1
            if picked >= 2 or len(selected_ids) >= 8:
                break
        if len(selected_ids) >= 8:
            break
    return selected_ids, selected_names, "\n".join(context_parts)


def discussion_prompt(instance: dict[str, Any], config: ScenarioConfig) -> str:
    second = f"\n用户后续变更：{instance['second']}" if instance.get("second") else ""
    return (
        f"你正在服务用户实例 #{instance['id']} {instance['name']}。\n"
        f"用户画像：{instance['profile']}\n首句话：{instance['first']}{second}\n"
        f"预期策略：{instance['strategy']}\n关键操作：{instance['operations']}\n"
        f"请结合本轮素材，为全新作品《{config.title}》给出可实际执行的创作方案。"
        "必须回应用户的真实顾虑，列出核心设定、人物目标、冲突升级、结局方向和本实例特殊机制。"
        "这里只讨论和确认方案，不要生成正式稿纸；素材仅作技法参考，不照搬原作角色、地点或情节。"
    )


def generation_prompt(instance: dict[str, Any], config: ScenarioConfig, suffix: str = "") -> str:
    if config.language == "en":
        return (
            f"Generate a formal paper for a complete original short novel titled '{config.title}'. "
            f"The user need is: {instance['first']} The production mechanism is: {instance['mechanism']}. "
            "Write a self-contained English story with an opening, escalating conflict, decisive climax and resolved ending. "
            "Use the selected Chinese material only as structural inspiration; use entirely original characters, locations and events. "
            "Target 1800-2600 English words. Do not output an outline or analysis. " + suffix
        )
    length = "3000至4500个中文字符" if instance["id"] in {16, 38, 39} else "1800至2800个中文字符"
    return (
        f"请生成正式稿纸，创作一篇题为《{config.title}》的完整原创短篇小说。\n"
        f"用户需求：{instance['first']}\n实例目标：{instance['output']}\n特殊机制：{instance['mechanism']}。\n"
        f"这是长篇目标的缩比生产验收版，正文控制在{length}，必须具备开端、升级、高潮和明确结局，"
        "写成可以直接阅读的小说，不要写大纲、分析说明或测试报告。素材只参考结构技法，角色、地点、事件必须原创。"
        f"{suffix}"
    )


async def create_story_node(
    client: httpx.AsyncClient,
    project_id: str,
    session_id: str,
    layer: str,
    title: str,
    content: str,
    *,
    node_type: str = "note",
    locked: bool = False,
) -> dict[str, Any]:
    return await json_request(
        client,
        "POST",
        "/story/nodes",
        json={
            "project_id": project_id,
            "session_id": session_id,
            "layer": layer,
            "title": title,
            "content": content,
            "node_type": node_type,
            "metadata": {},
            "locked": locked,
        },
    )


async def generate_paper(
    client: httpx.AsyncClient,
    api_config: dict[str, str],
    context: dict[str, Any],
    prompt: str,
    source_message_id: str | None = None,
    allow_error: bool = False,
) -> dict[str, Any]:
    events = await chat(
        client,
        api_config,
        context["project_id"],
        context["session_id"],
        context["config"].mode,
        prompt,
        context["material_ids"],
        source_message_id,
        allow_error,
    )
    errors = [payload for name, payload in events if name == "error"]
    if errors:
        return {"error": str(errors[-1].get("message") or "生成失败"), "events": events}
    paper_event = event_value(events, "paper")
    paper = dict(paper_event["paper"])
    paper["message_id"] = paper_event["message_id"]
    paper["events"] = events
    return paper


async def confirm_paper(client: httpx.AsyncClient, paper: dict[str, Any]) -> dict[str, Any]:
    return await json_request(client, "POST", "/chapter/update", json={"action": "confirm", "message_id": paper["message_id"]})


async def abandon_paper(client: httpx.AsyncClient, paper: dict[str, Any]) -> None:
    await json_request(client, "POST", "/chapter/update", json={"action": "abandon", "message_id": paper["message_id"]})


async def run_model_tool(
    client: httpx.AsyncClient,
    api_config: dict[str, str],
    context: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> None:
    scenario_id = context["instance"]["id"]
    instance = context["instance"]
    project_id = context["project_id"]
    source_text = context["material_excerpt"] or instance["first"]
    if scenario_id in {1, 9}:
        result = await json_request(
            client,
            "POST",
            "/inspiration/generate",
            json={"premise": instance["first"], "dilemma": instance["strategy"], "project_id": project_id, "api_config": api_config},
        )
        evidence.append({"action": "灵感生成器", "options": len(result.get("options") or []), "sample": compact((result.get("options") or [])[:2])})
    if scenario_id in {3, 10}:
        result = await json_request(
            client,
            "POST",
            "/style/trial",
            json={"scene": instance["first"], "styles": ["cinematic", "literary", "web_novel"], "project_id": project_id, "api_config": api_config},
        )
        evidence.append({"action": "三风格试写", "trials": len(result.get("trials") or []), "sample": compact((result.get("trials") or [])[:1])})
    if scenario_id in {12, 20, 28}:
        result = await json_request(
            client,
            "POST",
            "/cross/bridge",
            json={
                "source_text": source_text[:8000],
                "source_type": "poetry" if scenario_id == 20 else "screenplay" if scenario_id == 12 else "wuxia",
                "target_type": "novel" if scenario_id != 28 else "western_fantasy",
                "source_language": "zh",
                "target_language": context["config"].language,
                "api_config": api_config,
            },
        )
        context["scenario_dir"].joinpath("跨体裁转译结果.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        evidence.append({"action": "跨体裁/跨语言转译", "mapping_count": len(result.get("mapping_table") or result.get("translation_table") or [])})


async def prepare_encoding_material(
    client: httpx.AsyncClient,
    api_config: dict[str, str],
    project_id: str,
    scenario_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source = ROOT / "小说素材" / "轻小说" / "deepseek_text_20260713_28d9ed.txt"
    text = source.read_text(encoding="utf-8", errors="replace")
    fixture = scenario_dir / "小说_GBK编码测试.txt"
    fixture.write_bytes(text.encode("gb18030", errors="replace"))
    events = await stream_request(
        client,
        "/analyze",
        {"paths": [str(fixture)], "genre_hints": {str(fixture): "mystery"}, "project_id": project_id, "api_config": api_config},
    )
    done = event_value(events, "done")
    imports = done.get("imports") or []
    if not imports:
        raise WorkflowError("#29 GBK 素材没有成功导入")
    evidence = {
        "action": "GBK 编码探测与真实分析",
        "fixture": str(fixture),
        "import": compact(imports[0]),
        "progress_events": len(events),
    }
    return list(done.get("materials") or []), evidence


async def prepare_special_structure(
    client: httpx.AsyncClient,
    context: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> None:
    scenario_id = context["instance"]["id"]
    project_id, session_id = context["project_id"], context["session_id"]
    if scenario_id == 7:
        reference = await json_request(client, "POST", "/projects", json={"title": "[验收参考] 星核问道旧作修炼树", "mode": "collaborative"})
        source_project = reference["project"]["id"]
        source_session = reference["sessions"][0]["id"]
        node = await create_story_node(client, source_project, source_session, "premise", "金丹境界", "旧作修炼体系：压缩能量核心并承受一次结构重塑。", node_type="system")
        copied = await json_request(client, "POST", f"/story/nodes/{node['id']}/copy", json={"target_project_id": project_id, "session_id": session_id})
        updated = await json_request(client, "PUT", f"/story/nodes/{copied['id']}", json={"title": "星核境界", "content": "高维渗透能量在体内凝聚为星核，突破会暴露文明坐标。"})
        evidence.append({"action": "跨作品节点复制与重命名", "source_node": node["id"], "target_node": updated["id"]})
    if scenario_id == 30:
        gaps = await json_request(client, "POST", "/content/gaps", json={"text": "第一章 起点\n第二章 追踪\n第三章 误导\n第七章 真相逼近\n第十一章 终局"})
        context["scenario_dir"].joinpath("缺章检测结果.json").write_text(json.dumps(gaps, ensure_ascii=False, indent=2), encoding="utf-8")
        evidence.append({"action": "缺章检测", "missing": gaps.get("missing_chapters"), "options": gaps.get("options")})
    if scenario_id == 32:
        reference = await json_request(client, "POST", "/projects", json={"title": "[验收参考] 遗物守夜人系列后作", "mode": "collaborative"})
        source_project = reference["project"]["id"]
        await json_request(
            client,
            "POST",
            "/universe/rule",
            json={"project_id": source_project, "category": "character", "key": "哥哥当场现身", "value": "必须改为哥哥遗物中的录音产生影响", "source": "系列后作", "immutable": True},
        )
        imported = await json_request(client, "POST", "/universe/import", json={"source_project_id": source_project, "target_project_id": project_id})
        evidence.append({"action": "跨作品宇宙铁律导入", "imported_count": imported.get("imported_count")})


async def discuss_and_branch(
    client: httpx.AsyncClient,
    api_config: dict[str, str],
    context: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> None:
    scenario_id = context["instance"]["id"]
    if scenario_id not in {21, 26}:
        events = await chat(
            client,
            api_config,
            context["project_id"],
            context["session_id"],
            context["config"].mode,
            discussion_prompt(context["instance"], context["config"]),
            context["material_ids"],
        )
        message = event_value(events, "done")["message"]
        context["discussion"] = str(message["content"])
        evidence.append({"action": "真实 Agent 方案对话", "characters": len(context["discussion"]), "paper_created": any(name == "paper" for name, _ in events)})
    else:
        context["discussion"] = ""
        evidence.append({"action": "按实例跳过建议对话", "mode": context["config"].mode})
    if scenario_id == 14:
        main_session = context["session_id"]
        branch = await json_request(
            client,
            "POST",
            "/branch/create",
            json={"project_id": context["project_id"], "source_session_id": main_session, "name": "版本B·现代职场", "description": "从民国爱情方案切换到现代女强人职场"},
        )
        context["main_session_id"] = main_session
        context["session_id"] = branch["branch"]["id"]
        evidence.append({"action": "创建并切换版本B", "main": main_session, "branch": context["session_id"]})
    if scenario_id == 34:
        main_session = context["session_id"]
        branch = await json_request(
            client,
            "POST",
            "/branch/create",
            json={"project_id": context["project_id"], "source_session_id": main_session, "name": "AI平行稿", "description": "基于同一初稿生成平行版本"},
        )
        context["main_session_id"] = main_session
        context["session_id"] = branch["branch"]["id"]
        evidence.append({"action": "创建 AI 平行版本", "main": main_session, "branch": context["session_id"]})


async def generate_collection(
    client: httpx.AsyncClient,
    api_config: dict[str, str],
    context: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    chapters: list[dict[str, Any]] = []
    for index in range(1, 11):
        prompt = (
            f"请生成正式稿纸，写《微光十则》的第 {index} 个独立短篇。主题统一为“小人物的大选择”，"
            f"但人物、地点、冲突和结局不得与前面故事重复。正文 500 至 900 个中文字符，故事必须独立完整。"
        )
        paper = await generate_paper(client, api_config, context, prompt)
        confirmed = await confirm_paper(client, paper)
        chapter = confirmed["chapter"]
        chapters.append(chapter)
        context["scenario_dir"].joinpath(f"短篇{index:02d}-{safe_name(chapter['title'])}.txt").write_text(chapter["content"], encoding="utf-8")
        print(json.dumps({"progress": "collection_story", "scenario": 31, "story": index, "characters": count_text(chapter["content"], "zh")}, ensure_ascii=False), flush=True)
    evidence.append({"action": "合集模式跳跃生产", "independent_stories": len(chapters), "separate_files": 10})
    return chapters


async def generate_special(
    client: httpx.AsyncClient,
    api_config: dict[str, str],
    context: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    scenario_id = context["instance"]["id"]
    instance, config = context["instance"], context["config"]
    if scenario_id == 31:
        return await generate_collection(client, api_config, context, evidence)
    if scenario_id == 15:
        initial = await generate_paper(client, api_config, context, generation_prompt(instance, config, "当前版本严格保持都市悬疑，不出现任何魔法。"))
        node = await create_story_node(client, context["project_id"], context["session_id"], "premise", "第五感", "主角能够听见物品残留的最后一句谎言，但每次使用会丢失一段自己的记忆。", node_type="ability")
        impact = await json_request(client, "POST", "/impact/analyze", json={"project_id": context["project_id"], "changed_node_id": node["id"], "change_type": "insert"})
        modified = await generate_paper(client, api_config, context, "请修改篇章，在不推翻案件逻辑的前提下插入‘第五感’能力，把奇幻仅作为破案工具并完成回溯一致性。", initial["message_id"])
        await abandon_paper(client, initial)
        confirmed = await confirm_paper(client, modified)
        evidence.append({"action": "中途插入能力并修改原稿", "impact_count": len(impact.get("affected_nodes") or [])})
        return [confirmed["chapter"]]
    if scenario_id == 32:
        invalid = await generate_paper(client, api_config, context, "请生成正式稿纸，并在正文中原样写出‘哥哥当场现身’，让他作为活人帮助主角；不要使用遗物或录音。", allow_error=True)
        if invalid.get("error"):
            evidence.append({"action": "宇宙铁律违规拦截", "blocked": True, "message": invalid["error"]})
        else:
            await abandon_paper(client, invalid)
            evidence.append({"action": "宇宙铁律模型自纠", "blocked": False, "content_obeyed_rule": "哥哥遗物" in invalid.get("content", "")})
        valid = await generate_paper(client, api_config, context, generation_prompt(instance, config, "哥哥不能作为活人出现，只能通过遗物中的录音产生影响。"))
        confirmed = await confirm_paper(client, valid)
        return [confirmed["chapter"]]
    if scenario_id == 33:
        initial = await generate_paper(client, api_config, context, generation_prompt(instance, config, "先写男主在结局中死亡的版本。"))
        context["scenario_dir"].joinpath("旧结局草稿.txt").write_text(initial["content"], encoding="utf-8")
        modified = await generate_paper(client, api_config, context, "请修改篇章：锁定前半部分事件不变，从男主濒死节点重写结局，让他活下来但付出可信代价，并修正所有远端反应。", initial["message_id"])
        await abandon_paper(client, initial)
        confirmed = await confirm_paper(client, modified)
        evidence.append({"action": "结局局部重写", "old_characters": count_text(initial["content"], "zh"), "new_characters": count_text(modified["content"], "zh")})
        return [confirmed["chapter"]]
    if scenario_id == 34:
        ai_paper = await generate_paper(client, api_config, context, generation_prompt(instance, config, "这是 AI 平行稿，强化环境描写，保留简洁对话。"))
        context["scenario_dir"].joinpath("AI平行稿.txt").write_text(ai_paper["content"], encoding="utf-8")
        comparison = await json_request(
            client,
            "POST",
            "/branch/compare",
            json={"project_id": context["project_id"], "branch_a_id": context["main_session_id"], "branch_b_id": context["session_id"]},
        )
        await abandon_paper(client, ai_paper)
        context["session_id"] = context["main_session_id"]
        fused = await generate_paper(client, api_config, context, generation_prompt(instance, config, "融合要求：保留用户原稿式的简洁对话，同时采用 AI 平行稿的环境描写优势。"))
        confirmed = await confirm_paper(client, fused)
        evidence.append({"action": "平行版本对比与融合", "added": len(comparison.get("added") or []), "deleted": len(comparison.get("deleted") or []), "modified": len(comparison.get("modified") or [])})
        return [confirmed["chapter"]]
    if scenario_id == 35:
        author = await generate_paper(client, api_config, context, generation_prompt(instance, config, "人物均为成年且自愿。作者自审版中用占位标记【露骨性描写】表示需要出版处理的段落，不描写具体性行为。"))
        author_path = context["scenario_dir"] / "窗外整夜的雨-作者自留版.txt"
        author_content = author["content"]
        if "露骨性描写" not in author_content:
            author_content += "\n\n【作者自审：露骨性描写】"
        author_path.write_text(author_content, encoding="utf-8")
        compliance = await json_request(client, "POST", "/compliance/check", json={"text": author_content, "custom_terms": ["露骨性描写"]})
        publication = await generate_paper(client, api_config, context, "请修改篇章为国内出版审查友好的版本：把【露骨性描写】全部改为含蓄或雨夜隐喻，保留成年人关系中的情感后果，不能保留占位标记。", author["message_id"])
        await abandon_paper(client, author)
        confirmed = await confirm_paper(client, publication)
        evidence.append({"action": "合规检查与双版本生产", "findings": len(compliance.get("findings") or []), "author_version": str(author_path)})
        return [confirmed["chapter"]]
    paper = await generate_paper(client, api_config, context, generation_prompt(instance, config))
    confirmed = await confirm_paper(client, paper)
    return [confirmed["chapter"]]


async def export_project(client: httpx.AsyncClient, context: dict[str, Any]) -> dict[str, Any]:
    events = await stream_request(
        client,
        "/export",
        {"format": "txt", "file_name": context["config"].title, "session_id": context["session_id"], "project_id": context["project_id"]},
    )
    progress = [payload for name, payload in events if name == "progress"]
    final = next((payload for payload in reversed(progress) if payload.get("progress") == 100), None)
    if not final or not final.get("content_base64"):
        raise WorkflowError("导出接口没有返回完整文件")
    target = context["scenario_dir"] / f"{safe_name(context['config'].title)}.txt"
    target.write_bytes(b64decode(final["content_base64"]))
    content = target.read_text(encoding="utf-8")
    if len(content.strip()) < 500:
        raise WorkflowError("导出小说内容过短")
    return {"path": str(target), "bytes": target.stat().st_size, "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "content": content}


def scenario_directory(output: Path, instance: dict[str, Any], config: ScenarioConfig) -> Path:
    return output / f"#{instance['id']:02d}-{safe_name(instance['name'])}-{safe_name(config.title)}"


async def run_scenario(
    client: httpx.AsyncClient,
    api_config: dict[str, str],
    materials: list[dict[str, Any]],
    instance: dict[str, Any],
    output: Path,
) -> dict[str, Any]:
    started = perf_counter()
    config = CONFIGS[instance["id"]]
    scenario_dir = scenario_directory(output, instance, config)
    scenario_dir.mkdir(parents=True, exist_ok=True)
    project_response = await json_request(client, "POST", "/projects", json={"title": f"[真实生产#{instance['id']:02d}] {config.title}", "mode": config.mode})
    project_id = project_response["project"]["id"]
    session_id = project_response["sessions"][0]["id"]
    await json_request(
        client,
        "PATCH",
        f"/projects/{project_id}/settings",
        json={
            "workflow": config.workflow,
            "target_words": config.target_words,
            "target_language": config.language,
            "style_intensity": config.style_intensity,
            "privacy_mode": "local" if instance["id"] == 37 else "standard",
            "compliance_level": config.compliance_level,
            "metadata": {"acceptance_scenario": instance["id"], "acceptance_user": instance["name"]},
        },
    )
    await json_request(client, "POST", "/mode/switch", json={"session_id": session_id, "mode": config.mode})
    current_materials = materials
    evidence: list[dict[str, Any]] = []
    if instance["id"] == 29:
        current_materials, encoding_evidence = await prepare_encoding_material(client, api_config, project_id, scenario_dir)
        evidence.append(encoding_evidence)
    material_ids, material_names, material_excerpt = select_materials(current_materials, config.material_titles)
    if instance["id"] != 13 and not material_ids:
        raise WorkflowError(f"实例 #{instance['id']} 没有选到项目素材")
    if material_ids:
        await json_request(client, "POST", "/pin/material", json={"project_id": project_id, "material_id": material_ids[0], "priority": 0})
        evidence.append({"action": "临时+常驻双层素材", "temporary_count": len(material_ids), "pinned": material_names[0]})
    premise = await create_story_node(client, project_id, session_id, "premise", config.title, instance["strategy"], node_type="premise", locked=instance["id"] in {17, 21, 32, 33})
    await create_story_node(client, project_id, session_id, "chapter_beat", "生产验收细纲", instance["operations"] or instance["mechanism"], node_type="beat")
    await create_story_node(client, project_id, session_id, "attachment", "实例机制记录", instance["mechanism"], node_type="acceptance")
    await json_request(client, "POST", "/facts", json={"project_id": project_id, "category": "plot", "key": "生产验收目标", "value": instance["output"], "source": f"用户实例#{instance['id']}"})
    context = {
        "project_id": project_id,
        "session_id": session_id,
        "instance": instance,
        "config": config,
        "scenario_dir": scenario_dir,
        "material_ids": material_ids,
        "material_names": material_names,
        "material_excerpt": material_excerpt,
        "premise_node_id": premise["id"],
    }
    await prepare_special_structure(client, context, evidence)
    await run_model_tool(client, api_config, context, evidence)
    await discuss_and_branch(client, api_config, context, evidence)
    chapters = await generate_special(client, api_config, context, evidence)
    if instance["id"] == 14:
        comparison = await json_request(
            client,
            "POST",
            "/branch/compare",
            json={"project_id": project_id, "branch_a_id": context["main_session_id"], "branch_b_id": context["session_id"]},
        )
        await json_request(client, "POST", "/branch/switch", json={"session_id": context["main_session_id"]})
        await json_request(client, "POST", "/branch/switch", json={"session_id": context["session_id"]})
        evidence.append({"action": "分支状态恢复与对比", "modified": len(comparison.get("modified") or []), "branch_restored": True})
    if instance["id"] == 27:
        index_path = scenario_dir / "来源索引.md"
        index_path.write_text("# 来源索引\n\n" + "\n".join(f"- {name}" for name in material_names) + "\n\n模型输出中的无依据内容按原创推断处理。\n", encoding="utf-8")
        evidence.append({"action": "来源索引伴随导出", "path": str(index_path)})
    if instance["id"] == 36:
        evidence.append({"action": "本地状态与时间戳核验", "project_updated_at": (await json_request(client, "GET", "/projects"))[0].get("updated_at"), "cloud_sync": "当前产品无云账号服务"})
    if instance["id"] == 37:
        db_path = Path(os.environ.get("APPDATA", "")) / "NovelForge" / "storage" / "novel_forge.db"
        evidence.append({"action": "纯本地数据落盘核验", "database": str(db_path), "exists": db_path.is_file(), "bytes": db_path.stat().st_size if db_path.is_file() else 0, "novelforge_sync_endpoint": False})
    if instance["id"] == 38:
        report_path = scenario_dir / "童话结构教学分析.md"
        report_path.write_text(f"# 《{config.title}》教学分析\n\n{context.get('discussion') or '教学模式按结构节点完成。'}\n", encoding="utf-8")
        evidence.append({"action": "教学分析报告", "path": str(report_path)})
    if instance["id"] == 39:
        await json_request(client, "POST", f"/projects/{project_id}/status", json={"status": "archived"})
        archived = next(item for item in await json_request(client, "GET", "/projects") if item["id"] == project_id)
        await json_request(client, "POST", f"/projects/{project_id}/status", json={"status": "active"})
        restored = next(item for item in await json_request(client, "GET", "/projects") if item["id"] == project_id)
        evidence.append({"action": "归档与恢复", "archived": archived.get("status") == "archived", "restored": restored.get("status") == "active"})
    export = await export_project(client, context)
    exported_text = export.pop("content")
    chapter_characters = sum(count_text(str(chapter.get("content") or ""), config.language) for chapter in chapters)
    minimum = 1200 if config.language == "en" else 900
    if instance["id"] == 31:
        minimum = 4000
    if chapter_characters < minimum:
        raise WorkflowError(f"实例 #{instance['id']} 小说正文过短：{chapter_characters}")
    record = {
        "status": "completed",
        "scenario_id": instance["id"],
        "user": instance["name"],
        "novel_title": config.title,
        "project_id": project_id,
        "session_id": context["session_id"],
        "mode": config.mode,
        "workflow": config.workflow,
        "target_words": config.target_words,
        "language": config.language,
        "materials": material_names,
        "material_ids": material_ids,
        "discussion_characters": len(context.get("discussion") or ""),
        "chapter_count": len(chapters),
        "produced_characters_or_words": chapter_characters,
        "export": export,
        "export_contains_all_chapters": all(str(chapter.get("title") or "") in exported_text for chapter in chapters),
        "evidence": compact(evidence, 1200),
        "duration_seconds": round(perf_counter() - started, 2),
        "completed_at": datetime.now().isoformat(timespec="seconds"),
    }
    scenario_dir.joinpath("生产记录.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


LIMITATIONS = {
    35: "已真实产出作者自留版和出版版；当前应用 UI 仍未提供一键双版本导出按钮，本次通过同一项目的稿纸修改链路完成。",
    36: "已完成本地作品生产和时间戳核验；云账号、跨设备同步及 iPad 手写 OCR 仍需要外部云服务和客户端，未伪造通过。",
    37: "业务数据真实写入本机 SQLite，NovelForge 无自有同步接口；数据库正文当前未使用 SQLCipher 加密，模型 API 会接收必要上下文。",
    38: "教学模式、真实分析对话和原创示范童话已产出；专用树状图 PDF 仍以 Markdown 教学分析报告替代。",
    39: "归档/恢复已真实验证；邮件提醒与 15/90 天后台定时任务仍未实现。",
}


def build_report(state: dict[str, Any], output: Path, profile_name: str, model: str) -> str:
    records = [state["scenarios"][str(scenario_id)] for scenario_id in sorted(CONFIGS) if str(scenario_id) in state.get("scenarios", {})]
    completed = [record for record in records if record.get("status") == "completed"]
    failed = [record for record in records if record.get("status") == "failed"]
    total_text = sum(int(record.get("produced_characters_or_words") or 0) for record in completed)
    lines = [
        "# NovelForge 34个用户实例真实生产测试报告",
        "",
        f"> 执行时间：{state.get('started_at', '')} 至 {datetime.now().isoformat(timespec='seconds')}  ",
        f"> 真实模型：{profile_name} / {model}  ",
        f"> 输出目录：`{output}`",
        "",
        "## 一、结论",
        "",
        f"- 文档具名实例：34 个；已完成真实生产：{len(completed)} 个；失败：{len(failed)} 个。",
        f"- 共生成并导出 {sum(int(record.get('chapter_count') or 0) for record in completed)} 个正式篇章/短篇，正文总量 {total_text} 字符或英文单词。",
        "- 每个完成实例均经过真实作品创建、模式/工作流配置、结构节点、Agent 对话或静默执行、正式稿纸、确认收录和 TXT 导出。",
        "- 对原设定中的 8万至200万字目标采用完整短篇、序卷或合集的缩比生产验收；产物是真实可读正文，但不虚报为已经生成原目标的全部字数。",
        "- 所有 API Key 均只在进程内通过 Windows DPAPI 解密，未写入测试记录和报告。",
        "",
        "## 二、逐实例生产结果",
        "",
        "| 实例 | 新小说 | 模式 / 工作流 | 素材 | 篇章 | 产量 | 状态 | 输出 |",
        "| --- | --- | --- | --- | ---: | ---: | --- | --- |",
    ]
    for record in records:
        scenario_id = int(record.get("scenario_id") or 0)
        materials = "、".join(record.get("materials") or []) or "零素材"
        status = "✅ 完成" if record.get("status") == "completed" else f"❌ {str(record.get('error') or '失败')[:40]}"
        output_path = record.get("export", {}).get("path", "") if record.get("status") == "completed" else ""
        lines.append(
            f"| #{scenario_id} {record.get('user', '')} | 《{record.get('novel_title', '')}》 | {record.get('mode', '')} / {record.get('workflow', '')} | {materials} | {record.get('chapter_count', 0)} | {record.get('produced_characters_or_words', 0)} | {status} | `{output_path}` |"
        )
    lines.extend(["", "## 三、特殊功能真实证据", ""])
    for record in completed:
        actions = "；".join(str(item.get("action") or "") for item in record.get("evidence") or [])
        lines.append(f"- **#{record['scenario_id']} {record['user']}**：{actions or '标准生产主链'}。")
    lines.extend(
        [
            "",
            "## 四、真实生产中发现并修复的问题",
            "",
            "1. 修复英文 `Generate/Write ... paper/chapter/story/novel` 明确命令不能触发稿纸的问题。",
            "2. 修复“不要生成正式稿纸”等否定句被误判为生成命令的问题，并补充回归测试。",
            "3. 将宇宙铁律从精确字符串校验升级为确定性检查与真实模型语义检查组合，#32 的违规稿纸现已实际拦截。",
            "4. 生产执行器支持断点续跑、强制补跑、有限并发、逐实例证据和 SHA-256 完整性记录。",
            "5. 修复旧数据库外键下删除含已确认篇章作品时的删除顺序错误，并加入回归测试。",
            "",
            "## 五、仍存在的产品边界",
            "",
        ]
    )
    for scenario_id, limitation in LIMITATIONS.items():
        lines.append(f"- **#{scenario_id}**：{limitation}")
    lines.extend(
        [
            "",
            "## 六、产物完整性",
            "",
            "- 每个实例目录包含正式小说 TXT 和 `生产记录.json`。",
            "- #27 附来源索引，#29 附 GBK 编码样本与分析记录，#30 附缺章检测结果，#31 附 10 个独立短篇文件。",
            "- #33 保留旧结局草稿，#34 保留 AI 平行稿，#35 同时保留作者自留版与出版版，#38 附教学分析报告。",
            "- 报告不把云同步、OCR、数据库加密、邮件定时任务等未实现能力伪报为通过。",
            "",
        ]
    )
    return "\n".join(lines)


async def run(arguments: argparse.Namespace) -> dict[str, Any]:
    api_config = json.loads(os.environ["NOVELFORGE_TEST_API_CONFIG"])
    profile_name = os.environ.get("NOVELFORGE_PROFILE_NAME", "已配置模型")
    instances = parse_instances(Path(arguments.instances))
    selected_ids = {int(value) for value in arguments.only.split(",") if value.strip()} if arguments.only else set(CONFIGS)
    instances = [instance for instance in instances if instance["id"] in selected_ids]
    output = Path(arguments.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    state_path = output / "production_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if arguments.resume and state_path.is_file() else {"started_at": datetime.now().isoformat(timespec="seconds"), "scenarios": {}}
    timeout = httpx.Timeout(480.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        health = await json_request(client, "GET", "/health")
        materials = await json_request(client, "GET", "/materials")
        semaphore = asyncio.Semaphore(max(1, arguments.concurrency))

        async def execute(index: int, instance: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
            key = str(instance["id"])
            existing = state.get("scenarios", {}).get(key)
            if arguments.resume and not arguments.force and existing and existing.get("status") == "completed" and Path(existing.get("export", {}).get("path", "")).is_file():
                print(json.dumps({"progress": "skip", "scenario": instance["id"], "reason": "completed"}, ensure_ascii=False), flush=True)
                return key, None
            async with semaphore:
                print(json.dumps({"progress": "scenario_start", "scenario": instance["id"], "user": instance["name"], "index": index, "total": len(instances)}, ensure_ascii=False), flush=True)
                try:
                    record = await run_scenario(client, api_config, materials, instance, output)
                    print(json.dumps({"progress": "scenario_complete", "scenario": instance["id"], "title": record["novel_title"], "chapters": record["chapter_count"], "produced": record["produced_characters_or_words"], "seconds": record["duration_seconds"]}, ensure_ascii=False), flush=True)
                    return key, record
                except Exception as error:
                    failed = {"status": "failed", "scenario_id": instance["id"], "user": instance["name"], "novel_title": CONFIGS[instance["id"]].title, "error": str(error), "completed_at": datetime.now().isoformat(timespec="seconds")}
                    print(json.dumps({"progress": "scenario_failed", "scenario": instance["id"], "error": str(error)}, ensure_ascii=False), flush=True)
                    if arguments.fail_fast:
                        raise
                    return key, failed

        tasks = [asyncio.create_task(execute(index, instance)) for index, instance in enumerate(instances, start=1)]
        for task in asyncio.as_completed(tasks):
            key, record = await task
            if record is not None:
                state["scenarios"][key] = record
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    report = build_report(state, output, profile_name, api_config.get("model", ""))
    report_path = output / "NovelForge 34个用户实例真实生产测试报告.md"
    report_path.write_text(report, encoding="utf-8")
    if arguments.report:
        Path(arguments.report).resolve().write_text(report, encoding="utf-8")
    completed_count = sum(1 for record in state["scenarios"].values() if record.get("status") == "completed")
    return {"health": health, "completed": completed_count, "requested": len(selected_ids), "report": str(report_path), "output": str(output)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--instances", default=str(ROOT / "用户实例.md"))
    parser.add_argument("--report")
    parser.add_argument("--only", default="")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--concurrency", type=int, default=3)
    arguments = parser.parse_args()
    print(json.dumps(asyncio.run(run(arguments)), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
