from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


AGENT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT_ROOT))

import app as app_module
import database


def parse_sse(content: str) -> dict[str, list[dict[str, object]]]:
    events: dict[str, list[dict[str, object]]] = {}
    for block in content.split("\n\n"):
        event_name = "message"
        data: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
        if data:
            events.setdefault(event_name, []).append(json.loads("\n".join(data)))
    return events


def paper_result(index: int, character: str) -> dict[str, object]:
    content = character * 900
    return {
        "text": f"第{index}章已生成。",
        "paper": {
            "title": f"连续篇章{index}",
            "content": content,
            "memory": {
                "summary": f"第{index}章发生独立事件并留下下一章接口。",
                "key_events": [f"事件{index}"],
                "continuity_notes": [f"承接第{index}章结尾"],
                "facts": [],
            },
            "status": "draft",
            "chapter_id": None,
            "target_chapter_id": None,
            "word_count": len(content),
            "target_words": 1500,
            "length_status": "met",
            "generation_action": "continue" if index > 1 else "create",
        },
    }


class BatchAutoCollectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.previous_database = os.environ.get("NOVELFORGE_DB_PATH")
        os.environ["NOVELFORGE_DB_PATH"] = str(Path(self.temp_directory.name) / "novel_forge.db")
        database.initialize_database()
        project = database.create_project("批量收录测试", "silent")
        self.project_id = project["id"]
        self.session_id = project["session_id"]

    def tearDown(self) -> None:
        if self.previous_database is None:
            os.environ.pop("NOVELFORGE_DB_PATH", None)
        else:
            os.environ["NOVELFORGE_DB_PATH"] = self.previous_database
        self.temp_directory.cleanup()

    def request(self, message: str) -> dict[str, list[dict[str, object]]]:
        with TestClient(app_module.app) as client:
            response = client.post(
                "/chat",
                json={
                    "session_id": self.session_id,
                    "project_id": self.project_id,
                    "message": message,
                    "selected_material_ids": [],
                    "api_config": {
                        "provider": "compatible",
                        "api_key": "test",
                        "base_url": "https://example.invalid",
                        "model": "test-model",
                    },
                    "creation_action": "continue",
                    "chapter_target_words": 1500,
                },
            )
        response.raise_for_status()
        return parse_sse(response.text)

    def test_three_chapters_are_generated_and_collected_in_order(self) -> None:
        results = [paper_result(1, "甲"), paper_result(2, "乙"), paper_result(3, "丙")]
        with (
            patch.object(app_module, "create_paper_reply", AsyncMock(side_effect=results)) as create_mock,
            patch.object(app_module, "validate_universe_with_model", AsyncMock(return_value=[])),
            patch.object(app_module, "analyze_impact", return_value=[]),
        ):
            events = self.request("直接承接上一章，连续生成后面3章并直接收入篇章，无需逐章确认。")
        self.assertEqual(create_mock.await_count, 3)
        self.assertEqual(len(events.get("auto_collected", [])), 3)
        self.assertEqual(events["auto_collect_done"][-1]["completed"], 3)
        self.assertNotIn("paper", events)
        chapters = database.list_chapters(self.project_id)
        self.assertEqual([chapter["title"] for chapter in chapters], ["连续篇章1", "连续篇章2", "连续篇章3"])
        collected = [message for message in database.list_messages(self.session_id) if message.get("paper")]
        self.assertEqual([message["paper"]["status"] for message in collected], ["collected", "collected", "collected"])

    def test_ten_chapters_are_supported(self) -> None:
        characters = "甲乙丙丁戊己庚辛壬癸"
        results = [paper_result(index, character) for index, character in enumerate(characters, start=1)]
        with (
            patch.object(app_module, "create_paper_reply", AsyncMock(side_effect=results)) as create_mock,
            patch.object(app_module, "validate_universe_with_model", AsyncMock(return_value=[])),
            patch.object(app_module, "analyze_impact", return_value=[]),
        ):
            events = self.request("直接连续生成后面10章并收入篇章，无需确认。")
        self.assertEqual(create_mock.await_count, 10)
        self.assertEqual(len(events.get("auto_collected", [])), 10)
        self.assertEqual(events["auto_collect_start"][-1]["limit"], 10)
        self.assertEqual(len(database.list_chapters(self.project_id)), 10)

    def test_transient_connection_error_retries_current_chapter(self) -> None:
        with (
            patch.object(
                app_module,
                "create_paper_reply",
                AsyncMock(side_effect=[app_module.AgentError("无法连接模型服务，请检查 API 地址和网络"), paper_result(1, "甲")]),
            ) as create_mock,
            patch.object(app_module, "validate_universe_with_model", AsyncMock(return_value=[])),
            patch.object(app_module, "analyze_impact", return_value=[]),
            patch.object(app_module.asyncio, "sleep", AsyncMock()),
        ):
            events = self.request("生成下一章并直接收入篇章，无需确认。")
        self.assertEqual(create_mock.await_count, 2)
        self.assertTrue(any(event.get("code") == "network_retry" for event in events.get("stage", [])))
        self.assertEqual(len(database.list_chapters(self.project_id)), 1)

    def test_structured_output_error_retries_only_current_chapter(self) -> None:
        results: list[object] = [paper_result(1, "甲"), app_module.AgentError("模型没有按篇章分隔协议返回完整内容，请重试当前章")]
        results.extend(paper_result(index, character) for index, character in enumerate("乙丙丁戊己庚辛壬癸", start=2))
        with (
            patch.object(app_module, "create_paper_reply", AsyncMock(side_effect=results)) as create_mock,
            patch.object(app_module, "validate_universe_with_model", AsyncMock(return_value=[])),
            patch.object(app_module, "analyze_impact", return_value=[]),
            patch.object(app_module.asyncio, "sleep", AsyncMock()),
        ):
            events = self.request("直接连续生成后面10章并收入篇章，无需确认。")
        self.assertEqual(create_mock.await_count, 11)
        self.assertTrue(any(event.get("code") == "format_retry" for event in events.get("stage", [])))
        self.assertEqual(events["auto_collect_done"][-1]["completed"], 10)
        self.assertEqual(len(database.list_chapters(self.project_id)), 10)

    def test_partial_failure_keeps_already_collected_chapter(self) -> None:
        with (
            patch.object(
                app_module,
                "create_paper_reply",
                AsyncMock(side_effect=[paper_result(1, "甲"), app_module.AgentError("第二章生成失败")]),
            ),
            patch.object(app_module, "validate_universe_with_model", AsyncMock(return_value=[])),
            patch.object(app_module, "analyze_impact", return_value=[]),
        ):
            events = self.request("连续生成后面3章并自动收录，不用确认。")
        self.assertEqual(len(database.list_chapters(self.project_id)), 1)
        self.assertEqual(events["auto_collect_partial"][-1]["completed"], 1)
        self.assertTrue(events["done"][-1]["partial"])


if __name__ == "__main__":
    unittest.main()
