import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  ChevronDown,
  ChevronRight,
  FileText,
  FolderTree,
  Globe2,
  LoaderCircle,
  Sparkles,
  Trash2,
  Star,
  UserRound,
  X,
} from "lucide-react";
import type { MaterialNode as MaterialNodeData } from "../types";

type MaterialNodeProps = {
  node: MaterialNodeData;
  depth: number;
  expanded: boolean;
  hasChildren: boolean;
  checked: boolean;
  partial: boolean;
  deleting: boolean;
  selectedChildCount: number;
  totalChildCount: number;
  onExpand: () => void;
  onDelete: () => void;
  onToggle: () => void;
  pinned: boolean;
  onTogglePinned: () => void;
};

const truncate = (value: string, limit: number) => {
  const characters = Array.from(value.trim());
  return characters.length > limit ? `${characters.slice(0, limit).join("")}...` : characters.join("");
};

const parseContent = (content: string): unknown => {
  try {
    return JSON.parse(content) as unknown;
  } catch {
    return content;
  }
};

const FIELD_LABELS: Record<string, string> = {
  name: "名称",
  title: "标题",
  type: "类型",
  category: "分类",
  summary: "内容摘要",
  details: "详细信息",
  tags: "标签",
  description: "内容说明",
  status: "状态",
  index: "序号",
  primary_type: "主要类型",
  secondary_type: "辅助类型",
  type_source: "识别依据",
  identity: "身份",
  personality: "性格",
  appearance: "外形特征",
  background: "背景经历",
  relationship: "人物关系",
  relationships: "人物关系",
  motivation: "行动动机",
  goal: "核心目标",
  conflict: "主要冲突",
  ability: "能力特点",
  rule: "规则",
  rules: "规则",
  scene: "场景",
  effect: "叙事作用",
  value: "创作价值",
  example: "原文示例",
  examples: "原文示例",
  age: "年龄",
  gender: "性别",
  role: "角色定位",
  occupation: "职业",
  traits: "性格特征",
  strength: "优势",
  strengths: "优势",
  weakness: "弱点",
  weaknesses: "弱点",
  desire: "欲望",
  fear: "恐惧",
  arc: "人物弧光",
  function: "叙事功能",
  location: "地点",
  time: "时间",
  atmosphere: "氛围",
  cause: "起因",
  process: "过程",
  outcome: "结果",
  limitation: "限制",
  limitations: "限制",
  cost: "代价",
  trigger: "触发条件",
  symbolism: "象征意义",
  foreshadowing: "伏笔",
  payoff: "回收方式",
  technique: "写作技法",
  pacing: "叙事节奏",
  perspective: "叙事视角",
  tone: "语言基调",
  source_ranges: "原文位置",
  evolution: "发展变化",
  region: "全文区域",
  chapter_start: "起始章节",
  chapter_end: "结束章节",
  chapter_index: "章节序号",
  chapter_title_start: "起始章节标题",
  chapter_title_end: "结束章节标题",
  chapter_headings: "章节标题索引",
  chapter_count: "章节数量",
  first_chapter: "首次出现章节",
  last_chapter: "最近出现章节",
  timeline: "发展时间线",
  ordered_events: "剧情顺序事件",
  storage: "内容存储方式",
  highlights: "重点章节",
  chapters: "章节记录",
  events: "剧情事件",
  entity_changes: "人物与设定变化",
  threads: "伏笔与线索",
  craft: "写作技法",
  importance: "重要程度",
  confidence: "分析可信度",
  refined: "是否经过模型精读",
  refine_mode: "精读方式",
  analysis_status: "分析方式",
  key_passages: "关键原文片段",
  start_char: "原文起始位置",
  end_char: "原文结束位置",
  coverage: "全文覆盖情况",
  strategy: "分析策略",
  total_chars: "原文总字数",
  indexed_chars: "已建立索引字数",
  archived_chars: "已归档字数",
  retained_chars: "保留关键原文字数",
  retained_passages: "保留关键段落数",
  retention_ratio: "关键原文保留比例",
  archive_segments: "全文归档片段数",
  analyzed_chapters: "已建档章节数",
  model_analyzed_chapters: "模型精读章节数",
  refined_chapters: "重点精读章节数",
  chapter_batches: "章节建档批次数",
  refine_batches: "重点精读批次数",
  semantic_regions: "全书分析区域数",
  analyzed_regions: "已完成分析区域数",
  semantic_sampled_chars: "模型分析字数",
};

