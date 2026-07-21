from base64 import b64encode
from html import escape
from html.parser import HTMLParser
from io import BytesIO
import json
from pathlib import Path
import re
from typing import Any, Callable
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile
import xml.etree.ElementTree as ET

try:
    import jieba.analyse as jieba_analyse
except ModuleNotFoundError:
    jieba_analyse = None


SUPPORTED_EXTENSIONS = {".docx", ".pdf", ".txt", ".epub"}
MAX_ARCHIVE_TEXT_FILES = 5000
MAX_ARCHIVE_TEXT_BYTES = 300 * 1024 * 1024
TAG_STOP_WORDS = {
    "category",
    "content",
    "details",
    "name",
    "summary",
    "tags",
    "内容",
    "小说",
    "素材",
    "相关",
    "描述",
}


class FileParseError(Exception):
    pass


def validate_archive_members(members: list[Any], format_name: str) -> None:
    if len(members) > MAX_ARCHIVE_TEXT_FILES:
        raise FileParseError(f"{format_name} 文本文件数量异常，可能是损坏或恶意压缩包")
    expanded_bytes = sum(max(0, int(member.file_size)) for member in members)
    if expanded_bytes > MAX_ARCHIVE_TEXT_BYTES:
        raise FileParseError(f"{format_name} 解压后的文本超过 300 MB，已停止读取")


class HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if value:
            self.parts.append(value)


def text_quality(value: str) -> float:
    if not value:
        return float("-inf")
    printable = sum(character.isprintable() or character in "\r\n\t" for character in value) / len(value)
    cjk = sum("\u3400" <= character <= "\u9fff" for character in value) / len(value)
    kana = sum("\u3040" <= character <= "\u30ff" for character in value) / len(value)
    controls = sum(ord(character) < 32 and character not in "\r\n\t" for character in value) / len(value)
    mojibake = sum(value.count(marker) for marker in ("锟", "�", "縺", "繧", "譁")) / len(value)
    return printable + min(cjk, 0.35) + min(kana * 2, 0.45) - controls * 4 - mojibake * 3


def decode_text_bytes(data: bytes) -> tuple[str, str]:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "utf-16"
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    sample_size = 128000
    if len(data) > sample_size * 3:
        middle = max(0, len(data) // 2 - sample_size // 2)
        sample = data[:sample_size] + data[middle : middle + sample_size] + data[-sample_size:]
    else:
        sample = data
    candidates: list[tuple[float, str]] = []
    for encoding in ("gb18030", "big5", "shift_jis"):
        decoded_sample = sample.decode(encoding, errors="ignore")
        if decoded_sample:
            candidates.append((text_quality(decoded_sample), encoding))
    if not candidates:
        raise FileParseError("TXT 文件编码无法识别")
    for _, encoding in sorted(candidates, key=lambda item: item[0], reverse=True):
        try:
            return data.decode(encoding), encoding
        except UnicodeError:
            continue
    raise FileParseError("TXT 文件编码无法识别")


def extract_txt_info(path: Path) -> tuple[str, str]:
    return decode_text_bytes(path.read_bytes())


def extract_txt(path: Path) -> str:
    return extract_txt_info(path)[0]


def extract_docx(path: Path) -> str:
    try:
        with ZipFile(path) as archive:
            document_info = archive.getinfo("word/document.xml")
            validate_archive_members([document_info], "DOCX")
            document = archive.read(document_info)
    except FileParseError:
        raise
    except Exception as error:
        raise FileParseError("DOCX 文件结构损坏") from error
    root = ET.fromstring(document)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for paragraph in root.iter(f"{namespace}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{namespace}t"))
        if text.strip():
            paragraphs.append(text.strip())
    return "\n".join(paragraphs)


def extract_epub(path: Path) -> str:
    parts: list[str] = []
    try:
        with ZipFile(path) as archive:
            members = sorted(
                (
                    member
                    for member in archive.infolist()
                    if member.filename.lower().endswith((".xhtml", ".html", ".htm"))
                ),
                key=lambda member: member.filename,
            )
            validate_archive_members(members, "EPUB")
            for member in members:
                parser = HtmlTextExtractor()
                parser.feed(archive.read(member).decode("utf-8", errors="ignore"))
                parts.extend(parser.parts)
    except FileParseError:
        raise
    except Exception as error:
        raise FileParseError("EPUB 文件结构损坏") from error
    return "\n".join(parts)


