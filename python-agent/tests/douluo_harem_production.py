from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any

from fastapi.testclient import TestClient


AGENT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT_ROOT))

from agent import AgentError, complete_json
from app import app
import database
from memory import memory_engine


PROJECT_TITLE = "斗罗大陆后宫"
SOURCE_NOVEL_ID = "740f4a8b-4635-4b22-8031-057a86034a57"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / "测试输出" / PROJECT_TITLE
TARGET_TOTAL_WORDS = 900_000
CHAPTERS_PER_VOLUME = 25
TOTAL_VOLUMES = 12
TOTAL_CHAPTERS = TOTAL_VOLUMES * CHAPTERS_PER_VOLUME

ROSTER = [
    "小舞",
    "宁荣荣",
    "朱竹清",
    "孟依然",
    "独孤雁",
    "叶泠泠",
    "火舞",
    "水冰儿",
    "水月儿",
    "雪舞",
    "胡列娜",
    "千仞雪",
    "柳二龙",
    "唐月华",
    "阿银",
    "比比东",
    "波赛西",
    "紫珍珠",
    "白沉香",
    "雪珂",
    "朱竹云",
]

RULES = [
    ("character", "唯一主角", "林萧是唯一叙事主角，与唐三同龄，从圣魂村同一届武魂觉醒开始成长。"),
    ("system", "双生武魂", "林萧的双生武魂为混沌青莲与弑神枪；力量分层解封，必须经历修炼、魂环、实战和代价，不能开局直接无敌。"),
    ("system", "后宫系统边界", "后宫系统只发布成长、守护、羁绊和选择任务并给予修炼奖励，禁止操纵思想、强制好感、奴役或以惩罚逼迫感情。"),
    ("character", "成年亲密门槛", "第126章完成明确时间跳跃后，林萧及所有恋爱参与者均已年满十八岁；此前只写成长、竞争、友情、守护和朦胧羁绊，不写恋爱确认或亲密行为。"),
    ("character", "自愿多伴侣关系", "所有成年感情关系必须经过独立人物弧、明确知情、自主选择、相互尊重和可撤回同意；不把女性写成奖品，不以系统强迫任何人加入。"),
    ("plot", "唐三终局敌人", "唐三从长期竞争者逐渐因控制欲、双标和权力执念走向终局敌人；冲突必须逐步升级，最终神战中由林萧击杀。"),
    ("plot", "玉小刚与唐昊清算", "玉小刚和唐昊通过谎言揭露、阴谋失败、公开战败、名誉崩塌以及宗门或司法惩罚被持续清算；禁止无意义的长期酷刑和性暴力。"),
    ("plot", "女性角色完整人物弧", "主要正反派女性角色都必须保留目标、能力、阵营选择和成长代价；成年后通过各自剧情自愿加入林萧的多伴侣家庭或长期亲密同盟。"),
    ("world", "平行世界原则", "本作是以斗罗大陆素材为背景蓝本的平行世界同人；沿用武魂、魂环、势力和地理规则，但事件走向由林萧介入后产生原创变化，不复刻原文段落。"),
    ("plot", "三百章结构", "全书固定十二卷三百章，每卷二十五章；卷内必须有目标、升级、关系推进、反转、高潮和下一卷钩子。"),
    ("plot", "终局与日常", "林萧最终成为统御而非奴役众生的神王，完成对唐三阵营核心敌人的终局清算；第296至300章用于成年家庭、神界治理和人间旅行日常。"),
    ("world", "合规表达", "亲密场景只发生在成年人之间，明确自愿并采用含蓄、非露骨表达；不描写未成年人性内容、性暴力、胁迫性关系或露骨器官细节。"),
]