const VALUE_LABELS: Record<string, string> = {
  light_novel: "轻小说",
  web_novel: "网络小说",
  progression: "成长升级流",
  fantasy: "奇幻",
  scifi: "科幻",
  mystery: "推理 / 悬疑",
  romance: "爱情",
  historical: "历史",
  horror: "恐怖",
  wuxia: "武侠",
  fanfiction: "同人",
  thriller: "惊悚",
  western: "西部",
  stream_of_consciousness: "意识流",
  epistolary: "书信体",
  autobiographical: "自传体",
  allegory: "寓言",
  epic_myth: "史诗神话",
  experimental: "实验小说",
  postmodern: "后现代",
  danmei: "耽美",
  isekai: "异世界",
  dungeon_core: "地下城核心",
  revenge: "复仇流",
  rebirth: "重生流",
  system: "系统流",
  invincible: "无敌流",
  user_hint: "用户指定",
  main_plot: "主线情节",
  side_plot: "支线情节",
  payoff: "爽点 / 回收",
  character: "人物",
  world_rule: "世界规则",
  power: "力量体系",
  faction: "势力",
  opened: "新开启",
  advanced: "推进中",
  resolved: "已回收",
  pacing: "节奏设计",
  theme: "主题",
  technique: "写作技法",
  local: "本地章节建档",
  model: "模型精读",
  fallback: "兜底记录",
  full: "重要章节全文精读",
  evidence: "关键证据精读",
  novel_chapter_cards: "章节档案库",
  sqlite_segment: "全文片段库",
  sqlite_key_passages: "关键原文片段库",
  ordered_chapter_digest_adaptive_key_refinement: "逐章顺序建档、关键章分级精读与全书摘要分析",
  key_passage_archive_and_region_analysis: "关键原文归档与全书分区分析",
  ordered_local_chapter_records_adaptive_refinement_and_macro_analysis: "有序章节建档、重要章精读与全书区域分析",
  continuous_archive_and_region_analysis: "连续全文归档与分区分析",
  full_text: "全文分析",
  dimension_excerpts: "分类维度摘录分析",
};

const FIELD_VALUE_LABELS: Record<string, Record<string, string>> = {
  type_source: {
    user_hint: "用户指定",
    model: "Agent 自动识别",
  },
};

const CATEGORY_LABELS: Record<string, string> = {
  type: "作品类型",
  character: "人物",
  worldview: "世界观",
  world: "世界观",
  plot: "情节",
  theme: "主题",
  field: "写作技法",
  protagonist: "主角",
  antagonist: "反派",
  supporting_character: "配角",
  setting: "场景设定",
  technique: "写作技法",
};

const TAG_LABELS: Record<string, string> = {
  type: "作品类型",
  primary: "主要类型",
  secondary: "辅助类型",
  web: "网络小说",
  light: "轻小说",
  ...VALUE_LABELS,
};

const isRecord = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === "object" && !Array.isArray(value);

const formatFieldLabel = (key: string) => {
  if (FIELD_LABELS[key]) return FIELD_LABELS[key];
  if (/^[\u4e00-\u9fff]/.test(key)) return key;
  return key.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/[_-]+/g, " ").replace(/^./, (character) => character.toUpperCase());
};

const formatDisplayValue = (value: unknown, fieldKey?: string) => {
  if (typeof value === "boolean") return value ? "是" : "否";
  if (value === null || value === undefined) return "未补充";
  const rawText = String(value);
  const normalizedValue = rawText.trim().toLowerCase();
  return FIELD_VALUE_LABELS[fieldKey ?? ""]?.[normalizedValue]
    ?? VALUE_LABELS[normalizedValue]
    ?? rawText;
};

