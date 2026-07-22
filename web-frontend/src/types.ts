export type FileInfo = {
  path: string;
  name: string;
  extension: string;
  size: number;
  genre_hint?: string;
};

export type MaterialNode = {
  id: string;
  novel_id: string;
  parent_id: string | null;
  node_type: "collection" | "meta" | "character" | "worldview" | "plot" | "theme" | "field";
  category: string;
  display_name: string;
  content: string;
  summary: string;
  tags: string[];
  sort_order: number;
};

export type NovelMaterial = {
  id: string;
  title: string;
  file_path: string;
  file_size: number;
  created_at: string;
  nodes: MaterialNode[];
};

export type Paper = {
  title: string;
  content: string;
  status: "draft" | "collected" | "abandoned";
  chapter_id: string | null;
  target_chapter_id: string | null;
  word_count?: number;
  target_words?: number;
  length_status?: "met" | "short" | "long";
  generation_action?: "create" | "continue" | "modify";
};

export type CreationAction = "auto" | "discuss" | "create" | "continue" | "modify";

export type GenerationTask = {
  id: string;
  session_id: string;
  project_id: string;
  user_message_id: string | null;
  assistant_message_id: string;
  request_payload: {
    message: string;
    selected_material_ids: string[];
    paper_source_message_id: string | null;
    regenerate_assistant_id: string | null;
    creation_action: CreationAction;
    chapter_target_words: number | null;
    mode: Mode | null;
  };
  status: "running" | "interrupted" | "partial" | "failed" | "completed" | "abandoned";
  stage: string;
  batch_total: number;
  completed_count: number;
  message_ids: string[];
  chapter_ids: string[];
  error: string;
  created_at: string;
  updated_at: string;
};

export type ChatMessage = {
  id: string;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  selected_material_ids: string[];
  has_paper: boolean;
  paper: Paper | null;
  created_at: string;
  originalQuestion?: string;
};

export type ChatSession = {
  id: string;
  project_id: string;
  branch_of: string | null;
  branch_name: string;
  mode: Mode;
  title: string;
  created_at: string;
  last_accessed: string;
  active: boolean;
};

export type Chapter = {
  id: string;
  session_id: string;
  project_id: string;
  title: string;
  content: string;
  sort_order: number;
  created_at: string;
  updated_at: string;
};

export type ChapterVersion = {
  id: string;
  chapter_id: string;
  project_id: string;
  session_id: string | null;
  title: string;
  content: string;
  summary: string;
  memory: Record<string, unknown>;
  sort_order: number;
  event_type: "edit" | "ai_edit" | "restore" | "delete";
  chapter_created_at: string;
  created_at: string;
  restored_at: string | null;
};

export type ChapterDraft = {
  chapter_id: string;
  project_id: string;
  title: string;
  content: string;
  source_updated_at: string;
  created_at: string;
  updated_at: string;
};

export type Toast = { message: string; kind: "error" | "success" | "info" };

export type Mode = "guided" | "collaborative" | "silent" | "traceable" | "teaching";

export type Project = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  active: boolean;
  status: "active" | "archived";
  settings: ProjectSettings;
};

export type ProjectSettings = {
  workflow?: "standard" | "short" | "serial" | "collection" | "fanfiction" | "adaptation";
  target_words?: number;
  target_language?: string;
  style_intensity?: number;
  privacy_mode?: "local" | "standard";
  compliance_level?: "off" | "publication" | "custom";
  sensitive_terms?: string[];
  metadata?: Record<string, string | number | boolean>;
};

export type PublicationReadiness = {
  status: "ready" | "attention" | "blocked";
  can_export: boolean;
  summary: {
    chapter_count: number;
    total_words: number;
    target_words: number;
    progress_percent: number;
  };
  checks: Array<{
    id: string;
    level: "ok" | "warning" | "error";
    title: string;
    detail: string;
  }>;
  findings: Array<{
    chapter_id: string;
    chapter_title: string;
    term: string;
    count: number;
  }>;
};

export type StoryNode = {
  id: string;
  project_id: string;
  session_id: string | null;
  parent_id: string | null;
  layer: "premise" | "volume_outline" | "chapter_beat" | "content" | "attachment";
  node_type: string;
  title: string;
  content: string;
  metadata: Record<string, unknown>;
  locked: boolean;
  sort_order: number;
  created_at: string;
  updated_at: string;
};

export type PinnedMaterial = {
  id: string;
  project_id: string;
  material_id: string;
  priority: number;
  display_name: string;
  summary: string;
  content: string;
};

export type UniverseRule = {
  id: string;
  project_id: string;
  category: "character" | "world" | "plot" | "system";
  key: string;
  value: string;
  source: string;
  immutable: number;
  created_at: string;
};

export type ImpactHighlight = {
  id: string;
  changed_node_id: string;
  affected_node_id: string;
  relation: "causal" | "foreshadow" | "reference";
  action_required: "review" | "rewrite" | "none";
  resolved: number;
};

export type Fact = {
  id: string;
  project_id: string;
  category: "character" | "world" | "plot" | "system";
  key: string;
  value: string;
  source: string;
  created_at: string;
  updated_at: string;
};

export type BranchComparison = {
  added: string[];
  deleted: string[];
  modified: Array<{ id: string; old: string; new: string }>;
};
