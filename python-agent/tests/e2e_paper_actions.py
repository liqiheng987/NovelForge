import asyncio
import json
import os
from pathlib import Path

import httpx

from e2e_workflow import (
    AGENT_URL,
    chat,
    event_value,
    export_project,
    selected_materials,
)


async def run(output_directory: Path) -> dict:
    api_config = json.loads(os.environ["NOVELFORGE_TEST_API_CONFIG"])
    async with httpx.AsyncClient(timeout=httpx.Timeout(420.0, connect=15.0)) as client:
        sessions = (await client.get(f"{AGENT_URL}/sessions")).json()
        session = next(item for item in sessions if item["title"] == "星海静默协议")
        switched = await client.post(
            f"{AGENT_URL}/session/switch",
            json={"session_id": session["id"]},
        )
        switched.raise_for_status()
        messages = switched.json()["messages"]
        source_message = next(
            message
            for message in messages
            if message.get("paper") and message["paper"]["status"] == "collected"
        )
        source_chapter_id = source_message["paper"]["chapter_id"]
        materials = (await client.get(f"{AGENT_URL}/materials")).json()
        material_ids, _ = selected_materials(
            materials,
            "三体",
            ["科技设定", "社会结构", "情节结构", "主题"],
        )

        modification_events = await chat(
            client,
            api_config,
            session["id"],
            "修改这篇正式篇章：保持主线不变，强化主人公发现异常数据时的行动细节，正文长度保持相近。",
            material_ids,
            source_message["id"],
        )
        modification = event_value(modification_events, "paper")
        confirm = await client.post(
            f"{AGENT_URL}/chapter/update",
            json={"action": "confirm", "message_id": modification["message_id"]},
        )
        confirm.raise_for_status()
        confirmed = confirm.json()
        if confirmed["chapter"]["id"] != source_chapter_id:
            raise RuntimeError("修改稿纸没有覆盖原篇章")

        abandon_events = await chat(
            client,
            api_config,
            session["id"],
            "请生成一篇备选正式篇章，描写危机发生前的普通值班夜，控制在600至800个中文字符。",
            material_ids,
        )
        abandon_paper = event_value(abandon_events, "paper")
        abandon = await client.post(
            f"{AGENT_URL}/chapter/update",
            json={"action": "abandon", "message_id": abandon_paper["message_id"]},
        )
        abandon.raise_for_status()
        if abandon.json()["paper"]["status"] != "abandoned":
            raise RuntimeError("稿纸没有进入已放弃状态")

        chapters = (
            await client.get(
                f"{AGENT_URL}/chapters",
                params={"session_id": session["id"]},
            )
        ).json()
        if len(chapters) != 3:
            raise RuntimeError("修改或放弃操作错误地改变了篇章数量")
        exported = await export_project(
            client,
            {"title": session["title"], "session_id": session["id"]},
            output_directory,
        )
        return {
            "status": "ok",
            "modified_chapter_id": source_chapter_id,
            "abandoned_message_id": abandon_paper["message_id"],
            "chapter_count": len(chapters),
            "export": exported,
        }


def main() -> None:
    output_directory = Path(os.environ["NOVELFORGE_TEST_OUTPUT"])
    print(json.dumps(asyncio.run(run(output_directory)), ensure_ascii=False))


if __name__ == "__main__":
    main()