const displayContent = (value: unknown, displayName: string): unknown => {
  if (!isRecord(value)) return value;
  const details = isRecord(value.details) ? value.details : null;
  const extras = Object.entries(value).filter(([key]) => !["name", "category", "summary", "details", "tags"].includes(key));
  const detailEntries = details
    ? Object.entries(details).filter(([key, item]) => !(["姓名", "名称", "角色名", "条目名称"].includes(key) && String(item) === displayName))
    : [];
  if (details) return Object.fromEntries([...detailEntries, ...extras]);
  if (extras.length) return Object.fromEntries(extras);
  return value;
};

const fieldTone = (key: string) => {
  if (/关系|规则|限制|条件|冲突|目标|动机|作用|效果|价值|转折|反转/.test(key)) return "accent";
  if (/语句|台词|对白|话语|原文|示例/.test(key)) return "quote";
  return "default";
};

function ReadableValue({ value, depth = 0, fieldKey }: { value: unknown; depth?: number; fieldKey?: string }) {
  if (Array.isArray(value)) {
    if (!value.length) return <span className="material-empty-value">暂无内容</span>;
    return <ul className="material-readable-list">{value.map((item, index) => <li key={index}><ReadableValue depth={depth + 1} fieldKey={fieldKey} value={item} /></li>)}</ul>;
  }
  if (isRecord(value)) {
    const entries = Object.entries(value);
    if (!entries.length) return <span className="material-empty-value">该节点的完整内容位于子节点中</span>;
    return (
      <div className={`material-reading-grid ${depth > 0 ? "nested" : ""}`}>
        {entries.map(([key, item]) => (
          <article className={`material-reading-field ${fieldTone(key)}`} key={key}>
            <h4>{formatFieldLabel(key)}</h4>
            <ReadableValue depth={depth + 1} fieldKey={key} value={item} />
          </article>
        ))}
      </div>
    );
  }
  const text = formatDisplayValue(value, fieldKey);
  if (!text.trim()) return <span className="material-empty-value">未补充</span>;
  return <p className="material-readable-text">{text}</p>;
}

const NODE_TYPE_LABELS: Record<string, string> = {
  character: "人物素材",
  worldview: "世界观素材",
  plot: "情节素材",
  theme: "主题素材",
  field: "写作技法",
  meta: "作品信息",
  collection: "素材分类",
};

const nodeIcon = (node: MaterialNodeData) => {
  if (node.node_type === "character") return <UserRound size={13} />;
  if (node.node_type === "worldview") return <Globe2 size={13} />;
  if (node.node_type === "plot") return <Sparkles size={13} />;
  return node.node_type === "collection" ? <FolderTree size={13} /> : <FileText size={13} />;
};

