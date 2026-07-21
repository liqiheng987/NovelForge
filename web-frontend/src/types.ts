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
  metadata?: Record<string, string | number | boolean>;
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
