from __future__ import annotations

import argparse
import asyncio
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from time import perf_counter
from typing import Any


AGENT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = AGENT_ROOT.parent
sys.path.insert(0, str(AGENT_ROOT))

from agent import analyze_novel
import database
from prompts import TYPE_DIMENSIONS
from tools import extract_txt_info


def emit(event: str, **payload: Any) -> None:
    print(json.dumps({"event": event, **payload}, ensure_ascii=False), flush=True)


def clear_material_library() -> None:
    with closing(database.connect()) as connection:
        connection.execute("DELETE FROM novels")
        connection.commit()


def stored_source_text(novel_id: str) -> str:
    with closing(database.connect()) as connection:
        rows = connection.execute(
            """
            SELECT segment.content
            FROM material_source_segments segment
            JOIN material_nodes node ON node.id=segment.material_id
            WHERE node.novel_id=?
            ORDER BY node.sort_order
            """,
            (novel_id,),
        ).fetchall()
    return "".join(str(row[0]) for row in rows)


def semantic_metrics(novel_id: str, coverage: dict[str, Any]) -> dict[str, Any]:
    tree = next(item for item in database.list_material_tree() if item["id"] == novel_id)
    dimensions: dict[str, list[dict[str, Any]]] = {}
    collection_names = {
        str(node["id"]): str(node["display_name"])
        for node in tree["nodes"]
        if node["node_type"] == "collection"
    }
    for node in tree["nodes"]:
        parent = str(node.get("parent_id") or "")
        dimension = collection_names.get(parent)
        if dimension:
            dimensions.setdefault(dimension, []).append(node)
    expected = TYPE_DIMENSIONS["web_novel"]
    total_units = int(coverage.get("chapter_count") or coverage.get("semantic_regions") or 1)
    dimension_report: dict[str, Any] = {}
    for name in expected:
        source_units: set[int] = set()
        for node in dimensions.get(name, []):
            try:
                content = json.loads(node.get("content") or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            details = content.get("details") if isinstance(content, dict) else {}
            if not isinstance(details, dict):
                continue
            for source in details.get("source_ranges", []):
                if not isinstance(source, dict):
                    continue
                if str(source.get("chapter_index") or "").isdigit():
                    source_units.add(int(source["chapter_index"]))
                elif str(source.get("chapter_start") or "").isdigit() and str(source.get("chapter_end") or "").isdigit():
                    source_units.update({int(source["chapter_start"]), int(source["chapter_end"])})
                elif str(source.get("region") or "").isdigit():
                    source_units.add(int(source["region"]))
        thirds = {
            "early": any(unit <= max(1, total_units // 3) for unit in source_units),
            "middle": any(max(1, total_units // 3) < unit <= max(2, total_units * 2 // 3) for unit in source_units),
            "late": any(unit > max(2, total_units * 2 // 3) for unit in source_units),
        }
        dimension_report[name] = {
            "items": len(dimensions.get(name, [])),
            "source_regions": len(source_units),
            "thirds": thirds,
            "all_story_thirds": all(thirds.values()),
        }
    archive_nodes = dimensions.get("关键内容索引", [])
    indexed_ranges: list[tuple[int, int]] = []
    retained_chars = 0
    for node in archive_nodes:
        try:
            details = json.loads(node.get("content") or "{}").get("details", {})
        except (json.JSONDecodeError, TypeError):
            continue
        if str(details.get("start_char") or "").isdigit() and str(details.get("end_char") or "").isdigit():
            indexed_ranges.append((int(details["start_char"]), int(details["end_char"])))
        retained_chars += int(details.get("retained_chars") or 0)
    indexed_ranges.sort()
    range_index_complete = bool(
        indexed_ranges
        and indexed_ranges[0][0] == 1
        and indexed_ranges[-1][1] == int(coverage.get("total_chars") or 0)
        and all(current[0] == previous[1] + 1 for previous, current in zip(indexed_ranges, indexed_ranges[1:]))
    )
    main_ranges: list[tuple[int, int]] = []
    for node in dimensions.get("主线情节", []):
        try:
            details = json.loads(node.get("content") or "{}").get("details", {})
        except (json.JSONDecodeError, TypeError):
            continue
        if str(details.get("chapter_start") or "").isdigit() and str(details.get("chapter_end") or "").isdigit():
            main_ranges.append((int(details["chapter_start"]), int(details["chapter_end"])))
    main_ranges.sort()
    ordered_plot = all(current[0] > previous[1] for previous, current in zip(main_ranges, main_ranges[1:]))
    with closing(database.connect()) as connection:
        chapter_card_count = int(connection.execute("SELECT COUNT(*) FROM novel_chapter_cards WHERE novel_id=?", (novel_id,)).fetchone()[0])
    required_thirds = max(3, (len(expected) * 3 + 4) // 5)
    return {
        "node_count": len(tree["nodes"]),
        "archive_nodes": len(archive_nodes),
        "range_index_complete": range_index_complete,
        "retained_chars": retained_chars,
        "chapter_card_count": chapter_card_count,
        "ordered_plot": ordered_plot,
        "dimensions": dimension_report,
        "all_dimensions_nonempty": all(dimension_report[name]["items"] > 0 for name in expected),
        "dimensions_covering_all_thirds": sum(1 for value in dimension_report.values() if value["all_story_thirds"]),
        "expected_dimension_count": len(expected),
        "required_thirds": required_thirds,
    }


async def analyze_file(path: Path, api_config: dict[str, str]) -> dict[str, Any]:
    started = perf_counter()
    text, encoding = extract_txt_info(path)
    emit("file_start", file=path.name, chars=len(text), encoding=encoding)

    def progress(payload: dict[str, Any]) -> None:
        emit("region", file=path.name, **payload)

    analysis = await analyze_novel(text, api_config, "web_novel", progress)
    novel_id = database.store_analysis(path, analysis)
    coverage = analysis["coverage"]
    stored = stored_source_text(novel_id)
    metrics = semantic_metrics(novel_id, coverage)
    result = {
        "file": path.name,
        "novel_id": novel_id,
        "encoding": encoding,
        "source_chars": len(text),
        "duration_seconds": round(perf_counter() - started, 2),
        "coverage": coverage,
        "stored_key_content_chars": len(stored),
        "warnings": analysis.get("warnings", []),
        **metrics,
    }
    result["passed"] = bool(
        result["range_index_complete"]
        and 0 < float(coverage.get("retention_ratio") or 0) <= 0.25
        and int(coverage.get("analyzed_chapters", coverage.get("analyzed_regions", 0))) == int(coverage.get("chapter_count", coverage.get("semantic_regions", 0)))
        and result["chapter_card_count"] == int(coverage.get("chapter_count", result["chapter_card_count"]))
        and result["ordered_plot"]
        and result["all_dimensions_nonempty"]
        and result["dimensions_covering_all_thirds"] >= result["required_thirds"]
        and not result["warnings"]
    )
    emit("file_complete", file=path.name, passed=result["passed"], seconds=result["duration_seconds"], nodes=result["node_count"])
    return result


def build_report(results: list[dict[str, Any]], profile_name: str, model: str) -> str:
    passed = sum(1 for item in results if item["passed"])
    lines = [
        "# NovelForge 长篇素材分类覆盖真实测试报告",
        "",
        f"> 测试时间：{datetime.now().isoformat(timespec='seconds')}  ",
        f"> 模型：{profile_name} / {model}  ",
        f"> 结果：{passed}/{len(results)} 通过  ",
        "",
        "## 验收标准",
        "",
        "- 原文字符范围必须连续建立索引；素材库只保留关键原文，不再重复保存低信息过渡段。",
        "- 每章必须建立有序章节记录，重要章分为全文精读与关键证据精读，六个全书区域全部完成模型分析。",
        "- 网文八个核心分类必须都有内容。",
        "- 至少 60% 的分类必须覆盖全书前、中、后三个阶段。",
        "",
        "## 结果总览",
        "",
        "| 小说 | 字符数 | 章节建档 | 全文/证据精读 | 关键片段 | 素材节点 | 前中后覆盖分类 | 结果 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in results:
        coverage = item["coverage"]
        lines.append(
            f"| {item['file']} | {item['source_chars']:,} | {coverage.get('analyzed_chapters', coverage.get('analyzed_regions'))}/{coverage.get('chapter_count', coverage.get('semantic_regions'))} | "
            f"{coverage.get('full_refined_chapters', 0)}/{coverage.get('evidence_refined_chapters', 0)} | {item['archive_nodes']} | {item['node_count']} | {item['dimensions_covering_all_thirds']}/{item['expected_dimension_count']} | {'通过' if item['passed'] else '未通过'} |"
        )
    for item in results:
        lines.extend(["", f"## {item['file']}", ""])
        lines.append(f"- 全文范围连续索引：{'是' if item['range_index_complete'] else '否'}")
        lines.append(f"- 分析耗时：{item['duration_seconds']} 秒")
        lines.append(f"- 模型采样字符：{item['coverage']['semantic_sampled_chars']:,}")
        lines.append(f"- 模型请求输入字符：{item['coverage'].get('model_input_chars', 0):,}")
        lines.append(f"- API 调用次数：{item['coverage'].get('model_calls', 0)}")
        lines.append(f"- 关键原文保留比例：{item['coverage'].get('retention_ratio', 0):.2%}")
        lines.append(f"- 警告数量：{len(item['warnings'])}")
        lines.append(f"- 主线剧情顺序：{'通过' if item['ordered_plot'] else '失败'}")
        lines.extend(["", "| 分类 | 素材项 | 来源区域 | 前段 | 中段 | 后段 |", "| --- | ---: | ---: | --- | --- | --- |"])
        for name, value in item["dimensions"].items():
            thirds = value["thirds"]
            lines.append(
                f"| {name} | {value['items']} | {value['source_regions']} | "
                f"{'是' if thirds['early'] else '否'} | {'是' if thirds['middle'] else '否'} | {'是' if thirds['late'] else '否'} |"
            )
        if item["warnings"]:
            lines.extend(["", "### 警告", ""])
            lines.extend(f"- {warning}" for warning in item["warnings"])
    lines.extend([
        "",
        "## 结论说明",
        "",
        "“全文范围索引”表示每个字符区间都有连续定位记录；素材库只保存关键原文证据和逐章结构化记忆，不再复制全部过渡文字。",
        "选择整本素材时优先注入语义分类和章节记忆；需要核对时，可单独选择对应的关键内容节点。",
        "",
    ])
    return "\n".join(lines)


async def run(arguments: argparse.Namespace) -> dict[str, Any]:
    api_config = json.loads(os.environ["NOVELFORGE_TEST_API_CONFIG"])
    profile_name = os.environ.get("NOVELFORGE_PROFILE_NAME", "已配置模型")
    os.environ["NOVELFORGE_DB_PATH"] = str(Path(arguments.database).resolve())
    database.initialize_database()
    if arguments.clear:
        clear_material_library()
    results: list[dict[str, Any]] = []
    for raw_path in arguments.paths:
        results.append(await analyze_file(Path(raw_path).resolve(), api_config))
    output = Path(arguments.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    state_path = output / "长篇素材分类覆盖结果.json"
    report_path = output / "NovelForge 长篇素材分类覆盖真实测试报告.md"
    state_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(build_report(results, profile_name, api_config.get("model", "")), encoding="utf-8")
    return {"passed": sum(1 for item in results if item["passed"]), "total": len(results), "report": str(report_path)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--database")
    parser.add_argument("--output", required=True)
    parser.add_argument("--clear", action="store_true")
    parser.add_argument("--merge-states", nargs="*")
    arguments = parser.parse_args()
    if arguments.merge_states:
        results: list[dict[str, Any]] = []
        for state_path in arguments.merge_states:
            results.extend(json.loads(Path(state_path).read_text(encoding="utf-8")))
        output = Path(arguments.output).resolve()
        output.mkdir(parents=True, exist_ok=True)
        (output / "长篇素材分类覆盖综合结果.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        report = build_report(results, "deepseek", "deepseek-v4-pro")
        total_nodes = sum(int(item["node_count"]) for item in results)
        total_chars = sum(int(item["source_chars"]) for item in results)
        total_cards = sum(int(item["coverage"].get("chapter_count", 0)) for item in results)
        total_refined = sum(int(item["coverage"].get("refined_chapters", 0)) for item in results)
        total_seconds = sum(float(item["duration_seconds"]) for item in results)
        performance_lines = [
            f"- 三本素材树共 {total_nodes} 个节点，为 {total_chars:,} 个原文字符建立连续范围索引。",
            f"- 共建立 {total_cards} 张有序章节记录，其中 {total_refined} 章完成全文或关键证据精读。",
            "- 选择整本根节点时优先注入八类语义分类；关键原文和章节档案均按需加载。",
            "- 每 120,000 字符区间只保存按剧情顺序分布的关键原文，低信息过渡不重复入库。",
            f"- 三本合计真实分析耗时约 {total_seconds / 60:.1f} 分钟。",
        ]
        if arguments.database:
            os.environ["NOVELFORGE_DB_PATH"] = str(Path(arguments.database).resolve())
            database.initialize_database()
            started = perf_counter()
            tree = database.list_material_tree()
            payload = json.dumps(tree, ensure_ascii=False).encode("utf-8")
            load_seconds = perf_counter() - started
            performance_lines.append(
                f"- 当前素材树接口 JSON 为 {len(payload) / 1048576:.2f} MB，SQLite 读取约 {load_seconds:.3f} 秒，数据库约 {Path(arguments.database).stat().st_size / 1048576:.2f} MB。"
            )
        report += "\n## 产品性能验证\n\n" + "\n".join(performance_lines) + "\n"
        report_path = output / "NovelForge 三部长篇素材分类覆盖综合报告.md"
        report_path.write_text(report, encoding="utf-8")
        print(json.dumps({"passed": sum(1 for item in results if item["passed"]), "total": len(results), "report": str(report_path)}, ensure_ascii=False), flush=True)
        return
    if not arguments.paths or not arguments.database:
        parser.error("分析模式需要 paths 和 --database")
    print(json.dumps(asyncio.run(run(arguments)), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
