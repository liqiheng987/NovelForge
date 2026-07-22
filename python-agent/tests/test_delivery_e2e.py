from base64 import b64decode
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from fastapi.testclient import TestClient


AGENT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT_ROOT))

import app as app_module


def parse_sse(content: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in content.split("\n\n"):
        event_name = "message"
        data = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
        if data:
            events.append((event_name, json.loads("\n".join(data))))
    return events


class PublicationDeliveryE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.previous_database = os.environ.get("NOVELFORGE_DB_PATH")
        os.environ["NOVELFORGE_DB_PATH"] = str(Path(self.temp_directory.name) / "novel_forge.db")
        self.client_context = TestClient(app_module.app)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        if self.previous_database is None:
            os.environ.pop("NOVELFORGE_DB_PATH", None)
        else:
            os.environ["NOVELFORGE_DB_PATH"] = self.previous_database
        self.temp_directory.cleanup()

    def confirm_chapter(self, session_id: str, title: str, content: str) -> dict:
        message = app_module.save_assistant_message(
            session_id,
            "已生成正式稿纸",
            {"title": title, "content": content, "status": "draft"},
        )
        response = self.client.post(
            "/chapter/update",
            json={"action": "confirm", "message_id": message["id"]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["chapter"]

    def test_project_moves_from_blocked_to_reviewed_and_exported(self) -> None:
        created = self.client.post("/projects", json={"title": "雨港来信", "mode": "guided"})
        self.assertEqual(created.status_code, 200, created.text)
        project = created.json()["project"]
        session_id = created.json()["sessions"][0]["id"]

        empty_report = self.client.get(f"/projects/{project['id']}/publication-readiness")
        self.assertEqual(empty_report.status_code, 200)
        self.assertEqual(empty_report.json()["status"], "blocked")
        self.assertFalse(empty_report.json()["can_export"])
        blocked_export = self.client.post(
            "/export",
            json={
                "format": "txt",
                "file_name": "空作品",
                "session_id": session_id,
                "project_id": project["id"],
            },
        )
        self.assertEqual(blocked_export.status_code, 409, blocked_export.text)

        settings = self.client.patch(
            f"/projects/{project['id']}/settings",
            json={
                "target_words": 100,
                "compliance_level": "custom",
                "sensitive_terms": ["禁用表达", "禁用表达"],
            },
        )
        self.assertEqual(settings.status_code, 200, settings.text)
        self.assertEqual(settings.json()["settings"]["sensitive_terms"], ["禁用表达"])

        first = self.confirm_chapter(
            session_id,
            "黑钥匙",
            "黑钥匙在雨夜被交给林舟。禁用表达随后出现在旧信背面。" * 3,
        )
        second = self.confirm_chapter(
            session_id,
            "港口回声",
            "林舟沿着港口追查黑钥匙的来历，并在潮声中确认了守门人的证词。" * 3,
        )
        impact = self.client.post(
            "/impact/analyze",
            json={"project_id": project["id"], "changed_node_id": first["id"], "change_type": "modify"},
        )
        self.assertEqual(impact.status_code, 200, impact.text)
        self.assertTrue(impact.json()["affected_nodes"])
        draft = self.client.put(
            "/chapter/draft",
            json={
                "chapter_id": first["id"],
                "title": first["title"],
                "content": first["content"] + "尚未保存的结尾。",
                "source_updated_at": first["updated_at"],
            },
        )
        self.assertEqual(draft.status_code, 200, draft.text)

        report = self.client.get(f"/projects/{project['id']}/publication-readiness").json()
        self.assertEqual(report["status"], "attention")
        self.assertTrue(report["can_export"])
        self.assertGreaterEqual(report["summary"]["total_words"], 100)
        check_levels = {check["id"]: check["level"] for check in report["checks"]}
        self.assertEqual(check_levels["drafts"], "warning")
        self.assertEqual(check_levels["impacts"], "warning")
        self.assertEqual(check_levels["compliance"], "warning")
        self.assertEqual(report["findings"][0]["chapter_id"], first["id"])
        self.assertEqual(report["findings"][0]["term"], "禁用表达")
        unacknowledged_export = self.client.post(
            "/export",
            json={
                "format": "txt",
                "file_name": "雨港来信",
                "session_id": session_id,
                "project_id": project["id"],
            },
        )
        self.assertEqual(unacknowledged_export.status_code, 409, unacknowledged_export.text)
        acknowledged_export = self.client.post(
            "/export",
            json={
                "format": "txt",
                "file_name": "雨港来信-待复核",
                "session_id": session_id,
                "project_id": project["id"],
                "acknowledge_warnings": True,
            },
        )
        self.assertEqual(acknowledged_export.status_code, 200, acknowledged_export.text)
        self.assertTrue(
            any(
                event == "progress" and payload.get("progress") == 100
                for event, payload in parse_sse(acknowledged_export.text)
            )
        )

        revised = self.client.post(
            "/chapter/update",
            json={
                "action": "edit",
                "chapter_id": first["id"],
                "title": first["title"],
                "content": "黑钥匙在雨夜被交给林舟，旧信只留下一个模糊的港口印章。" * 4,
            },
        )
        self.assertEqual(revised.status_code, 200, revised.text)
        unresolved = self.client.get(
            "/impact",
            params={"project_id": project["id"], "unresolved_only": True},
        ).json()
        for impact in unresolved:
            resolved = self.client.post(f"/impact/{impact['id']}/resolve")
            self.assertEqual(resolved.status_code, 200, resolved.text)

        ready_report = self.client.get(f"/projects/{project['id']}/publication-readiness").json()
        self.assertEqual(ready_report["status"], "ready")
        self.assertTrue(ready_report["can_export"])
        self.assertFalse(ready_report["findings"])
        self.assertTrue(all(check["level"] == "ok" for check in ready_report["checks"]))

        exported = self.client.post(
            "/export",
            json={
                "format": "txt",
                "file_name": "雨港来信",
                "session_id": session_id,
                "project_id": project["id"],
                "acknowledge_warnings": True,
            },
        )
        self.assertEqual(exported.status_code, 200, exported.text)
        final = next(
            payload
            for event, payload in parse_sse(exported.text)
            if event == "progress" and payload.get("progress") == 100
        )
        manuscript = b64decode(final["content_base64"]).decode("utf-8-sig")
        self.assertIn("第1章 黑钥匙", manuscript)
        self.assertIn("第2章 港口回声", manuscript)
        self.assertNotIn("禁用表达", manuscript)


if __name__ == "__main__":
    unittest.main()
