import argparse
import asyncio
from base64 import b64decode
import json
import os
from pathlib import Path
import re
from time import perf_counter
from typing import Any

import httpx


AGENT_URL = "http://127.0.0.1:8000"

PROJECTS = [
    {
        "title": "星海静默协议",
        "material_title": "三体",
        "dimensions": ["科技设定", "社会结构", "情节结构", "主题"],
        "discussion": "请逐条列出本轮四项素材的准确名称和关键规则，再为一部全新的严肃科幻小说安排三幕结构。只做讨论，不写正式稿纸。",
        "chapters": [
            "请基于当前素材与三幕安排，生成第一篇正式篇章，作为新小说开篇。控制在700至1000个中文字符，建立科学危机与人物选择，不照搬原作情节。",
            "请生成第二篇正式篇章，承接上一章推进调查，让科技规则与社会压力同时产生后果。控制在700至1000个中文字符。",
            "请生成第三篇正式篇章，形成阶段性高潮并留下可继续发展的核心悬念。控制在700至1000个中文字符。",
        ],
    },
    {
        "title": "欲望刻度",
        "material_title": "巨爽人生，从看见欲望词条开始 作者：小明阿加西",
        "dimensions": ["系统功能", "奖励机制", "属性面板", "升级路径"],
        "discussion": "请逐条列出本轮四项素材的准确名称、触发条件和限制，再为一部全新的都市爽文安排三幕升级路线。只做讨论，不写正式稿纸。",
        "chapters": [
            "请基于当前素材与升级路线，生成第一篇正式篇章，作为新小说开篇。控制在700至1000个中文字符，明确主角困境、能力触发与第一次收益。",
            "请生成第二篇正式篇章，承接上一章设置一次有代价的反转，让属性和奖励机制真正推动选择。控制在700至1000个中文字符。",
            "请生成第三篇正式篇章，完成第一阶段逆袭，同时暴露更高层级的风险。控制在700至1000个中文字符。",
        ],
    },
    {
        "title": "第十三份判决",
        "material_title": "神的模仿犯",
        "dimensions": ["角色系统", "案件结构", "线索网络", "诡计设计"],
        "discussion": "请逐条列出本轮四项素材的准确名称、证据和误导方式，再为一部全新的封闭空间推理小说安排三幕案件结构。只做讨论，不写正式稿纸。",
        "chapters": [
            "请基于当前素材与案件结构，生成第一篇正式篇章，作为新小说开篇。控制在700至1000个中文字符，呈现规则、受害者与第一处可验证线索。",
            "请生成第二篇正式篇章，承接上一章推进调查，加入公平的误导并确保线索能够回溯。控制在700至1000个中文字符。",
            "请生成第三篇正式篇章，揭开第一层诡计但保留隐藏设计者的更大谜团。控制在700至1000个中文字符。",
        ],
    },
]


class WorkflowError(RuntimeError):
    pass


async def response_events(response: httpx.Response) -> list[tuple[str, dict[str, Any]]]:
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
            event_name = "message"
            data = ""
    if data:
        events.append((event_name, json.loads(data)))
    return events


