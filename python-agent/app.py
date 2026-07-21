import asyncio
from contextlib import asynccontextmanager
import hmac
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
from queue import Queue
import sys
from time import perf_counter
from typing import AsyncIterator, Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
import uvicorn

from agent import (
    AgentError,
    analyze_novel,
    bounded_generation_history,
    create_paper_reply,
    cross_genre_bridge,
    generate_inspirations,
    get_mode_prompt,
    infer_user_preferences,
    paper_intent,
    project_prompt_context,
    requested_auto_collect_count,
    requests_whole_novel,
    stream_normal_reply,
    style_trial,
    test_api_connection,
    validate_universe_with_model,
    workflow_prompt,
)
from database import (
    abandon_paper,
    analyze_impact,
    branch_compare,
    branch_merge,
    chapter_summaries,
    chat_history,
    confirm_paper,
    create_branch,
    create_project,
    create_session,
    create_story_node,
    create_universe_rule,
    database_path,
    delete_chapter,
    delete_fact,
    delete_material,
    delete_project,
    delete_session,
    delete_story_node,
    delete_universe_rule,
    delete_user_message,
    edit_chapter,
    get_paper,
    get_project_for_session,
    import_universe_rules,
    initialize_database,
    list_chapters,
    list_facts,
    list_impacts,
    list_material_tree,
    list_messages,
    list_pinned_materials,
    list_projects,
    list_sessions,
    list_story_nodes,
    list_universe_rules,
    material_context,
    pin_material,
    pinned_context,
    rename_project,
    reorder_story_nodes,
    reorder_chapters,
    resolve_impact,
    save_assistant_message,
    save_user_message,
    store_analysis,
    switch_project,
    switch_session,
    set_project_status,
    unpin_material,
    update_session_mode,
    update_project_settings,
    update_story_node,
    update_universe_rule,
    upsert_fact,
    copy_story_node,
)
from models import (
    AnalyzeRequest,
    ApiConfig,
    BranchCompareRequest,
    BranchCreateRequest,
    BranchMergeRequest,
    BranchSwitchRequest,
    ChapterReorderRequest,
    ChapterUpdateRequest,
    ChatRequest,
    CrossBridgeRequest,
    ComplianceCheckRequest,
    ContentGapRequest,
    ExportRequest,
    FactUpsertRequest,
    ImpactAnalyzeRequest,
    InspirationRequest,
    ModeSwitchRequest,
    PinMaterialRequest,
    ProjectCreateRequest,
    ProjectSettingsRequest,
    ProjectStatusRequest,
    ProjectSwitchRequest,
    SessionCreateRequest,
    SessionSwitchRequest,
    StyleTrialRequest,
    StoryNodeCopyRequest,
    StoryNodeCreateRequest,
    StoryNodeReorderRequest,
    StoryNodeUpdateRequest,
    UniverseImportRequest,
    UniverseRuleCreate,
    UniverseRuleUpdate,
)
from tools import FileParseError, check_compliance, detect_content_gaps, export_novel, extract_text, extract_txt_info


LOG_PATH = database_path().parent.parent / "logs" / "agent.log"
AGENT_TOKEN = os.environ.get("NOVELFORGE_AGENT_TOKEN", "").strip()
AGENT_INSTANCE_ID = os.environ.get("NOVELFORGE_AGENT_INSTANCE_ID", "development").strip() or "development"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("novelforge.agent")
if not logger.handlers:
    handler = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


MODE_DESCRIPTIONS = {
    "guided": "主动提问、给建议、搭骨架",
    "collaborative": "共同讨论、多方案选择",
    "silent": "按明确指令直接执行",
    "traceable": "关键结论附来源标注",
    "teaching": "执行同时讲解写作方法",
}


def sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def stream_headers() -> dict[str, str]:
    return {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def as_http_error(error: ValueError, status_code: int = 400) -> HTTPException:
    return HTTPException(status_code=status_code, detail=str(error))


async def api_config_for_project(config: ApiConfig, project_id: str | None) -> dict[str, Any]:
    result = config.model_dump()
    if not project_id:
        result["privacy_mode"] = "standard"
        return result
    projects = await asyncio.to_thread(list_projects)
    project = next((item for item in projects if item["id"] == project_id), None)
    if not project:
        raise AgentError("作品不存在")
    result["privacy_mode"] = project.get("settings", {}).get("privacy_mode", "standard")
    return result


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="NovelForge Python Agent", version="2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:1420",
        "http://localhost:1420",
        "http://tauri.localhost",
        "https://tauri.localhost",
        "tauri://localhost",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def authenticate_local_client(request: Request, call_next):
    if AGENT_TOKEN and request.method != "OPTIONS" and request.url.path != "/health":
        expected = f"Bearer {AGENT_TOKEN}"
        provided = request.headers.get("Authorization", "")
        if not hmac.compare_digest(provided, expected):
            return JSONResponse(status_code=401, content={"detail": "Agent 访问凭据无效"})
    return await call_next(request)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": "2.0", "instance_id": AGENT_INSTANCE_ID}


@app.post("/api/test")
async def test_api(config: ApiConfig) -> dict[str, object]:
    if config.provider == "openai" and not config.api_key.strip():
        raise HTTPException(status_code=400, detail="OpenAI 配置需要 API Key")
    try:
        return await test_api_connection(config.model_dump())
    except AgentError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


def validated_paths(request: AnalyzeRequest) -> tuple[list[Path], dict[str, str]]:
    paths: list[Path] = []
    for raw_path in dict.fromkeys(request.paths):
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"文件不存在：{path.name}")
        paths.append(path)
    hints = {
        str(Path(raw_path).expanduser().resolve()): hint.strip()
        for raw_path, hint in request.genre_hints.items()
        if hint.strip()
    }
    return paths, hints


