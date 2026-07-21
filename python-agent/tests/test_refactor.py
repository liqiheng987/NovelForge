import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


AGENT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT_ROOT))

import database
from memory import MemoryEngine


class RefactorDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.previous_database = os.environ.get("NOVELFORGE_DB_PATH")
        os.environ["NOVELFORGE_DB_PATH"] = str(Path(self.temp_directory.name) / "novel_forge.db")
        database.initialize_database()

    def tearDown(self) -> None:
        if self.previous_database is None:
            os.environ.pop("NOVELFORGE_DB_PATH", None)
        else:
            os.environ["NOVELFORGE_DB_PATH"] = self.previous_database
        self.temp_directory.cleanup()

    def test_projects_sessions_modes_and_branches(self) -> None:
        project = database.create_project("星海纪元")
        project_id = project["id"]
        source_id = project["session_id"]
        session = database.create_session(project_id, "世界观讨论", "collaborative")
        self.assertEqual(session["project_id"], project_id)
        self.assertEqual(database.update_session_mode(session["id"], "traceable")["mode"], "traceable")
        branch = database.create_branch(project_id, source_id, "黑暗结局")
        self.assertEqual(branch["branch_of"], source_id)
        self.assertEqual(database.branch_compare(project_id, source_id, branch["id"])["modified"], [])
        database.save_assistant_message(branch["id"], "分支新增内容")
        self.assertEqual(len(database.branch_compare(project_id, source_id, branch["id"])["added"]), 1)
        database.branch_merge(project_id, branch["id"], source_id)
        self.assertIn("分支新增内容", [message["content"] for message in database.list_messages(source_id)])

    def test_rules_facts_and_memory_are_project_scoped(self) -> None:
        first = database.create_project("第一宇宙")["id"]
        second = database.create_project("第二宇宙")["id"]
        database.create_universe_rule(first, "world", "跃迁代价", "消耗反物质", immutable=False)
        database.upsert_fact(first, "character", "林舟状态", "受伤", "chapter:1")
        self.assertEqual(len(database.list_universe_rules(first)), 1)
        self.assertEqual(database.list_universe_rules(second), [])
        self.assertIn("林舟状态", MemoryEngine().prompt_context(first))
        self.assertNotIn("林舟状态", MemoryEngine().prompt_context(second))

    def test_story_structure_settings_and_archive(self) -> None:
        first = database.create_project("长篇工程")
        second = database.create_project("衍生作品")
        node = database.create_story_node(first["id"], "premise", "核心设定", "潮汐会删除记忆", session_id=first["session_id"])
        child = database.create_story_node(first["id"], "chapter_beat", "第一章", "主角寻找妹妹", parent_id=node["id"])
        self.assertEqual(len(database.list_story_nodes(first["id"])), 2)
        self.assertTrue(database.update_story_node(child["id"], locked=True)["locked"])
        copied = database.copy_story_node(node["id"], second["id"])
        self.assertEqual(copied["metadata"]["source_project_id"], first["id"])
        updated = database.update_project_settings(first["id"], {"workflow": "serial", "target_words": 2_000_000})
        self.assertEqual(updated["settings"]["workflow"], "serial")
        self.assertEqual(database.set_project_status(first["id"], "archived")["status"], "archived")
        self.assertEqual(database.set_project_status(first["id"], "active")["status"], "active")

    def test_project_with_confirmed_chapter_can_be_deleted(self) -> None:
        project = database.create_project("待删除作品")
        database.create_project("保留作品")
        message = database.save_assistant_message(
            project["session_id"],
            "已生成篇章",
            {"title": "雨夜", "content": "雨声落在窗台。" * 80, "status": "draft", "chapter_id": None, "target_chapter_id": None},
        )
        database.confirm_paper(message["id"])
        database.delete_project(project["id"])
        self.assertNotIn(project["id"], [item["id"] for item in database.list_projects()])

    def test_deleting_session_stays_in_current_project(self) -> None:
        first = database.create_project("第一作品")
        removable = database.create_session(first["id"], "待删除会话")
        second = database.create_project("第二作品")
        database.switch_session(second["session_id"])

        fallback_id = database.delete_session(removable["id"])
        switched = database.switch_session(fallback_id)

        self.assertEqual(switched["project_id"], first["id"])

    def test_deleting_last_session_recreates_same_project_session(self) -> None:
        project = database.create_project("仅有一个会话")

        fallback_id = database.delete_session(project["session_id"])
        switched = database.switch_session(fallback_id)

        self.assertEqual(switched["project_id"], project["id"])
        self.assertEqual(len(database.list_sessions(project["id"])), 1)

    def test_chapter_memory_covers_full_chapter_and_updates_facts(self) -> None:
        project = database.create_project("长篇记忆测试")
        content = "开篇信号出现。" * 80 + "中段主角发现钥匙来自失踪的父亲。" + "追逐持续。" * 80 + "结尾钥匙打开了地下档案室。"
        memory = {
            "summary": "开篇出现异常信号；中段主角确认钥匙来自失踪的父亲；结尾钥匙打开地下档案室。",
            "key_events": ["获得父亲留下的钥匙", "打开地下档案室"],
            "character_changes": ["主角从怀疑转为确认父亲参与事件"],
            "unresolved_threads": ["父亲为何留下钥匙"],
            "resolved_threads": ["钥匙用途已确认"],
            "facts": [{"category": "plot", "key": "钥匙用途", "value": "打开地下档案室"}],
        }
        message = database.save_assistant_message(
            project["session_id"],
            "已生成篇章",
            {"title": "地下档案室", "content": content, "memory": memory, "status": "draft"},
        )
        confirmed = database.confirm_paper(message["id"])
        self.assertEqual(confirmed["chapter"]["memory"]["unresolved_threads"], ["父亲为何留下钥匙"])
        context = database.chapter_summaries(project["id"])
        self.assertIn("中段主角确认钥匙来自失踪的父亲", context)
        self.assertIn("未解线索", context)
        self.assertIn("钥匙用途", [fact["key"] for fact in database.list_facts(project["id"])])

    def test_existing_chapter_fallback_reads_start_middle_and_end(self) -> None:
        project = database.create_project("旧作品兼容测试")
        content = "开篇标记。" + "甲" * 1500 + "中段标记。" + "乙" * 1500 + "结尾标记。"
        message = database.save_assistant_message(
            project["session_id"],
            "旧稿纸",
            {"title": "三段记忆", "content": content, "status": "draft"},
        )
        database.confirm_paper(message["id"])
        context = database.chapter_summaries(project["id"])
        self.assertIn("开篇标记", context)
        self.assertIn("中段标记", context)
        self.assertIn("结尾标记", context)

    def test_long_serial_index_keeps_first_and_last_chapter(self) -> None:
        project = database.create_project("两千章连载测试")
        timestamp = database.now()
        connection = database.connect()
        try:
            connection.executemany(
                "INSERT INTO chapters (id,session_id,project_id,title,content,summary,memory,sort_order,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        f"chapter-{index}",
                        project["session_id"],
                        project["id"],
                        f"连续篇章{index}",
                        f"第{index}章发生的事件",
                        f"第{index}章发生的事件",
                        json.dumps({"summary": f"第{index}章发生的事件"}, ensure_ascii=False),
                        index,
                        timestamp,
                        timestamp,
                    )
                    for index in range(1, 2001)
                ],
            )
            connection.commit()
        finally:
            connection.close()
        context = database.chapter_summaries(project["id"])
        self.assertLessEqual(len(context), database.CHAPTER_CONTEXT_LIMIT)
        self.assertIn("第1章", context)
        self.assertIn("第2000章", context)


if __name__ == "__main__":
    unittest.main()