VOLUMES = [
    {
        "title": "第一卷 圣魂村的第二道光",
        "range": "1-25",
        "arc": "林萧与唐三同龄觉醒，后宫系统以成长辅助形态启动；混沌青莲和弑神枪分层封印。林萧拒绝玉小刚收徒，用可验证的修炼成果第一次击破其理论权威。",
        "focus": "林萧、唐三、唐昊、玉小刚、小舞；只建立同伴与竞争关系。",
        "climax": "林萧在第一魂环事件中救下同伴并击败唐三，迫使唐昊第一次正视这个变量。",
    },
    {
        "title": "第二卷 诺丁城双星争锋",
        "range": "26-50",
        "arc": "围绕工读生、魂师注册、猎魂森林和学院资源展开竞争。林萧建立自己的小队原则，小舞从被安排的人生中获得自主选择，唐三的控制欲初露。",
        "focus": "小舞形成独立成长线；玉小刚的教学漏洞连续暴露。",
        "climax": "学院公开考核中林萧以有限能力组合取胜，玉小刚第一次名誉受挫。",
    },
    {
        "title": "第三卷 史莱克不是唯一答案",
        "range": "51-75",
        "arc": "林萧进入索托区域后拒绝盲从史莱克规则，结识宁荣荣、朱竹清和孟依然，建立更公平的训练与资源分配方式。",
        "focus": "宁荣荣、朱竹清、孟依然获得独立目标；戴沐白与唐三阵营成为阶段对手。",
        "climax": "林萧小队在公开实战中击败史莱克核心阵容，证明另一条成长路线。",
    },
    {
        "title": "第四卷 天斗斗魂风暴",
        "range": "76-100",
        "arc": "斗魂场、皇斗战队和学院选择交织。林萧结识独孤雁、叶泠泠，并通过治疗、毒素与团队协作破解玉小刚的僵化战术。",
        "focus": "独孤雁、叶泠泠、宁荣荣、朱竹清；关系仍停留在信任与共同成长。",
        "climax": "皇斗与史莱克多方战中林萧完成逆转，唐三首次使用越界手段仍告失败。",
    },
    {
        "title": "第五卷 全大陆魂师大赛",
        "range": "101-125",
        "arc": "学院大赛汇聚火舞、水冰儿、水月儿、雪舞、胡列娜与千仞雪暗线。林萧以战术、格局和个人突破赢得尊重，唐三逐渐把胜负置于伙伴之上。",
        "focus": "多阵营女性角色先作为对手或盟友建立完整人物弧，不确认恋爱关系。",
        "climax": "决赛与赛后阴谋同时爆发，林萧保护各队成员并揭穿玉小刚借学生证明自己的算计。",
    },
    {
        "title": "第六卷 成年后的各自选择",
        "range": "126-150",
        "arc": "开篇明确多年时间跳跃，主要角色均已成年。众人因各自目标重聚，感情线从明确沟通、边界和共同责任开始，系统不得替任何人作决定。",
        "focus": "小舞、宁荣荣、朱竹清、独孤雁、叶泠泠、火舞、水冰儿等首批成年关系线。",
        "climax": "林萧公开多伴侣关系原则并承担政治代价，首批伴侣在完全知情下自主确认关系。",
    },
    {
        "title": "第七卷 杀戮之都的红月",
        "range": "151-175",
        "arc": "林萧进入杀戮之都追查神位与唐三黑化线，和胡列娜从敌对合作走向成年感情选择；比比东、阿银与唐昊旧事逐步揭开。",
        "focus": "胡列娜、比比东、阿银；以救赎、真相和阵营选择为核心。",
        "climax": "林萧夺得关键神性线索，唐三为力量牺牲无辜，双方正式决裂。",
    },
    {
        "title": "第八卷 海神岛与瀚海盟约",
        "range": "176-200",
        "arc": "海洋试炼引出波赛西、紫珍珠和白沉香。林萧不接受以献祭换神位的旧规则，尝试建立不牺牲守护者的新传承。",
        "focus": "波赛西、紫珍珠、白沉香及既有成年伴侣共同处理信任和职责。",
        "climax": "林萧改写海神试炼并保住波赛西，唐三失去原本可获得的海神核心认可。",
    },
    {
        "title": "第九卷 天斗宫变与月轩棋局",
        "range": "201-225",
        "arc": "千仞雪、雪珂、唐月华、柳二龙和朱竹云卷入帝国与宗门战争。林萧用证据、联盟和战斗拆解唐三与唐昊的政治布局。",
        "focus": "千仞雪、雪珂、唐月华、柳二龙、朱竹云；每人先完成阵营与人生选择。",
        "climax": "宫变被改写，唐昊公开战败，玉小刚的关键谎言形成完整证据链。",
    },
    {
        "title": "第十卷 武魂帝国的新生",
        "range": "226-250",
        "arc": "林萧阻止武魂殿与两大帝国的全面毁灭，推动比比东和千仞雪摆脱神位操纵。玉小刚接受公开审判，唐昊失去以武力凌驾规则的资格。",
        "focus": "比比东、千仞雪、柳二龙、唐月华、阿银的旧怨与新选择完成收束。",
        "climax": "武魂帝国重组为受约束的魂师共同体，唐三夺取禁忌神力逃入神界裂隙。",
    },
    {
        "title": "第十一卷 双生神位终战",
        "range": "251-275",
        "arc": "林萧完成双武魂神位融合，各阵营伙伴以独立战线参与神战。唐三将控制与牺牲合理化，成为必须被终结的神级敌人。",
        "focus": "全部成年伴侣与盟友拥有实际战斗、治理或救援任务，不做旁观花瓶。",
        "climax": "林萧击溃唐三神国和核心追随者，终战前揭示后宫系统真正目的是学习尊重选择而非收集人数。",
    },
    {
        "title": "第十二卷 神王纪元与人间灯火",
        "range": "276-300",
        "arc": "前二十章完成唐三终战、神界改制、各阵营清算与成年关系总收束；最后五章转入神界家庭、人间旅行、学院庆典、育儿讨论与平静生活。",
        "focus": "完整群像收束，强调知情、自愿、协商、各自事业和共同家庭。",
        "climax": "林萧成为新神王并亲手击杀拒绝停战的唐三；第296至300章全部为大战后的成年日常。",
    },
]


