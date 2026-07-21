from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import sys
from time import perf_counter
from typing import Any

from fastapi.testclient import TestClient


AGENT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT_ROOT))

from app import app
import database


def parse_sse(content: str) -> dict[str, list[dict[str, Any]]]:
    events: dict[str, list[dict[str, Any]]] = {}
    for block in content.split("\n\n"):
        event = "message"
        data: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
        if data:
            events.setdefault(event, []).append(json.loads("\n".join(data)))
    return events


def chat(client: TestClient, session_id: str, project_id: str, api_config: dict[str, str], message: str, action: str, target: int | None = None) -> tuple[dict[str, list[dict[str, Any]]], float]:
    started = perf_counter()
    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "project_id": project_id,
            "message": message,
            "selected_material_ids": [],
            "api_config": api_config,
            "creation_action": action,
            "chapter_target_words": target,
        },
    )
    response.raise_for_status()
    events = parse_sse(response.text)
    if events.get("error"):
        raise RuntimeError(str(events["error"][-1].get("message")))
    return events, round(perf_counter() - started, 2)


def event(events: dict[str, list[dict[str, Any]]], name: str) -> dict[str, Any]:
    if not events.get(name):
        raise RuntimeError(f"缺少 {name} 事件")
    return events[name][-1]


def run(output: Path) -> dict[str, Any]:
    api_config = json.loads(os.environ["NOVELFORGE_TEST_API_CONFIG"])
    database.initialize_database()
    output.mkdir(parents=True, exist_ok=True)
    with TestClient(app) as client:
        created = client.post("/projects", json={"title": "雾港回声协议", "mode": "collaborative"})
        created.raise_for_status()
        project_id = created.json()["project"]["id"]
        session_id = created.json()["sessions"][0]["id"]

        discussion, discussion_seconds = chat(
            client,
            session_id,
            project_id,
            api_config,
            "规划一部全新的近未来悬疑小说：海港城市的声音档案会被人为篡改，主角是负责修复旧录音的工程师。请给出核心设定、主角目标、主要冲突、全书三阶段和前三章任务，只做规划。",
            "discuss",
        )
        if discussion.get("paper"):
            raise RuntimeError("规划动作错误地产生稿纸")
        discussion_text = event(discussion, "done")["message"]["content"]

        first, first_seconds = chat(
            client,
            session_id,
            project_id,
            api_config,
            "根据刚才的规划生成正式开篇：主角在修复一段港口事故录音时发现本应存在的警报声被替换。建立人物困境、可验证线索和行动选择，结尾留下下一步调查钩子。",
            "create",
            1500,
        )
        first_paper_event = event(first, "paper")
        first_paper = first_paper_event["paper"]
        first_confirm = client.post("/chapter/update", json={"action": "confirm", "message_id": first_paper_event["message_id"]})
        first_confirm.raise_for_status()

        second, second_seconds = chat(
            client,
            session_id,
            project_id,
            api_config,
            "承接上一章结尾生成下一章：主角追查警报声替换的时间戳，在档案馆夜班记录中发现一个不可能存在的操作账号。保持线索、人物状态和时间顺序连续。",
            "continue",
            1500,
        )
        second_paper_event = event(second, "paper")
        second_paper = second_paper_event["paper"]
        second_confirm = client.post("/chapter/update", json={"action": "confirm", "message_id": second_paper_event["message_id"]})
        second_confirm.raise_for_status()

        long_request, long_seconds = chat(
            client,
            session_id,
            project_id,
            api_config,
            "请现在一次生成一部100万字的完整长篇小说。",
            "create",
            5000,
        )
        if long_request.get("paper"):
            raise RuntimeError("超长小说请求错误地产生单篇稿纸")
        long_plan = event(long_request, "done")["message"]["content"]

        chapters = client.get("/chapters", params={"project_id": project_id}).json()
        if len(chapters) != 2:
            raise RuntimeError(f"确认后篇章数量错误：{len(chapters)}")
        if first_paper["length_status"] != "met" or second_paper["length_status"] != "met":
            raise RuntimeError("真实生成篇章未达到最低长度要求")
        if first_paper["content"] == second_paper["content"]:
            raise RuntimeError("续写章节与开篇重复")
        stages = [item["message"] for item in first.get("stage", [])]
        if not any("生成" in stage for stage in stages) or not any("检查" in stage for stage in stages):
            raise RuntimeError("生成阶段反馈不完整")

        novel_text = "\n\n".join(f"第{index}章 {chapter['title']}\n\n{chapter['content']}" for index, chapter in enumerate(chapters, start=1))
        novel_path = output / "雾港回声协议-两章真实生成测试.txt"
        novel_path.write_text(novel_text, encoding="utf-8")
        result = {
            "project_id": project_id,
            "session_id": session_id,
            "discussion_seconds": discussion_seconds,
            "first_seconds": first_seconds,
            "second_seconds": second_seconds,
            "long_plan_seconds": long_seconds,
            "first_title": first_paper["title"],
            "first_words": first_paper["word_count"],
            "second_title": second_paper["title"],
            "second_words": second_paper["word_count"],
            "chapter_count": len(chapters),
            "stage_messages": stages,
            "long_plan_chars": len(long_plan),
            "output": str(novel_path),
            "passed": True,
        }
        (output / "Chat与新小说流程真实测试结果.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        report = [
            "# NovelForge Chat 与新小说生成流程真实测试报告",
            "",
            f"> 测试时间：{datetime.now().isoformat(timespec='seconds')}  ",
            "> 结果：通过  ",
            "",
            "## 验收路径",
            "",
            "1. 显式选择规划动作，只讨论设定与章节路线，不生成稿纸。",
            "2. 显式生成约 1500 字正式开篇并确认收录。",
            "3. 基于已收录篇章记忆续写第二章并确认收录。",
            "4. 请求一次生成 100 万字小说，系统转为分章创作蓝图，不伪造单篇完成结果。",
            "",
            "## 结果",
            "",
            f"- 开篇：《{first_paper['title']}》，{first_paper['word_count']} 字，用时 {first_seconds} 秒。",
            f"- 第二章：《{second_paper['title']}》，{second_paper['word_count']} 字，用时 {second_seconds} 秒。",
            f"- 规划回复用时：{discussion_seconds} 秒；超长小说蓝图用时：{long_seconds} 秒。",
            f"- 生成阶段：{' → '.join(stages)}。",
            f"- 成品文件：`{novel_path}`。",
            "",
            "## 结论",
            "",
            "显式动作、目标字数、阶段反馈、篇章确认、连续续写和超长小说分章规划均可正常工作。",
            "",
        ]
        (output / "NovelForge Chat与新小说流程真实测试报告.md").write_text("\n".join(report), encoding="utf-8")
        return result


def main() -> None:
    output = Path(os.environ["NOVELFORGE_TEST_OUTPUT"]).resolve()
    print(json.dumps(run(output), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