async def analysis_events(request: AnalyzeRequest, paths: list[Path], hints: dict[str, str]) -> AsyncIterator[str]:
    imports: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    total_files = len(paths)
    api_config = await api_config_for_project(request.api_config, request.project_id)
    for file_index, path in enumerate(paths, start=1):
        started = perf_counter()
        yield sse("progress", {"file": path.name, "step": file_index, "total": total_files, "dimension": "读取文本", "status": "analyzing"})
        try:
            if path.suffix.lower() == ".txt":
                text, encoding = await asyncio.to_thread(extract_txt_info, path)
                yield sse("progress", {"file": path.name, "step": file_index, "total": total_files, "dimension": "文本编码", "status": "done", "encoding": encoding, "converted": encoding not in {"utf-8", "utf-8-sig"}})
            else:
                text = await asyncio.to_thread(extract_text, path)
            yield sse("progress", {"file": path.name, "step": file_index, "total": total_files, "dimension": "类型与维度路由", "status": "analyzing"})
            region_progress: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

            def report_region(payload: dict[str, Any]) -> None:
                region_progress.put_nowait(payload)

            analysis_task = asyncio.create_task(
                analyze_novel(text, api_config, hints.get(str(path), ""), report_region)
            )
            while not analysis_task.done() or not region_progress.empty():
                try:
                    region = await asyncio.wait_for(region_progress.get(), timeout=0.5)
                except TimeoutError:
                    continue
                yield sse(
                    "progress",
                    {
                        "file": path.name,
                        "step": region.get("step", region.get("region", 1)),
                        "total": region["total"],
                        "dimension": (
                            f"章节建档 {region.get('step', 1)}/{region['total']}"
                            if region.get("stage") == "chapter_cards"
                            else f"重要章精读 {region.get('step', 1)}/{region['total']}"
                            if region.get("stage") == "important_refine"
                            else f"全书区域 {region.get('step', 1)}/{region['total']}"
                            if region.get("stage") == "macro_regions"
                            else f"区域重试 {region.get('step', 1)}/{region['total']}"
                            if region.get("stage") == "macro_retry"
                            else f"长篇区域 {region.get('region', 1)}/{region['total']}"
                        ),
                        "status": region["status"],
                        "message": region.get("message", ""),
                    },
                )
            analysis = await analysis_task
            for dimension_index, dimension in enumerate(analysis["dimensions"], start=1):
                yield sse(
                    "progress",
                    {
                        "file": path.name,
                        "step": dimension_index,
                        "total": len(analysis["dimensions"]),
                        "dimension": dimension["name"],
                        "status": "done",
                    },
                )
            novel_id = await asyncio.to_thread(store_analysis, path, analysis)
            item = {
                "novel_id": novel_id,
                "file_name": path.name,
                "primary_type": analysis["primary_type"],
                "type_source": analysis["type_source"],
                "dimensions": len(analysis["dimensions"]),
                "warnings": analysis.get("warnings", []),
                "coverage": analysis.get("coverage", {}),
                "duration_ms": round((perf_counter() - started) * 1000),
            }
            imports.append(item)
            logger.info("analysis completed file=%s dimensions=%s duration_ms=%s", path.name, item["dimensions"], item["duration_ms"])
        except (FileParseError, AgentError) as error:
            errors.append({"file_name": path.name, "message": str(error)})
            yield sse("progress", {"file": path.name, "step": file_index, "total": total_files, "dimension": "分析", "status": "error", "message": str(error)})
            logger.warning("analysis failed file=%s error=%s", path.name, error)
        except Exception:
            errors.append({"file_name": path.name, "message": "文件分析失败，请检查格式"})
            logger.exception("analysis crashed file=%s", path.name)
    payload = {
        "novel_ids": [item["novel_id"] for item in imports],
        "imports": imports,
        "errors": errors,
        "materials": await asyncio.to_thread(list_material_tree),
    }
    if imports:
        yield sse("done", payload)
    else:
        yield sse("error", {"message": errors[0]["message"] if len(errors) == 1 else "全部文件分析失败", **payload})