async def chat(
    client: httpx.AsyncClient,
    api_config: dict[str, str],
    session_id: str,
    message: str,
    material_ids: list[str],
    paper_source_message_id: str | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    async with client.stream(
        "POST",
        f"{AGENT_URL}/chat",
        json={
            "session_id": session_id,
            "message": message,
            "selected_material_ids": material_ids,
            "api_config": api_config,
            "paper_source_message_id": paper_source_message_id,
        },
    ) as response:
        response.raise_for_status()
        return await response_events(response)


def event_value(
    events: list[tuple[str, dict[str, Any]]],
    event_name: str,
) -> dict[str, Any]:
    values = [payload for name, payload in events if name == event_name]
    if not values:
        errors = [payload.get("message") for name, payload in events if name == "error"]
        raise WorkflowError(str(errors[0] if errors else f"缺少 {event_name} 事件"))
    return values[-1]


def selected_materials(
    materials: list[dict[str, Any]],
    material_title: str,
    dimensions: list[str],
) -> tuple[list[str], list[str]]:
    novel = next((item for item in materials if item["title"] == material_title), None)
    if not novel:
        raise WorkflowError(f"没有找到素材：{material_title}")
    nodes = novel["nodes"]
    ids: list[str] = []
    names: list[str] = []
    for dimension in dimensions:
        collection = next(
            (
                node
                for node in nodes
                if node["node_type"] == "collection" and node["display_name"] == dimension
            ),
            None,
        )
        if not collection:
            raise WorkflowError(f"素材 {material_title} 缺少分类：{dimension}")
        child = next((node for node in nodes if node["parent_id"] == collection["id"]), None)
        if not child:
            raise WorkflowError(f"分类 {dimension} 没有可用素材")
        ids.append(child["id"])
        names.append(child["display_name"])
    return ids, names


async def create_project(
    client: httpx.AsyncClient,
    api_config: dict[str, str],
    materials: list[dict[str, Any]],
    specification: dict[str, Any],
) -> dict[str, Any]:
    started = perf_counter()
    session_response = await client.post(
        f"{AGENT_URL}/sessions",
        json={"title": specification["title"]},
    )
    session_response.raise_for_status()
    session_id = session_response.json()["session"]["id"]
    material_ids, material_names = selected_materials(
        materials,
        specification["material_title"],
        specification["dimensions"],
    )
    print(
        json.dumps({"progress": "discussion_start", "title": specification["title"]}, ensure_ascii=False),
        flush=True,
    )

    discussion_events = await chat(
        client,
        api_config,
        session_id,
        specification["discussion"],
        material_ids,
    )
    discussion = event_value(discussion_events, "done")["message"]["content"]
    if any(name == "paper" for name, _ in discussion_events):
        raise WorkflowError("普通讨论错误地产生了稿纸")
    referenced_material = any(name in discussion for name in material_names)
    print(
        json.dumps(
            {"progress": "discussion_complete", "title": specification["title"], "referenced_material": referenced_material},
            ensure_ascii=False,
        ),
        flush=True,
    )

    chapter_ids: list[str] = []
    chapter_lengths: list[int] = []
    for chapter_index, prompt in enumerate(specification["chapters"], start=1):
        print(
            json.dumps({"progress": "chapter_start", "title": specification["title"], "chapter": chapter_index}, ensure_ascii=False),
            flush=True,
        )
        chapter_events = await chat(
            client,
            api_config,
            session_id,
            prompt,
            material_ids,
        )
        paper_event = event_value(chapter_events, "paper")
        paper = paper_event["paper"]
        content_length = len("".join(str(paper["content"]).split()))
        if content_length < 500:
            raise WorkflowError(f"篇章《{paper['title']}》过短：{content_length} 字")
        confirm_response = await client.post(
            f"{AGENT_URL}/chapter/update",
            json={"action": "confirm", "message_id": paper_event["message_id"]},
        )
        confirm_response.raise_for_status()
        confirmed = confirm_response.json()
        if confirmed["paper"]["status"] != "collected":
            raise WorkflowError("稿纸确认后没有进入已收录状态")
        chapter_ids.append(confirmed["chapter"]["id"])
        chapter_lengths.append(content_length)
        print(
            json.dumps(
                {"progress": "chapter_complete", "title": specification["title"], "chapter": chapter_index, "length": content_length},
                ensure_ascii=False,
            ),
            flush=True,
        )

    chapter_response = await client.get(
        f"{AGENT_URL}/chapters",
        params={"session_id": session_id},
    )
    chapter_response.raise_for_status()
    chapters = chapter_response.json()
    if len(chapters) != 3:
        raise WorkflowError(f"作品 {specification['title']} 的篇章数不是 3")
    chapter_titles = [chapter["title"] for chapter in chapters]
    if len(set(chapter_titles)) != len(chapter_titles):
        raise WorkflowError(f"作品 {specification['title']} 出现重复篇章标题")
    if any(re.search(r"第\s*\d+\s*章", title) for title in chapter_titles):
        raise WorkflowError(f"作品 {specification['title']} 的篇章标题含编号")

    reverse_response = await client.post(
        f"{AGENT_URL}/chapters/reorder",
        json={"chapter_ids": list(reversed(chapter_ids))},
    )
    reverse_response.raise_for_status()
    restore_response = await client.post(
        f"{AGENT_URL}/chapters/reorder",
        json={"chapter_ids": chapter_ids},
    )
    restore_response.raise_for_status()

    first_chapter = restore_response.json()[0]
    edit_response = await client.post(
        f"{AGENT_URL}/chapter/update",
        json={
            "action": "edit",
            "chapter_id": first_chapter["id"],
            "title": first_chapter["title"],
            "content": first_chapter["content"],
        },
    )
    edit_response.raise_for_status()

    return {
        "title": specification["title"],
        "session_id": session_id,
        "material_ids": material_ids,
        "material_names": material_names,
        "discussion_referenced_selected_name": referenced_material,
        "chapter_ids": chapter_ids,
        "chapter_lengths": chapter_lengths,
        "duration_ms": round((perf_counter() - started) * 1000),
    }


async def verify_failure_rollback(
    client: httpx.AsyncClient,
    session_id: str,
) -> bool:
    before = await client.post(
        f"{AGENT_URL}/session/switch",
        json={"session_id": session_id},
    )
    before.raise_for_status()
    before_ids = [message["id"] for message in before.json()["messages"]]
    invalid_config = {
        "provider": "compatible",
        "api_key": "",
        "base_url": "http://127.0.0.1:9",
        "model": "unreachable-test",
    }
    events = await chat(client, invalid_config, session_id, "网络失败回滚测试", [])
    if not any(name == "error" for name, _ in events):
        raise WorkflowError("无效 API 配置没有产生错误事件")
    after = await client.post(
        f"{AGENT_URL}/session/switch",
        json={"session_id": session_id},
    )
    after.raise_for_status()
    after_ids = [message["id"] for message in after.json()["messages"]]
    return before_ids == after_ids


async def export_project(
    client: httpx.AsyncClient,
    project: dict[str, Any],
    output_directory: Path,
) -> dict[str, Any]:
    async with client.stream(
        "POST",
        f"{AGENT_URL}/export",
        json={
            "format": "txt",
            "file_name": project["title"],
            "session_id": project["session_id"],
        },
    ) as response:
        response.raise_for_status()
        events = await response_events(response)
    final = event_value(events, "progress")
    content_base64 = final.get("content_base64")
    if not content_base64:
        raise WorkflowError("导出接口没有返回文件内容")
    output_directory.mkdir(parents=True, exist_ok=True)
    target = output_directory / f"{project['title']}.txt"
    target.write_bytes(b64decode(content_base64))
    content = target.read_text(encoding="utf-8")
    if content.count("第") < 3 or not all(str(index) in content for index in range(1, 4)):
        raise WorkflowError(f"导出文件章节结构不完整：{target}")
    return {"path": str(target), "bytes": target.stat().st_size}


async def run(output_directory: Path) -> dict[str, Any]:
    api_config = json.loads(os.environ["NOVELFORGE_TEST_API_CONFIG"])
    timeout = httpx.Timeout(420.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        health = await client.get(f"{AGENT_URL}/health")
        health.raise_for_status()
        materials_response = await client.get(f"{AGENT_URL}/materials")
        materials_response.raise_for_status()
        materials = materials_response.json()
        initial_sessions = (await client.get(f"{AGENT_URL}/sessions")).json()

        projects = []
        for specification in PROJECTS:
            project = await create_project(client, api_config, materials, specification)
            print(
                json.dumps(
                    {
                        "progress": "project_complete",
                        "title": project["title"],
                        "chapter_lengths": project["chapter_lengths"],
                        "duration_ms": project["duration_ms"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            projects.append(project)

        rollback_ok = await verify_failure_rollback(client, projects[0]["session_id"])
        exports = [
            await export_project(client, project, output_directory)
            for project in projects
        ]

        created_ids = {project["session_id"] for project in projects}
        for session in initial_sessions:
            if session["id"] not in created_ids:
                delete_response = await client.delete(
                    f"{AGENT_URL}/sessions/{session['id']}"
                )
                delete_response.raise_for_status()

        final_sessions = (await client.get(f"{AGENT_URL}/sessions")).json()
        return {
            "status": "ok",
            "projects": projects,
            "exports": exports,
            "failure_rollback": rollback_ok,
            "final_session_count": len(final_sessions),
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    result = asyncio.run(run(Path(arguments.output)))
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