OUTLINE_SYSTEM_PROMPT = """
你是 NovelForge 的长篇连载章纲工具。请根据给出的素材摘要、宇宙铁律和卷级目标，生成指定连续章节的细纲。
要求：
1. 只能输出严格 JSON，不要 Markdown，不要解释。
2. chapters 数量和 chapter_number 必须完全匹配请求，不得跳号、合并或新增。
3. 每章必须推进主线、成长、关系或伏笔中的至少两项，不能连续写无效过渡。
4. 每章字段：chapter_number、title、beat、cast、growth、relationship、hook。
5. beat 45-90 个中文字符，按起因、行动、结果写具体事件；其余字段简洁，总输出保持紧凑。
6. 第1-125章所有角色处于少年成长阶段，只写友情、竞争、守护与羁绊，禁止恋爱确认和亲密行为。
7. 第126章必须明确时间跳跃且相关角色均已成年；之后感情推进也必须知情、自愿、非强迫、非露骨。
8. 唐三的敌对升级必须循序渐进；玉小刚和唐昊以揭露、失败、审判和失去权力清算，不写无意义酷刑。
9. 不复述《斗罗大陆》原文段落，只使用背景规则并创作新的事件链。
JSON 格式：{"chapters":[{"chapter_number":1,"title":"章名","beat":"事件链","cast":["人物"],"growth":"成长变化","relationship":"关系变化","hook":"章末钩子"}]}
""".strip()


def parse_sse(content: str) -> dict[str, list[dict[str, Any]]]:
    events: dict[str, list[dict[str, Any]]] = {}
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


def api_config() -> dict[str, str]:
    value = os.environ.get("NOVELFORGE_TEST_API_CONFIG", "").strip()
    if not value:
        raise RuntimeError("缺少 NOVELFORGE_TEST_API_CONFIG")
    config = json.loads(value)
    return {
        "provider": str(config.get("provider") or "compatible"),
        "api_key": str(config.get("api_key") or ""),
        "base_url": str(config["base_url"]),
        "model": str(config["model"]),
    }


def find_project() -> dict[str, Any] | None:
    return next((item for item in database.list_projects() if item["title"] == PROJECT_TITLE), None)


def ensure_project(client: TestClient) -> tuple[str, str, str]:
    project = find_project()
    if not project:
        response = client.post("/projects", json={"title": PROJECT_TITLE, "mode": "silent"})
        response.raise_for_status()
        payload = response.json()
        project = payload["project"]
        main_session_id = payload["sessions"][0]["id"]
    else:
        sessions = database.list_sessions(str(project["id"]))
        main_session_id = next((item["id"] for item in reversed(sessions) if item.get("branch_of") is None), sessions[-1]["id"])
    project_id = str(project["id"])
    settings = {
        "workflow": "fanfiction",
        "target_words": TARGET_TOTAL_WORDS,
        "target_language": "zh",
        "style_intensity": 4,
        "privacy_mode": "standard",
        "compliance_level": "publication",
        "metadata": {
            "chapter_count": TOTAL_CHAPTERS,
            "source_novel": "斗罗大陆",
            "genre": "系统流、成长升级、群像后宫爽文",
            "relationship_policy": "成年人、知情、自愿、非露骨",
        },
    }
    response = client.patch(f"/projects/{project_id}/settings", json=settings)
    response.raise_for_status()
    sessions = database.list_sessions(project_id)
    planning = next((item for item in sessions if item["title"] == "三百章路线规划"), None)
    if planning:
        planning_session_id = str(planning["id"])
    else:
        response = client.post(
            "/sessions",
            json={"project_id": project_id, "title": "三百章路线规划", "mode": "silent"},
        )
        response.raise_for_status()
        planning_session_id = str(response.json()["session"]["id"])
    return project_id, str(main_session_id), planning_session_id


def ensure_rules(client: TestClient, project_id: str) -> None:
    existing = {item["key"] for item in database.list_universe_rules(project_id)}
    for category, key, value in RULES:
        if key in existing:
            continue
        response = client.post(
            "/universe/rule",
            json={
                "project_id": project_id,
                "category": category,
                "key": key,
                "value": value,
                "source": f"material:{SOURCE_NOVEL_ID}",
                "immutable": True,
            },
        )
        response.raise_for_status()


def ensure_pinned_materials(client: TestClient, project_id: str) -> list[str]:
    wanted = {
        "角色系统",
        "世界观与规则",
        "力量与成长体系",
        "势力与关系网络",
        "主线情节",
        "支线与伏笔",
        "爽点与节奏",
        "章节剧情档案",
        "世界观",
        "魔法体系",
        "情节结构",
    }
    with database.closing(database.connect()) as connection:
        nodes = connection.execute(
            "SELECT id,display_name FROM material_nodes WHERE novel_id=? AND parent_id IS NULL ORDER BY sort_order",
            (SOURCE_NOVEL_ID,),
        ).fetchall()
    chosen = [str(node["id"]) for node in nodes if str(node["display_name"]) in wanted]
    existing = {item["material_id"] for item in database.list_pinned_materials(project_id)}
    for priority, material_id in enumerate(chosen):
        if material_id in existing:
            continue
        response = client.post(
            "/pin/material",
            json={"project_id": project_id, "material_id": material_id, "priority": priority},
        )
        response.raise_for_status()
    return chosen


