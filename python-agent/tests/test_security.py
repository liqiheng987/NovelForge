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


if __name__ == "__main__":
    unittest.main()
