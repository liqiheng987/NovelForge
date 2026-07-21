import asyncio
from contextvars import ContextVar
import json
import re
from time import perf_counter
from typing import AsyncIterator, Any, Callable

import httpx

from prompts import (
    CHAT_SYSTEM_PROMPT,
    PAPER_INTENT_PROMPT,
    PAPER_SYSTEM_PROMPT,
    PAPER_TRIGGER_WORDS,
    TYPE_DIMENSIONS,
    TYPE_SYSTEM_PROMPT,
    CROSS_GENRE_PROMPTS,
    INSPIRATION_PROMPT,
    MODE_PROMPTS,
    STYLE_TRIAL_PROMPT,
    UNIVERSE_CHECK_PROMPT,
    chapter_batch_prompt,
    dimension_prompt,
    region_analysis_prompt,
)
from memory import memory_engine
from database import normalize_chapter_memory
from tools import (
    analysis_excerpt,
    chapter_signal_excerpt,
    dimension_excerpt,
    key_content_coverage_items,
    pack_chapter_inputs,
    render_chapter_batch,
    semantic_analysis_regions,
    split_novel_chapters,
)


class AgentError(Exception):
    pass


ANALYSIS_USAGE: ContextVar[dict[str, int] | None] = ContextVar("analysis_usage", default=None)


def compact_text(value: Any) -> str:
    if isinstance(value, dict):
        parts = [f"{key}：{compact_text(item)}" for key, item in value.items()]
        return "；".join(part for part in parts if not part.endswith("："))
    if isinstance(value, list):
        return "；".join(filter(None, (compact_text(item) for item in value)))
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_paper_title(value: Any) -> str:
    title = compact_text(value)
    title = re.sub(r"^第\s*[0-9一二三四五六七八九十百千]+\s*章[\s·:：—-]*", "", title)
    title = re.sub(
        r"[\s·:：—-]*[（(]?第\s*[0-9一二三四五六七八九十百千]+\s*章[)）]?$",
        "",
        title,
    )
    return title.strip() or "未命名篇章"


def infer_user_preferences(message: str) -> dict[str, Any]:
    preferences: dict[str, Any] = {}
    length_match = re.search(r"(\d+(?:\.\d+)?)\s*(万)?\s*字", message)
    if length_match and re.search(r"全书|整部|整本|总字数|总体|全篇小说|目标篇幅|短篇|长篇|连载|新书", message):
        target_words = int(float(length_match.group(1)) * (10_000 if length_match.group(2) else 1))
        preferences["target_words"] = target_words
        preferences["workflow"] = "short" if target_words <= 10_000 else "serial" if target_words >= 500_000 else "standard"
    if re.search(r"\d+\s*个短篇|短篇集|合集", message):
        preferences["workflow"] = "collection"
    if "同人" in message or "原著设定" in message:
        preferences["workflow"] = "fanfiction"
    if "剧本" in message and ("小说" in message or "改编" in message):
        preferences["workflow"] = "adaptation"
    if re.search(r"英文|英语|English", message, re.I):
        preferences["target_language"] = "en"
    if "纯本地" in message or "只存本地" in message:
        preferences["privacy_mode"] = "local"
    if "出版审查" in message or "出版标准" in message or "敏感词" in message:
        preferences["compliance_level"] = "publication"
    if re.search(r"别给我建议|不要建议|听我的|静默", message):
        preferences["mode"] = "silent"
    elif re.search(r"标注.*来源|依据.*段落|可溯源", message):
        preferences["mode"] = "traceable"
    elif re.search(r"教学|讲解.*依据|为什么.*分类", message):
        preferences["mode"] = "teaching"
    return preferences


def requested_chapter_words(message: str, explicit_target: int | None = None) -> int:
    if explicit_target:
        return max(500, min(12000, int(explicit_target)))
    matches = list(re.finditer(r"(\d+(?:\.\d+)?)\s*(万)?\s*(?:个?中文字符|字)", message))
    if not matches:
        return 3000
    match = matches[-1]
    target = int(float(match.group(1)) * (10_000 if match.group(2) else 1))
    return max(500, min(12000, target))


def requests_whole_novel(message: str) -> bool:
    length_match = re.search(r"(\d+(?:\.\d+)?)\s*(万)?\s*字", message)
    target = int(float(length_match.group(1)) * (10_000 if length_match.group(2) else 1)) if length_match else 0
    return target > 12000 and bool(re.search(r"全书|整部|整本|一部|长篇|总字数|完整小说", message))


def requested_auto_collect_count(message: str) -> int:
    normalized = re.sub(r"\s+", "", str(message or ""))
    if not normalized or not re.search(r"生成|续写|写出|创作", normalized):
        return 0
    if re.search(r"(?:不要|别|禁止)(?:直接|自动)?(?:收录|收入篇章|确认)", normalized):
        return 0
    auto_collect = re.search(
        r"直接(?:收录|收入篇章)|自动收录|收入篇章|生成后(?:直接)?收录|"
        r"无需(?:逐章)?确认|不用(?:逐章)?确认|不必(?:逐章)?确认|免确认|不用经过同意",
        normalized,
    )
    if not auto_collect:
        return 0
    chinese_numbers = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    count_match = re.search(
        r"(?:连续生成|直接生成|生成|续写|后面|接下来|一次)(?:后面|接下来)?"
        r"([1-9]\d*|[一二两三四五六七八九十])(?:个)?(?:章|篇|章节|篇章|文章)",
        normalized,
    )
    if count_match:
        raw = count_match.group(1)
        count = chinese_numbers.get(raw, int(raw) if raw.isdigit() else 1)
        return max(1, min(10, count))
    if re.search(r"(?:后面|接下来|连续)?几(?:个)?(?:章|篇|章节|篇章|文章)", normalized):
        return 3
    return 1


def workflow_prompt(settings: dict[str, Any]) -> str:
    workflow = settings.get("workflow", "standard")
    instructions = {
        "standard": "按设定卡→卷大纲→章节细纲→正文逐层推进。",
        "short": "采用短篇压缩流程：设定卡→单章细纲→正文，跳过卷大纲。",
        "serial": "采用连载流程：先规划卷级节奏、伏笔清单和人物弧光，只展开近期内容。",
        "collection": "采用合集流程：共享主题，每个短篇使用相互隔离的卷级节点。",
        "fanfiction": "采用同人流程：明确区分原著事实、合理推断与原创设定，并保留免责声明。",
        "adaptation": "采用改编流程：维护场景→章节映射，保留画面行动并补足内心描写。",
    }
    result = instructions.get(workflow, instructions["standard"])
    if settings.get("target_words"):
        result += f" 目标总字数约 {settings['target_words']} 字。"
    if settings.get("target_language") and settings["target_language"] != "zh":
        result += f" 最终创作语言锁定为 {settings['target_language']}，文化概念需要等效转译。"
    if settings.get("compliance_level") in {"publication", "custom"}:
        result += " 生成稿件后执行出版合规检查，保留含蓄、隐喻、作者自审三种处理选项。"
    return result


