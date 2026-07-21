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


class AgentAuthenticationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.previous_database = os.environ.get("NOVELFORGE_DB_PATH")
        self.previous_token = app_module.AGENT_TOKEN
        self.previous_instance = app_module.AGENT_INSTANCE_ID
        os.environ["NOVELFORGE_DB_PATH"] = str(Path(self.temp_directory.name) / "novel_forge.db")
        app_module.AGENT_TOKEN = "test-agent-token"
        app_module.AGENT_INSTANCE_ID = "test-instance"
        self.client_context = TestClient(app_module.app)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        app_module.AGENT_TOKEN = self.previous_token
        app_module.AGENT_INSTANCE_ID = self.previous_instance
        if self.previous_database is None:
            os.environ.pop("NOVELFORGE_DB_PATH", None)
        else:
            os.environ["NOVELFORGE_DB_PATH"] = self.previous_database
        self.temp_directory.cleanup()

    def test_health_exposes_instance_without_token(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["instance_id"], "test-instance")

    def test_data_routes_require_matching_token(self) -> None:
        self.assertEqual(self.client.get("/materials").status_code, 401)
        self.assertEqual(
            self.client.get("/materials", headers={"Authorization": "Bearer wrong"}).status_code,
            401,
        )
        response = self.client.get(
            "/materials",
            headers={"Authorization": "Bearer test-agent-token"},
        )
        self.assertEqual(response.status_code, 200)

    def test_cross_bridge_inherits_active_project_privacy_mode(self) -> None:
        headers = {"Authorization": "Bearer test-agent-token"}
        project = self.client.get("/projects", headers=headers).json()[0]
        response = self.client.patch(
            f"/projects/{project['id']}/settings",
            headers=headers,
            json={"privacy_mode": "local"},
        )
        self.assertEqual(response.status_code, 200)

        bridge = AsyncMock(return_value={"bridged_content": "fixture"})
        with patch.object(app_module, "cross_genre_bridge", bridge):
            response = self.client.post(
                "/cross/bridge",
                headers=headers,
                json={
                    "source_text": "测试片段",
                    "source_type": "武侠",
                    "target_type": "奇幻",
                    "api_config": {
                        "provider": "compatible",
                        "api_key": "",
                        "base_url": "https://example.com/v1",
                        "model": "fixture",
                    },
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(bridge.await_args.args[3]["privacy_mode"], "local")

    def test_analysis_rejects_unsupported_and_oversized_files(self) -> None:
        headers = {"Authorization": "Bearer test-agent-token"}
        api_config = {
            "provider": "compatible",
            "api_key": "",
            "base_url": "http://127.0.0.1:11434/v1",
            "model": "fixture",
        }
        unsupported = Path(self.temp_directory.name) / "notes.md"
        unsupported.write_text("fixture", encoding="utf-8")
        response = self.client.post(
            "/analyze",
            headers=headers,
            json={"paths": [str(unsupported)], "api_config": api_config},
        )
        self.assertEqual(response.status_code, 415)

        oversized = Path(self.temp_directory.name) / "oversized.txt"
        with oversized.open("wb") as file:
            file.seek(app_module.MAX_IMPORT_FILE_BYTES)
            file.write(b"x")
        response = self.client.post(
            "/analyze",
            headers=headers,
            json={"paths": [str(oversized)], "api_config": api_config},
        )
        self.assertEqual(response.status_code, 413)

    def test_authenticated_client_can_create_database_backup(self) -> None:
        headers = {"Authorization": "Bearer test-agent-token"}
        response = self.client.post("/maintenance/backup", headers=headers)
        self.assertEqual(response.status_code, 200)
        backup = response.json()["backup"]
        self.assertTrue(Path(backup["path"]).is_file())
        status = self.client.get("/maintenance/database", headers=headers)
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["status"], "ok")

    def test_deleting_chapter_reports_affected_references(self) -> None:
        headers = {"Authorization": "Bearer test-agent-token"}
        project = app_module.create_project("删除影响测试")

        def confirm(title: str, content: str) -> dict:
            message = app_module.save_assistant_message(
                project["session_id"],
                "已生成篇章",
                {"title": title, "content": content, "status": "draft"},
            )
            return app_module.confirm_paper(message["id"])["chapter"]

        source = confirm("黑钥匙", "黑钥匙在雨夜出现。")
        affected = confirm("港口回声", "守门人仍在追查黑钥匙的来历。")
        response = self.client.delete(
            "/chapter/delete",
            headers=headers,
            params={"chapter_id": source["id"]},
        )
        self.assertEqual(response.status_code, 200)
        affected_ids = {item["affected_node_id"] for item in response.json()["affected_nodes"]}
        self.assertIn(affected["id"], affected_ids)

    def test_deleted_chapter_can_be_restored_through_api(self) -> None:
        headers = {"Authorization": "Bearer test-agent-token"}
        project = app_module.create_project("回收站接口测试")
        message = app_module.save_assistant_message(
            project["session_id"],
            "已生成篇章",
            {"title": "可恢复章节", "content": "这段正文不能意外丢失。", "status": "draft"},
        )
        chapter = app_module.confirm_paper(message["id"])["chapter"]

        deleted = self.client.delete(
            "/chapter/delete",
            headers=headers,
            params={"chapter_id": chapter["id"]},
        )
        self.assertEqual(deleted.status_code, 200)
        trash = self.client.get(
            "/chapters/trash",
            headers=headers,
            params={"project_id": project["id"]},
        )
        self.assertEqual(trash.status_code, 200)
        self.assertEqual(len(trash.json()), 1)

        restored = self.client.post(
            f"/chapter/version/{trash.json()[0]['id']}/restore",
            headers=headers,
        )
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json()["chapter"]["id"], chapter["id"])
        self.assertTrue(restored.json()["restored_from_deleted"])


if __name__ == "__main__":
    unittest.main()
