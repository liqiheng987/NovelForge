from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Mode(str, Enum):
    guided = "guided"
    collaborative = "collaborative"
    silent = "silent"
    traceable = "traceable"
    teaching = "teaching"


class ApiConfig(BaseModel):
    provider: Literal["openai", "compatible"]
    api_key: str = Field(default="", max_length=500)
    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=200)


class AnalyzeRequest(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=5)
    api_config: ApiConfig
    genre_hints: dict[str, str] = Field(default_factory=dict, max_length=5)
    project_id: str | None = None


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=12000)
    selected_material_ids: list[str] = Field(default_factory=list)
    api_config: ApiConfig
    mode: Mode | None = None
    project_id: str | None = None
    regenerate_assistant_id: str | None = None
    paper_source_message_id: str | None = None
    creation_action: Literal["auto", "discuss", "create", "continue", "modify"] = "auto"
    chapter_target_words: int | None = Field(default=None, ge=500, le=12000)


class SessionSwitchRequest(BaseModel):
    session_id: str


class SessionCreateRequest(BaseModel):
    title: str = Field(default="新会话", max_length=80)
    project_id: str | None = None
    mode: Mode = Mode.guided


class ProjectCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    mode: Mode = Mode.guided


class ProjectSwitchRequest(BaseModel):
    project_id: str


class ProjectSettingsRequest(BaseModel):
    workflow: Literal["standard", "short", "serial", "collection", "fanfiction", "adaptation"] | None = None
    target_words: int | None = Field(default=None, ge=100, le=5_000_000)
    target_language: str | None = Field(default=None, min_length=2, max_length=20)
    style_intensity: int | None = Field(default=None, ge=0, le=5)
    privacy_mode: Literal["local", "standard"] | None = None
    compliance_level: Literal["off", "publication", "custom"] | None = None
    metadata: dict[str, str | int | float | bool] | None = None


class ProjectStatusRequest(BaseModel):
    status: Literal["active", "archived"]


class ModeSwitchRequest(BaseModel):
    session_id: str
    mode: Mode


class BranchCreateRequest(BaseModel):
    project_id: str
    source_session_id: str
    name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=200)


class BranchSwitchRequest(BaseModel):
    session_id: str


class BranchMergeRequest(BaseModel):
    project_id: str
    source_session_id: str
    target_session_id: str


class BranchCompareRequest(BaseModel):
    project_id: str
    branch_a_id: str
    branch_b_id: str


class PinMaterialRequest(BaseModel):
    project_id: str
    material_id: str
    priority: int = Field(default=0, ge=0, le=50)


class UniverseRuleCreate(BaseModel):
    project_id: str
    category: Literal["character", "world", "plot", "system"]
    key: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=2000)
    source: str = Field(default="manual", max_length=200)
    immutable: bool = True


class UniverseRuleUpdate(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=2000)
    immutable: bool | None = None


class UniverseImportRequest(BaseModel):
    source_project_id: str
    target_project_id: str


class ImpactAnalyzeRequest(BaseModel):
    project_id: str
    changed_node_id: str
    change_type: Literal["modify", "delete", "insert"]


class FactUpsertRequest(BaseModel):
    project_id: str
    category: Literal["character", "world", "plot", "system"]
    key: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=2000)
    source: str = Field(default="user", max_length=200)


class InspirationRequest(BaseModel):
    premise: str = Field(min_length=1, max_length=6000)
    dilemma: str = Field(default="", max_length=3000)
    api_config: ApiConfig
    project_id: str | None = None


class StyleTrialRequest(BaseModel):
    scene: str = Field(min_length=1, max_length=12000)
    styles: list[str] = Field(default_factory=lambda: ["cinematic", "literary", "web_novel"], min_length=1, max_length=5)
    api_config: ApiConfig
    project_id: str | None = None


class CrossBridgeRequest(BaseModel):
    source_text: str = Field(min_length=1, max_length=20000)
    source_type: str = Field(min_length=1, max_length=80)
    target_type: str = Field(min_length=1, max_length=80)
    source_language: str = Field(default="zh", max_length=20)
    target_language: str = Field(default="zh", max_length=20)
    api_config: ApiConfig


class StoryNodeCreateRequest(BaseModel):
    project_id: str
    layer: Literal["premise", "volume_outline", "chapter_beat", "content", "attachment"]
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(default="", max_length=200000)
    session_id: str | None = None
    parent_id: str | None = None
    node_type: str = Field(default="note", max_length=80)
    metadata: dict[str, object] = Field(default_factory=dict)
    locked: bool = False


class StoryNodeUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = Field(default=None, max_length=200000)
    metadata: dict[str, object] | None = None
    locked: bool | None = None


class StoryNodeCopyRequest(BaseModel):
    target_project_id: str
    target_parent_id: str | None = None
    session_id: str | None = None


class StoryNodeReorderRequest(BaseModel):
    project_id: str
    parent_id: str | None = None
    node_ids: list[str]


class ContentGapRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500000)


class ComplianceCheckRequest(BaseModel):
    text: str = Field(min_length=1, max_length=200000)
    custom_terms: list[str] = Field(default_factory=list, max_length=200)


class ChapterReorderRequest(BaseModel):
    project_id: str | None = None
    chapter_ids: list[str]


class ChapterUpdateRequest(BaseModel):
    action: Literal["confirm", "abandon", "edit"]
    message_id: str | None = None
    chapter_id: str | None = None
    title: str | None = Field(default=None, max_length=200)
    content: str | None = Field(default=None, max_length=200000)


class ExportRequest(BaseModel):
    format: Literal["epub", "txt", "pdf"]
    file_name: str = Field(min_length=1, max_length=120)
    session_id: str = Field(min_length=1, max_length=100)
    project_id: str | None = None


class Paper(BaseModel):
    title: str
    content: str
    status: Literal["draft", "collected", "abandoned"] = "draft"
    chapter_id: str | None = None
    target_chapter_id: str | None = None