def _normalize_dimension_items_clean(value: Any, dimension: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw_item in enumerate(value):
        if not isinstance(raw_item, dict):
            continue
        name = compact_text(raw_item.get("name")) or f"{dimension}{index + 1}"
        category = compact_text(raw_item.get("category")) or dimension
        identity = (name.casefold(), category.casefold())
        if identity in seen:
            continue
        seen.add(identity)
        details = raw_item.get("details")
        if not isinstance(details, dict):
            details = {str(key): item for key, item in raw_item.items() if key not in {"name", "category", "summary", "tags"}}
        summary = compact_text(raw_item.get("summary"))
        if len(summary) < 50:
            summary = f"{summary}；{name}属于{category}，原文可确认的具体信息已保留在结构化字段中；未出现的背景、关系和规则不会被补造。".strip("；")
        summary = summary[:200].rstrip()
        tags = [compact_text(tag) for tag in raw_item.get("tags", []) if compact_text(tag)] if isinstance(raw_item.get("tags"), list) else []
        normalized.append({"name": name, "category": category, "summary": summary, "details": details, "tags": list(dict.fromkeys(tags))[:3]})
    return normalized


def normalize_dimension_items(value: Any, dimension: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw_item in enumerate(value):
        if not isinstance(raw_item, dict):
            continue
        name = compact_text(raw_item.get("name")) or f"{dimension} {index + 1}"
        category = compact_text(raw_item.get("category")) or dimension
        identity = (name.casefold(), category.casefold())
        if identity in seen:
            continue
        seen.add(identity)
        details = raw_item.get("details")
        if not isinstance(details, dict):
            details = {
                str(key): item
                for key, item in raw_item.items()
                if key not in {"name", "category", "summary", "tags"}
            }
        summary = compact_text(raw_item.get("summary"))
        detail_text = compact_text(details)
        if len(summary) < 50 and detail_text:
            supplement = f"{name}属于{category}。{detail_text}"
            if supplement not in summary:
                summary = f"{summary} {supplement}".strip()
        if len(summary) < 50:
            summary = (
                f"{summary} 该条目仅整理原文能够确认的信息；未出现的背景、关系与规则均未补造，"
                "使用时应以完整结构化内容为准。"
            ).strip()
        if len(summary) > 200:
            summary = f"{summary[:197].rstrip()}..."
        tags = raw_item.get("tags")
        normalized_tags = []
        if isinstance(tags, list):
            for tag in tags:
                cleaned = compact_text(tag)
                if cleaned and cleaned not in normalized_tags:
                    normalized_tags.append(cleaned)
                if len(normalized_tags) == 3:
                    break
        normalized.append(
            {
                "name": name,
                "category": category,
                "summary": summary or f"原文将{name}归入{category}，但没有提供足够信息形成更详细的摘要。",
                "details": details,
                "tags": normalized_tags,
            }
        )
    return normalized


def chat_completions_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    return normalized if normalized.endswith("/chat/completions") else f"{normalized}/chat/completions"


def api_headers(api_config: dict[str, str]) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_config.get("api_key", "").strip():
        headers["Authorization"] = f"Bearer {api_config['api_key'].strip()}"
    return headers


def upstream_error(response: httpx.Response) -> str:
    message = ""
    try:
        payload = response.json()
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            message = str(error.get("message") or "")
        elif error:
            message = str(error)
        elif isinstance(payload, dict):
            message = str(payload.get("message") or payload.get("detail") or "")
    except (ValueError, TypeError):
        pass
    message = re.sub(r"\s+", " ", message).strip()[:240]
    return f"模型服务返回 HTTP {response.status_code}{f'：{message}' if message else ''}"


async def request_completion(
    api_config: dict[str, str],
    messages: list[dict[str, str]],
    *,
    stream: bool = False,
    timeout_seconds: float = 120.0,
    retries: int = 2,
) -> httpx.Response:
    payload = {"model": api_config["model"], "messages": messages, "stream": stream}
    retryable_statuses = {429, 500, 502, 503, 504}
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        client = httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds, connect=15.0))
        try:
            response = await client.post(
                chat_completions_url(api_config["base_url"]),
                headers=api_headers(api_config),
                json=payload,
            )
        except (httpx.TimeoutException, httpx.RequestError) as error:
            await client.aclose()
            last_error = error
            if attempt < retries:
                await asyncio.sleep(1.5 * (2**attempt))
                continue
            if isinstance(error, httpx.TimeoutException):
                raise AgentError("模型响应超时，已自动重试，请稍后再试") from error
            raise AgentError("无法连接模型服务，请检查 API 地址和网络") from error
        if response.status_code in retryable_statuses and attempt < retries:
            await response.aread()
            await client.aclose()
            await asyncio.sleep(1.5 * (2**attempt))
            continue
        if not response.is_success:
            await response.aread()
            message = upstream_error(response)
            await client.aclose()
            raise AgentError(message)
        response.extensions["novelforge_client"] = client
        return response
    raise AgentError("模型请求失败，请稍后重试") from last_error


