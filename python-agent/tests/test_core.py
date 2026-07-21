import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest


AGENT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT_ROOT))

from unittest.mock import AsyncMock, patch

from agent import AgentError, analyze_novel, bounded_generation_history, cards_to_ordered_dimensions, complete_json, create_paper_reply, infer_user_preferences, normalize_dimension_items, normalize_paper_title, paper_intent, parse_json_object, requested_auto_collect_count, requested_chapter_words, requests_whole_novel, select_adaptive_refinement_chapters, validate_universe_with_model
import database
from models import ChatRequest
from prompts import PAPER_SYSTEM_PROMPT
from tools import dimension_excerpt, full_text_coverage_items, key_content_coverage_items, semantic_analysis_regions, split_novel_chapters


def sample_analysis(name: str = "测试角色") -> dict:
    return {
        "primary_type": "scifi",
        "secondary_type": "",
        "dimensions": [
            {
                "name": "角色系统",
                "items": [
                    {
                        "name": name,
                        "category": "主角",
                        "summary": "负责调查异常信号并坚持核对证据。",
                        "details": {"身份": "观测站研究员", "动机": "查清信号来源"},
                        "tags": ["研究员", "信号", "调查"],
                    }
                ],
            }
        ],
    }


class ExcerptTests(unittest.TestCase):
    def test_dimension_excerpt_prefers_relevant_chunks(self) -> None:
        filler = "平静的日常描述。" * 300
        relevant = "量子引擎受到物理规则限制，跃迁会消耗反物质燃料。" * 80
        text = f"{filler}\n{filler}\n{relevant}\n{filler}\n{filler}"
        excerpt = dimension_excerpt(text, "科技设定", 5000)
        self.assertIn("量子引擎", excerpt)
        self.assertLess(len(excerpt), len(text))

    def test_short_summaries_are_safely_expanded(self) -> None:
        items = normalize_dimension_items(
            [{"name": "林舟", "category": "主角", "summary": "谨慎", "details": {}}],
            "角色系统",
        )
        self.assertGreaterEqual(len(items[0]["summary"]), 50)

    def test_paper_title_removes_chapter_numbers(self) -> None:
        self.assertEqual(normalize_paper_title("第3章 无声警报"), "无声警报")
        self.assertEqual(normalize_paper_title("无声警报·第三章"), "无声警报")

    def test_full_text_coverage_segments_preserve_every_character(self) -> None:
        text = "第一段" * 1000 + "\n第二段" * 1000 + "\n结尾"
        items = full_text_coverage_items(text, 777)
        self.assertEqual("".join(str(item["_source_content"]) for item in items), text)
        self.assertEqual(items[0]["details"]["start_char"], 1)
        self.assertEqual(items[-1]["details"]["end_char"], len(text))

    def test_semantic_regions_cover_continuous_ranges(self) -> None:
        text = "甲" * 2501
        regions = semantic_analysis_regions(text, 1000)
        self.assertEqual("".join(str(region["content"]) for region in regions), text)
        self.assertEqual(regions[-1]["end_char"], len(text))

    def test_key_content_index_covers_ranges_without_copying_full_text(self) -> None:
        text = "".join(f"第{index}章\n主角发现第{index}条线索并决定继续调查。" + "普通行程描写。" * 100 for index in range(1, 31))
        items = key_content_coverage_items(text, 5000, 900)
        self.assertEqual(items[0]["details"]["start_char"], 1)
        self.assertEqual(items[-1]["details"]["end_char"], len(text))
        self.assertLess(sum(item["details"]["retained_chars"] for item in items), len(text) // 2)
        self.assertTrue(all(item["_source_content"] for item in items))

    def test_adaptive_refinement_is_bounded_and_spread_across_novel(self) -> None:
        text = "".join(f"第{index}章 测试{index}\n主角发现线索并决定行动。\n" for index in range(1, 401))
        chapters = split_novel_chapters(text)
        lookup = {int(chapter["index"]): chapter for chapter in chapters}
        cards = [
            {
                "index": chapter["index"],
                "title": chapter["title"],
                "summary": "本章发现具体线索并推动行动，结尾形成下一阶段目标。",
                "importance": 6 + int(chapter["index"]) % 4,
                "confidence": 0.45,
            }
            for chapter in chapters
        ]
        full, evidence = select_adaptive_refinement_chapters(cards, lookup)
        selected = sorted(int(chapter["index"]) for chapter in [*full, *evidence])
        self.assertLessEqual(len(full), 24)
        self.assertLessEqual(len(evidence), 48)
        self.assertLess(selected[0], len(chapters) // 3)
        self.assertGreater(selected[-1], len(chapters) * 2 // 3)


class PaperIntentTests(unittest.IsolatedAsyncioTestCase):
    def test_generation_history_drops_large_outline_json_and_stays_bounded(self) -> None:
        history = [
            {"role": "assistant", "content": json.dumps({"volume": index, "chapters": ["章纲" * 4000]}, ensure_ascii=False)}
            for index in range(20)
        ]
        history.extend(
            [
                {"role": "user", "content": "承接最后一章继续写"},
                {"role": "assistant", "content": "上一章记忆摘要" * 300},
            ]
        )
        compacted = bounded_generation_history(history)
        self.assertLessEqual(sum(len(item["content"]) for item in compacted), 18000)
        self.assertTrue(any("承接最后一章" in item["content"] for item in compacted))
        self.assertFalse(any('"volume"' in item["content"] and '"chapters"' in item["content"] for item in compacted))

    def test_explicit_auto_collect_count_is_bounded(self) -> None:
        self.assertEqual(requested_auto_collect_count("连续生成后面3章并直接收录，无需确认"), 3)
        self.assertEqual(requested_auto_collect_count("直接生成后面十章并收入篇章"), 10)
        self.assertEqual(requested_auto_collect_count("直接生成后面20章并收入篇章"), 10)
        self.assertEqual(requested_auto_collect_count("生成后面几章并自动收录"), 3)
        self.assertEqual(requested_auto_collect_count("生成下一章，但不要直接收录"), 0)
        self.assertEqual(requested_auto_collect_count("连续生成三章，之后我再确认"), 0)

    def test_chapter_length_does_not_overwrite_project_total_words(self) -> None:
        self.assertNotIn("target_words", infer_user_preferences("请生成一篇3000字的正式篇章"))
        self.assertEqual(infer_user_preferences("这部小说全书目标100万字")["target_words"], 1_000_000)
        self.assertEqual(requested_chapter_words("请写3000字正文"), 3000)

    def test_whole_novel_request_uses_serial_planning(self) -> None:
        self.assertTrue(requests_whole_novel("请生成一部100万字完整小说"))
        self.assertFalse(requests_whole_novel("请生成一篇3000字正式篇章"))

    async def test_explicit_chinese_paper_command_is_deterministic(self) -> None:
        result = await paper_intent("请生成正式稿纸，写成完整短篇。", {})
        self.assertTrue(result["should_create"])
        self.assertEqual(result["mode"], "create")

    async def test_explicit_english_paper_command_is_supported(self) -> None:
        result = await paper_intent("Generate a formal paper for a complete English novel.", {})
        self.assertTrue(result["should_create"])
        self.assertEqual(result["mode"], "create")

    async def test_explicit_rewrite_uses_modify_mode(self) -> None:
        result = await paper_intent("请修改篇章并重写结局。", {})
        self.assertTrue(result["should_create"])
        self.assertEqual(result["mode"], "modify")

    async def test_negative_paper_command_stays_discussion(self) -> None:
        result = await paper_intent("这里只讨论方案，不要生成正式稿纸。", {})
        self.assertFalse(result["should_create"])

    async def test_semantic_universe_conflict_is_detected(self) -> None:
        rules = [{"category": "character", "key": "哥哥当场现身", "value": "必须改为哥哥遗物中的录音产生影响"}]
        model_result = {"violations": [{"rule_key": "哥哥当场现身", "reason": "已死角色以活人身份出现", "excerpt": "哥哥推门走了进来"}]}
        with patch("agent.complete_json", AsyncMock(return_value=model_result)):
            conflicts = await validate_universe_with_model(rules, "哥哥推门走了进来。", {})
        self.assertEqual(conflicts[0]["rule_key"], "哥哥当场现身")

    async def test_unmentioned_universe_details_are_not_conflicts(self) -> None:
        rules = [{"category": "system", "key": "双生武魂", "value": "双生武魂为混沌青莲与弑神枪，按阶段解封"}]
        with patch("agent.complete_json", AsyncMock(return_value={"violations": []})):
            conflicts = await validate_universe_with_model(rules, "林萧掌心浮现出一片青莲虚影。", {})
        self.assertEqual(conflicts, [])

    async def test_short_paper_is_retried_once(self) -> None:
        short = {"text": "初稿", "paper": {"title": "测试篇章", "content": "短" * 300, "memory": {"summary": "短稿摘要"}}}
        complete = {"text": "已完成", "paper": {"title": "测试篇章", "content": "完整正文" * 220, "memory": {"summary": "完整稿件摘要"}}}
        with patch("agent.complete_json", AsyncMock(side_effect=[short, complete])) as mocked:
            result = await create_paper_reply("生成正式篇章", [], "", {}, None, "", "create", "", "create", 1000)
        self.assertEqual(mocked.await_count, 2)
        self.assertEqual(result["paper"]["length_status"], "met")
        self.assertGreaterEqual(result["paper"]["word_count"], 700)

    async def test_overlong_paper_is_retried_once(self) -> None:
        overlong = {"text": "初稿", "paper": {"title": "测试篇章", "content": "冗长正文" * 500, "memory": {"summary": "过长稿摘要"}}}
        complete = {"text": "已压缩", "paper": {"title": "测试篇章", "content": "有效正文" * 250, "memory": {"summary": "压缩后摘要"}}}
        with patch("agent.complete_json", AsyncMock(side_effect=[overlong, complete])) as mocked:
            result = await create_paper_reply("生成正式篇章", [], "", {}, None, "", "create", "", "create", 1000)
        self.assertEqual(mocked.await_count, 2)
        self.assertEqual(result["paper"]["length_status"], "met")

    def test_paper_protocol_preserves_long_text_punctuation(self) -> None:
        raw = """<<<NOVELFORGE_TITLE>>>
雨夜回声
<<<NOVELFORGE_CONTENT>>>
林萧问：“路径 C:\\archive 真的存在吗？”

雨声落下，他没有回头。
<<<NOVELFORGE_MEMORY>>>
{"summary":"林萧在雨夜确认路径并继续前进。","key_events":["确认路径"]}
<<<NOVELFORGE_END>>>"""
        result = parse_json_object(raw)
        self.assertEqual(result["paper"]["title"], "雨夜回声")
        self.assertIn("C:\\archive", result["paper"]["content"])
        self.assertIn("\n\n雨声落下", result["paper"]["content"])
        self.assertEqual(result["paper"]["memory"]["key_events"], ["确认路径"])

    def test_paper_protocol_keeps_chapter_when_memory_json_is_malformed(self) -> None:
        raw = """<<<NOVELFORGE_TITLE>>>
未尽之路
<<<NOVELFORGE_CONTENT>>>
正文依然完整，应当保留。
<<<NOVELFORGE_MEMORY>>>
{"summary":"未闭合"
<<<NOVELFORGE_END>>>"""
        result = parse_json_object(raw)
        self.assertEqual(result["paper"]["content"], "正文依然完整，应当保留。")
        self.assertEqual(result["paper"]["memory"], {})

    async def test_paper_protocol_failure_regenerates_original_request(self) -> None:
        valid = """<<<NOVELFORGE_TITLE>>>
重试成功
<<<NOVELFORGE_CONTENT>>>
这是重新生成后的完整正文。
<<<NOVELFORGE_MEMORY>>>
{"summary":"重试成功。"}
<<<NOVELFORGE_END>>>"""
        with patch("agent.complete_text", AsyncMock(side_effect=["格式损坏", valid])) as mocked:
            result = await complete_json({}, PAPER_SYSTEM_PROMPT, "生成下一章")
        self.assertEqual(mocked.await_count, 2)
        self.assertEqual(result["paper"]["title"], "重试成功")
        retry_messages = mocked.await_args_list[1].args[1]
        self.assertIn("生成下一章", retry_messages[-1]["content"])


class LongAnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def test_long_analysis_scans_every_region_and_adds_key_content_index(self) -> None:
        text = "".join(f"第{index}章 测试章节{index}\n林舟发现第{index}条线索。" + "甲" * 5000 + "\n" for index in range(1, 81))

        macro_failed = False

        async def fake_complete_json(_config, system_prompt, user_content):
            nonlocal macro_failed
            if "长篇小说分区分析工具" in system_prompt:
                if not macro_failed:
                    macro_failed = True
                    raise AgentError("模拟区域结构化失败")
                return {"dimensions": []}
            matches = re.findall(r"\[章节 (\d+)\] ([^\n]+)", user_content)
            if len(matches) > 1:
                matches = matches[:-1]
            return {
                "chapters": [
                    {
                        "index": int(index),
                        "title": title,
                        "summary": f"林舟在第{index}章发现线索并继续推进调查，章节结尾保留下一步行动目标。",
                        "events": [{"type": "main_plot", "name": "调查线索", "description": f"取得第{index}条线索"}],
                        "entity_changes": [{"type": "character", "name": "林舟", "description": "调查经验继续增加"}],
                        "threads": [{"name": "异常信号", "status": "advanced", "description": "线索继续推进"}],
                        "craft": [{"type": "pacing", "name": "线索推进节奏", "description": "以新线索维持连续推进"}],
                        "importance": 7,
                        "confidence": 0.9,
                    }
                    for index, title in matches
                ]
            }

        with patch("agent.complete_json", AsyncMock(side_effect=fake_complete_json)) as mocked:
            analysis = await analyze_novel(text, {}, "web_novel")
        self.assertGreater(mocked.await_count, 1)
        self.assertEqual(analysis["coverage"]["analyzed_chapters"], analysis["coverage"]["chapter_count"])
        self.assertEqual(analysis["warnings"], [])
        self.assertEqual(analysis["coverage"]["indexed_chars"], len(text))
        self.assertLess(analysis["coverage"]["archived_chars"], len(text))
        self.assertEqual(analysis["dimensions"][-1]["name"], "关键内容索引")

    def test_plot_dimensions_remain_in_chapter_order(self) -> None:
        cards = [
            {
                "index": index,
                "title": f"第{index}章",
                "start_char": index * 100,
                "end_char": index * 100 + 99,
                "summary": f"第{index}章剧情摘要，事件按照章节顺序持续向前推进并形成明确结果。",
                "events": [{"type": "main_plot", "name": f"事件{index}", "description": f"第{index}个主线事件"}],
                "entity_changes": [],
                "threads": [],
                "craft": [],
                "importance": 7,
                "confidence": 0.9,
                "refined": False,
                "analysis_status": "model",
            }
            for index in range(1, 121)
        ]
        main_plot = next(item for item in cards_to_ordered_dimensions(cards, 50) if item["name"] == "主线情节")
        ranges = [(item["details"]["chapter_start"], item["details"]["chapter_end"]) for item in main_plot["items"]]
        self.assertEqual(ranges, [(1, 50), (51, 100), (101, 120)])


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.previous_database = os.environ.get("NOVELFORGE_DB_PATH")
        os.environ["NOVELFORGE_DB_PATH"] = str(
            Path(self.temp_directory.name) / "novel_forge.db"
        )
        database.initialize_database()

    def tearDown(self) -> None:
        if self.previous_database is None:
            os.environ.pop("NOVELFORGE_DB_PATH", None)
        else:
            os.environ["NOVELFORGE_DB_PATH"] = self.previous_database
        self.temp_directory.cleanup()

    def create_source(self) -> Path:
        path = Path(self.temp_directory.name) / "测试小说.txt"
        path.write_text("真实测试原文", encoding="utf-8")
        return path

    def create_chapter(self, session_id: str, title: str) -> dict:
        message = database.save_assistant_message(
            session_id,
            "稿纸已生成",
            {
                "title": title,
                "content": f"{title}的正文内容。",
                "status": "draft",
                "chapter_id": None,
                "target_chapter_id": None,
            },
        )
        return database.confirm_paper(message["id"])["chapter"]

    def test_reimport_replaces_existing_material_tree(self) -> None:
        source = self.create_source()
        first_id = database.store_analysis(source, sample_analysis("旧角色"))
        second_id = database.store_analysis(source, sample_analysis("新角色"))
        novels = database.list_material_tree()
        self.assertEqual(len(novels), 1)
        self.assertNotEqual(first_id, second_id)
        names = {node["display_name"] for node in novels[0]["nodes"]}
        self.assertIn("新角色", names)
        self.assertNotIn("旧角色", names)
        self.assertTrue(all(len(node["summary"]) >= 50 for node in novels[0]["nodes"]))

    def test_parent_material_context_contains_leaf_details(self) -> None:
        source = self.create_source()
        database.store_analysis(source, sample_analysis())
        novel = database.list_material_tree()[0]
        collection = next(
            node for node in novel["nodes"] if node["node_type"] == "collection"
        )
        context = database.material_context([collection["id"]])
        self.assertIn("观测站研究员", context)
        self.assertIn("查清信号来源", context)

    def test_full_text_segment_is_lazy_loaded_for_agent_context(self) -> None:
        source = self.create_source()
        analysis = sample_analysis()
        analysis["dimensions"].append(
            {
                "name": "全文覆盖索引",
                "items": [
                    {
                        "name": "全文片段 1/1",
                        "category": "全文覆盖索引",
                        "summary": "完整保存测试原文，供精确回查使用，不在素材树接口中直接传输大段正文。",
                        "details": {"start_char": 1, "end_char": 6, "storage": "sqlite_segment"},
                        "_source_content": "这是只在选择时加载的完整原文",
                        "tags": ["全文覆盖"],
                    }
                ],
            }
        )
        database.store_analysis(source, analysis)
        novel = database.list_material_tree()[0]
        segment = next(node for node in novel["nodes"] if node["display_name"] == "全文片段 1/1")
        self.assertNotIn("这是只在选择时加载的完整原文", segment["content"])
        self.assertIn("这是只在选择时加载的完整原文", database.material_context([segment["id"]]))

    def test_chat_history_uses_paper_memory_instead_of_full_collected_body(self) -> None:
        project = database.create_project("历史压缩测试")
        body = "不会重复进入历史的正文标记" * 500
        database.save_assistant_message(
            project["session_id"],
            "稿纸生成完成",
            {
                "title": "压缩历史",
                "content": body,
                "status": "collected",
                "chapter_id": "chapter-test",
                "target_chapter_id": None,
                "memory": {"summary": "主角完成调查并留下尚未解决的核心线索。", "key_events": ["发现关键证据"]},
            },
        )
        history = database.chat_history(project["session_id"])
        self.assertIn("主角完成调查", history[-1]["content"])
        self.assertIn("发现关键证据", history[-1]["content"])
        self.assertNotIn(body, history[-1]["content"])

    def test_chapter_archive_cards_are_lazy_loaded(self) -> None:
        source = self.create_source()
        analysis = sample_analysis()
        card = {
            "index": 1,
            "title": "第一章 起点",
            "start_char": 1,
            "end_char": 100,
            "summary": "主角在第一章发现关键线索并决定离开故乡，故事主线由此正式启动。",
            "events": [],
            "entity_changes": [],
            "threads": [],
            "craft": [],
            "importance": 8,
            "confidence": 0.9,
            "refined": True,
            "analysis_status": "model",
        }
        analysis["chapter_cards"] = [card]
        analysis["dimensions"].append(
            {
                "name": "章节剧情档案",
                "items": [
                    {
                        "name": "章节档案 1-1",
                        "category": "有序章节记录",
                        "summary": "按剧情顺序保存第一章记录。",
                        "details": {"chapter_start": 1, "chapter_end": 1, "chapter_count": 1, "storage": "novel_chapter_cards"},
                        "tags": ["章节档案"],
                    }
                ],
            }
        )
        database.store_analysis(source, analysis)
        archive = next(node for node in database.list_material_tree()[0]["nodes"] if node["display_name"] == "章节档案 1-1")
        self.assertNotIn(card["summary"], archive["content"])
        self.assertIn(card["summary"], database.material_context([archive["id"]]))

    def test_legacy_novel_id_and_child_context_can_coexist(self) -> None:
        source = self.create_source()
        novel_id = database.store_analysis(source, sample_analysis())
        novel = database.list_material_tree()[0]
        character_id = next(node["id"] for node in novel["nodes"] if node["node_type"] == "character")
        context = database.material_context([novel_id, character_id])
        self.assertIn("整本素材", context)
        self.assertIn("观测站研究员", context)
        self.assertIn("查清信号来源", context)
        self.assertIn("[素材：测试角色]", context)

    def test_material_context_budget_is_150000_characters(self) -> None:
        context = database.bounded_material_context(["甲" * 80000, "乙" * 80000])
        self.assertEqual(database.MATERIAL_CONTEXT_LIMIT, 150000)
        self.assertGreater(len(context), 100000)
        self.assertLessEqual(len(context), database.MATERIAL_CONTEXT_LIMIT)

    def test_material_tags_use_user_facing_labels(self) -> None:
        source = self.create_source()
        database.store_analysis(source, sample_analysis())
        novel = database.list_material_tree()[0]
        meta = next(node for node in novel["nodes"] if node["node_type"] == "meta")
        character = next(node for node in novel["nodes"] if node["node_type"] == "character")
        self.assertEqual(meta["category"], "作品类型")
        self.assertEqual(meta["tags"], ["作品类型", "科幻", "Agent 自动识别"])
        self.assertEqual(character["tags"], ["研究员", "信号", "调查"])

    def test_more_than_ten_materials_are_preserved_and_used(self) -> None:
        source = self.create_source()
        analysis = sample_analysis()
        analysis["dimensions"][0]["items"] = [
            {
                "name": f"角色{index}",
                "category": "角色",
                "summary": f"角色{index}负责第{index}条线索并参与核心调查。",
                "details": {"线索编号": index},
                "tags": ["调查", "线索", f"角色{index}"],
            }
            for index in range(1, 13)
        ]
        database.store_analysis(source, analysis)
        novel = database.list_material_tree()[0]
        material_ids = [node["id"] for node in novel["nodes"] if node["node_type"] == "character"]
        context = database.material_context(material_ids)
        self.assertEqual(len(material_ids), 12)
        self.assertIn("角色1", context)
        self.assertIn("角色12", context)
        session_id = next(session["id"] for session in database.list_sessions() if session["active"])
        message = database.save_user_message(session_id, "引用全部素材", material_ids)
        self.assertEqual(message["selected_material_ids"], material_ids)
        request = ChatRequest(
            session_id=session_id,
            message="引用全部素材",
            selected_material_ids=material_ids,
            api_config={"provider": "compatible", "api_key": "test", "base_url": "http://127.0.0.1:1", "model": "test"},
        )
        self.assertEqual(request.selected_material_ids, material_ids)

    def test_chapters_are_scoped_to_their_project(self) -> None:
        first_session = next(
            session["id"] for session in database.list_sessions() if session["active"]
        )
        second_session = database.create_session("第二部小说")["id"]
        first_a = self.create_chapter(first_session, "第一幕")
        first_b = self.create_chapter(first_session, "第二幕")
        second_a = self.create_chapter(second_session, "另一部的开篇")

        reordered = database.reorder_chapters([first_b["id"], first_a["id"]])
        self.assertEqual([chapter["title"] for chapter in reordered], ["第二幕", "第一幕"])
        self.assertEqual(
            [chapter["title"] for chapter in database.list_chapters(second_session)],
            ["另一部的开篇"],
        )

        database.delete_chapter(first_b["id"])
        self.assertEqual(database.list_chapters(first_session)[0]["sort_order"], 1)
        database.delete_session(first_session)
        self.assertEqual(database.list_chapters(first_session), [])
        self.assertEqual(database.list_chapters(second_session)[0]["id"], second_a["id"])

    def test_regeneration_replaces_only_after_success(self) -> None:
        session_id = next(
            session["id"] for session in database.list_sessions() if session["active"]
        )
        database.save_user_message(session_id, "重新生成", [])
        original = database.save_assistant_message(session_id, "旧回复")
        history = database.chat_history(session_id, 40, original["id"])
        self.assertNotIn("旧回复", [message["content"] for message in history])
        database.save_assistant_message(
            session_id,
            "新回复",
            message_id=original["id"],
        )
        messages = database.list_messages(session_id)
        assistant_messages = [message for message in messages if message["role"] == "assistant"]
        self.assertEqual(len(assistant_messages), 1)
        self.assertEqual(assistant_messages[0]["content"], "新回复")

    def test_modification_cannot_overwrite_with_another_chapter(self) -> None:
        session_id = next(
            session["id"] for session in database.list_sessions() if session["active"]
        )
        first = self.create_chapter(session_id, "第一幕")
        second = self.create_chapter(session_id, "第二幕")
        modification = database.save_assistant_message(
            session_id,
            "修改稿",
            {
                "title": "第一幕",
                "content": second["content"],
                "status": "draft",
                "chapter_id": None,
                "target_chapter_id": first["id"],
            },
        )
        with self.assertRaisesRegex(ValueError, "高度重复"):
            database.confirm_paper(modification["id"])
        self.assertEqual(database.list_chapters(session_id)[0]["content"], first["content"])


if __name__ == "__main__":
    unittest.main()