@app.post("/analyze")
async def analyze(request: AnalyzeRequest) -> StreamingResponse:
    paths, hints = validated_paths(request)
    return StreamingResponse(analysis_events(request, paths, hints), media_type="text/event-stream", headers=stream_headers())


@app.get("/materials")
async def materials() -> list[dict[str, Any]]:
    return await asyncio.to_thread(list_material_tree)


@app.delete("/materials/{material_id}")
async def remove_material(material_id: str) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(delete_material, material_id)
    except ValueError as error:
        raise as_http_error(error, 404) from error


@app.get("/projects")
async def projects(include_archived: bool = True) -> list[dict[str, Any]]:
    return await asyncio.to_thread(list_projects, include_archived)


@app.post("/projects")
async def new_project(request: ProjectCreateRequest) -> dict[str, Any]:
    try:
        project = await asyncio.to_thread(create_project, request.title, request.mode.value)
        return {"project": project, "sessions": await asyncio.to_thread(list_sessions, project["id"]), "messages": []}
    except ValueError as error:
        raise as_http_error(error) from error


@app.put("/projects/{project_id}")
async def update_project(project_id: str, request: ProjectCreateRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(rename_project, project_id, request.title)
    except ValueError as error:
        raise as_http_error(error, 404) from error


@app.patch("/projects/{project_id}/settings")
async def project_settings(project_id: str, request: ProjectSettingsRequest) -> dict[str, Any]:
    settings = {key: value for key, value in request.model_dump().items() if value is not None}
    try:
        return await asyncio.to_thread(update_project_settings, project_id, settings)
    except ValueError as error:
        raise as_http_error(error, 404) from error


@app.post("/projects/{project_id}/status")
async def project_status(project_id: str, request: ProjectStatusRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(set_project_status, project_id, request.status)
    except ValueError as error:
        raise as_http_error(error, 404) from error


@app.delete("/projects/{project_id}")
async def remove_project(project_id: str) -> dict[str, str]:
    try:
        return {"active_session_id": await asyncio.to_thread(delete_project, project_id)}
    except ValueError as error:
        raise as_http_error(error) from error


@app.post("/project/switch")
async def change_project(request: ProjectSwitchRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(switch_project, request.project_id)
    except ValueError as error:
        raise as_http_error(error, 404) from error


@app.get("/sessions")
async def sessions(project_id: str | None = Query(default=None)) -> list[dict[str, Any]]:
    return await asyncio.to_thread(list_sessions, project_id)


@app.post("/sessions")
async def new_session(request: SessionCreateRequest) -> dict[str, Any]:
    try:
        project_id = request.project_id
        if not project_id:
            active = next((project for project in await asyncio.to_thread(list_projects) if project["active"]), None)
            if not active:
                raise ValueError("没有可用作品")
            project_id = str(active["id"])
        session = await asyncio.to_thread(create_session, project_id, request.title, request.mode.value)
        return {"session": session, "messages": []}
    except ValueError as error:
        raise as_http_error(error) from error


@app.delete("/sessions/{session_id}")
async def remove_session(session_id: str) -> dict[str, str]:
    try:
        return {"active_session_id": await asyncio.to_thread(delete_session, session_id)}
    except ValueError as error:
        raise as_http_error(error) from error


@app.post("/session/switch")
async def change_session(request: SessionSwitchRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(switch_session, request.session_id)
    except ValueError as error:
        raise as_http_error(error, 404) from error


@app.post("/mode/switch")
async def switch_mode(request: ModeSwitchRequest) -> dict[str, Any]:
    try:
        session = await asyncio.to_thread(update_session_mode, request.session_id, request.mode.value)
        return {"mode": request.mode.value, "description": MODE_DESCRIPTIONS[request.mode.value], "session": session}
    except ValueError as error:
        raise as_http_error(error, 404) from error


@app.post("/branch/create")
async def branch_create(request: BranchCreateRequest) -> dict[str, Any]:
    try:
        branch = await asyncio.to_thread(create_branch, request.project_id, request.source_session_id, request.name, request.description)
        return {"branch": branch, "branch_id": branch["id"], "messages": await asyncio.to_thread(chat_history, branch["id"], 200)}
    except ValueError as error:
        raise as_http_error(error) from error


@app.post("/branch/switch")
async def branch_switch(request: BranchSwitchRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(switch_session, request.session_id)
    except ValueError as error:
        raise as_http_error(error, 404) from error


@app.post("/branch/compare")
async def compare_branches(request: BranchCompareRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(branch_compare, request.project_id, request.branch_a_id, request.branch_b_id)
    except ValueError as error:
        raise as_http_error(error) from error


@app.post("/branch/merge")
async def merge_branches(request: BranchMergeRequest) -> dict[str, Any]:
    try:
        result = await asyncio.to_thread(branch_merge, request.project_id, request.source_session_id, request.target_session_id)
        return {"status": "merged", "comparison": result}
    except ValueError as error:
        raise as_http_error(error, 409) from error


@app.get("/story/nodes")
async def story_nodes(project_id: str = Query(min_length=1), session_id: str | None = None) -> list[dict[str, Any]]:
    return await asyncio.to_thread(list_story_nodes, project_id, session_id)


@app.post("/story/nodes")
async def add_story_node(request: StoryNodeCreateRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            create_story_node,
            request.project_id,
            request.layer,
            request.title,
            request.content,
            session_id=request.session_id,
            parent_id=request.parent_id,
            node_type=request.node_type,
            metadata=request.metadata,
            locked=request.locked,
        )
    except ValueError as error:
        raise as_http_error(error) from error


@app.put("/story/nodes/{node_id}")
async def edit_story_node(node_id: str, request: StoryNodeUpdateRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            update_story_node,
            node_id,
            title=request.title,
            content=request.content,
            metadata=request.metadata,
            locked=request.locked,
        )
    except ValueError as error:
        raise as_http_error(error, 404) from error


@app.delete("/story/nodes/{node_id}")
async def remove_story_node(node_id: str) -> dict[str, str]:
    try:
        await asyncio.to_thread(delete_story_node, node_id)
        return {"status": "ok"}
    except ValueError as error:
        raise as_http_error(error, 404) from error


@app.post("/story/nodes/{node_id}/copy")
async def duplicate_story_node(node_id: str, request: StoryNodeCopyRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(copy_story_node, node_id, request.target_project_id, request.target_parent_id, request.session_id)
    except ValueError as error:
        raise as_http_error(error) from error


@app.post("/story/nodes/reorder")
async def story_nodes_reorder(request: StoryNodeReorderRequest) -> list[dict[str, Any]]:
    try:
        return await asyncio.to_thread(reorder_story_nodes, request.project_id, request.parent_id, request.node_ids)
    except ValueError as error:
        raise as_http_error(error) from error


@app.get("/pin/material")
async def pinned_materials(project_id: str = Query(min_length=1)) -> list[dict[str, Any]]:
    return await asyncio.to_thread(list_pinned_materials, project_id)


@app.post("/pin/material")
async def add_pinned_material(request: PinMaterialRequest) -> dict[str, Any]:
    try:
        return {"pinned_list": await asyncio.to_thread(pin_material, request.project_id, request.material_id, request.priority)}
    except ValueError as error:
        raise as_http_error(error) from error


@app.delete("/pin/material/{material_id}")
@app.delete("/unpin/material/{material_id}")
async def remove_pinned_material(material_id: str, project_id: str = Query(min_length=1)) -> dict[str, Any]:
    return {"pinned_list": await asyncio.to_thread(unpin_material, project_id, material_id)}


@app.get("/universe/rules")
async def universe_rules(project_id: str = Query(min_length=1)) -> list[dict[str, Any]]:
    return await asyncio.to_thread(list_universe_rules, project_id)


@app.post("/universe/rule")
async def add_universe_rule(request: UniverseRuleCreate) -> dict[str, Any]:
    try:
        rule = await asyncio.to_thread(
            create_universe_rule,
            request.project_id,
            request.category,
            request.key,
            request.value,
            request.source,
            request.immutable,
        )
        return {"rule": rule, "rule_id": rule["id"]}
    except ValueError as error:
        raise as_http_error(error) from error


@app.put("/universe/rule/{rule_id}")
async def edit_universe_rule(rule_id: str, request: UniverseRuleUpdate) -> dict[str, Any]:
    try:
        return {"rule": await asyncio.to_thread(update_universe_rule, rule_id, request.key, request.value, request.immutable)}
    except ValueError as error:
        raise as_http_error(error) from error


@app.delete("/universe/rule/{rule_id}")
async def remove_universe_rule(rule_id: str) -> dict[str, str]:
    try:
        await asyncio.to_thread(delete_universe_rule, rule_id)
        return {"status": "ok"}
    except ValueError as error:
        raise as_http_error(error) from error


@app.post("/universe/import")
async def import_rules(request: UniverseImportRequest) -> dict[str, Any]:
    try:
        rules = await asyncio.to_thread(import_universe_rules, request.source_project_id, request.target_project_id)
        return {"imported_count": len(rules), "rules": rules}
    except ValueError as error:
        raise as_http_error(error) from error


@app.get("/facts")
async def facts(project_id: str = Query(min_length=1)) -> list[dict[str, Any]]:
    return await asyncio.to_thread(list_facts, project_id)


@app.post("/facts")
async def save_fact(request: FactUpsertRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(upsert_fact, request.project_id, request.category, request.key, request.value, request.source)
    except ValueError as error:
        raise as_http_error(error) from error


@app.delete("/facts/{fact_id}")
async def remove_fact(fact_id: str) -> dict[str, str]:
    try:
        await asyncio.to_thread(delete_fact, fact_id)
        return {"status": "ok"}
    except ValueError as error:
        raise as_http_error(error, 404) from error


@app.get("/impact")
async def impacts(project_id: str = Query(min_length=1), unresolved_only: bool = False) -> list[dict[str, Any]]:
    return await asyncio.to_thread(list_impacts, project_id, unresolved_only)


@app.post("/impact/analyze")
async def analyze_change_impact(request: ImpactAnalyzeRequest) -> dict[str, Any]:
    try:
        affected = await asyncio.to_thread(analyze_impact, request.project_id, request.changed_node_id, request.change_type)
        return {"affected_nodes": affected}
    except ValueError as error:
        raise as_http_error(error) from error


@app.post("/impact/{impact_id}/resolve")
async def mark_impact_resolved(impact_id: str) -> dict[str, str]:
    try:
        await asyncio.to_thread(resolve_impact, impact_id)
        return {"status": "ok"}
    except ValueError as error:
        raise as_http_error(error, 404) from error


@app.post("/inspiration/generate")
async def inspiration(request: InspirationRequest) -> dict[str, Any]:
    try:
        premise = request.premise
        if request.project_id:
            premise += f"\n\n{await asyncio.to_thread(project_prompt_context, request.project_id)}"
        api_config = await api_config_for_project(request.api_config, request.project_id)
        return {"options": await generate_inspirations(premise, request.dilemma, api_config)}
    except AgentError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/style/trial")
async def trial(request: StyleTrialRequest) -> dict[str, Any]:
    try:
        scene = request.scene
        if request.project_id:
            scene += f"\n\n{await asyncio.to_thread(project_prompt_context, request.project_id)}"
        api_config = await api_config_for_project(request.api_config, request.project_id)
        return {"trials": await style_trial(scene, request.styles, api_config)}
    except AgentError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/cross/bridge")
async def bridge(request: CrossBridgeRequest) -> dict[str, Any]:
    try:
        return await cross_genre_bridge(
            request.source_text,
            request.source_type,
            request.target_type,
            request.api_config.model_dump(),
            request.source_language,
            request.target_language,
        )
    except AgentError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/content/gaps")
async def content_gaps(request: ContentGapRequest) -> dict[str, Any]:
    return await asyncio.to_thread(detect_content_gaps, request.text)


@app.post("/compliance/check")
async def compliance_check(request: ComplianceCheckRequest) -> dict[str, Any]:
    return await asyncio.to_thread(check_compliance, request.text, request.custom_terms)


async def chat_events(request: ChatRequest) -> AsyncIterator[str]:
    assistant_id = request.regenerate_assistant_id or str(uuid4())
    user_message: dict[str, Any] | None = None
    completed = False
    auto_collected: list[dict[str, Any]] = []
    affected_nodes: list[dict[str, Any]] = []
    last_saved_message: dict[str, Any] | None = None

    async def rollback_user_message() -> None:
        nonlocal user_message
        if user_message:
            await asyncio.to_thread(delete_user_message, str(user_message["id"]), request.session_id)
            user_message = None

    try:
        project_id = await asyncio.to_thread(get_project_for_session, request.session_id)
        if request.project_id and request.project_id != project_id:
            raise AgentError("会话不属于指定作品")
        session = next((item for item in await asyncio.to_thread(list_sessions, project_id) if item["id"] == request.session_id), None)
        project = next((item for item in await asyncio.to_thread(list_projects) if item["id"] == project_id), None)
        preferences = infer_user_preferences(request.message)
        inferred_mode = preferences.pop("mode", None)
        if preferences:
            project = await asyncio.to_thread(update_project_settings, project_id, preferences)
        api_config = request.api_config.model_dump()
        api_config["privacy_mode"] = (project or {}).get("settings", {}).get("privacy_mode", "standard")
        mode = str(inferred_mode or (request.mode.value if request.mode else (session or {}).get("mode") or "guided"))
        if request.mode or inferred_mode:
            await asyncio.to_thread(update_session_mode, request.session_id, mode)
        yield sse("mode", {"mode": mode, "description": MODE_DESCRIPTIONS[mode], "prompt": get_mode_prompt(mode)})
        yield sse("workflow", {"settings": (project or {}).get("settings", {}), "guidance": workflow_prompt((project or {}).get("settings", {}))})
        history = await asyncio.to_thread(chat_history, request.session_id, 40, request.regenerate_assistant_id)
        if history and history[-1]["role"] == "user" and history[-1]["content"] == request.message:
            history = history[:-1]
        if not request.regenerate_assistant_id:
            user_message = await asyncio.to_thread(save_user_message, request.session_id, request.message, request.selected_material_ids)
        yield sse("start", {"assistant_message_id": assistant_id, "user_message": user_message})
        yield sse("stage", {"code": "context", "message": "正在整理素材、作品记忆和最近对话…"})
        temporary_context, permanent_context, memory_context = await asyncio.gather(
            asyncio.to_thread(material_context, request.selected_material_ids),
            asyncio.to_thread(pinned_context, project_id),
            asyncio.to_thread(project_prompt_context, project_id),
        )
        context = "\n\n".join(part for part in (temporary_context, permanent_context) if part)
        memory_context = "\n\n".join(part for part in (memory_context, workflow_prompt((project or {}).get("settings", {}))) if part)
        auto_collect_count = (
            requested_auto_collect_count(request.message)
            if not request.paper_source_message_id and request.creation_action != "modify"
            else 0
        )
        whole_novel_plan = requests_whole_novel(request.message) and request.creation_action in {"auto", "create"} and not auto_collect_count
        if whole_novel_plan:
            intent = {"should_create": False, "mode": "create", "reason": "超长小说改为分章规划"}
        elif auto_collect_count:
            intent = {"should_create": True, "mode": "create", "reason": "用户明确要求批量生成并直接收录"}
        elif request.creation_action == "discuss":
            intent = {"should_create": False, "mode": "create", "reason": "用户选择讨论"}
        elif request.creation_action in {"create", "continue", "modify"}:
            intent = {"should_create": True, "mode": "modify" if request.creation_action == "modify" else "create", "reason": "用户选择明确创作动作"}
        else:
            intent = await paper_intent(request.message, api_config)
        conflicts: list[dict[str, str]] = []
        if intent["should_create"]:
            generation_history = bounded_generation_history(history)
            generation_action = "modify" if intent["mode"] == "modify" else request.creation_action if request.creation_action in {"create", "continue"} else "create"
            if auto_collect_count and await asyncio.to_thread(list_chapters, project_id):
                generation_action = "continue"
            source_paper = await asyncio.to_thread(get_paper, request.paper_source_message_id) if request.paper_source_message_id else None
            batch_count = auto_collect_count or 1
            if auto_collect_count:
                yield sse("auto_collect_start", {"total": batch_count, "limit": 10})
            for batch_index in range(batch_count):
                current_number = batch_index + 1
                current_assistant_id = assistant_id if batch_index == 0 else str(uuid4())
                current_action = generation_action if batch_index == 0 else "continue"
                current_command = request.message
                if auto_collect_count:
                    current_command = (
                        f"批量任务原始要求：{request.message}\n\n"
                        f"当前只执行第 {current_number}/{batch_count} 章。一次调用只能生成这一章，禁止把多章合并在同一篇正文中。"
                        "必须承接刚刚自动收录的上一章，推进新事件，不得复述前文；本章完成后将直接收入篇章。"
                    )
                yield sse(
                    "stage",
                    {
                        "code": "writing",
                        "message": f"正在生成并核对第 {current_number}/{batch_count} 章…" if auto_collect_count else "正在核对连续性并生成完整篇章…",
                    },
                )
                retry_delays = (5, 15, 30)
                chapter_retry_delays = (1, 3, 8)
                result: dict[str, Any] | None = None
                for retry_index in range(len(chapter_retry_delays) + 1):
                    try:
                        result = await create_paper_reply(
                            current_command,
                            generation_history,
                            context,
                            api_config,
                            source_paper if batch_index == 0 else None,
                            await asyncio.to_thread(chapter_summaries, project_id),
                            intent["mode"],
                            "\n\n".join(
                                part
                                for part in (
                                    await asyncio.to_thread(project_prompt_context, project_id),
                                    workflow_prompt((project or {}).get("settings", {})),
                                )
                                if part
                            ),
                            current_action,
                            request.chapter_target_words,
                        )
                        break
                    except AgentError as error:
                        reason = str(error)
                        format_error = any(marker in reason for marker in ("结构化内容", "篇章分隔协议", "完整篇章", "无法识别的内容"))
                        network_error = any(marker in reason for marker in ("无法连接模型服务", "模型响应超时", "HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504"))
                        if not (format_error or network_error) or retry_index >= len(chapter_retry_delays):
                            raise
                        delay = chapter_retry_delays[retry_index]
                        logger.warning(
                            "batch chapter retry session=%s chapter=%s/%s attempt=%s delay=%ss reason=%s",
                            request.session_id,
                            current_number,
                            batch_count,
                            retry_index + 1,
                            delay,
                            error,
                        )
                        yield sse(
                            "stage",
                            {
                                "code": "format_retry" if format_error else "network_retry",
                                "message": (
                                    f"第 {current_number}/{batch_count} 章返回格式不完整，{delay} 秒后重新生成当前章…"
                                    if format_error
                                    else f"第 {current_number}/{batch_count} 章连接模型失败，{delay} 秒后自动重试…"
                                ),
                            },
                        )
                        await asyncio.sleep(delay)
                if result is None:
                    raise AgentError("模型没有返回篇章内容")
                yield sse(
                    "stage",
                    {
                        "code": "validation",
                        "message": f"正在校验并收录第 {current_number}/{batch_count} 章…" if auto_collect_count else "正在检查长度、宇宙铁律和篇章记忆…",
                    },
                )
                current_conflicts: list[dict[str, str]] | None = None
                for retry_index in range(len(retry_delays) + 1):
                    try:
                        current_conflicts = await validate_universe_with_model(
                            await asyncio.to_thread(list_universe_rules, project_id),
                            result["paper"]["content"],
                            api_config,
                        )
                        break
                    except AgentError as error:
                        recoverable = any(marker in str(error) for marker in ("无法连接模型服务", "模型响应超时", "HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504"))
                        if not recoverable or retry_index >= len(retry_delays):
                            raise
                        delay = retry_delays[retry_index]
                        logger.warning(
                            "batch validation retry session=%s chapter=%s/%s attempt=%s delay=%ss reason=%s",
                            request.session_id,
                            current_number,
                            batch_count,
                            retry_index + 1,
                            delay,
                            error,
                        )
                        yield sse(
                            "stage",
                            {
                                "code": "network_retry",
                                "message": f"第 {current_number}/{batch_count} 章校验连接失败，{delay} 秒后自动重试…",
                            },
                        )
                        await asyncio.sleep(delay)
                if current_conflicts is None:
                    raise AgentError("宇宙铁律校验没有返回结果")
                if current_conflicts:
                    raise AgentError("生成内容违反宇宙铁律：" + "；".join(item["rule_key"] for item in current_conflicts))
                conflicts.extend(current_conflicts)
                if (project or {}).get("settings", {}).get("compliance_level") in {"publication", "custom"}:
                    custom_terms = (project or {}).get("settings", {}).get("metadata", {}).get("sensitive_terms", [])
                    compliance = await asyncio.to_thread(check_compliance, result["paper"]["content"], custom_terms if isinstance(custom_terms, list) else [])
                    yield sse("compliance", {**compliance, "batch_index": current_number, "batch_total": batch_count})
                last_saved_message = await asyncio.to_thread(
                    save_assistant_message,
                    request.session_id,
                    result["text"],
                    result["paper"],
                    current_assistant_id,
                )
                if auto_collect_count:
                    confirmed = await asyncio.to_thread(confirm_paper, current_assistant_id)
                    affected = await asyncio.to_thread(analyze_impact, project_id, confirmed["chapter"]["id"], "insert")
                    affected_nodes.extend(affected)
                    last_saved_message = next(
                        (item for item in await asyncio.to_thread(list_messages, request.session_id) if item["id"] == current_assistant_id),
                        last_saved_message,
                    )
                    auto_collected.append(
                        {
                            "message_id": current_assistant_id,
                            "chapter": confirmed["chapter"],
                            "paper": confirmed["paper"],
                            "affected_nodes": affected,
                        }
                    )
                    yield sse(
                        "auto_collected",
                        {
                            "index": current_number,
                            "total": batch_count,
                            "message_id": current_assistant_id,
                            "title": confirmed["chapter"]["title"],
                            "chapter_id": confirmed["chapter"]["id"],
                            "word_count": confirmed["paper"].get("word_count"),
                        },
                    )
                else:
                    for index in range(0, len(result["text"]), 4):
                        yield sse("delta", {"content": result["text"][index : index + 4]})
                        await asyncio.sleep(0.02)
                    yield sse("paper", {"message_id": current_assistant_id, "paper": result["paper"]})
            message = last_saved_message
            if auto_collect_count:
                yield sse(
                    "auto_collect_done",
                    {
                        "completed": len(auto_collected),
                        "total": batch_count,
                        "chapter_ids": [item["chapter"]["id"] for item in auto_collected],
                    },
                )
        else:
            yield sse("stage", {"code": "planning" if whole_novel_plan else "replying", "message": "正在规划可执行的分章创作路线…" if whole_novel_plan else "正在结合素材组织回复…"})
            chunks: list[str] = []
            effective_message = (
                "用户希望创作一部长篇或超长小说。不要在一次回复中声称已经写完整本；请先给出可执行的创作蓝图，包括核心设定、主线阶段、卷/章结构、近期三章任务和下一步可直接生成的开篇目标。\n\n"
                + request.message
                if whole_novel_plan
                else request.message
            )
            async for chunk in stream_normal_reply(
                effective_message,
                history,
                context,
                api_config,
                mode,
                memory_context,
            ):
                chunks.append(chunk)
                yield sse("delta", {"content": chunk})
            content = "".join(chunks).strip()
            if not content:
                raise AgentError("模型没有返回内容，请重试")
            message = await asyncio.to_thread(save_assistant_message, request.session_id, content, None, assistant_id)
            last_saved_message = message
        yield sse("impact", {"affected_nodes": affected_nodes, "conflicts": conflicts})
        completed = True
        yield sse("stage", {"code": "done", "message": "本轮创作已完成"})
        yield sse("done", {"message": message, "auto_collected": len(auto_collected)})
    except (AgentError, ValueError) as error:
        logger.warning("chat rejected session=%s reason=%s", request.session_id, error)
        if auto_collected and last_saved_message:
            completed = True
            yield sse(
                "auto_collect_partial",
                {
                    "completed": len(auto_collected),
                    "total": requested_auto_collect_count(request.message),
                    "message": str(error),
                },
            )
            yield sse("done", {"message": last_saved_message, "auto_collected": len(auto_collected), "partial": True})
        else:
            await rollback_user_message()
            yield sse("error", {"message": str(error)})
    except Exception:
        logger.exception("chat failed session=%s", request.session_id)
        if auto_collected and last_saved_message:
            completed = True
            yield sse(
                "auto_collect_partial",
                {
                    "completed": len(auto_collected),
                    "total": requested_auto_collect_count(request.message),
                    "message": "后续篇章生成失败，请从已收录的最后一章继续",
                },
            )
            yield sse("done", {"message": last_saved_message, "auto_collected": len(auto_collected), "partial": True})
        else:
            await rollback_user_message()
            yield sse("error", {"message": "对话生成失败，请检查模型设置后重试"})
    finally:
        if not completed:
            await rollback_user_message()


@app.post("/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    return StreamingResponse(chat_events(request), media_type="text/event-stream", headers=stream_headers())


@app.get("/chapters")
async def chapters(
    project_id: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(list_chapters, project_id or session_id)


@app.post("/chapters/reorder")
async def chapters_reorder(request: ChapterReorderRequest) -> list[dict[str, Any]]:
    try:
        if request.project_id:
            return await asyncio.to_thread(reorder_chapters, request.project_id, request.chapter_ids)
        return await asyncio.to_thread(reorder_chapters, request.chapter_ids)
    except ValueError as error:
        raise as_http_error(error) from error


@app.post("/chapter/update")
async def chapter_update(request: ChapterUpdateRequest) -> dict[str, Any]:
    try:
        if request.action == "confirm" and request.message_id:
            result = await asyncio.to_thread(confirm_paper, request.message_id)
            project_id = str(result["chapter"]["project_id"])
            result["affected_nodes"] = await asyncio.to_thread(analyze_impact, project_id, result["chapter"]["id"], "insert")
            return result
        if request.action == "abandon" and request.message_id:
            return {"paper": await asyncio.to_thread(abandon_paper, request.message_id)}
        if request.action == "edit" and request.chapter_id and request.title and request.content:
            chapter = await asyncio.to_thread(edit_chapter, request.chapter_id, request.title, request.content)
            affected = await asyncio.to_thread(analyze_impact, chapter["project_id"], chapter["id"], "modify")
            return {"chapter": chapter, "affected_nodes": affected}
        raise ValueError("篇章操作参数不完整")
    except ValueError as error:
        raise as_http_error(error) from error


@app.delete("/chapter/delete")
async def chapter_delete(chapter_id: str = Query(min_length=1)) -> dict[str, str]:
    try:
        await asyncio.to_thread(delete_chapter, chapter_id)
        return {"status": "ok"}
    except ValueError as error:
        raise as_http_error(error, 404) from error


async def export_events(request: ExportRequest) -> AsyncIterator[str]:
    chapter_scope = request.project_id or request.session_id
    chapters_data = await asyncio.to_thread(list_chapters, chapter_scope)
    events: Queue[dict[str, Any]] = Queue()

    def report(progress: int, message: str) -> None:
        events.put({"progress": progress, "message": message})

    def run_export() -> None:
        try:
            result = export_novel(chapters_data, request.format, request.file_name, report)
            events.put({"progress": 100, "message": "导出内容生成完成", **result})
        except Exception:
            logger.exception("export failed session=%s", request.session_id)
            events.put({"error": "导出失败，请重试"})

    worker = asyncio.create_task(asyncio.to_thread(run_export))
    while True:
        event = await asyncio.to_thread(events.get)
        yield sse("progress", event)
        if event.get("progress") == 100 or event.get("error"):
            break
    await worker


@app.post("/export")
async def export(request: ExportRequest) -> StreamingResponse:
    return StreamingResponse(export_events(request), media_type="text/event-stream", headers=stream_headers())


if __name__ == "__main__":
    reload_enabled = "--reload" in sys.argv[1:]
    quiet_mode = sys.stdout is None or sys.stderr is None
    try:
        agent_port = int(os.environ.get("NOVELFORGE_AGENT_PORT", "8000"))
    except ValueError:
        agent_port = 8000
    if not quiet_mode:
        print(f"NovelForge Python agent listening on http://127.0.0.1:{agent_port}", flush=True)
    uvicorn.run(
        "app:app" if reload_enabled else app,
        host="127.0.0.1",
        port=agent_port,
        reload=reload_enabled,
        reload_dirs=["python-agent"] if reload_enabled else None,
        log_config=None if quiet_mode else uvicorn.config.LOGGING_CONFIG,
        access_log=not quiet_mode,
    )