def extract_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError as error:
        raise FileParseError("PDF 解析组件未安装，请安装 requirements.txt") from error
    try:
        return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    except Exception as error:
        raise FileParseError("PDF 文件解析失败") from error


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise FileParseError("仅支持 DOCX、PDF、TXT 和 EPUB")
    extractors = {
        ".txt": extract_txt,
        ".docx": extract_docx,
        ".epub": extract_epub,
        ".pdf": extract_pdf,
    }
    text = extractors[suffix](path).strip()
    if not text:
        raise FileParseError("文件中没有可识别的文字")
    return text


CHINESE_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def chapter_number(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    total = 0
    current = 0
    units = {"十": 10, "百": 100, "千": 1000}
    for character in value:
        if character in CHINESE_DIGITS:
            current = CHINESE_DIGITS[character]
        elif character in units:
            total += (current or 1) * units[character]
            current = 0
        else:
            return None
    return total + current if total + current > 0 else None


def detect_content_gaps(text: str) -> dict[str, Any]:
    headings = re.findall(r"第\s*([0-9一二两三四五六七八九十百千零〇]+)\s*章", text)
    numbers = [number for value in headings if (number := chapter_number(value)) is not None]
    unique = sorted(set(numbers))
    missing = [number for number in range(unique[0], unique[-1] + 1) if number not in unique] if len(unique) >= 2 else []
    return {
        "detected_chapters": unique,
        "missing_chapters": missing,
        "has_gaps": bool(missing),
        "options": ["generate_draft", "insert_placeholder", "user_supply"] if missing else [],
    }


PUBLICATION_SENSITIVE_TERMS = ("露骨性描写", "强制性行为", "未成年人色情", "仇恨煽动")


def check_compliance(text: str, custom_terms: list[str] | None = None) -> dict[str, Any]:
    terms = list(dict.fromkeys([*PUBLICATION_SENSITIVE_TERMS, *(custom_terms or [])]))
    findings = []
    for term in terms:
        if term and term in text:
            findings.append(
                {
                    "term": term,
                    "count": text.count(term),
                    "options": {
                        "implicit": "改为含蓄概述",
                        "metaphorical": "改为隐喻表达",
                        "author_only": "仅保留在作者自审版",
                    },
                }
            )
    return {"safe": not findings, "findings": findings, "profile": "publication"}


def analysis_excerpt(text: str, limit: int = 48000) -> str:
    if len(text) <= limit:
        return text
    segment_count = 6
    marker_budget = segment_count * 24
    segment_size = max(1, (limit - marker_budget) // segment_count)
    last_start = len(text) - segment_size
    chunks = []
    for index in range(segment_count):
        start = round(last_start * index / (segment_count - 1))
        chunks.append(f"[原文片段 {index + 1}/{segment_count}]\n{text[start:start + segment_size]}")
    return "\n\n".join(chunks)


CHAPTER_HEADING_PATTERN = re.compile(
    r"(?m)^\s*(第\s*[0-9一二三四五六七八九十百千万零〇两]+\s*[章节回卷部篇集].{0,60})\s*$"
)


def full_text_coverage_items(text: str, target_size: int = 120000) -> list[dict[str, object]]:
    if not text:
        return []
    items: list[dict[str, object]] = []
    total = (len(text) + target_size - 1) // target_size
    for index, start in enumerate(range(0, len(text), target_size), start=1):
        content = text[start : start + target_size]
        headings = [match.group(1).strip() for match in CHAPTER_HEADING_PATTERN.finditer(content)]
        heading_label = " → ".join(headings[:1] + headings[-1:]) if headings else "未识别到标准章节标题"
        items.append(
            {
                "name": f"全文片段 {index}/{total}",
                "category": "全文覆盖索引",
                "summary": (
                    f"完整保存原文字符区间 {start + 1}-{start + len(content)}，"
                    f"章节范围：{heading_label}。该节点用于精确回查，内容没有经过摘要删减。"
                ),
                "details": {
                    "start_char": start + 1,
                    "end_char": start + len(content),
                    "chapter_headings": headings,
                    "storage": "sqlite_segment",
                },
                "_source_content": content,
                "tags": ["全文覆盖", "原文索引", f"片段{index}"],
            }
        )
    return items


def _key_passage_candidates(text: str, offset: int = 0) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    for match in re.finditer(r".+?(?:[。！？!?]+|\n{2,}|$)", text, flags=re.S):
        raw = match.group(0)
        leading = len(raw) - len(raw.lstrip())
        cleaned = re.sub(r"\s+", " ", raw).strip()
        if not cleaned:
            continue
        raw_start = match.start() + leading
        for piece_start in range(0, len(cleaned), 900):
            passage = cleaned[piece_start : piece_start + 900]
            if not passage:
                continue
            start = offset + raw_start + piece_start
            keyword_hits = sum(passage.count(keyword) for keyword in CHAPTER_SIGNAL_KEYWORDS)
            score = (
                keyword_hits * 12
                + min(8, passage.count("！") * 2 + passage.count("？") * 2)
                + min(6, passage.count("“") + passage.count("："))
                + min(5, len(passage) // 120)
            )
            candidates.append(
                {
                    "start_char": start + 1,
                    "end_char": start + len(passage),
                    "content": passage,
                    "score": score,
                }
            )
    return candidates


def key_content_coverage_items(
    text: str,
    target_size: int = 120000,
    retention_limit: int = 12000,
) -> list[dict[str, object]]:
    """Index the whole source while retaining only concrete, representative passages."""
    if not text:
        return []
    items: list[dict[str, object]] = []
    total = (len(text) + target_size - 1) // target_size
    for index, start in enumerate(range(0, len(text), target_size), start=1):
        content = text[start : start + target_size]
        candidates = _key_passage_candidates(content, start)
        selected: dict[int, dict[str, object]] = {}
        bucket_count = min(10, max(1, len(candidates)))
        for bucket_index in range(bucket_count):
            bucket_start = start + round(len(content) * bucket_index / bucket_count)
            bucket_end = start + round(len(content) * (bucket_index + 1) / bucket_count)
            bucket = [
                candidate
                for candidate in candidates
                if bucket_start <= int(candidate["start_char"]) - 1 < bucket_end
            ]
            if bucket:
                best = max(bucket, key=lambda candidate: (int(candidate["score"]), len(str(candidate["content"]))))
                selected[int(best["start_char"])] = best
        retained = sum(len(str(candidate["content"])) for candidate in selected.values())
        for candidate in sorted(candidates, key=lambda item: (int(item["score"]), len(str(item["content"]))), reverse=True):
            candidate_start = int(candidate["start_char"])
            if candidate_start in selected:
                continue
            passage_size = len(str(candidate["content"]))
            if retained + passage_size > retention_limit:
                continue
            selected[candidate_start] = candidate
            retained += passage_size
            if retained >= retention_limit:
                break
        ordered = sorted(selected.values(), key=lambda candidate: int(candidate["start_char"]))
        headings = [match.group(1).strip() for match in CHAPTER_HEADING_PATTERN.finditer(content)]
        heading_label = " → ".join(headings[:1] + headings[-1:]) if headings else "未识别到标准章节标题"
        retained_content = "\n\n".join(
            f"【原文位置 {candidate['start_char']}-{candidate['end_char']}】\n{candidate['content']}"
            for candidate in ordered
        )
        items.append(
            {
                "name": f"关键内容 {index}/{total}",
                "category": "关键内容索引",
                "summary": (
                    f"索引原文字符区间 {start + 1}-{start + len(content)}，章节范围：{heading_label}。"
                    f"保留 {len(ordered)} 段具体关键内容，重复铺陈与低信息过渡不再全文复制。"
                ),
                "details": {
                    "start_char": start + 1,
                    "end_char": start + len(content),
                    "chapter_headings": headings,
                    "retained_passages": len(ordered),
                    "retained_chars": retained,
                    "storage": "sqlite_key_passages",
                },
                "_source_content": retained_content,
                "tags": ["全文索引", "关键原文", f"片段{index}"],
            }
        )
    return items


def semantic_analysis_regions(text: str, target_size: int = 500000) -> list[dict[str, object]]:
    if not text:
        return []
    regions: list[dict[str, object]] = []
    total = (len(text) + target_size - 1) // target_size
    for index, start in enumerate(range(0, len(text), target_size), start=1):
        content = text[start : start + target_size]
        headings = [match.group(1).strip() for match in CHAPTER_HEADING_PATTERN.finditer(content)]
        regions.append(
            {
                "index": index,
                "total": total,
                "start_char": start + 1,
                "end_char": start + len(content),
                "chapter_headings": headings,
                "content": content,
            }
        )
    return regions


CHAPTER_SIGNAL_KEYWORDS = (
    "发现", "决定", "真相", "秘密", "身份", "死亡", "牺牲", "背叛", "突破", "晋级",
    "觉醒", "获得", "失去", "击败", "战胜", "失败", "危机", "阴谋", "伏笔", "回归",
    "重逢", "离开", "加入", "建立", "毁灭", "复活", "承诺", "婚约", "关系", "规则",
)


def split_novel_chapters(text: str) -> list[dict[str, object]]:
    matches = list(CHAPTER_HEADING_PATTERN.finditer(text))
    if len(matches) < 3:
        fallback_size = 8000
        return [
            {
                "index": index,
                "title": f"连续片段 {index}",
                "start_char": start + 1,
                "end_char": min(len(text), start + fallback_size),
                "content": text[start : start + fallback_size],
            }
            for index, start in enumerate(range(0, len(text), fallback_size), start=1)
        ]
    chapters: list[dict[str, object]] = []
    preamble = text[: matches[0].start()]
    if preamble.strip():
        chapters.append(
            {
                "index": 1,
                "title": "正文前置内容",
                "start_char": 1,
                "end_char": matches[0].start(),
                "content": preamble,
            }
        )
    offset = len(chapters)
    for position, match in enumerate(matches):
        start = match.start()
        end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
        chapters.append(
            {
                "index": position + 1 + offset,
                "title": match.group(1).strip(),
                "start_char": start + 1,
                "end_char": end,
                "content": text[start:end],
            }
        )
    return chapters


def chapter_signal_excerpt(chapter: dict[str, object], limit: int = 700) -> str:
    content = re.sub(r"\s+", " ", str(chapter.get("content") or "")).strip()
    if len(content) <= limit:
        return content
    segment = max(80, limit // 5)
    middle_start = max(0, len(content) // 2 - segment // 2)
    selected = [content[:segment], content[middle_start : middle_start + segment], content[-segment:]]
    sentences = [part.strip() for part in re.split(r"(?<=[。！？!?])", content) if part.strip()]
    ranked = sorted(
        sentences,
        key=lambda sentence: (sum(sentence.count(keyword) for keyword in CHAPTER_SIGNAL_KEYWORDS), len(sentence)),
        reverse=True,
    )
    for sentence in ranked:
        if not any(keyword in sentence for keyword in CHAPTER_SIGNAL_KEYWORDS):
            break
        if sentence not in selected:
            selected.append(sentence[: segment * 2])
        if sum(len(item) for item in selected) >= limit:
            break
    return "\n".join(selected)[:limit]


def pack_chapter_inputs(
    chapters: list[dict[str, object]],
    limit: int = 40000,
    full_text: bool = False,
) -> list[list[dict[str, object]]]:
    batches: list[list[dict[str, object]]] = []
    current: list[dict[str, object]] = []
    used = 0
    for chapter in chapters:
        source = str(chapter.get("content") or "") if full_text else chapter_signal_excerpt(chapter)
        packet = {
            "index": int(chapter["index"]),
            "title": str(chapter["title"]),
            "start_char": int(chapter["start_char"]),
            "end_char": int(chapter["end_char"]),
            "source": source,
            "refine_mode": str(chapter.get("refine_mode") or ("full" if full_text else "evidence")),
        }
        required = len(source) + len(packet["title"]) + 80
        if current and used + required > limit:
            batches.append(current)
            current = []
            used = 0
        current.append(packet)
        used += required
    if current:
        batches.append(current)
    return batches


def render_chapter_batch(batch: list[dict[str, object]]) -> str:
    return "\n\n".join(
        f"[章节 {item['index']}] {item['title']}\n字符范围：{item['start_char']}-{item['end_char']}\n{item['source']}"
        for item in batch
    )


DIMENSION_KEYWORD_GROUPS = (
    (("角色", "人物", "主角", "写作者", "攻略对象", "英雄", "神灵"), ("人物", "角色", "主角", "姓名", "关系", "父亲", "母亲", "老师", "同伴", "敌人", "性格", "动机")),
    (("世界", "设定", "体系", "规则", "社会", "背景", "势力", "江湖", "帝国", "地下城"), ("世界", "规则", "力量", "等级", "能力", "组织", "势力", "城市", "国家", "学院", "宗门", "限制", "代价")),
    (("情节", "事件", "线索", "诡计", "冲突", "时刻", "节奏", "节点", "路径", "副本", "挑战"), ("事件", "线索", "发现", "决定", "冲突", "危机", "任务", "秘密", "真相", "结果", "原因", "之后")),
    (("科技", "物理", "魔法", "武功", "技能", "系统", "奖励", "属性", "金手指", "升级", "修炼", "实力"), ("科技", "规则", "能力", "技能", "系统", "任务", "奖励", "等级", "修炼", "突破", "武魂", "法术", "代价")),
    (("主题", "寓意", "心理", "情感", "关系", "语言", "风格", "意象"), ("主题", "情感", "心理", "选择", "欲望", "恐惧", "成长", "牺牲", "关系", "象征", "语言")),
)


def text_chunks(text: str, target_size: int = 1600) -> list[str]:
    normalized = re.sub(r"\r\n?", "\n", text)
    paragraphs = [re.sub(r"[ \t]+", " ", part).strip() for part in normalized.split("\n")]
    paragraphs = [part for part in paragraphs if part]
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0
    for paragraph in paragraphs:
        pieces = [paragraph[index : index + target_size] for index in range(0, len(paragraph), target_size)]
        for piece in pieces:
            if current and current_size + len(piece) > target_size:
                chunks.append("\n".join(current))
                current = []
                current_size = 0
            current.append(piece)
            current_size += len(piece)
    if current:
        chunks.append("\n".join(current))
    return chunks or [text.strip()]


def dimension_excerpt(text: str, dimension: str, limit: int = 12000) -> str:
    if len(text) <= limit:
        return text
    chunks = text_chunks(text)
    keywords = [dimension]
    for triggers, values in DIMENSION_KEYWORD_GROUPS:
        if any(trigger in dimension for trigger in triggers):
            keywords.extend(values)
    keywords = list(dict.fromkeys(keyword for keyword in keywords if keyword))
    anchors = {0, len(chunks) // 2, len(chunks) - 1}
    ranked = sorted(
        range(len(chunks)),
        key=lambda index: (
            sum(chunks[index].count(keyword) for keyword in keywords),
            -index,
        ),
        reverse=True,
    )
    selected = set(anchors)
    used = sum(len(chunks[index]) + 32 for index in selected)
    for index in ranked:
        if index in selected:
            continue
        required = len(chunks[index]) + 32
        if used + required > limit:
            continue
        selected.add(index)
        used += required
    ordered = sorted(selected)
    return "\n\n".join(
        f"[与{dimension}相关的原文片段 {position + 1}/{len(ordered)}]\n{chunks[index]}"
        for position, index in enumerate(ordered)
    )


def generate_tags(text: str, count: int = 3) -> list[str]:
    cleaned_text = re.sub(
        r'"(?:name|category|summary|details|tags)"\s*:',
        " ",
        text,
        flags=re.I,
    )
    candidates: list[str] = []
    if jieba_analyse:
        tfidf = jieba_analyse.extract_tags(cleaned_text, topK=count * 4)
        textrank = jieba_analyse.textrank(cleaned_text, topK=count * 4)
        for index in range(max(len(tfidf), len(textrank))):
            if index < len(tfidf):
                candidates.append(tfidf[index])
            if index < len(textrank):
                candidates.append(textrank[index])
    if not candidates:
        candidates = re.findall(r"[\u4e00-\u9fff]{2,8}", cleaned_text)
    tags: list[str] = []
    for candidate in candidates:
        tag = "".join(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]+", candidate))
        if len(tag) >= 2 and tag.casefold() not in TAG_STOP_WORDS and tag not in tags:
            tags.append(tag)
        if len(tags) == count:
            break
    return (tags + ["创作元素", "叙事设定", "情节线索"])[:count]


def combined_novel_text(chapters: list[dict[str, Any]]) -> str:
    return "\n\n".join(
        f"第{index}章 {chapter['title']}\n\n{chapter['content']}"
        for index, chapter in enumerate(chapters, start=1)
    )


def export_txt(chapters: list[dict[str, Any]]) -> bytes:
    return combined_novel_text(chapters).encode("utf-8-sig")


def export_epub(chapters: list[dict[str, Any]], title: str) -> bytes:
    output = BytesIO()
    identifier = str(uuid4())
    with ZipFile(output, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
            compress_type=ZIP_DEFLATED,
        )
        manifest = []
        spine = []
        nav_items = []
        for index, chapter in enumerate(chapters, start=1):
            file_name = f"chapter-{index}.xhtml"
            item_id = f"chapter-{index}"
            paragraphs = "".join(
                f"<p>{escape(paragraph)}</p>"
                for paragraph in str(chapter["content"]).splitlines()
                if paragraph.strip()
            )
            archive.writestr(
                f"OEBPS/{file_name}",
                f'<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>{escape(str(chapter["title"]))}</title><style>p{{text-indent:2em;line-height:1.8;margin-bottom:1.5em}}</style></head><body><h1>{escape(str(chapter["title"]))}</h1>{paragraphs}</body></html>',
                compress_type=ZIP_DEFLATED,
            )
            manifest.append(f'<item id="{item_id}" href="{file_name}" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="{item_id}"/>')
            nav_items.append(f'<li><a href="{file_name}">{escape(str(chapter["title"]))}</a></li>')
        archive.writestr(
            "OEBPS/nav.xhtml",
            f'<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>目录</title></head><body><nav epub:type="toc"><ol>{"".join(nav_items)}</ol></nav></body></html>',
            compress_type=ZIP_DEFLATED,
        )
        archive.writestr(
            "OEBPS/content.opf",
            f'<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="book-id">{identifier}</dc:identifier><dc:title>{escape(title)}</dc:title><dc:language>zh-CN</dc:language></metadata><manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>{"".join(manifest)}</manifest><spine>{"".join(spine)}</spine></package>',
            compress_type=ZIP_DEFLATED,
        )
    return output.getvalue()


def export_pdf(chapters: list[dict[str, Any]]) -> bytes:
    try:
        from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer
    except ModuleNotFoundError as error:
        raise RuntimeError("PDF 导出组件未安装，请安装 requirements.txt") from error
    output = BytesIO()
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    title_style = ParagraphStyle("Title", fontName="STSong-Light", fontSize=18, leading=26, alignment=TA_CENTER, spaceAfter=12 * mm)
    body_style = ParagraphStyle("Body", fontName="STSong-Light", fontSize=11, leading=19, alignment=TA_JUSTIFY, firstLineIndent=22, spaceAfter=16)
    document = SimpleDocTemplate(output, pagesize=A4, leftMargin=22 * mm, rightMargin=22 * mm, topMargin=20 * mm, bottomMargin=20 * mm)
    story = []
    for index, chapter in enumerate(chapters):
        if index:
            story.append(PageBreak())
        story.append(Paragraph(escape(str(chapter["title"])), title_style))
        for paragraph in str(chapter["content"]).splitlines():
            if paragraph.strip():
                story.extend((Paragraph(escape(paragraph), body_style), Spacer(1, 2 * mm)))
    document.build(story)
    return output.getvalue()


def export_novel(
    chapters: list[dict[str, Any]],
    export_format: str,
    file_name: str,
    report: Callable[[int, str], None],
) -> dict[str, str]:
    if not chapters:
        raise ValueError("没有可导出的篇章")
    report(20, "章节内容拼接完成")
    if export_format == "txt":
        data = export_txt(chapters)
    elif export_format == "epub":
        data = export_epub(chapters, file_name)
    elif export_format == "pdf":
        data = export_pdf(chapters)
    else:
        raise ValueError("不支持的导出格式")
    report(80, "导出文件生成完成")
    return {"content_base64": b64encode(data).decode("ascii"), "extension": export_format}
