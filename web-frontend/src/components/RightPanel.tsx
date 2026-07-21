import { useState } from "react";
import {
  BookOpenCheck,
  Clipboard,
  Download,
  FilePenLine,
  GripVertical,
  Layers3,
  LibraryBig,
  ShieldCheck,
  Trash2,
  Wrench,
  X,
} from "lucide-react";
import type { Chapter, Fact, ImpactHighlight, Project, StoryNode, UniverseRule } from "../types";
import UniverseRules from "./UniverseRules";
import ImpactVisualization from "./ImpactVisualization";
import StyleTrial from "./StyleTrial";
import StoryStructure from "./StoryStructure";
import FactTable from "./FactTable";
import FeatureGuideCard from "./FeatureGuideCard";

type RightPanelProps = {
  chapters: Chapter[];
  projectTitle: string;
  selectedChapterId: string | null;
  onDelete: (chapterId: string) => Promise<void>;
  onEdit: (chapterId: string, title: string, content: string) => Promise<void>;
  onExport: () => void;
  onReorder: (chapterIds: string[]) => Promise<void>;
  onSelect: (chapterId: string) => void;
  rules: UniverseRule[];
  impacts: ImpactHighlight[];
  projectId: string | null;
  apiConfig: Record<string, string>;
  onCreateRule: (key: string, value: string, category: UniverseRule["category"]) => Promise<void>;
  onDeleteRule: (id: string) => Promise<void>;
  projects: Project[];
  sessionId: string | null;
  storyNodes: StoryNode[];
  onRefreshStory: () => Promise<void>;
  onToast: (message: string) => void;
  facts: Fact[];
  onRefreshFacts: () => Promise<void>;
};

