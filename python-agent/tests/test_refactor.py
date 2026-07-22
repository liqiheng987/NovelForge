from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch


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

    def test_database_backup_is_consistent_and_visible_in_status(self) -> None:
        database.create_project("备份验收")
        backup = database.create_database_backup(force=True)
        self.assertTrue(Path(backup["path"]).is_file())
        with closing(sqlite3.connect(backup["path"])) as snapshot:
            titles = [row[0] for row in snapshot.execute("SELECT title FROM projects")]
        self.assertIn("备份验收", titles)
        status = database.database_status()
        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["backup_count"], 1)
        self.assertEqual(status["latest_backup"]["path"], backup["path"])

    def test_forced_backups_created_in_the_same_second_have_unique_names(self) -> None:
        fixed_time = datetime(2026, 7, 22, 8, 30, 15, tzinfo=timezone.utc)
        with patch.object(database, "datetime") as mocked_datetime:
            mocked_datetime.now.return_value = fixed_time
            mocked_datetime.fromtimestamp.side_effect = datetime.fromtimestamp
            first = database.create_database_backup(force=True)
            second = database.create_database_backup(force=True)

        self.assertNotEqual(first["name"], second["name"])
        self.assertEqual(second["name"], "novel_forge-20260722-083015-01.db")
        self.assertTrue(Path(first["path"]).is_file())
        self.assertTrue(Path(second["path"]).is_file())

    def test_database_restore_recovers_old_data_and_preserves_safety_backup(self) -> None:
        database.create_project("恢复前作品")
        old_backup = database.create_database_backup(force=True)
        database.create_project("恢复前新增内容")

        result = database.restore_database_backup(old_backup["name"])

        restored_titles = {project["title"] for project in database.list_projects()}
        self.assertIn("恢复前作品", restored_titles)
        self.assertNotIn("恢复前新增内容", restored_titles)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["restored_backup"]["name"], old_backup["name"])
        with closing(sqlite3.connect(result["safety_backup"]["path"])) as snapshot:
            safety_titles = {row[0] for row in snapshot.execute("SELECT title FROM projects")}
        self.assertIn("恢复前新增内容", safety_titles)

    def test_corrupt_database_backup_is_rejected_without_changing_current_data(self) -> None:
        database.create_project("当前数据不可丢失")
        corrupt = database.backup_directory() / "novel_forge-20260722-090000.db"
        corrupt.parent.mkdir(parents=True, exist_ok=True)
        corrupt.write_bytes(b"not a sqlite database")

        listed = {item["name"]: item for item in database.list_database_backups(True)}
        self.assertFalse(listed[corrupt.name]["valid"])
        with self.assertRaises(ValueError):
            database.restore_database_backup(corrupt.name)
        self.assertIn("当前数据不可丢失", {project["title"] for project in database.list_projects()})

    def test_running_generation_task_blocks_database_restore(self) -> None:
        project = database.create_project("生成中作品")
        backup = database.create_database_backup(force=True)
        database.begin_generation_task(
            "running-task",
            project["session_id"],
            project["id"],
            "assistant-placeholder",
            {"message": "继续写作"},
            1,
        )

        with self.assertRaisesRegex(ValueError, "生成任务正在运行"):
            database.restore_database_backup(backup["name"])

    def test_database_restore_rolls_back_when_initialization_fails(self) -> None:
        database.create_project("备份中的旧数据")
        old_backup = database.create_database_backup(force=True)
        database.create_project("必须保留的当前数据")
        initialize_database = database.initialize_database
        calls = 0

        def fail_once() -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("simulated initialization failure")
            initialize_database()

        with patch.object(database, "initialize_database", side_effect=fail_once):
            with self.assertRaisesRegex(ValueError, "恢复备份失败"):
                database.restore_database_backup(old_backup["name"])

        titles = {project["title"] for project in database.list_projects()}
        self.assertIn("必须保留的当前数据", titles)
        self.assertEqual(calls, 2)

    def test_rules_facts_and_memory_are_project_scoped(self) -> None:
        first = database.create_project("第一宇宙")["id"]
        second = database.create_project("第二宇宙")["id"]
        database.create_universe_rule(first, "world", "跃迁代价", "消耗反物质", immutable=False)
        database.upsert_fact(first, "character", "林舟状态", "受伤", "chapter:1")
        self.assertEqual(len(database.list_universe_rules(first)), 1)
        self.assertEqual(database.list_universe_rules(second), [])
        self.assertIn("林舟状态", MemoryEngine().prompt_context(first))
        self.assertNotIn("林舟状态", MemoryEngine().prompt_context(second))

    def test_manual_rules_are_deletable_but_locked_rules_stay_protected(self) -> None:
        project_id = database.create_project("规则管理")["id"]
        manual = database.create_universe_rule(project_id, "world", "潮汐周期", "每七日变化")
        self.assertFalse(manual["immutable"])
        database.delete_universe_rule(manual["id"])
        self.assertEqual(database.list_universe_rules(project_id), [])

        locked = database.create_universe_rule(
            project_id,
            "world",
            "核心设定",
            "不可覆盖",
            immutable=True,
        )
        with self.assertRaises(ValueError):
            database.delete_universe_rule(locked["id"])
        with self.assertRaises(ValueError):
            database.import_universe_rules(project_id, project_id)

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
        chapter = database.confirm_paper(message["id"])["chapter"]
        database.edit_chapter(chapter["id"], "雨夜（修订）", "雨声停在窗台。" * 80)
        self.assertEqual(len(database.list_chapter_versions(chapter["id"])), 1)
        database.delete_project(project["id"])
        self.assertNotIn(project["id"], [item["id"] for item in database.list_projects()])
        self.assertEqual(database.list_chapter_versions(chapter["id"]), [])

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

        database.upsert_fact(project["id"], "character", "主角身份", "用户确认的调查员", "user")
        database.edit_chapter(confirmed["chapter"]["id"], "地下档案室（修订）", "主角重新整理了档案。")
        facts = {fact["key"]: fact for fact in database.list_facts(project["id"])}
        self.assertNotIn("钥匙用途", facts)
        self.assertEqual(facts["主角身份"]["value"], "用户确认的调查员")
        self.assertEqual(facts["主角身份"]["source"], "user")

    def test_chapter_facts_rebuild_from_remaining_chapters(self) -> None:
        project = database.create_project("事实回退测试")

        def confirm(title: str, value: str) -> dict:
            message = database.save_assistant_message(
                project["session_id"],
                "已生成篇章",
                {
                    "title": title,
                    "content": f"{title}正文。",
                    "memory": {
                        "facts": [
                            {"category": "character", "key": "林舟状态", "value": value}
                        ]
                    },
                    "status": "draft",
                },
            )
            return database.confirm_paper(message["id"])["chapter"]

        first = confirm("第一章", "受伤")
        second = confirm("第二章", "痊愈")
        self.assertEqual(database.list_facts(project["id"])[0]["value"], "痊愈")
        database.delete_chapter(second["id"])
        self.assertEqual(database.list_facts(project["id"])[0]["value"], "受伤")
        database.delete_chapter(first["id"])
        self.assertEqual(database.list_facts(project["id"]), [])

    def test_chapter_history_and_trash_restore_content_and_memory(self) -> None:
        project = database.create_project("章节恢复测试")
        message = database.save_assistant_message(
            project["session_id"],
            "已生成篇章",
            {
                "title": "雨夜来客",
                "content": "林舟带着黑钥匙进入港口。",
                "memory": {
                    "facts": [
                        {"category": "plot", "key": "黑钥匙位置", "value": "港口"}
                    ]
                },
                "status": "draft",
            },
        )
        original = database.confirm_paper(message["id"])["chapter"]

        database.edit_chapter(original["id"], "雨夜来客（修订）", "林舟独自离开港口。")
        history = database.list_chapter_versions(original["id"])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["title"], "雨夜来客")
        restored = database.restore_chapter_version(history[0]["id"])
        self.assertFalse(restored["restored_from_deleted"])
        self.assertEqual(restored["chapter"]["content"], "林舟带着黑钥匙进入港口。")
        self.assertEqual(database.list_facts(project["id"])[0]["value"], "港口")

        database.delete_chapter(original["id"])
        deleted = database.list_deleted_chapters(project["id"])
        self.assertEqual(len(deleted), 1)
        self.assertEqual(database.list_chapters(project["id"]), [])
        recovered = database.restore_chapter_version(deleted[0]["id"])
        self.assertTrue(recovered["restored_from_deleted"])
        self.assertEqual(recovered["chapter"]["id"], original["id"])
        self.assertEqual(database.list_deleted_chapters(project["id"]), [])

    def test_deleted_chapter_can_be_permanently_purged(self) -> None:
        project = database.create_project("永久删除测试")
        message = database.save_assistant_message(
            project["session_id"],
            "已生成篇章",
            {"title": "待清理章节", "content": "不会再恢复的正文。", "status": "draft"},
        )
        chapter = database.confirm_paper(message["id"])["chapter"]
        database.edit_chapter(chapter["id"], "待清理章节（修订）", "修订后的正文。")
        database.delete_chapter(chapter["id"])
        deleted = database.list_deleted_chapters(project["id"])[0]

        database.purge_deleted_chapter(deleted["id"])

        self.assertEqual(database.list_deleted_chapters(project["id"]), [])
        self.assertEqual(database.list_chapter_versions(chapter["id"]), [])
        with self.assertRaises(ValueError):
            database.restore_chapter_version(deleted["id"])

    def test_ai_chapter_update_creates_history_version(self) -> None:
        project = database.create_project("AI 修改历史测试")
        first_message = database.save_assistant_message(
            project["session_id"],
            "初稿",
            {"title": "旧标题", "content": "旧版正文。", "status": "draft"},
        )
        chapter = database.confirm_paper(first_message["id"])["chapter"]
        revised_message = database.save_assistant_message(
            project["session_id"],
            "修改稿",
            {
                "title": "新标题",
                "content": "新版正文。",
                "target_chapter_id": chapter["id"],
                "status": "draft",
            },
        )

        result = database.confirm_paper(revised_message["id"])
        history = database.list_chapter_versions(chapter["id"])

        self.assertEqual(result["chapter_operation"], "updated")
        self.assertEqual(history[0]["event_type"], "ai_edit")
        self.assertEqual(history[0]["title"], "旧标题")

    def test_chapter_draft_autosave_rejects_stale_content_and_cleans_up(self) -> None:
        project = database.create_project("自动草稿测试")
        message = database.save_assistant_message(
            project["session_id"],
            "已生成篇章",
            {"title": "原始标题", "content": "原始正文。", "status": "draft"},
        )
        chapter = database.confirm_paper(message["id"])["chapter"]
        draft = database.save_chapter_draft(
            chapter["id"],
            "未完成标题",
            "尚未正式保存的正文。",
            chapter["updated_at"],
        )
        self.assertEqual(draft["content"], "尚未正式保存的正文。")
        self.assertEqual(database.get_chapter_draft(chapter["id"])["title"], "未完成标题")

        updated = database.edit_chapter(chapter["id"], "正式标题", "正式保存的正文。")
        self.assertIsNone(database.get_chapter_draft(chapter["id"]))
        with self.assertRaises(ValueError):
            database.save_chapter_draft(
                chapter["id"],
                "过期草稿",
                "不能覆盖正式内容。",
                chapter["updated_at"],
            )

        database.save_chapter_draft(
            chapter["id"],
            "新的草稿",
            "基于最新版本的草稿。",
            updated["updated_at"],
        )
        database.delete_chapter(chapter["id"])
        self.assertIsNone(database.get_chapter_draft(chapter["id"]))

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