def material_brief() -> str:
    with database.closing(database.connect()) as connection:
        novel = connection.execute("SELECT title,file_path FROM novels WHERE id=?", (SOURCE_NOVEL_ID,)).fetchone()
        if not novel:
            raise RuntimeError("正式素材库中没有《斗罗大陆》")
        roots = connection.execute(
            "SELECT display_name,summary FROM material_nodes WHERE novel_id=? AND parent_id IS NULL ORDER BY sort_order",
            (SOURCE_NOVEL_ID,),
        ).fetchall()
        characters = connection.execute(
            """
            SELECT child.display_name,child.summary
            FROM material_nodes child
            JOIN material_nodes parent ON parent.id=child.parent_id
            WHERE child.novel_id=? AND parent.display_name='角色系统'
            ORDER BY child.sort_order
            """,
            (SOURCE_NOVEL_ID,),
        ).fetchall()
    lines = [f"素材作品：{novel['title']}", "【全书分类摘要】"]
    lines.extend(f"- {row['display_name']}：{row['summary']}" for row in roots)
    lines.append("【主要人物摘要】")
    selected = [row for row in characters if any(name in str(row["display_name"]) for name in ["唐三", "唐昊", "玉小刚", *ROSTER])]
    lines.extend(f"- {row['display_name']}：{row['summary']}" for row in selected)
    return "\n".join(lines)[:28_000]


def existing_story_index(project_id: str) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    volumes: dict[int, dict[str, Any]] = {}
    chapters: dict[int, dict[str, Any]] = {}
    for node in database.list_story_nodes(project_id):
        metadata = node.get("metadata") or {}
        volume_number = metadata.get("volume_number")
        chapter_number = metadata.get("chapter_number")
        if node["layer"] == "volume_outline" and isinstance(volume_number, int):
            volumes[volume_number] = node
        if node["layer"] == "chapter_beat" and isinstance(chapter_number, int):
            chapters[chapter_number] = node
    return volumes, chapters


def ensure_structure_roots(project_id: str, main_session_id: str) -> dict[int, dict[str, Any]]:
    nodes = database.list_story_nodes(project_id)
    if not any(node["layer"] == "premise" and node["title"] == "斗罗大陆后宫·总设定" for node in nodes):
        database.create_story_node(
            project_id,
            "premise",
            "斗罗大陆后宫·总设定",
            (
                "林萧与唐三同龄，从圣魂村共同觉醒开始。林萧拥有分层解封的混沌青莲与弑神枪双生武魂，"
                "后宫系统只奖励守护、成长与平等羁绊，不能操纵感情。全书以唐三逐渐黑化为终局敌人的竞争线为主轴，"
                "在三百章内完成魂师、封号斗罗、神位与神王四级成长；前125章只写少年成长，126章成年后才展开知情自愿的多伴侣关系。"
            ),
            session_id=main_session_id,
            node_type="series_bible",
            metadata={"source_novel_id": SOURCE_NOVEL_ID, "total_chapters": TOTAL_CHAPTERS},
            locked=True,
        )
    volume_nodes, _ = existing_story_index(project_id)
    for volume_number, volume in enumerate(VOLUMES, start=1):
        if volume_number in volume_nodes:
            continue
        volume_nodes[volume_number] = database.create_story_node(
            project_id,
            "volume_outline",
            volume["title"],
            f"章节：{volume['range']}\n卷目标：{volume['arc']}\n人物重点：{volume['focus']}\n卷末高潮：{volume['climax']}",
            session_id=main_session_id,
            node_type="volume",
            metadata={"volume_number": volume_number, "chapter_range": volume["range"]},
            locked=True,
        )
    return volume_nodes