async def complete_text(api_config: dict[str, str], messages: list[dict[str, str]]) -> str:
    response = await request_completion(api_config, messages)
    client = response.extensions["novelforge_client"]
    try:
        payload = response.json()
        usage_tracker = ANALYSIS_USAGE.get()
        if usage_tracker is not None:
            usage = payload.get("usage") if isinstance(payload, dict) else None
            usage_tracker["model_calls"] += 1
            usage_tracker["model_input_chars"] += sum(len(message.get("content", "")) for message in messages)
            if isinstance(usage, dict):
                usage_tracker["input_tokens"] += int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
                usage_tracker["output_tokens"] += int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        content = payload["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError
        return content.strip()
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise AgentError("模型返回了无法识别的内容，请更换模型后重试") from error
    finally:
        await client.aclose()


def _strip_outer_code_fence(value: str) -> str:
    cleaned = value.strip().lstrip("\ufeff")
    cleaned = re.sub(r"^```[^\n]*\n", "", cleaned, count=1)
    cleaned = re.sub(r"\n```\s*$", "", cleaned, count=1)
    return cleaned.strip()


def _parse_standard_json_object(value: str) -> dict[str, Any]:
    cleaned = _strip_outer_code_fence(value)
    candidates = [cleaned]
    repaired = re.sub(r",\s*([}\]])", r"\1", cleaned)
    if repaired != cleaned:
        candidates.append(repaired)
    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            result = json.loads(candidate)
        except json.JSONDecodeError:
            for match in re.finditer(r"\{", candidate):
                try:
                    result, _ = decoder.raw_decode(candidate[match.start() :])
                except json.JSONDecodeError:
                    continue
                if isinstance(result, dict):
                    return result
            continue
        if isinstance(result, dict):
            return result
        raise ValueError("JSON root must be an object")
    raise json.JSONDecodeError("No valid JSON object found", cleaned, 0)


def _parse_paper_protocol(value: str) -> dict[str, Any] | None:
    cleaned = _strip_outer_code_fence(value)
    title_marker = "<<<NOVELFORGE_TITLE>>>"
    content_marker = "<<<NOVELFORGE_CONTENT>>>"
    memory_marker = "<<<NOVELFORGE_MEMORY>>>"
    end_marker = "<<<NOVELFORGE_END>>>"
    title_start = cleaned.find(title_marker)
    content_start = cleaned.find(content_marker)
    end_start = cleaned.rfind(end_marker)
    if title_start < 0 or content_start < 0 or end_start < 0 or not title_start < content_start < end_start:
        return None
    memory_start = cleaned.find(memory_marker, content_start + len(content_marker), end_start)
    title = cleaned[title_start + len(title_marker) : content_start].strip()
    content_end = memory_start if memory_start >= 0 else end_start
    content = cleaned[content_start + len(content_marker) : content_end].strip()
    if not title or not content:
        raise ValueError("Paper protocol requires a title and content")
    memory: dict[str, Any] = {}
    if memory_start >= 0:
        memory_text = cleaned[memory_start + len(memory_marker) : end_start].strip()
        if memory_text:
            try:
                memory = _parse_standard_json_object(memory_text)
            except (json.JSONDecodeError, ValueError):
                memory = {}
    return {
        "text": "已根据你的命令生成完整篇章。",
        "paper": {
            "title": title.splitlines()[0].strip(),
            "content": content,
            "target_chapter_id": None,
            "memory": memory,
        },
    }


def parse_json_object(value: str) -> dict[str, Any]:
    paper_result = _parse_paper_protocol(value)
    if paper_result is not None:
        return paper_result
    result = _parse_standard_json_object(value)
    if not isinstance(result, dict):
        raise ValueError("JSON root must be an object")
    return result


async def complete_json(
    api_config: dict[str, str],
    system_prompt: str,
    user_content: str,
) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    raw = await complete_text(api_config, messages)
    paper_protocol = "<<<NOVELFORGE_TITLE>>>" in system_prompt
    for attempt in range(3):
        try:
            return parse_json_object(raw)
        except (json.JSONDecodeError, ValueError):
            if attempt >= 2:
                break
            if attempt == 0 and not paper_protocol and len(raw) <= 8000:
                retry_messages = [
                    {"role": "system", "content": "修复下面内容为严格 JSON，只输出一个完整 JSON 对象，不改变语义。"},
                    {"role": "user", "content": raw},
                ]
            else:
                format_instruction = (
                    "上一次输出格式不完整。请从头重新生成同一篇章，只输出规定的 NOVELFORGE_TITLE、"
                    "NOVELFORGE_CONTENT、NOVELFORGE_MEMORY、NOVELFORGE_END 四个分隔段；正文不要放入 JSON。"
                    if paper_protocol
                    else "上一次输出不是有效 JSON。请重新完成原任务，只输出一个严格、完整、可解析的 JSON 对象。"
                )
                retry_messages = [
                    {"role": "system", "content": f"{system_prompt}\n\n{format_instruction}"},
                    {"role": "user", "content": user_content},
                ]
            raw = await complete_text(api_config, retry_messages)
    if paper_protocol:
        raise AgentError("模型没有按篇章分隔协议返回完整内容，请重试当前章")
    raise AgentError("模型没有按要求返回结构化内容，请重试或更换模型")


async def test_api_connection(api_config: dict[str, str]) -> dict[str, object]:
    started = perf_counter()
    result = await complete_json(
        api_config,
        "你是接口兼容性测试工具。严格返回 JSON：{\"status\":\"ok\",\"value\":\"NovelForge\"}",
        "执行一次结构化输出测试。",
    )
    if result.get("status") != "ok":
        raise AgentError("模型可连接，但结构化 JSON 输出不兼容")
    return {
        "status": "ok",
        "model": api_config["model"],
        "latency_ms": round((perf_counter() - started) * 1000),
        "structured_json": True,
    }


def merge_region_dimensions(
    dimensions: list[str],
    region_results: list[tuple[dict[str, Any], dict[str, Any]]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, dict[str, Any]]] = {name: {} for name in dimensions}
    for region, result in region_results:
        returned = result.get("dimensions")
        if not isinstance(returned, list):
            continue
        by_name = {
            str(item.get("name") or "").strip(): item.get("items")
            for item in returned
            if isinstance(item, dict)
        }
        headings = list(region.get("chapter_headings") or [])
        source = {
            "region": int(region["index"]),
            "start_char": int(region["start_char"]),
            "end_char": int(region["end_char"]),
            "chapter_start": int(region.get("chapter_start") or 0),
            "chapter_end": int(region.get("chapter_end") or 0),
            "chapter_title_start": headings[0] if headings else "",
            "chapter_title_end": headings[-1] if headings else "",
            "chapter_count": len(headings),
        }
        for dimension in dimensions:
            for item in _normalize_dimension_items_clean(by_name.get(dimension), dimension):
                identity = re.sub(r"[^\w\u4e00-\u9fff]+", "", item["name"].casefold()) or item["name"].casefold()
                details = dict(item.get("details") or {})
                details["source_ranges"] = [source]
                item["details"] = details
                existing = merged[dimension].get(identity)
                if not existing:
                    merged[dimension][identity] = item
                    continue
                existing_details = existing.setdefault("details", {})
                source_ranges = existing_details.setdefault("source_ranges", [])
                if source not in source_ranges:
                    source_ranges.append(source)
                evolution = existing_details.setdefault("evolution", [])
                evolution.append(
                    {
                        "region": source["region"],
                        "summary": item["summary"],
                        "details": {key: value for key, value in details.items() if key != "source_ranges"},
                    }
                )
                if item["summary"] not in existing["summary"]:
                    existing["summary"] = f"{existing['summary']} 后续变化：{item['summary']}"[:200]
                existing["tags"] = list(dict.fromkeys([*existing.get("tags", []), *item.get("tags", [])]))[:3]
    return [{"name": dimension, "items": list(merged[dimension].values())} for dimension in dimensions]


CHAPTER_EVENT_TYPES = {"main_plot", "side_plot", "payoff"}
CHAPTER_ENTITY_TYPES = {"character", "world_rule", "power", "faction"}
CHAPTER_THREAD_STATUSES = {"opened", "advanced", "resolved"}
CHAPTER_CRAFT_TYPES = {"pacing", "payoff", "theme", "technique"}


def normalize_card_entries(value: Any, allowed_types: set[str], limit: int) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    entries: list[dict[str, str]] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        entry_type = str(raw.get("type") or "").strip()
        name = compact_text(raw.get("name"))[:120]
        description = compact_text(raw.get("description"))[:240]
        if entry_type in allowed_types and name and description:
            entries.append({"type": entry_type, "name": name, "description": description})
        if len(entries) >= limit:
            break
    return entries


def normalize_thread_entries(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    entries: list[dict[str, str]] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        name = compact_text(raw.get("name"))[:120]
        status = str(raw.get("status") or "").strip()
        description = compact_text(raw.get("description"))[:240]
        if name and status in CHAPTER_THREAD_STATUSES and description:
            entries.append({"name": name, "status": status, "description": description})
        if len(entries) >= 2:
            break
    return entries


def normalize_chapter_cards(
    result: dict[str, Any],
    batch: list[dict[str, object]],
    refined: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    raw_cards = result.get("chapters")
    by_index = {
        int(raw["index"]): raw
        for raw in raw_cards if isinstance(raw, dict) and str(raw.get("index") or "").isdigit()
    } if isinstance(raw_cards, list) else {}
    cards: list[dict[str, Any]] = []
    missing = 0
    for packet in batch:
        index = int(packet["index"])
        refine_mode = str(packet.get("refine_mode") or ("full" if refined else "evidence"))
        raw = by_index.get(index)
        if not raw:
            missing += 1
            summary = compact_text(packet.get("source"))[:180] or "该章节已建立原文索引，但模型未返回语义摘要。"
            raw = {"summary": summary, "importance": 3, "confidence": 0.2}
        summary = compact_text(raw.get("summary"))[:300]
        if len(summary) < 50:
            summary = f"{summary} 本章的起因、行动结果和结尾状态需结合对应原文片段继续核对。".strip()
        try:
            importance = max(0, min(10, int(float(raw.get("importance", 3)))))
        except (TypeError, ValueError):
            importance = 3
        try:
            confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.2))))
        except (TypeError, ValueError):
            confidence = 0.2
        cards.append(
            {
                "index": index,
                "title": str(packet["title"]),
                "start_char": int(packet["start_char"]),
                "end_char": int(packet["end_char"]),
                "summary": summary,
                "events": normalize_card_entries(raw.get("events"), CHAPTER_EVENT_TYPES, 2),
                "entity_changes": normalize_card_entries(raw.get("entity_changes"), CHAPTER_ENTITY_TYPES, 3),
                "threads": normalize_thread_entries(raw.get("threads")),
                "craft": normalize_card_entries(raw.get("craft"), CHAPTER_CRAFT_TYPES, 1),
                "importance": importance,
                "confidence": confidence,
                "refined": refined,
                "refine_mode": refine_mode if refined else "local",
                "analysis_status": "fallback" if index not in by_index else "model",
            }
        )
    return cards, missing