export default function RightPanel({
  chapters,
  projectTitle,
  selectedChapterId,
  onDelete,
  onEdit,
  onExport,
  onReorder,
  onSelect,
  rules,
  impacts,
  projectId,
  apiConfig,
  onCreateRule,
  onDeleteRule,
  projects,
  sessionId,
  storyNodes,
  onRefreshStory,
  onToast,
  facts,
  onRefreshFacts,
}: RightPanelProps) {
  const [activeTab, setActiveTab] = useState<"chapters" | "structure" | "memory" | "tools">("chapters");
  const [draggedId, setDraggedId] = useState<string | null>(null);
  const [editing, setEditing] = useState<Chapter | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editContent, setEditContent] = useState("");
  const [saving, setSaving] = useState(false);

  const beginEdit = (chapter: Chapter) => {
    setEditing(chapter);
    setEditTitle(chapter.title);
    setEditContent(chapter.content);
  };

  const dropBefore = async (targetId: string) => {
    if (!draggedId || draggedId === targetId) return;
    const ids = chapters.map((chapter) => chapter.id);
    const from = ids.indexOf(draggedId);
    const to = ids.indexOf(targetId);
    ids.splice(to, 0, ids.splice(from, 1)[0]);
    setDraggedId(null);
    await onReorder(ids);
  };

  const saveEdit = async () => {
    if (!editing || !editTitle.trim() || !editContent.trim()) return;
    setSaving(true);
    try {
      await onEdit(editing.id, editTitle, editContent);
      setEditing(null);
    } finally {
      setSaving(false);
    }
  };

  const guide = activeTab === "chapters"
    ? { title: "篇章库", description: "只收录你确认过的正式稿件", items: ["拖动篇章卡片可调整最终阅读顺序。", "编辑已收录篇章会同步检查可能受影响的设定与结构。", "导出按当前排序生成完整小说文件。"] }
    : activeTab === "structure"
      ? { title: "故事结构", description: "从作品到场景逐层规划", items: ["四层结构用于管理作品、卷、章节与场景目标。", "结构节点是创作路线图，不会自动覆盖已确认正文。", "可引用其他作品项目作为跨作品结构参考。"] }
      : activeTab === "memory"
        ? { title: "设定与记忆", description: "铁律强约束，事实表持续记忆", items: ["宇宙铁律用于不可违背的人物、世界、情节或系统规则。", "事实表从创作过程沉淀可更新的结构化记忆。", "生成时 Agent 会同时校验铁律并读取相关事实。"] }
        : { title: "创作工具", description: "在修改前看影响，在定稿前试风格", items: ["影响范围展示改动可能牵连的结构、伏笔与引用。", "风格试写用于比较表达方向，不会直接写入篇章库。", "工具结果仅供决策，正式内容仍需在 Chat 中生成并确认。"] };

  return (
    <aside className="workspace-panel chapter-panel glass">
      <header className="section-header chapter-section-header">
        <div className="section-title"><LibraryBig size={18} /><div><h2>作品工作台</h2><p>{projectTitle} · 结构、设定与成稿</p></div></div>
        <span className="selection-count">{chapters.length} 章</span>
      </header>

      <nav aria-label="作品工作台分类" className="right-panel-tabs">
        <button className={activeTab === "chapters" ? "active" : ""} type="button" onClick={() => setActiveTab("chapters")}><BookOpenCheck size={14} /><span>篇章</span></button>
        <button className={activeTab === "structure" ? "active" : ""} type="button" onClick={() => setActiveTab("structure")}><Layers3 size={14} /><span>结构</span></button>
        <button className={activeTab === "memory" ? "active" : ""} type="button" onClick={() => setActiveTab("memory")}><ShieldCheck size={14} /><span>设定</span></button>
        <button className={activeTab === "tools" ? "active" : ""} type="button" onClick={() => setActiveTab("tools")}><Wrench size={14} /><span>工具</span>{impacts.length > 0 && <i>{impacts.length}</i>}</button>
      </nav>

      <FeatureGuideCard title={guide.title} description={guide.description} items={guide.items} />

      <div className="right-panel-page" data-tab={activeTab}>
        {activeTab === "chapters" && <>
          <div className="chapter-page-actions"><button className="export-novel-button" disabled={chapters.length === 0} type="button" onClick={onExport}><Download size={15} />导出小说</button><span>拖拽排序 · 点击查看</span></div>
          <div className="chapter-list">
            {chapters.length === 0 && <div className="empty-state"><LibraryBig size={28} /><p>还没有确认篇章</p><span>Agent 生成的稿纸经你确认后才会进入这里</span></div>}
            {chapters.map((chapter, index) => (
              <article
                className={`chapter-card ${selectedChapterId === chapter.id ? "selected" : ""} ${draggedId === chapter.id ? "dragging" : ""}`}
                draggable
                key={chapter.id}
                onClick={() => onSelect(chapter.id)}
                onDragEnd={() => setDraggedId(null)}
                onDragOver={(event) => event.preventDefault()}
                onDragStart={() => setDraggedId(chapter.id)}
                onDrop={() => void dropBefore(chapter.id)}
              >
                <GripVertical className="chapter-grip" size={15} />
                <div className="chapter-index">{String(index + 1).padStart(2, "0")}</div>
                <div className="chapter-card-content">
                  <strong>第{index + 1}章 · {chapter.title}</strong>
                  <p>{chapter.content.replace(/\s+/g, " ").slice(0, 88)}</p>
                  <small>{chapter.content.replace(/\s/g, "").length} 字 · {new Date(chapter.created_at).toLocaleDateString("zh-CN")}</small>
                  <div className="chapter-card-actions">
                    <button title="复制" type="button" onClick={(event) => { event.stopPropagation(); void navigator.clipboard.writeText(chapter.content); }}><Clipboard size={13} /></button>
                    <button title="编辑" type="button" onClick={(event) => { event.stopPropagation(); beginEdit(chapter); }}><FilePenLine size={13} /></button>
                    <button title="删除" type="button" onClick={(event) => { event.stopPropagation(); if (window.confirm(`确定删除《${chapter.title}》吗？`)) void onDelete(chapter.id); }}><Trash2 size={13} /></button>
                  </div>
                </div>
              </article>
            ))}
          </div>
        </>}
        {activeTab === "structure" && <StoryStructure projectId={projectId} sessionId={sessionId} projects={projects} nodes={storyNodes} onRefresh={onRefreshStory} onToast={onToast} />}
        {activeTab === "memory" && <div className="right-panel-stack"><UniverseRules rules={rules} onCreate={onCreateRule} onDelete={onDeleteRule} /><FactTable projectId={projectId} facts={facts} onRefresh={onRefreshFacts} /></div>}
        {activeTab === "tools" && <div className="right-panel-stack"><ImpactVisualization impacts={impacts} /><StyleTrial apiConfig={apiConfig} projectId={projectId} /></div>}
      </div>

      {editing && (
        <div className="modal-backdrop">
          <section aria-modal="true" className="chapter-edit-dialog glass" role="dialog">
            <header><div><span className="eyebrow">篇章编辑</span><h2>修改已收录篇章</h2></div><button aria-label="关闭" type="button" onClick={() => setEditing(null)}><X size={17} /></button></header>
            <label><span>标题</span><input value={editTitle} onChange={(event) => setEditTitle(event.target.value)} /></label>
            <label className="chapter-edit-content"><span>正文</span><textarea value={editContent} onChange={(event) => setEditContent(event.target.value)} /></label>
            <footer><button type="button" onClick={() => setEditing(null)}>取消</button><button className="primary-button" disabled={saving || !editTitle.trim() || !editContent.trim()} type="button" onClick={() => void saveEdit()}>{saving ? "保存中…" : "保存"}</button></footer>
          </section>
        </div>
      )}
    </aside>
  );
}
