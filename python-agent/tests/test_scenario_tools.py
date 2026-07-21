from pathlib import Path
import sys
import tempfile
import unittest


AGENT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT_ROOT))

from agent import AgentError, infer_user_preferences, validate_model_endpoint, workflow_prompt
from tools import check_compliance, detect_content_gaps, extract_txt_info
from prompts import CROSS_GENRE_PROMPTS


class ScenarioToolTests(unittest.TestCase):
    def test_workflow_preferences_cover_extreme_scenarios(self) -> None:
        self.assertEqual(infer_user_preferences("写一个5000字短篇")["workflow"], "short")
        self.assertEqual(infer_user_preferences("新书写200万字")["workflow"], "serial")
        self.assertEqual(infer_user_preferences("写10个短篇合集")["workflow"], "collection")
        self.assertEqual(infer_user_preferences("别给我建议，听我的")["mode"], "silent")
        self.assertEqual(infer_user_preferences("所有内容标注来源依据")["mode"], "traceable")
        self.assertEqual(infer_user_preferences("英文创作")["target_language"], "en")
        self.assertIn("跳过卷大纲", workflow_prompt({"workflow": "short"}))

    def test_style_intensity_changes_workflow_prompt(self) -> None:
        self.assertIn("克制简洁", workflow_prompt({"workflow": "standard", "style_intensity": 1}))
        self.assertIn("表现力", workflow_prompt({"workflow": "standard", "style_intensity": 5}))

    def test_local_privacy_mode_only_allows_loopback_model(self) -> None:
        validate_model_endpoint({"base_url": "http://127.0.0.1:11434/v1", "privacy_mode": "local"})
        validate_model_endpoint({"base_url": "http://localhost:1234/v1", "privacy_mode": "local"})
        with self.assertRaises(AgentError):
            validate_model_endpoint({"base_url": "https://api.openai.com/v1", "privacy_mode": "local"})

    def test_multiencoding_recovery(self) -> None:
        samples = {"gb18030": "中文素材与人物关系", "big5": "繁體素材與人物關係", "shift_jis": "物語の登場人物"}
        with tempfile.TemporaryDirectory() as directory:
            for encoding, content in samples.items():
                path = Path(directory) / f"{encoding}.txt"
                path.write_bytes(content.encode(encoding))
                decoded, detected = extract_txt_info(path)
                self.assertEqual(decoded, content)
                self.assertIn(detected, {"gb18030", "big5", "shift_jis"})

    def test_gap_and_compliance_tools(self) -> None:
        gap = detect_content_gaps("第一章 开始\n第二章 发展\n第四章 结局")
        self.assertEqual(gap["missing_chapters"], [3])
        self.assertEqual(len(gap["options"]), 3)
        result = check_compliance("这里包含自定义禁词", ["自定义禁词"])
        self.assertFalse(result["safe"])
        self.assertEqual(set(result["findings"][0]["options"]), {"implicit", "metaphorical", "author_only"})

    def test_cross_genre_prompt_formats_json_examples(self) -> None:
        rendered = CROSS_GENRE_PROMPTS["default"].format(source_type="wuxia", target_type="fantasy", source_language="zh", target_language="en", content="片段")
        self.assertIn('"bridged_content"', rendered)
        self.assertNotIn("mapping_table'", rendered)


if __name__ == "__main__":
    unittest.main()