def local_chapter_card(chapter: dict[str, object]) -> dict[str, Any]:
    content = re.sub(r"\s+", " ", str(chapter.get("content") or "")).strip()
    sentences = [part.strip() for part in re.split(r"(?<=[。！？!?])", content) if part.strip()]
    signal_words = (
        "发现", "决定", "真相", "秘密", "身份", "死亡", "牺牲", "背叛", "突破", "晋级", "觉醒",
        "获得", "失去", "击败", "失败", "危机", "阴谋", "复活", "重逢", "离开", "加入", "建立",
        "毁灭", "承诺", "关系", "规则", "最终", "结果", "原来", "竟然", "突然",
    )
    scored = [
        (
            sum(sentence.count(word) for word in signal_words) * 5
            + min(5, sentence.count("！") + sentence.count("？"))
            + min(5, len(sentence) // 80),
            position,
            sentence,
        )
        for position, sentence in enumerate(sentences)
    ]
    important = sorted(scored, key=lambda item: (item[0], len(item[2])), reverse=True)[:6]
    positions = {0, max(0, len(sentences) - 1), *(position for score, position, _ in important if score > 0)}
    selected = [sentences[position] for position in sorted(positions) if position < len(sentences)]
    summary = " ".join(selected)[:300] or content[:300]
    if len(summary) < 50:
        summary = f"{summary} 本章内容已按原文顺序建立索引，重要程度将结合标题和高信号句进一步判断。".strip()
    max_score = max((score for score, _, _ in scored), default=0)
    title = str(chapter["title"])
    title_bonus = 3 if re.search(r"大结局|真相|决战|终战|死亡|牺牲|突破|觉醒|复活|背叛|身份|毁灭|最终|最后", title) else 0
    importance = max(1, min(10, 2 + title_bonus + min(5, max_score // 5)))
    return {
        "index": int(chapter["index"]),
        "title": title,
        "start_char": int(chapter["start_char"]),
        "end_char": int(chapter["end_char"]),
        "summary": summary,
        "events": [{"type": "main_plot", "name": title, "description": summary[:220]}],
        "entity_changes": [],
        "threads": [],
        "craft": [],
        "importance": importance,
        "confidence": 0.45,
        "refined": False,
        "refine_mode": "local",
        "analysis_status": "local",
        "key_passages": selected[:8],
    }


def select_important_chapters(cards: list[dict[str, Any]], chapter_lookup: dict[int, dict[str, object]]) -> list[dict[str, object]]:
    title_pattern = re.compile(r"大结局|真相|决战|终战|死亡|牺牲|突破|觉醒|复活|背叛|身份|婚礼|毁灭|陨落|晋入|最终|最后")
    ranked = sorted(
        cards,
        key=lambda card: (
            card["importance"],
            1.0 - card["confidence"],
            bool(title_pattern.search(card["title"])),
        ),
        reverse=True,
    )
    target = max(30, min(180, round(len(cards) * 0.08)))
    selected = [
        card
        for card in ranked
        if card["importance"] >= 8 or card["confidence"] < 0.65 or title_pattern.search(card["title"])
    ][:target]
    if len(selected) < target:
        selected_ids = {card["index"] for card in selected}
        selected.extend(
            [card for card in ranked if card["index"] not in selected_ids][: target - len(selected)]
        )
    return [chapter_lookup[card["index"]] for card in sorted(selected, key=lambda item: item["index"]) if card["index"] in chapter_lookup]


def _chapter_priority(card: dict[str, Any]) -> tuple[float, int, int]:
    title_pattern = re.compile(r"大结局|真相|决战|终战|死亡|牺牲|突破|觉醒|复活|背叛|身份|婚礼|毁灭|陨落|晋入|最终|最后")
    return (
        float(card.get("importance", 0)) * 2 + (1.0 - float(card.get("confidence", 0))) * 3,
        int(bool(title_pattern.search(str(card.get("title") or "")))),
        len(str(card.get("summary") or "")),
    )


def _spread_best_cards(cards: list[dict[str, Any]], target: int) -> list[dict[str, Any]]:
    if not cards or target <= 0:
        return []
    ordered = sorted(cards, key=lambda card: int(card["index"]))
    selected: list[dict[str, Any]] = []
    for bucket_index in range(min(target, len(ordered))):
        start = round(len(ordered) * bucket_index / target)
        end = round(len(ordered) * (bucket_index + 1) / target)
        bucket = ordered[start:max(start + 1, end)]
        if bucket:
            selected.append(max(bucket, key=_chapter_priority))
    return selected


def select_adaptive_refinement_chapters(
    cards: list[dict[str, Any]],
    chapter_lookup: dict[int, dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    chapter_count = len(cards)
    full_target = min(24, max(12, round(chapter_count * 0.012)))
    evidence_target = min(48, max(24, round(chapter_count * 0.025)))
    full_cards = _spread_best_cards(cards, full_target)
    full_ids = {int(card["index"]) for card in full_cards}
    evidence_cards = [card for card in _spread_best_cards(cards, evidence_target) if int(card["index"]) not in full_ids]
    evidence_ids = {int(card["index"]) for card in evidence_cards}
    for card in sorted(cards, key=_chapter_priority, reverse=True):
        index = int(card["index"])
        if index in full_ids or index in evidence_ids:
            continue
        evidence_cards.append(card)
        evidence_ids.add(index)
        if len(evidence_cards) >= evidence_target:
            break
    full_chapters = [
        {**chapter_lookup[int(card["index"])], "refine_mode": "full"}
        for card in sorted(full_cards, key=lambda item: int(item["index"]))
        if int(card["index"]) in chapter_lookup
    ]
    evidence_chapters = [
        {
            **chapter_lookup[int(card["index"])],
            "content": chapter_signal_excerpt(chapter_lookup[int(card["index"])], 1600),
            "refine_mode": "evidence",
        }
        for card in sorted(evidence_cards, key=lambda item: int(item["index"]))
        if int(card["index"]) in chapter_lookup
    ]
    return full_chapters, evidence_chapters


def _summary_edges(summary: str, limit: int) -> str:
    compact = re.sub(r"\s+", " ", summary).strip()
    if len(compact) <= limit:
        return compact
    head = max(1, limit * 3 // 5)
    return f"{compact[:head]}…{compact[-(limit - head - 1):]}"


def build_region_digest(
    region_chapters: list[dict[str, object]],
    card_lookup: dict[int, dict[str, Any]],
    limit: int = 20000,
) -> str:
    if not region_chapters:
        return ""
    chronological_budget = int(limit * 0.76)
    summary_budget = max(24, min(90, chronological_budget // len(region_chapters) - 32))
    lines = []
    for chapter in region_chapters:
        index = int(chapter["index"])
        card = card_lookup[index]
        lines.append(f"[{index}] {chapter['title']}｜{_summary_edges(str(card['summary']), summary_budget)}")
    digest = "【逐章顺序索引】\n" + "\n".join(lines)
    remaining = limit - len(digest) - 20
    if remaining > 300:
        important = sorted(
            (card_lookup[int(chapter["index"])] for chapter in region_chapters),
            key=_chapter_priority,
            reverse=True,
        )[:8]
        evidence: list[str] = []
        per_chapter = max(180, remaining // max(1, len(important)) - 30)
        chapter_by_index = {int(chapter["index"]): chapter for chapter in region_chapters}
        for card in sorted(important, key=lambda item: int(item["index"])):
            chapter = chapter_by_index[int(card["index"])]
            excerpt = chapter_signal_excerpt(chapter, per_chapter)
            evidence.append(f"[关键章 {card['index']}] {card['title']}\n{excerpt}")
        digest += "\n\n【区域关键原文】\n" + "\n\n".join(evidence)
    return digest[:limit]


def timeline_item(name: str, category: str, timeline: list[dict[str, Any]], tags: list[str]) -> dict[str, Any]:
    first = timeline[0]
    last = timeline[-1]
    summary = f"{name}首次在第{first['chapter_index']}节记录：{first['description']}"
    if last["chapter_index"] != first["chapter_index"]:
        summary += f"；至第{last['chapter_index']}节的最新变化：{last['description']}"
    return {
        "name": name,
        "category": category,
        "summary": summary[:200],
        "details": {
            "first_chapter": first["chapter_index"],
            "last_chapter": last["chapter_index"],
            "timeline": timeline,
            "source_ranges": [
                {"chapter_index": item["chapter_index"], "title": item["title"]}
                for item in timeline
            ],
        },
        "tags": tags[:3],
    }


def cards_to_ordered_dimensions(cards: list[dict[str, Any]], arc_size: int = 50) -> list[dict[str, Any]]:
    ordered = sorted(cards, key=lambda card: card["index"])
    entity_dimensions = {
        "character": ("角色系统", "人物变化"),
        "world_rule": ("世界观与规则", "规则演进"),
        "power": ("力量与成长体系", "力量变化"),
        "faction": ("势力与关系网络", "势力变化"),
    }
    aggregated: dict[str, dict[str, list[dict[str, Any]]]] = {
        dimension: {} for dimension, _ in entity_dimensions.values()
    }
    for card in ordered:
        for change in card["entity_changes"]:
            dimension, _ = entity_dimensions[change["type"]]
            aggregated[dimension].setdefault(change["name"], []).append(
                {
                    "chapter_index": card["index"],
                    "title": card["title"],
                    "description": change["description"],
                }
            )
    results: list[dict[str, Any]] = []
    for change_type, (dimension, category) in entity_dimensions.items():
        items = [
            timeline_item(name, category, timeline, [dimension, category])
            for name, timeline in aggregated[dimension].items()
        ]
        results.append({"name": dimension, "items": items})

    main_items: list[dict[str, Any]] = []
    payoff_items: list[dict[str, Any]] = []
    archive_items: list[dict[str, Any]] = []
    theme_timelines: dict[str, list[dict[str, Any]]] = {}
    thread_timelines: dict[str, list[dict[str, Any]]] = {}
    for start in range(0, len(ordered), arc_size):
        arc = ordered[start : start + arc_size]
        chapter_start, chapter_end = arc[0]["index"], arc[-1]["index"]
        main_events = [
            {"chapter_index": card["index"], "title": card["title"], **event}
            for card in arc
            for event in card["events"]
            if event["type"] == "main_plot" and (card["refined"] or card["importance"] >= 7)
        ]
        if not main_events:
            main_events = [
                {"chapter_index": card["index"], "title": card["title"], "name": card["title"], "description": card["summary"]}
                for card in arc if card["importance"] >= 7
            ][:8]
        if len(main_events) > 12:
            positions = sorted({round((len(main_events) - 1) * index / 11) for index in range(12)})
            main_events = [main_events[position] for position in positions]
        if main_events:
            main_items.append(
                {
                    "name": f"剧情阶段 {chapter_start}-{chapter_end}",
                    "category": "主线顺序",
                    "summary": "；".join(event["description"] for event in main_events)[:200],
                    "details": {"chapter_start": chapter_start, "chapter_end": chapter_end, "ordered_events": main_events, "source_ranges": [{"chapter_index": event["chapter_index"], "title": event["title"]} for event in main_events]},
                    "tags": ["主线", "剧情顺序", f"第{chapter_start}-{chapter_end}节"],
                }
            )
        payoff_events = [
            {"chapter_index": card["index"], "title": card["title"], **entry}
            for card in arc
            for entry in [*card["events"], *card["craft"]]
            if entry["type"] in {"payoff", "pacing"}
        ]
        if payoff_events:
            payoff_items.append(
                {
                    "name": f"爽点与节奏 {chapter_start}-{chapter_end}",
                    "category": "阶段节奏",
                    "summary": "；".join(event["description"] for event in payoff_events)[:200],
                    "details": {"chapter_start": chapter_start, "chapter_end": chapter_end, "ordered_events": payoff_events, "source_ranges": [{"chapter_index": event["chapter_index"], "title": event["title"]} for event in payoff_events]},
                    "tags": ["爽点", "节奏", f"第{chapter_start}-{chapter_end}节"],
                }
            )
        archive_items.append(
            {
                "name": f"章节档案 {chapter_start}-{chapter_end}",
                "category": "有序章节记录",
                "summary": f"按剧情顺序保存第 {chapter_start}-{chapter_end} 节的章节摘要、事件、人物变化和伏笔状态，共 {len(arc)} 节。",
                "details": {
                    "chapter_start": chapter_start,
                    "chapter_end": chapter_end,
                    "chapter_count": len(arc),
                    "storage": "novel_chapter_cards",
                    "highlights": [
                        {"index": card["index"], "title": card["title"], "summary": card["summary"]}
                        for card in arc if card["refined"] or card["importance"] >= 8
                    ][:8],
                },
                "tags": ["章节档案", "剧情顺序", f"第{chapter_start}-{chapter_end}节"],
            }
        )
        for card in arc:
            for thread in card["threads"]:
                thread_timelines.setdefault(thread["name"], []).append(
                    {"chapter_index": card["index"], "title": card["title"], "status": thread["status"], "description": thread["description"]}
                )
            for craft in card["craft"]:
                if craft["type"] in {"theme", "technique"}:
                    theme_timelines.setdefault(craft["name"], []).append(
                        {"chapter_index": card["index"], "title": card["title"], "description": craft["description"]}
                    )
    results.append({"name": "主线情节", "items": main_items})
    results.append({"name": "支线与伏笔", "items": [timeline_item(name, "伏笔时间线", timeline, ["伏笔", timeline[-1]["status"]]) for name, timeline in thread_timelines.items()]})
    results.append({"name": "爽点与节奏", "items": payoff_items})
    results.append({"name": "主题与写法", "items": [timeline_item(name, "主题或技法", timeline, ["主题", "写法"]) for name, timeline in theme_timelines.items()]})
    results.append({"name": "章节剧情档案", "items": archive_items})
    return results


async def _analyze_novel(
    text: str,
    api_config: dict[str, str],
    genre_hint: str = "",
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    normalized_hint = genre_hint.strip()
    if normalized_hint:
        if normalized_hint not in TYPE_DIMENSIONS:
            raise AgentError("指定的分析侧重点不受支持")
        primary_type = normalized_hint
        secondary_type = ""
        type_source = "user_hint"
    else:
        detected = await complete_json(
            api_config,
            TYPE_SYSTEM_PROMPT,
            analysis_excerpt(text, 18000),
        )
        primary_type = str(detected.get("primary_type") or "").strip()
        if primary_type not in TYPE_DIMENSIONS:
            raise AgentError("模型未能识别受支持的小说类型")
        secondary_type = str(detected.get("secondary_type") or "").strip()
        if secondary_type not in TYPE_DIMENSIONS:
            secondary_type = ""
        type_source = "model"
    dimensions = list(TYPE_DIMENSIONS[primary_type])
    if secondary_type:
        dimensions.extend(
            dimension for dimension in TYPE_DIMENSIONS[secondary_type] if dimension not in dimensions
        )

    if len(text) > 300000:
        chapters = split_novel_chapters(text)
        if len(chapters) >= 20:
            chapter_lookup = {int(chapter["index"]): chapter for chapter in chapters}
            cards = [local_chapter_card(chapter) for chapter in chapters]
            if progress_callback:
                progress_callback({"stage": "chapter_cards", "step": 1, "total": 1, "status": "done"})
            full_refine_chapters, evidence_refine_chapters = select_adaptive_refinement_chapters(cards, chapter_lookup)
            refinement_chapters = sorted(
                [*full_refine_chapters, *evidence_refine_chapters],
                key=lambda chapter: int(chapter["index"]),
            )
            refinement_lookup = {int(chapter["index"]): chapter for chapter in refinement_chapters}
            refine_batches = pack_chapter_inputs(refinement_chapters, 45000, full_text=True)
            local_card_lookup = {int(card["index"]): card for card in cards}
            region_count = min(6, len(chapters))
            regions: list[dict[str, Any]] = []
            for region_index in range(region_count):
                chapter_start_position = round(len(chapters) * region_index / region_count)
                chapter_end_position = round(len(chapters) * (region_index + 1) / region_count)
                region_chapters = chapters[chapter_start_position:chapter_end_position]
                start_char = int(region_chapters[0]["start_char"])
                end_char = int(region_chapters[-1]["end_char"])
                regions.append(
                    {
                        "index": region_index + 1,
                        "total": region_count,
                        "start_char": start_char,
                        "end_char": end_char,
                        "chapter_start": int(region_chapters[0]["index"]),
                        "chapter_end": int(region_chapters[-1]["index"]),
                        "chapter_headings": [str(chapter["title"]) for chapter in region_chapters],
                        "content": build_region_digest(region_chapters, local_card_lookup, 20000),
                    }
                )
            semaphore = asyncio.Semaphore(5)

            async def analyze_chapter_batch(
                batch_index: int,
                batch: list[dict[str, object]],
                batch_total: int,
                refined: bool = False,
            ) -> tuple[list[dict[str, Any]], int, str | None, int]:
                source = render_chapter_batch(batch)
                async with semaphore:
                    try:
                        result = await complete_json(
                            api_config,
                            chapter_batch_prompt(batch_index, batch_total, len(batch), refined),
                            source,
                        )
                        cards, missing = normalize_chapter_cards(result, batch, refined)
                        if progress_callback:
                            progress_callback(
                                {
                                    "stage": "important_refine" if refined else "chapter_cards",
                                    "step": batch_index,
                                    "total": batch_total,
                                    "status": "done",
                                }
                            )
                        warning = f"重要章批次 {batch_index}/{batch_total} 缺少 {missing} 张章节卡" if missing else None
                        return cards, missing, warning, len(source)
                    except AgentError as error:
                        cards, missing = normalize_chapter_cards({}, batch, refined)
                        if progress_callback:
                            progress_callback(
                                {
                                    "stage": "important_refine" if refined else "chapter_cards",
                                    "step": batch_index,
                                    "total": batch_total,
                                    "status": "error",
                                    "message": str(error),
                                }
                            )
                        return cards, missing, f"重要章批次 {batch_index}/{batch_total}：{error}", len(source)

            async def analyze_macro_region(region: dict[str, Any], retry: bool = False) -> tuple[dict[str, Any], dict[str, Any] | None, str | None, int]:
                excerpt = analysis_excerpt(str(region["content"]), 14000) if retry else str(region["content"])
                async with semaphore:
                    try:
                        result = await complete_json(
                            api_config,
                            region_analysis_prompt(dimensions, int(region["index"]), int(region["total"])),
                            excerpt,
                        )
                        if progress_callback:
                            progress_callback({"stage": "macro_retry" if retry else "macro_regions", "step": region["index"], "total": region["total"], "status": "done"})
                        return region, result, None, len(excerpt)
                    except AgentError as error:
                        if progress_callback:
                            progress_callback({"stage": "macro_retry" if retry else "macro_regions", "step": region["index"], "total": region["total"], "status": "error", "message": str(error)})
                        return region, None, f"全书区域 {region['index']}/{region['total']}：{error}", len(excerpt)

            refine_future = asyncio.gather(
                *(analyze_chapter_batch(index, batch, len(refine_batches), True) for index, batch in enumerate(refine_batches, start=1))
            ) if refine_batches else asyncio.sleep(0, result=[])
            region_future = asyncio.gather(*(analyze_macro_region(region) for region in regions))
            refine_outcomes, region_outcomes = await asyncio.gather(refine_future, region_future)
            failed_regions = [region for region, result, _, _ in region_outcomes if result is None]
            region_retry_outcomes = await asyncio.gather(*(analyze_macro_region(region, True) for region in failed_regions)) if failed_regions else []
            card_by_index = {card["index"]: card for card in cards}
            for refined_cards, _, _, _ in refine_outcomes:
                card_by_index.update({card["index"]: card for card in refined_cards if card["analysis_status"] == "model"})
            important_indices = set(refinement_lookup)
            retry_chapters = [
                refinement_lookup[index]
                for index in sorted(important_indices)
                if card_by_index[index]["analysis_status"] != "model"
            ]
            retry_batches = pack_chapter_inputs(retry_chapters, 12000, full_text=True)
            retry_outcomes = await asyncio.gather(
                *(analyze_chapter_batch(index, batch, len(retry_batches), True) for index, batch in enumerate(retry_batches, start=1))
            ) if retry_batches else []
            for refined_cards, _, _, _ in retry_outcomes:
                card_by_index.update({card["index"]: card for card in refined_cards if card["analysis_status"] == "model"})
            final_retry_chapters = [
                {
                    **chapter_lookup[index],
                    "content": chapter_signal_excerpt(chapter_lookup[index], 900),
                    "refine_mode": "evidence",
                }
                for index in sorted(important_indices)
                if card_by_index[index]["analysis_status"] != "model"
            ]
            final_retry_batches = pack_chapter_inputs(final_retry_chapters, 1, full_text=True)
            final_retry_outcomes = await asyncio.gather(
                *(analyze_chapter_batch(index, batch, len(final_retry_batches), True) for index, batch in enumerate(final_retry_batches, start=1))
            ) if final_retry_batches else []
            for refined_cards, _, _, _ in final_retry_outcomes:
                card_by_index.update({card["index"]: card for card in refined_cards if card["analysis_status"] == "model"})
            warnings = [
                warning
                for _, _, warning, _ in [*refine_outcomes, *retry_outcomes, *final_retry_outcomes, *region_retry_outcomes]
                if warning and "缺少" not in warning
            ]
            remaining_missing = [index for index in sorted(important_indices) if card_by_index[index]["analysis_status"] != "model"]
            if remaining_missing:
                warnings.append(f"仍有 {len(remaining_missing)} 个重要章节未完成模型精读，已保留本地有序章节记录")
            cards = [card_by_index[index] for index in sorted(card_by_index)]
            results = cards_to_ordered_dimensions(cards)
            region_result_lookup = {int(region["index"]): (region, result) for region, result, _, _ in region_outcomes if result is not None}
            region_result_lookup.update({int(region["index"]): (region, result) for region, result, _, _ in region_retry_outcomes if result is not None})
            successful_regions = [region_result_lookup[index] for index in sorted(region_result_lookup)]
            if len(successful_regions) < len(regions):
                warnings.append(f"仍有 {len(regions) - len(successful_regions)} 个全书区域未完成语义分类")
            macro_dimensions = merge_region_dimensions(dimensions, successful_regions)
            result_lookup = {dimension["name"]: dimension for dimension in results}
            for macro_dimension in macro_dimensions:
                target = result_lookup.get(macro_dimension["name"])
                if target:
                    target["items"].extend(macro_dimension["items"])
                elif macro_dimension["items"]:
                    results.append(macro_dimension)

            def item_order(item: dict[str, Any]) -> int:
                details = item.get("details") if isinstance(item.get("details"), dict) else {}
                candidates = [details.get("first_chapter"), details.get("chapter_start")]
                for source in details.get("source_ranges", []) if isinstance(details.get("source_ranges"), list) else []:
                    if isinstance(source, dict):
                        candidates.extend([source.get("chapter_index"), source.get("chapter_start")])
                numeric = [int(value) for value in candidates if str(value or "").isdigit() and int(value) > 0]
                return min(numeric) if numeric else 10**9

            for dimension in results:
                dimension["items"].sort(key=item_order)
            coverage_items = key_content_coverage_items(text)
            results.append({"name": "关键内容索引", "items": coverage_items})
            retained_chars = sum(int(item.get("details", {}).get("retained_chars") or 0) for item in coverage_items)
            return {
                "primary_type": primary_type,
                "secondary_type": secondary_type,
                "type_source": type_source,
                "dimensions": results,
                "chapter_cards": cards,
                "warnings": warnings,
                "coverage": {
                    "strategy": "ordered_chapter_digest_adaptive_key_refinement",
                    "total_chars": len(text),
                    "indexed_chars": len(text),
                    "archived_chars": retained_chars,
                    "retained_chars": retained_chars,
                    "retention_ratio": round(retained_chars / max(1, len(text)), 4),
                    "archive_segments": len(coverage_items),
                    "chapter_count": len(chapters),
                    "analyzed_chapters": len(cards),
                    "model_analyzed_chapters": sum(card["analysis_status"] == "model" for card in cards),
                    "refined_chapters": sum(card["refined"] for card in cards),
                    "full_refined_chapters": sum(card.get("refine_mode") == "full" and card["analysis_status"] == "model" for card in cards),
                    "evidence_refined_chapters": sum(card.get("refine_mode") == "evidence" and card["analysis_status"] == "model" for card in cards),
                    "chapter_batches": 1,
                    "refine_batches": len(refine_batches) + len(retry_batches) + len(final_retry_batches),
                    "semantic_regions": len(regions),
                    "analyzed_regions": len(successful_regions),
                    "semantic_sampled_chars": sum(sampled for _, _, _, sampled in [*refine_outcomes, *retry_outcomes, *final_retry_outcomes, *region_outcomes, *region_retry_outcomes]),
                },
            }

        region_size = max(500000, (len(text) + 5) // 6)
        regions = semantic_analysis_regions(text, region_size)
        semaphore = asyncio.Semaphore(2)

        async def analyze_region(region: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None, str | None, int]:
            excerpt = analysis_excerpt(str(region["content"]), 36000)
            async with semaphore:
                try:
                    result = await complete_json(
                        api_config,
                        region_analysis_prompt(dimensions, int(region["index"]), int(region["total"])),
                        excerpt,
                    )
                    if progress_callback:
                        progress_callback({"region": region["index"], "total": region["total"], "status": "done"})
                    return region, result, None, len(excerpt)
                except AgentError as error:
                    if progress_callback:
                        progress_callback({"region": region["index"], "total": region["total"], "status": "error", "message": str(error)})
                    return region, None, f"区域 {region['index']}/{region['total']}：{error}", len(excerpt)

        region_outcomes = await asyncio.gather(*(analyze_region(region) for region in regions))
        successful_regions = [(region, result) for region, result, _, _ in region_outcomes if result is not None]
        warnings = [warning for _, _, warning, _ in region_outcomes if warning]
        results = merge_region_dimensions(dimensions, successful_regions)
        results = [dimension for dimension in results if dimension["items"]]
        if not results:
            raise AgentError("长篇分区分析全部失败，请检查模型兼容性后重试")
        coverage_items = key_content_coverage_items(text)
        results.append({"name": "关键内容索引", "items": coverage_items})
        retained_chars = sum(int(item.get("details", {}).get("retained_chars") or 0) for item in coverage_items)
        return {
            "primary_type": primary_type,
            "secondary_type": secondary_type,
            "type_source": type_source,
            "dimensions": results,
            "warnings": warnings,
            "coverage": {
                "strategy": "key_passage_archive_and_region_analysis",
                "total_chars": len(text),
                "indexed_chars": len(text),
                "archived_chars": retained_chars,
                "retained_chars": retained_chars,
                "retention_ratio": round(retained_chars / max(1, len(text)), 4),
                "archive_segments": len(coverage_items),
                "semantic_regions": len(regions),
                "analyzed_regions": len(successful_regions),
                "semantic_sampled_chars": sum(sampled for _, _, _, sampled in region_outcomes),
            },
        }

    semaphore = asyncio.Semaphore(2)

    async def analyze_dimension(index: int, name: str) -> tuple[int, dict[str, Any] | None, str | None]:
        async with semaphore:
            try:
                result = await complete_json(
                    api_config,
                    dimension_prompt(name),
                    dimension_excerpt(text, name, 9000),
                )
                return (
                    index,
                    {
                        "name": name,
                        "items": _normalize_dimension_items_clean(result.get("items"), name),
                    },
                    None,
                )
            except AgentError as error:
                return index, None, f"{name}：{error}"

    outcomes = await asyncio.gather(
        *(analyze_dimension(index, name) for index, name in enumerate(dimensions))
    )
    results = [result for _, result, _ in sorted(outcomes) if result is not None]
    warnings = [warning for _, _, warning in sorted(outcomes) if warning]
    if not results:
        raise AgentError("所有素材维度均分析失败，请检查模型兼容性后重试")
    return {
        "primary_type": primary_type,
        "secondary_type": secondary_type,
        "type_source": type_source,
        "dimensions": results,
        "warnings": warnings,
        "coverage": {
            "strategy": "full_text" if len(text) <= 9000 else "dimension_excerpts",
            "total_chars": len(text),
            "archived_chars": len(text),
            "archive_segments": 1,
            "semantic_regions": 1,
            "analyzed_regions": 1,
        },
    }


async def analyze_novel(
    text: str,
    api_config: dict[str, str],
    genre_hint: str = "",
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    usage = {
        "model_calls": 0,
        "model_input_chars": 0,
        "input_tokens": 0,
        "output_tokens": 0,
    }
    usage_token = ANALYSIS_USAGE.set(usage)
    try:
        result = await _analyze_novel(text, api_config, genre_hint, progress_callback)
    finally:
        ANALYSIS_USAGE.reset(usage_token)
    coverage = result.setdefault("coverage", {})
    coverage.update(
        {
            **usage,
            "total_tokens": usage["input_tokens"] + usage["output_tokens"],
            "estimated_input_tokens": usage["input_tokens"] or round(usage["model_input_chars"] / 1.8),
        }
    )
    return result


async def paper_intent(
    message: str,
    api_config: dict[str, str],
) -> dict[str, Any]:
    normalized = message.casefold()
    negative_chinese = re.search(r"(?:不要|不用|无需|别|不必).{0,12}(?:生成|写|创作|整理).{0,16}(?:稿纸|篇章|正文|小说)", message, flags=re.S)
    negative_english = re.search(r"\b(?:do not|don't|no need to|without)\b.{0,50}\b(?:generate|write|create)\b.{0,50}\b(?:paper|chapter|story|novel|manuscript)\b", normalized, flags=re.S)
    if negative_chinese or negative_english:
        return {"should_create": False, "mode": "create", "reason": "检测到明确的否定生成命令"}
    direct_chinese = ("正式稿纸", "正式篇章", "生成正文", "写一篇", "创作一篇", "整理成篇章", "修改篇章", "续写")
    direct_english = re.search(r"\b(generate|write|create|revise|rewrite)\b.{0,40}\b(paper|chapter|story|novel|manuscript)\b", normalized, flags=re.S)
    if any(trigger in message for trigger in direct_chinese) or direct_english:
        modify = any(trigger in normalized for trigger in ("修改", "重写", "改写", "revise", "rewrite"))
        return {"should_create": True, "mode": "modify" if modify else "create", "reason": "检测到明确的正式稿纸命令"}
    if not any(trigger in message for trigger in PAPER_TRIGGER_WORDS) and not re.search(r"\b(generate|write|create)\b", normalized):
        return {"should_create": False, "mode": "create", "reason": "没有明确命令"}
    result = await complete_json(api_config, PAPER_INTENT_PROMPT, message)
    return {
        "should_create": result.get("should_create") is True,
        "mode": "modify" if result.get("mode") == "modify" else "create",
        "reason": str(result.get("reason") or ""),
    }


def chat_messages(
    message: str,
    history: list[dict[str, str]],
    material_context: str,
    mode: str = "guided",
    project_context: str = "",
) -> list[dict[str, str]]:
    context = material_context or "本轮没有勾选素材，请基于对话历史回应，不要假装读取了素材。"
    system = f"{CHAT_SYSTEM_PROMPT}\n\n本轮素材上下文：\n{context}"
    system += f"\n\n当前创作模式：{mode}\n模式行为：{MODE_PROMPTS.get(mode, MODE_PROMPTS['guided'])}"
    if project_context:
        system += f"\n\n{project_context}"
    if mode == "traceable":
        system += "\n\n请在关键结论末尾使用[来源:素材/铁律/事实/篇章/推断]标注。"
    elif mode == "teaching":
        system += "\n\n回答后追加‘方法摘要’，解释可复用的结构和检查步骤。"
    return [
        {"role": "system", "content": system},
        *history[-40:],
        {"role": "user", "content": message},
    ]


def bounded_generation_history(
    history: list[dict[str, str]],
    max_chars: int = 18000,
    max_items: int = 12,
) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    used = 0
    for message in reversed(history[-40:]):
        role = str(message.get("role") or "assistant")
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        if role == "assistant" and len(content) > 3000 and '"volume"' in content and '"chapters"' in content:
            continue
        if len(content) > 6000:
            content = f"{content[:3000].rstrip()}\n[长消息中段已压缩]\n{content[-3000:].lstrip()}"
        remaining = max_chars - used
        if remaining <= 0:
            break
        if len(content) > remaining:
            if remaining < 600:
                break
            content = f"{content[:remaining - 18].rstrip()}\n[历史已压缩]"
        selected.append({"role": role, "content": content})
        used += len(content)
        if len(selected) >= max_items:
            break
    return list(reversed(selected))


async def stream_normal_reply(
    message: str,
    history: list[dict[str, str]],
    material_context: str,
    api_config: dict[str, str],
    mode: str = "guided",
    project_context: str = "",
) -> AsyncIterator[str]:
    payload = {
        "model": api_config["model"],
        "stream": True,
        "messages": chat_messages(message, history, material_context, mode, project_context),
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=15.0)) as client:
            async with client.stream(
                "POST",
                chat_completions_url(api_config["base_url"]),
                headers=api_headers(api_config),
                json=payload,
            ) as response:
                if not response.is_success:
                    await response.aread()
                    raise AgentError(upstream_error(response))
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                        content = event["choices"][0]["delta"].get("content")
                    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                        continue
                    if content:
                        yield str(content)
    except httpx.TimeoutException as error:
        raise AgentError("灵感正在酝酿，请稍候...") from error
    except httpx.RequestError as error:
        raise AgentError("无法连接模型服务，请检查 API 地址和网络") from error


async def create_paper_reply(
    message: str,
    history: list[dict[str, str]],
    material_context: str,
    api_config: dict[str, str],
    source_paper: dict[str, Any] | None,
    chapter_context: str,
    mode: str,
    project_context: str = "",
    generation_action: str = "create",
    target_words: int | None = None,
) -> dict[str, Any]:
    source = f"\n项目长期约束与记忆：\n{project_context}" if project_context else ""
    if source_paper:
        source = f"\n需要修改的稿纸：\n标题：{source_paper['title']}\n正文：{source_paper['content']}"
    if chapter_context:
        source += f"\n已收录篇章目录与摘要：\n{chapter_context}"
    context = material_context or "本轮没有勾选素材。用户已明确要求创作，可以基于对话历史写作。"
    if source_paper and target_words is None:
        target_words = len(re.sub(r"\s+", "", str(source_paper.get("content") or "")))
    target = requested_chapter_words(message, target_words)
    action_instruction = {
        "continue": "这是续写下一章：必须承接最后一篇已收录篇章的结尾状态，推进新事件，不能重写上一章。",
        "modify": "这是修改现有稿件：只修改用户指出的部分，未要求变化的事实、主线和标题保持稳定。",
        "create": "这是生成新篇章：正文必须有完整场景、推进、转折或结果，并为后续留下自然接口。",
    }.get(generation_action, "生成一篇完整正式篇章。")
    user_content = (
        f"本轮素材：\n{context}\n{source}\n\n"
        f"最近对话：\n{json.dumps(history[-40:], ensure_ascii=False)}\n\n"
        f"操作模式：{mode}\n创作动作：{generation_action}\n目标正文长度：约 {target} 个中文字符，允许上下浮动 15%。\n"
        f"动作要求：{action_instruction}\n用户命令：{message}"
    )
    result: dict[str, Any] = {}
    paper: dict[str, Any] | None = None
    for attempt in range(2):
        result = await complete_json(api_config, PAPER_SYSTEM_PROMPT, user_content)
        candidate = result.get("paper")
        if not isinstance(candidate, dict) or not str(candidate.get("title") or "").strip() or not str(candidate.get("content") or "").strip():
            if attempt == 0:
                user_content += "\n\n上一次没有返回完整稿件。请重新生成，必须包含非空标题、完整正文和记忆包。"
                continue
            raise AgentError("模型没有生成完整篇章，请重试")
        paper = candidate
        actual_length = len(re.sub(r"\s+", "", str(paper["content"])))
        minimum_length = max(400, round(target * 0.7))
        maximum_length = round(target * 1.35)
        if minimum_length <= actual_length <= maximum_length or attempt == 1:
            break
        if actual_length < minimum_length:
            user_content += f"\n\n上一次正文只有 {actual_length} 字，明显短于 {target} 字目标。请重新完整生成，不要用提纲、摘要或省略号代替正文。"
        else:
            user_content += f"\n\n上一次正文达到 {actual_length} 字，明显超过 {target} 字目标。请重新压缩到目标篇幅，保留完整事件链，删除重复描写和不推进剧情的段落。"
    if paper is None:
        raise AgentError("模型没有生成完整篇章，请重试")
    actual_length = len(re.sub(r"\s+", "", str(paper["content"])))
    length_status = "short" if actual_length < max(400, round(target * 0.7)) else "long" if actual_length > round(target * 1.35) else "met"
    generated_title = normalize_paper_title(paper["title"])
    if (
        mode == "modify"
        and source_paper
        and not any(word in message for word in ("标题", "题目", "改名", "更名"))
    ):
        generated_title = normalize_paper_title(source_paper["title"])
    return {
        "text": str(result.get("text") or "已根据你的命令整理成篇章。") + ("" if length_status == "met" else f" 当前稿件约 {actual_length} 字，{'短于' if length_status == 'short' else '长于'} {target} 字目标，可继续调整。"),
        "paper": {
            "title": generated_title,
            "content": str(paper["content"]).strip(),
            "memory": normalize_chapter_memory(
                paper.get("memory") or result.get("memory"),
                str(paper["content"]).strip(),
            ),
            "status": "draft",
            "chapter_id": None,
            "target_chapter_id": (
                source_paper.get("chapter_id")
                if source_paper and source_paper.get("chapter_id")
                else paper.get("target_chapter_id")
            ),
            "word_count": actual_length,
            "target_words": target,
            "length_status": length_status,
            "generation_action": generation_action,
        },
    }


def get_mode_prompt(mode: str) -> str:
    return MODE_PROMPTS.get(mode, MODE_PROMPTS["guided"])


def validate_universe(rules: list[dict[str, Any]], content: str) -> list[dict[str, str]]:
    return []


async def validate_universe_with_model(
    rules: list[dict[str, Any]],
    content: str,
    api_config: dict[str, str],
) -> list[dict[str, str]]:
    deterministic = validate_universe(rules, content)
    if not rules:
        return deterministic
    payload = {
        "rules": [
            {"category": rule.get("category"), "key": rule.get("key"), "value": rule.get("value")}
            for rule in rules
        ],
        "candidate_text": content,
    }
    result = await complete_json(api_config, UNIVERSE_CHECK_PROMPT, json.dumps(payload, ensure_ascii=False))
    rule_keys = {str(rule.get("key") or "") for rule in rules}
    semantic = [
        {
            "rule_key": str(item.get("rule_key") or ""),
            "reason": str(item.get("reason") or "语义上违反宇宙铁律"),
            "excerpt": str(item.get("excerpt") or ""),
        }
        for item in result.get("violations", [])
        if isinstance(item, dict) and str(item.get("rule_key") or "") in rule_keys
    ]
    merged = {item["rule_key"]: item for item in [*deterministic, *semantic]}
    return list(merged.values())


async def generate_inspirations(premise: str, dilemma: str, api_config: dict[str, str]) -> list[dict[str, Any]]:
    result = await complete_json(
        api_config,
        INSPIRATION_PROMPT,
        json.dumps({"premise": premise, "dilemma": dilemma}, ensure_ascii=False),
    )
    options = result.get("options")
    if not isinstance(options, list):
        raise AgentError("灵感生成结果格式无效")
    return [item for item in options[:10] if isinstance(item, dict)]


async def style_trial(scene: str, styles: list[str], api_config: dict[str, str]) -> list[dict[str, Any]]:
    result = await complete_json(
        api_config,
        STYLE_TRIAL_PROMPT,
        json.dumps({"scene": scene, "styles": styles}, ensure_ascii=False),
    )
    trials = result.get("trials")
    if not isinstance(trials, list):
        raise AgentError("风格试写结果格式无效")
    return [item for item in trials[: len(styles)] if isinstance(item, dict)]


async def cross_genre_bridge(
    source_text: str,
    source_type: str,
    target_type: str,
    api_config: dict[str, str],
    source_language: str = "zh",
    target_language: str = "zh",
) -> dict[str, Any]:
    template = CROSS_GENRE_PROMPTS["translation"] if source_language != target_language else CROSS_GENRE_PROMPTS["default"]
    prompt = template.format(
        source_type=source_type,
        target_type=target_type,
        source_language=source_language,
        target_language=target_language,
        content=source_text,
    )
    return await complete_json(api_config, prompt, source_text)


def project_prompt_context(project_id: str) -> str:
    return memory_engine.prompt_context(project_id)


def compact_text(value: Any) -> str:
    if isinstance(value, dict):
        return "；".join(f"{key}：{compact_text(item)}" for key, item in value.items() if item not in (None, ""))
    if isinstance(value, list):
        return "；".join(filter(None, (compact_text(item) for item in value)))
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_paper_title(value: Any) -> str:
    title = compact_text(value)
    numerals = "0-9一二三四五六七八九十百千万零〇两"
    title = re.sub(rf"^第\s*[{numerals}]+\s*章[\s·:：—-]*", "", title)
    title = re.sub(rf"[\s·:：—-]*[（(]?第\s*[{numerals}]+\s*章[）)]?$", "", title)
    return title.strip() or "未命名篇章"