def normalize_beats(result: dict[str, Any], expected_numbers: list[int]) -> list[dict[str, Any]]:
    raw = result.get("chapters")
    if not isinstance(raw, list):
        raise ValueError("章纲缺少 chapters")
    by_number: dict[int, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            number = int(item.get("chapter_number"))
        except (TypeError, ValueError):
            continue
        if number not in expected_numbers:
            continue
        title = re.sub(r"^第\s*\d+\s*章[：:·\s-]*", "", str(item.get("title") or "")).strip()
        beat = str(item.get("beat") or "").strip()
        if not title or not beat:
            continue
        cast = item.get("cast") if isinstance(item.get("cast"), list) else []
        by_number[number] = {
            "chapter_number": number,
            "title": title,
            "beat": beat,
            "cast": [str(name).strip() for name in cast if str(name).strip()][:10],
            "growth": str(item.get("growth") or "").strip(),
            "relationship": str(item.get("relationship") or "").strip(),
            "hook": str(item.get("hook") or "").strip(),
        }
    missing = [number for number in expected_numbers if number not in by_number]
    if missing:
        raise ValueError(f"章纲缺少编号：{missing}")
    return [by_number[number] for number in expected_numbers]


def controller_beats(volume_number: int, expected_numbers: list[int]) -> list[dict[str, Any]]:
    volume = VOLUMES[volume_number - 1]
    stages = [
        ("旧局裂缝", "用一个具体异常打破原有平衡，明确本卷目标与失败代价。"),
        ("系统校验", "后宫系统只给出成长任务，林萧主动验证奖励边界并拒绝感情操控。"),
        ("规则门槛", "通过训练或调查确认本阶段魂师规则，建立不能轻易跨越的限制。"),
        ("第一次选择", "林萧在利益与守护之间作出选择，并承担可见损失。"),
        ("双星试探", "唐三以当前优势试探林萧，双方第一次交换有效情报与胜负判断。"),
        ("资源争夺", "围绕魂环、名额、导师、情报或阵营资源展开可验证竞争。"),
        ("人物入局", "让本卷重点人物因自身目标进入主线，而不是被动等待主角。"),
        ("合作条款", "林萧提出公平合作原则，队友可以拒绝并保留独立行动空间。"),
        ("修炼代价", "双生武魂解封遇到反噬、资源或精神负担，迫使林萧调整方案。"),
        ("小胜立旗", "以一场完整实战验证新方案，同时留下不能靠蛮力解决的问题。"),
        ("暗线落子", "唐三、玉小刚、唐昊或本卷敌对阵营布置反击，动机必须具体。"),
        ("证据与误判", "主角团发现一条关键证据，但因信息不全产生有代价的误判。"),
        ("中段危机", "前半卷积累的问题同时爆发，迫使主要人物公开立场。"),
        ("武魂新解", "林萧通过推演与实战获得阶段能力，不得无代价跳级。"),
        ("关系检验", "重点人物用行动检验林萧是否尊重承诺、边界与各自目标。"),
        ("阵营邀请", "新的势力给出有吸引力但附带条件的邀请，形成真正两难。"),
        ("拒绝代价", "林萧或重点人物拒绝不合理条件，失去资源并赢得长期信任。"),
        ("伏笔推进", "回收前段证据的一部分，同时打开指向唐三终局黑化的新问题。"),
        ("对手越界", "唐三阵营为胜利越过一道道德或规则边界，敌对关系升级。"),
        ("团队反攻", "每位在场人物按能力承担任务，主角不能独占全部解决过程。"),
        ("突破前夜", "高潮前完成计划、告别和风险确认，明确失败会失去什么。"),
        ("高潮开战", "卷级决战开场，先让敌方优势成立，再寻找规则缝隙。"),
        ("代价逆转", "林萧以此前选择积累的盟友、证据或能力完成逆转并支付代价。"),
        ("结果清算", "处理战果、伤势、名誉与阵营变化，让胜负真正改变世界状态。"),
        ("下一扇门", "完成本卷人物小弧光，以新的地点、敌人或神位线索接入下一卷。"),
    ]
    beats: list[dict[str, Any]] = []
    for offset, chapter_number in enumerate(expected_numbers):
        stage_name, stage_action = stages[min(len(stages) - 1, offset)]
        focus_names = [name for name in ROSTER if name in volume["focus"]]
        cast = ["林萧", "唐三", *focus_names[:4]]
        beats.append(
            {
                "chapter_number": chapter_number,
                "title": f"{volume['title'].split(' ', 1)[-1]}·{stage_name}",
                "beat": f"本章承担“{stage_name}”功能：{stage_action}必须服务于本卷目标“{volume['arc']}”。",
                "cast": list(dict.fromkeys(cast)),
                "growth": "双生武魂、战术、团队或政治能力取得一项可验证的小幅进展，并保留阶段限制。",
                "relationship": "让重点人物基于自身目标作出选择；少年期只推进信任，成年期才可在明确同意后推进恋爱。",
                "hook": volume["climax"] if offset >= 20 else "把本章结果转化为下一章必须处理的新阻力或新证据。",
            }
        )
    return beats


async def generate_volume_beats(
    config: dict[str, str],
    project_id: str,
    planning_session_id: str,
    volume_number: int,
    source_brief: str,
    expected_numbers: list[int],
) -> tuple[list[dict[str, Any]], str]:
    volume = VOLUMES[volume_number - 1]
    previous = VOLUMES[volume_number - 2]["climax"] if volume_number > 1 else "林萧与唐三即将在圣魂村同届觉醒。"
    next_hook = VOLUMES[volume_number]["arc"] if volume_number < TOTAL_VOLUMES else "终局后进入神王家庭与人间日常。"
    volume_start = (volume_number - 1) * CHAPTERS_PER_VOLUME + 1
    chunk_index = (expected_numbers[0] - volume_start) // len(expected_numbers) + 1
    stage_names = ["开局与目标建立", "阻力升级与能力试错", "中段转折与关系检验", "反派反击与突破代价", "卷末高潮与下一卷钩子"]
    stage = stage_names[min(len(stage_names) - 1, (expected_numbers[0] - volume_start) // 5)]
    prompt = (
        f"作品：《{PROJECT_TITLE}》\n"
        f"本卷：{json.dumps(volume, ensure_ascii=False)}\n"
        f"上一卷接口：{previous}\n下一卷接口：{next_hook}\n"
        f"本卷当前分段：第{chunk_index}段，功能是“{stage}”。\n"
        f"指定章节编号：{expected_numbers[0]}-{expected_numbers[-1]}，必须恰好返回{len(expected_numbers)}章。\n"
        f"后宫主要人物池：{'、'.join(ROSTER)}。不是每卷都强行登场，只安排符合阶段与阵营的人物。\n"
        f"项目约束：\n{memory_engine.prompt_context(project_id)}\n\n"
        f"素材摘要：\n{source_brief}"
    )
    database.save_user_message(
        planning_session_id,
        f"为{volume['title']}生成第{expected_numbers[0]}-{expected_numbers[-1]}章连续章纲。",
        [SOURCE_NOVEL_ID],
    )
    try:
        result = await complete_json(config, OUTLINE_SYSTEM_PROMPT, prompt)
        beats = normalize_beats(result, expected_numbers)
        source = "model"
    except (AgentError, ValueError) as first_error:
        retry_prompt = prompt + f"\n\n上次失败原因：{first_error}。这次进一步压缩字段，但必须补齐全部25个编号。"
        try:
            result = await complete_json(config, OUTLINE_SYSTEM_PROMPT, retry_prompt)
            beats = normalize_beats(result, expected_numbers)
            source = "model_retry"
        except (AgentError, ValueError):
            beats = controller_beats(volume_number, expected_numbers)
            source = "controller"
    database.save_assistant_message(
        planning_session_id,
        json.dumps({"volume": volume_number, "source": source, "chapters": beats}, ensure_ascii=False),
    )
    return beats, source


async def ensure_chapter_beats(
    config: dict[str, str],
    project_id: str,
    main_session_id: str,
    planning_session_id: str,
    volume_nodes: dict[int, dict[str, Any]],
    output: Path,
    use_model: bool,
) -> dict[str, int]:
    source_brief = material_brief()
    _, existing = existing_story_index(project_id)
    source_counts = {"model": 0, "model_retry": 0, "controller": 0, "existing": len(existing)}
    if not use_model:
        if not existing:
            with database.closing(database.connect()) as connection:
                with connection:
                    connection.execute("DELETE FROM messages WHERE session_id=?", (planning_session_id,))
        for volume_number in range(1, TOTAL_VOLUMES + 1):
            start = (volume_number - 1) * CHAPTERS_PER_VOLUME + 1
            numbers = list(range(start, start + CHAPTERS_PER_VOLUME))
            missing = [number for number in numbers if number not in existing]
            for beat in controller_beats(volume_number, missing):
                number = int(beat["chapter_number"])
                content = (
                    f"事件链：{beat['beat']}\n"
                    f"登场人物：{'、'.join(beat['cast'])}\n"
                    f"成长推进：{beat['growth']}\n"
                    f"关系推进：{beat['relationship']}\n"
                    f"章末钩子：{beat['hook']}"
                )
                existing[number] = database.create_story_node(
                    project_id,
                    "chapter_beat",
                    f"第{number}章 {beat['title']}",
                    content,
                    session_id=main_session_id,
                    parent_id=str(volume_nodes[volume_number]["id"]),
                    node_type="chapter",
                    metadata={
                        "chapter_number": number,
                        "volume_number": volume_number,
                        "outline_source": "controller",
                        "cast": beat["cast"],
                        "hook": beat["hook"],
                    },
                    locked=True,
                )
            source_counts["controller"] += len(missing)
            write_outline(output, project_id)
            print(f"outline volume {volume_number}/{TOTAL_VOLUMES}: controller", flush=True)
        return source_counts
    semaphore = __import__("asyncio").Semaphore(4)

    async def produce(volume_number: int, numbers: list[int]) -> tuple[int, list[int], list[dict[str, Any]], str]:
        async with semaphore:
            beats, source = await generate_volume_beats(
                config,
                project_id,
                planning_session_id,
                volume_number,
                source_brief,
                numbers,
            )
            print(
                f"outline chunk {numbers[0]}-{numbers[-1]}/{TOTAL_CHAPTERS}: {source}",
                flush=True,
            )
            return volume_number, numbers, beats, source

    jobs = []
    for volume_number in range(1, TOTAL_VOLUMES + 1):
        start = (volume_number - 1) * CHAPTERS_PER_VOLUME + 1
        for chunk_start in range(start, start + CHAPTERS_PER_VOLUME, 5):
            numbers = [
                number
                for number in range(chunk_start, min(chunk_start + 5, start + CHAPTERS_PER_VOLUME))
                if number not in existing
            ]
            if numbers:
                jobs.append(produce(volume_number, numbers))

    outcomes = await __import__("asyncio").gather(*jobs)
    completed_volumes: set[int] = set()
    for volume_number, missing, beats, source in sorted(outcomes, key=lambda item: item[1][0]):
        source_counts[source] += len(missing)
        for beat in beats:
            number = int(beat["chapter_number"])
            if number in existing:
                continue
            content = (
                f"事件链：{beat['beat']}\n"
                f"登场人物：{'、'.join(beat['cast'])}\n"
                f"成长推进：{beat['growth']}\n"
                f"关系推进：{beat['relationship']}\n"
                f"章末钩子：{beat['hook']}"
            )
            existing[number] = database.create_story_node(
                project_id,
                "chapter_beat",
                f"第{number}章 {beat['title']}",
                content,
                session_id=main_session_id,
                parent_id=str(volume_nodes[volume_number]["id"]),
                node_type="chapter",
                metadata={
                    "chapter_number": number,
                    "volume_number": volume_number,
                    "outline_source": source,
                    "cast": beat["cast"],
                    "hook": beat["hook"],
                },
                locked=True,
            )
        if volume_number not in completed_volumes and all(
            number in existing
            for number in range(
                (volume_number - 1) * CHAPTERS_PER_VOLUME + 1,
                volume_number * CHAPTERS_PER_VOLUME + 1,
            )
        ):
            completed_volumes.add(volume_number)
            write_outline(output, project_id)
            print(f"outline volume {volume_number}/{TOTAL_VOLUMES}: complete", flush=True)
    return source_counts


def write_outline(output: Path, project_id: str) -> Path:
    volume_nodes, chapter_nodes = existing_story_index(project_id)
    lines = [f"# 《{PROJECT_TITLE}》300章连载蓝图", ""]
    for volume_number in range(1, TOTAL_VOLUMES + 1):
        volume = volume_nodes.get(volume_number)
        if not volume:
            continue
        lines.extend([f"## {volume['title']}", "", str(volume["content"]), ""])
        start = (volume_number - 1) * CHAPTERS_PER_VOLUME + 1
        for number in range(start, start + CHAPTERS_PER_VOLUME):
            node = chapter_nodes.get(number)
            if node:
                lines.extend([f"### {node['title']}", "", str(node["content"]), ""])
    path = output / f"{PROJECT_TITLE}-300章完整蓝图.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def chapter_instruction(project_id: str, chapter_number: int, target_words: int) -> str:
    volume_number = (chapter_number - 1) // CHAPTERS_PER_VOLUME + 1
    volume = VOLUMES[volume_number - 1]
    _, chapter_nodes = existing_story_index(project_id)
    current = chapter_nodes[chapter_number]
    next_node = chapter_nodes.get(chapter_number + 1)
    previous_node = chapter_nodes.get(chapter_number - 1)
    age_instruction = (
        "本章仍处于少年成长阶段，禁止恋爱确认和任何性或亲密描写，只能写友情、竞争、守护与朦胧羁绊。"
        if chapter_number <= 125
        else "本章角色均按铁律明确为成年人；若推进感情，必须知情、自愿、尊重边界且非露骨。"
    )
    return (
        f"请生成《{PROJECT_TITLE}》第{chapter_number}章正式正文，目标约{target_words}字。\n"
        f"当前卷：{volume['title']}\n卷目标：{volume['arc']}\n"
        f"上一章结构接口：{previous_node['content'] if previous_node else '开篇，从圣魂村武魂觉醒前夕开始。'}\n"
        f"本章锁定细纲：{current['title']}\n{current['content']}\n"
        f"下一章接口：{next_node['content'] if next_node else volume['climax']}\n"
        f"写作要求：{age_instruction} 唐三的敌对必须循序渐进；林萧不能无代价碾压；"
        "每章至少包含一个完整场景、一项具体选择、一次可验证推进和自然章末钩子。"
        "不得复述原著段落，不得把章纲、素材标签、系统提示或创作说明写进正文。"
    )


def generate_one_chapter(
    client: TestClient,
    config: dict[str, str],
    project_id: str,
    session_id: str,
    chapter_number: int,
    target_words: int,
) -> dict[str, Any]:
    action = "create" if chapter_number == 1 else "continue"
    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "project_id": project_id,
            "message": chapter_instruction(project_id, chapter_number, target_words),
            "selected_material_ids": [],
            "api_config": config,
            "mode": "silent",
            "creation_action": action,
            "chapter_target_words": target_words,
        },
    )
    response.raise_for_status()
    events = parse_sse(response.text)
    if events.get("error"):
        raise RuntimeError(str(events["error"][-1].get("message")))
    paper_events = events.get("paper") or []
    if not paper_events:
        raise RuntimeError(f"第{chapter_number}章没有生成稿纸")
    paper_event = paper_events[-1]
    confirm = client.post("/chapter/update", json={"action": "confirm", "message_id": paper_event["message_id"]})
    confirm.raise_for_status()
    paper = paper_event["paper"]
    return {
        "chapter_number": chapter_number,
        "title": paper["title"],
        "word_count": paper["word_count"],
        "target_words": paper["target_words"],
        "length_status": paper["length_status"],
        "chapter_id": confirm.json()["chapter"]["id"],
    }


def export_novel(output: Path, project_id: str) -> Path:
    chapters = database.list_chapters(project_id)
    sections = []
    for index, chapter in enumerate(chapters, start=1):
        sections.append(f"第{index}章 {chapter['title']}\n\n{chapter['content']}")
    path = output / f"{PROJECT_TITLE}-当前连载正文.txt"
    path.write_text("\n\n".join(sections), encoding="utf-8")
    return path


def write_state(
    output: Path,
    project_id: str,
    main_session_id: str,
    planning_session_id: str,
    source_counts: dict[str, int],
    generated: list[dict[str, Any]],
) -> Path:
    chapters = database.list_chapters(project_id)
    _, beats = existing_story_index(project_id)
    state = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "project_title": PROJECT_TITLE,
        "project_id": project_id,
        "main_session_id": main_session_id,
        "planning_session_id": planning_session_id,
        "source_novel_id": SOURCE_NOVEL_ID,
        "planned_chapters": len(beats),
        "confirmed_chapters": len(chapters),
        "next_chapter": len(chapters) + 1,
        "outline_sources": source_counts,
        "this_run": generated,
        "safety": "所有亲密关系仅限成年人、知情自愿、非露骨；前125章只写成长与羁绊。",
    }
    path = output / "连载生产状态.json"
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_report(
    output: Path,
    project_id: str,
    source_counts: dict[str, int],
    generated: list[dict[str, Any]],
    outline_path: Path,
    novel_path: Path,
) -> Path:
    chapters = database.list_chapters(project_id)
    lines = [
        f"# NovelForge《{PROJECT_TITLE}》连载生产报告",
        "",
        f"> 更新时间：{datetime.now().isoformat(timespec='seconds')}  ",
        f"> 正式作品 ID：`{project_id}`  ",
        f"> 规划：{TOTAL_VOLUMES} 卷 / {TOTAL_CHAPTERS} 章 / 目标约 {TARGET_TOTAL_WORDS:,} 字  ",
        "",
        "## 已完成",
        "",
        f"- 已在正式数据库创建或恢复作品、主会话和独立规划会话。",
        f"- 已写入 {len(RULES)} 条宇宙铁律并绑定《斗罗大陆》常驻分类素材。",
        f"- 已建立 {TOTAL_VOLUMES} 个卷节点和 {sum(source_counts.values())} 个章节细纲节点。",
        f"- 当前已确认收录 {len(chapters)} 章正文；本轮新增 {len(generated)} 章。",
        f"- 蓝图来源统计：{json.dumps(source_counts, ensure_ascii=False)}。",
        "",
        "## 已收录正文",
        "",
    ]
    if chapters:
        lines.extend(
            f"- 第{index}章《{chapter['title']}》：{len(re.sub(r'\\s+', '', str(chapter['content'])))} 字。"
            for index, chapter in enumerate(chapters, start=1)
        )
    else:
        lines.append("- 尚未确认收录正文。")
    lines.extend(["", "## 本次新增", ""])
    if generated:
        lines.extend(
            f"- 第{item['chapter_number']}章《{item['title']}》：{item['word_count']} 字，长度状态 `{item['length_status']}`。"
            for item in generated
        )
    else:
        lines.append("- 本次运行没有新增正文。")
    lines.extend(
        [
            "",
            "## 连载约束",
            "",
            "- 第1-125章只写少年成长、竞争、守护和羁绊。",
            "- 第126章明确时间跳跃，之后仅写成年人之间知情、自愿、可撤回且非露骨的关系。",
            "- 后宫系统不能操纵感情；所有女性角色保留目标、能力、阵营与独立人物弧。",
            "- 玉小刚、唐昊采用揭露、战败、审判与失权清算；唐三在终局神战中被击杀。",
            "",
            "## 文件",
            "",
            f"- 300章蓝图：`{outline_path}`",
            f"- 当前正文：`{novel_path}`",
            f"- 断点状态：`{output / '连载生产状态.json'}`",
            "",
            "## 继续生产",
            "",
            "后续再次运行本脚本并指定 `--count` 即可从已确认章节之后继续，已有卷纲、章纲和正文不会重复创建。",
            "",
        ]
    )
    path = output / f"NovelForge《{PROJECT_TITLE}》连载生产报告.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


