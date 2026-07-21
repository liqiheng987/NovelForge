import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


AGENT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = AGENT_ROOT.parent
sys.path.insert(0, str(AGENT_ROOT))

import database
from app import app


SCENARIOS = {
    1: ("guided", "standard", "我想写个东西，请生成开篇"),
    2: ("guided", "standard", "生成校园言情开篇"),
    3: ("collaborative", "standard", "生成轻松探险开篇"),
    4: ("traceable", "standard", "生成星际航行开篇并标注来源依据"),
    5: ("collaborative", "standard", "生成女性联盟宫廷故事"),
    6: ("collaborative", "standard", "生成密室推理开篇"),
    7: ("collaborative", "standard", "生成科幻修仙开篇"),
    8: ("collaborative", "standard", "生成悬疑言情开篇"),
    9: ("guided", "standard", "生成废土种田开篇"),
    10: ("teaching", "standard", "生成第六部新作开篇"),
    11: ("silent", "standard", "生成都市奇幻职场开篇"),
    12: ("collaborative", "adaptation", "把剧本改编成小说并生成开篇"),
    13: ("guided", "standard", "无素材，生成校园故事开篇"),
    14: ("collaborative", "standard", "生成现代职场分支开篇"),
    15: ("collaborative", "standard", "生成带记忆能力的悬疑开篇"),
    16: ("guided", "short", "生成5000字乡村短篇开篇"),
    17: ("guided", "serial", "生成200万字连载开篇"),
    19: ("traceable", "fanfiction", "生成同人前传开篇"),
    20: ("collaborative", "adaptation", "把诗歌意象转成小说开篇"),
    21: ("silent", "standard", "生成正文"),
    26: ("silent", "standard", "别给我建议，听我的，生成正文"),
    27: ("traceable", "standard", "生成内容并标注来源依据"),
    28: ("collaborative", "standard", "用英文生成武侠开篇"),
    29: ("guided", "standard", "生成编码修复后的素材开篇"),
    30: ("guided", "standard", "生成缺章补全草稿"),
    31: ("guided", "collection", "生成10个短篇合集的第一篇"),
    32: ("guided", "standard", "生成系列前传开篇"),
    33: ("collaborative", "standard", "生成结局重写稿"),
    34: ("collaborative", "standard", "生成平行AI版本"),
    35: ("guided", "standard", "按出版审查标准生成开篇"),
    36: ("guided", "standard", "生成跨设备续写开篇"),
    37: ("silent", "standard", "只存本地，生成开篇"),
    38: ("teaching", "standard", "教学演示：生成童话结构示例"),
    39: ("guided", "short", "生成5000字克苏鲁短篇开篇"),
}


def sample_analysis(label: str) -> dict:
    return {
        "primary_type": "web_novel" if label == "网文" else "light_novel",
        "secondary_type": "",
        "type_source": "test_fixture",
        "dimensions": [
            {
                "name": "角色系统",
                "items": [
                    {
                        "name": f"{label}参考角色",
                        "category": "主角",
                        "summary": f"来自{label}素材的参考角色，具备明确目标、行动约束和可复用的人物关系，用于场景化验收测试。",
                        "details": {"来源": label, "目标": "推进故事"},
                        "tags": [label, "角色", "参考"],
                    }
                ],
            }
        ],
        "warnings": [],
    }


def parse_sse(content: str) -> dict[str, list[dict]]:
    events: dict[str, list[dict]] = {}
    for block in content.split("\n\n"):
        event = "message"
        data = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
        if data:
            events.setdefault(event, []).append(json.loads("\n".join(data)))
    return events


class UserScenarioWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.previous_database = os.environ.get("NOVELFORGE_DB_PATH")
        os.environ["NOVELFORGE_DB_PATH"] = str(Path(self.temp_directory.name) / "novel_forge.db")
        self.client = TestClient(app)
        self.client.__enter__()
        web_path = PROJECT_ROOT / "小说素材" / "网文" / "三体.txt"
        light_path = PROJECT_ROOT / "小说素材" / "轻小说" / "86-不存在的战区.txt"
        self.assertTrue(web_path.is_file())
        self.assertTrue(light_path.is_file())
        database.store_analysis(web_path, sample_analysis("网文"))
        database.store_analysis(light_path, sample_analysis("轻小说"))
        trees = database.list_material_tree()
        self.material_ids = [next(node["id"] for node in novel["nodes"] if node["node_type"] != "meta") for novel in trees]

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        if self.previous_database is None:
            os.environ.pop("NOVELFORGE_DB_PATH", None)
        else:
            os.environ["NOVELFORGE_DB_PATH"] = self.previous_database
        self.temp_directory.cleanup()

    def test_all_documented_user_paths(self) -> None:
        executed: list[int] = []
        api_config = {"provider": "compatible", "api_key": "", "base_url": "http://unused", "model": "fixture"}
        fake_intent = AsyncMock(return_value={"should_create": True, "mode": "create", "reason": "fixture"})

        async def fake_paper(message, history, material_context, api_config, source_paper, chapter_context, mode, project_context="", generation_action="create", target_words=None):
            return {"text": "已生成验收稿件。", "paper": {"title": "验收开篇", "content": f"潮汐城市中，主角开始行动。{message}", "status": "draft", "chapter_id": None, "target_chapter_id": None, "generation_action": generation_action, "target_words": target_words}}

        with patch("app.paper_intent", fake_intent), patch("app.create_paper_reply", fake_paper):
            for case_id, (mode, workflow, message) in SCENARIOS.items():
                with self.subTest(case_id=case_id):
                    created = self.client.post("/projects", json={"title": f"实例{case_id}", "mode": mode})
                    self.assertEqual(created.status_code, 200, created.text)
                    payload = created.json()
                    project_id = payload["project"]["id"]
                    session_id = payload["sessions"][0]["id"]
                    settings = {"workflow": workflow, "target_words": 5000 if workflow == "short" else 2_000_000 if workflow == "serial" else 80_000}
                    self.assertEqual(self.client.patch(f"/projects/{project_id}/settings", json=settings).status_code, 200)
                    self.assertEqual(self.client.post("/mode/switch", json={"session_id": session_id, "mode": mode}).status_code, 200)
                    selected = [] if case_id == 13 else [self.material_ids[case_id % 2]]
                    if selected:
                        pin = self.client.post("/pin/material", json={"project_id": project_id, "material_id": selected[0]})
                        self.assertEqual(pin.status_code, 200, pin.text)
                    premise = self.client.post("/story/nodes", json={"project_id": project_id, "session_id": session_id, "layer": "premise", "title": "核心设定", "content": message})
                    self.assertEqual(premise.status_code, 200, premise.text)
                    beat = self.client.post("/story/nodes", json={"project_id": project_id, "session_id": session_id, "parent_id": premise.json()["id"], "layer": "chapter_beat", "title": "第一章细纲", "content": "主角做出选择并留下悬念"})
                    self.assertEqual(beat.status_code, 200, beat.text)
                    chat = self.client.post("/chat", json={"session_id": session_id, "project_id": project_id, "mode": mode, "message": message, "selected_material_ids": selected, "api_config": api_config})
                    self.assertEqual(chat.status_code, 200, chat.text)
                    events = parse_sse(chat.text)
                    self.assertIn("paper", events)
                    self.assertIn("workflow", events)
                    message_id = events["paper"][0]["message_id"]
                    confirmed = self.client.post("/chapter/update", json={"action": "confirm", "message_id": message_id})
                    self.assertEqual(confirmed.status_code, 200, confirmed.text)
                    chapters = self.client.get("/chapters", params={"project_id": project_id}).json()
                    self.assertEqual(len(chapters), 1)
                    exported = self.client.post("/export", json={"format": "txt", "file_name": f"实例{case_id}", "session_id": session_id, "project_id": project_id})
                    self.assertIn("content_base64", exported.text)
                    self.exercise_special_path(case_id, project_id, session_id, premise.json()["id"], api_config)
                    executed.append(case_id)
        self.assertEqual(executed, list(SCENARIOS))
        self.assertEqual(len(executed), 34)

    def exercise_special_path(self, case_id: int, project_id: str, session_id: str, node_id: str, api_config: dict) -> None:
        if case_id in {7, 21}:
            target = self.client.post("/projects", json={"title": f"实例{case_id}复制目标", "mode": "silent"}).json()["project"]["id"]
            self.assertEqual(self.client.post(f"/story/nodes/{node_id}/copy", json={"target_project_id": target}).status_code, 200)
        if case_id in {14, 34}:
            branch = self.client.post("/branch/create", json={"project_id": project_id, "source_session_id": session_id, "name": "平行版本"})
            self.assertEqual(branch.status_code, 200)
            compared = self.client.post("/branch/compare", json={"project_id": project_id, "branch_a_id": session_id, "branch_b_id": branch.json()["branch_id"]})
            self.assertEqual(compared.json(), {"added": [], "deleted": [], "modified": []})
        if case_id in {15, 33}:
            other = self.client.post("/story/nodes", json={"project_id": project_id, "layer": "chapter_beat", "title": "远端呼应", "content": "核心设定需要同步修改"}).json()
            impact = self.client.post("/impact/analyze", json={"project_id": project_id, "changed_node_id": node_id, "change_type": "modify"})
            self.assertEqual(impact.status_code, 200)
            self.assertIsInstance(other["id"], str)
        if case_id in {12, 20, 28}:
            result = {"bridged_content": "bridged", "mapping_table": [{"source": "江湖", "target": "Martial World", "reason": "文化等效"}]}
            with patch("app.cross_genre_bridge", AsyncMock(return_value=result)):
                response = self.client.post("/cross/bridge", json={"source_text": "江湖", "source_type": "wuxia", "target_type": "fantasy", "source_language": "zh", "target_language": "en" if case_id == 28 else "zh", "api_config": api_config})
                self.assertEqual(response.json()["bridged_content"], "bridged")
        if case_id in {1, 2, 9, 13, 31, 39}:
            with patch("app.generate_inspirations", AsyncMock(return_value=[{"id": index, "title": f"方向{index}"} for index in range(10)])):
                response = self.client.post("/inspiration/generate", json={"premise": "当前困境", "dilemma": "如何推进", "project_id": project_id, "api_config": api_config})
                self.assertEqual(len(response.json()["options"]), 10)
        if case_id in {3, 10, 35}:
            with patch("app.style_trial", AsyncMock(return_value=[{"style": style, "text": "试写"} for style in ("cinematic", "literary", "web_novel")])):
                response = self.client.post("/style/trial", json={"scene": "雨夜相遇", "styles": ["cinematic", "literary", "web_novel"], "project_id": project_id, "api_config": api_config})
                self.assertEqual(len(response.json()["trials"]), 3)
        if case_id == 30:
            gap = self.client.post("/content/gaps", json={"text": "第一章 开始\n第三章 继续"}).json()
            self.assertEqual(gap["missing_chapters"], [2])
        if case_id == 32:
            rule = self.client.post("/universe/rule", json={"project_id": project_id, "category": "character", "key": "哥哥", "value": "已经死亡", "immutable": True})
            self.assertEqual(rule.status_code, 200)
        if case_id == 35:
            compliance = self.client.post("/compliance/check", json={"text": "包含作者禁词", "custom_terms": ["作者禁词"]}).json()
            self.assertFalse(compliance["safe"])
        if case_id == 39:
            archived = self.client.post(f"/projects/{project_id}/status", json={"status": "archived"}).json()
            self.assertEqual(archived["status"], "archived")
            restored = self.client.post(f"/projects/{project_id}/status", json={"status": "active"}).json()
            self.assertEqual(restored["status"], "active")


if __name__ == "__main__":
    unittest.main()