export default function MaterialNode({
  node,
  depth,
  expanded,
  hasChildren,
  checked,
  partial,
  deleting,
  selectedChildCount,
  totalChildCount,
  onExpand,
  onDelete,
  onToggle,
  pinned,
  onTogglePinned,
}: MaterialNodeProps) {
  const [hovered, setHovered] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const hoverTimer = useRef<number | null>(null);
  const checkboxRef = useRef<HTMLInputElement | null>(null);
  const displayCategory = CATEGORY_LABELS[node.category.trim().toLowerCase()] ?? node.category;
  const displayTags = [...new Set(node.tags.map((tag) => TAG_LABELS[tag.trim().toLowerCase()] ?? tag.trim()).filter(Boolean))];

  useEffect(() => {
    if (checkboxRef.current) checkboxRef.current.indeterminate = partial;
  }, [partial]);

  useEffect(() => {
    if (!detailsOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDetailsOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [detailsOpen]);

  useEffect(() => () => {
    if (hoverTimer.current !== null) window.clearTimeout(hoverTimer.current);
  }, []);

  const showHoverCard = () => {
    hoverTimer.current = window.setTimeout(() => setHovered(true), 500);
  };

  const hideHoverCard = () => {
    if (hoverTimer.current !== null) window.clearTimeout(hoverTimer.current);
    hoverTimer.current = null;
    setHovered(false);
  };

  return (
    <>
      <div
        className={`tree-node material-node-row ${checked || partial ? "selected" : ""}`}
        style={{ paddingLeft: `${10 + depth * 15}px` }}
        onMouseEnter={showHoverCard}
        onMouseLeave={hideHoverCard}
      >
        <button aria-label={expanded ? "折叠" : "展开"} className="tree-toggle" disabled={!hasChildren} type="button" onClick={onExpand}>
          {hasChildren ? expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} /> : null}
        </button>
        <label className="material-checkbox" title="选择素材">
          <input ref={checkboxRef} checked={checked} type="checkbox" onChange={onToggle} />
          {nodeIcon(node)}
        </label>
        <button className="material-node-trigger" type="button" onClick={() => setDetailsOpen(true)}>
          <strong>{node.display_name}</strong>
          <small>{truncate(node.summary || "暂无摘要", 30)}</small>
        </button>
        {totalChildCount > 0 && <span className="branch-selection-count">已选 {selectedChildCount}/{totalChildCount}</span>}
        <button aria-label={pinned ? "取消常驻" : "设为常驻"} className={`material-pin-button ${pinned ? "active" : ""}`} type="button" onClick={(event) => { event.stopPropagation(); onTogglePinned(); }}><Star size={12} fill={pinned ? "currentColor" : "none"} /></button>
        <button
          aria-label={`删除 ${node.display_name}`}
          className="material-delete-button"
          disabled={deleting}
          title={hasChildren ? "删除该分类及全部子素材" : "删除素材"}
          type="button"
          onClick={(event) => { event.stopPropagation(); onDelete(); }}
        >
          {deleting ? <LoaderCircle className="spin" size={12} /> : <Trash2 size={12} />}
        </button>
        {hovered && (
          <div className="material-hover-card" role="tooltip">
            <strong>{node.display_name}</strong>
            <p>{truncate(node.summary || "暂无摘要", 200)}</p>
          </div>
        )}
      </div>

      {detailsOpen && createPortal(
        <div className="modal-backdrop material-detail-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setDetailsOpen(false); }}>
          <section aria-labelledby={`material-title-${node.id}`} aria-modal="true" className="material-detail-dialog glass" role="dialog">
            <header>
              <div className="material-detail-heading">
                <span className="material-detail-kind">{nodeIcon(node)}{NODE_TYPE_LABELS[node.node_type] ?? "创作素材"}<i />{displayCategory}</span>
                <h2 id={`material-title-${node.id}`}>{node.display_name}</h2>
                <p>已由 Agent 整理为可直接用于创作的素材档案</p>
              </div>
              <button aria-label="关闭素材详情" type="button" onClick={() => setDetailsOpen(false)}><X size={17} /></button>
            </header>
            <div className="material-detail-body">
              <section className="material-detail-section material-summary-section"><div className="material-detail-section-title"><Sparkles size={15} /><div><h3>创作摘要</h3><span>快速了解这条素材为什么值得使用</span></div></div><p>{node.summary || "暂无摘要"}</p></section>
              <section className="material-detail-section"><div className="material-detail-section-title"><FileText size={15} /><div><h3>素材细节</h3><span>按创作语义整理，重点信息一目了然</span></div></div><div className="material-structured-content"><ReadableValue value={displayContent(parseContent(node.content), node.display_name)} /></div></section>
              {displayTags.length > 0 && <section className="material-detail-section material-tags-section"><div className="material-detail-section-title"><span className="material-tag-symbol">#</span><div><h3>创作标签</h3><span>可用于判断适合的场景与写作方向</span></div></div><div className="material-detail-tags">{displayTags.map((tag) => <span key={tag}>{tag}</span>)}</div></section>}
            </div>
          </section>
        </div>,
        document.body,
      )}
    </>
  );
}