async def run(args: argparse.Namespace) -> dict[str, Any]:
    config = api_config()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    database.initialize_database()
    with TestClient(app) as client:
        project_id, main_session_id, planning_session_id = ensure_project(client)
        ensure_rules(client, project_id)
        ensure_pinned_materials(client, project_id)
        volume_nodes = ensure_structure_roots(project_id, main_session_id)
        source_counts = await ensure_chapter_beats(
            config,
            project_id,
            main_session_id,
            planning_session_id,
            volume_nodes,
            output,
            args.model_outline,
        )
        generated: list[dict[str, Any]] = []
        if args.count:
            existing_count = len(database.list_chapters(project_id))
            start = args.start or existing_count + 1
            if start != existing_count + 1:
                raise RuntimeError(f"为保持连续性，本次必须从第{existing_count + 1}章开始，而不是第{start}章")
            end = min(TOTAL_CHAPTERS, start + args.count - 1)
            for chapter_number in range(start, end + 1):
                item = generate_one_chapter(
                    client,
                    config,
                    project_id,
                    main_session_id,
                    chapter_number,
                    args.target_words,
                )
                generated.append(item)
                export_novel(output, project_id)
                write_state(output, project_id, main_session_id, planning_session_id, source_counts, generated)
                print(
                    f"chapter {chapter_number}/{TOTAL_CHAPTERS}: {item['title']} ({item['word_count']} chars)",
                    flush=True,
                )
        outline_path = write_outline(output, project_id)
        novel_path = export_novel(output, project_id)
        state_path = write_state(output, project_id, main_session_id, planning_session_id, source_counts, generated)
        report_path = write_report(output, project_id, source_counts, generated, outline_path, novel_path)
    return {
        "project_id": project_id,
        "planned_chapters": len(existing_story_index(project_id)[1]),
        "confirmed_chapters": len(database.list_chapters(project_id)),
        "generated": generated,
        "outline": str(outline_path),
        "novel": str(novel_path),
        "state": str(state_path),
        "report": str(report_path),
    }


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NovelForge《斗罗大陆后宫》300章断点续跑生产器")
    parser.add_argument("--start", type=int, default=None, help="正文起始章；默认从已收录下一章开始")
    parser.add_argument("--count", type=int, default=3, help="本轮真实生成正文数量；0表示只准备蓝图")
    parser.add_argument("--target-words", type=int, default=3000, choices=(1500, 3000, 5000, 8000))
    parser.add_argument("--model-outline", action="store_true", help="用模型生成章纲；默认使用稳定的卷级控制器")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    return parser.parse_args()


def main() -> None:
    import asyncio

    result = asyncio.run(run(arguments()))
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
