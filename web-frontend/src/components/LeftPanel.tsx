import { useMemo, useState } from "react";
import Dropzone from "react-dropzone";
import type { Accept, FileRejection } from "react-dropzone";
import {
  BookOpen,
  ChevronDown,
  ChevronRight,
  FileText,
  FolderTree,
  LoaderCircle,
  Sparkles,
  Trash2,
  UploadCloud,
} from "lucide-react";
import MaterialNode from "./MaterialNode";
import { useMaterialSelectionStore } from "../stores/materialStore";
import type { FileInfo, MaterialNode as MaterialNodeData, NovelMaterial } from "../types";
import CrossBridge from "./CrossBridge";
import FeatureGuideCard from "./FeatureGuideCard";

const ACCEPTED_FILES: Accept = {
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [".docx"],
  "application/pdf": [".pdf"],
  "text/plain": [".txt"],
  "application/epub+zip": [".epub"],
};

const ANALYSIS_GENRES = [
  ["", "自动识别"],
  ["scifi", "科幻视角"],
  ["mystery", "推理 / 悬疑视角"],
  ["fantasy", "奇幻视角"],
  ["wuxia", "武侠视角"],
  ["romance", "爱情视角"],
  ["historical", "历史视角"],
  ["horror", "恐怖视角"],
  ["web_novel", "网络小说视角"],
  ["light_novel", "轻小说视角"],
  ["fanfiction", "同人视角"],
  ["系统流", "系统流视角"],
  ["重生流", "重生流视角"],
  ["升级流", "升级流视角"],
  ["无敌流", "无敌流视角"],
] as const;

type LeftPanelProps = {
  novels: NovelMaterial[];
  pendingFiles: FileInfo[];
  analyzing: boolean;
  deletingMaterialId: string | null;
  loading: boolean;
  onAnalyze: () => void;
  onClearPending: () => void;
  onDeleteMaterial: (id: string, name: string, kind: "novel" | "node") => void;
  onPickFiles: () => void;
  onRemovePending: (path: string) => void;
  onSetGenreHint: (path: string, genreHint: string) => void;
  onStagePaths: (paths: string[]) => void;
  onToast: (message: string) => void;
  pinnedIds: string[];
  onTogglePinned: (materialId: string) => Promise<void>;
  apiConfig: Record<string, string>;
};

const filePath = (file: File) => (file as File & { path?: string }).path ?? "";

const formatBytes = (bytes: number) => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

export default function LeftPanel({
  novels,
  pendingFiles,
  analyzing,
  deletingMaterialId,
  loading,
  onAnalyze,
  onClearPending,
  onDeleteMaterial,
  onPickFiles,
  onRemovePending,
  onSetGenreHint,
  onStagePaths,
  onToast,
  pinnedIds,
  onTogglePinned,
  apiConfig,
}: LeftPanelProps) {
  const selectedIds = useMaterialSelectionStore((state) => state.selectedIds);
  const toggleSelectedBranch = useMaterialSelectionStore((state) => state.toggleSelectedBranch);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const { branchIdsById, childrenByParent } = useMemo(() => {
    const children = new Map<string, MaterialNodeData[]>();
    for (const novel of novels) {
      for (const node of novel.nodes) {
        const key = node.parent_id ?? novel.id;
        children.set(key, [...(children.get(key) ?? []), node]);
      }
    }
    const branches = new Map<string, string[]>();
    const collectBranch = (id: string, visiting = new Set<string>()): string[] => {
      if (branches.has(id)) return branches.get(id)!;
      if (visiting.has(id)) return [id];
      const nextVisiting = new Set(visiting).add(id);
      const childNodes = children.get(id) ?? [];
      const branch = childNodes.length
        ? childNodes.flatMap((child) => collectBranch(child.id, nextVisiting))
        : [id];
      branches.set(id, branch);
      return branch;
    };
    for (const novel of novels) collectBranch(novel.id);
    return { branchIdsById: branches, childrenByParent: children };
  }, [novels]);

  const branchState = (id: string) => {
    const branchIds = branchIdsById.get(id) ?? [id];
    const selectedCount = branchIds.reduce((count, branchId) => count + Number(selectedIds.includes(branchId)), 0);
    const checked = selectedCount === branchIds.length;
    const hasChildren = (childrenByParent.get(id)?.length ?? 0) > 0;
    return {
      branchIds,
      checked,
      partial: selectedCount > 0 && !checked,
      selectedChildCount: hasChildren ? selectedCount : 0,
      totalChildCount: hasChildren ? branchIds.length : 0,
    };
  };

  const toggleBranch = (id: string) => {
    const state = branchState(id);
    toggleSelectedBranch(state.branchIds);
  };

  const toggleExpanded = (id: string) =>
    setExpandedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const renderNodes = (parentId: string, depth: number) =>
    (childrenByParent.get(parentId) ?? []).map((node) => {
      const children = childrenByParent.get(node.id) ?? [];
      const expanded = expandedIds.has(node.id);
      const state = branchState(node.id);
      return (
        <div key={node.id}>
          <MaterialNode
            checked={state.checked}
            depth={depth}
            deleting={deletingMaterialId === node.id}
            expanded={expanded}
            hasChildren={children.length > 0}
            node={node}
            partial={state.partial}
            selectedChildCount={state.selectedChildCount}
            totalChildCount={state.totalChildCount}
            onExpand={() => toggleExpanded(node.id)}
            onDelete={() => onDeleteMaterial(node.id, node.display_name, "node")}
            onToggle={() => toggleBranch(node.id)}
            pinned={pinnedIds.includes(node.id)}
            onTogglePinned={() => void onTogglePinned(node.id)}
          />
          {expanded && renderNodes(node.id, depth + 1)}
        </div>
      );
    });

  const handleDrop = (accepted: File[], rejected: FileRejection[]) => {
    if (rejected.length || accepted.length + pendingFiles.length > 5) {
      onToast(rejected.length ? "文件解析失败，请检查格式" : "最多支持 5 个文件");
      return;
    }
    const paths = accepted.map(filePath).filter(Boolean);
    if (paths.length !== accepted.length) {
      onToast("请在桌面应用中选择本地文件");
      return;
    }
    onStagePaths(paths);
  };

  return (
    <aside className="workspace-panel material-panel glass">
      <header className="section-header">
        <div className="section-title"><BookOpen size={18} /><div><h2>素材集</h2><p>小说参考书架 · 每次发送后清空选择</p></div></div>
        <span className="selection-count">已选 {selectedIds.length}</span>
      </header>

      <FeatureGuideCard
        title="素材如何参与创作"
        description="勾选管一轮，星标管整部作品"
        items={["勾选小说根部会选择其下全部分类和具体素材；取消任一分类后，根部自动变为部分选择。", "临时素材不限制选择数量；系统会保存全部选择，并按 150,000 字符上下文预算组织给 Agent。", "点击具体素材旁的星标可设为常驻素材，持续服务当前作品。"]}
      />

      <div className="import-workflow">
        <Dropzone accept={ACCEPTED_FILES} disabled={analyzing || pendingFiles.length >= 5} maxFiles={5} multiple noClick onDrop={handleDrop}>
          {({ getInputProps, getRootProps, isDragActive }) => (
            <div {...getRootProps({ className: `material-dropzone ${isDragActive ? "active" : ""}` })}>
              <input {...getInputProps()} />
              <UploadCloud size={20} />
              <div><strong>拖入小说素材</strong><span>DOCX / PDF / TXT / EPUB · 单次最多 5 个</span></div>
              <button className="material-pick-button" disabled={analyzing} type="button" onClick={(event) => { event.stopPropagation(); onPickFiles(); }}>选择文件</button>
            </div>
          )}
        </Dropzone>

        {pendingFiles.length > 0 && (
          <div className="pending-imports">
            <div className="pending-title"><span>等待分析 · {pendingFiles.length}/5</span><button type="button" onClick={onClearPending}>清空</button></div>
            <div className="pending-file-list">
              {pendingFiles.map((file) => (
                <div className="pending-file" key={file.path}>
                  <FileText size={15} />
                  <div>
                    <strong>{file.name}</strong>
                    <small>{file.extension.toUpperCase()} · {formatBytes(file.size)}</small>
                    <select aria-label={`${file.name} 的分析侧重点`} value={file.genre_hint ?? ""} onChange={(event) => onSetGenreHint(file.path, event.target.value)}>
                      {ANALYSIS_GENRES.map(([value, label]) => <option key={value || "auto"} value={value}>{label}</option>)}
                    </select>
                  </div>
                  <button aria-label={`移除 ${file.name}`} type="button" onClick={() => onRemovePending(file.path)}><Trash2 size={13} /></button>
                </div>
              ))}
            </div>
            <button className="confirm-import" disabled={analyzing} type="button" onClick={onAnalyze}>
              {analyzing ? <LoaderCircle className="spin" size={16} /> : <Sparkles size={16} />}
              {analyzing ? "Agent 正在逐维度分析…" : "确定导入并智能分类"}
            </button>
          </div>
        )}
      </div>

      <details className="advanced-tool-drawer">
        <summary><span><Sparkles size={14} /><strong>高级素材工具</strong></span><span>跨体裁 / 跨语言转译</span><ChevronDown size={14} /></summary>
        <CrossBridge apiConfig={apiConfig} />
      </details>

      <div className="material-tree">
        {loading && <div className="empty-state"><LoaderCircle className="spin" size={22} /><p>正在恢复素材…</p></div>}
        {!loading && novels.length === 0 && <div className="empty-state"><FolderTree size={28} /><p>还没有素材</p><span>导入小说后，Agent 会按类型建立层级素材树</span></div>}
        {novels.map((novel) => {
          const expanded = expandedIds.has(novel.id);
          const state = branchState(novel.id);
          return (
            <div className="novel-tree" key={novel.id}>
              <div className={`tree-node novel-root ${state.checked || state.partial ? "selected" : ""}`}>
                <button className="tree-toggle" type="button" onClick={() => toggleExpanded(novel.id)}>{expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</button>
                <label title="勾选整本小说下的全部分类和具体素材">
                  <input
                    ref={(element) => { if (element) element.indeterminate = state.partial; }}
                    checked={state.checked}
                    type="checkbox"
                    onChange={() => toggleBranch(novel.id)}
                  />
                  <BookOpen size={14} /><span>{novel.title}</span>
                </label>
                {state.totalChildCount > 0 && <span className="branch-selection-count">已选 {state.selectedChildCount}/{state.totalChildCount}</span>}
                <small>{formatBytes(novel.file_size)}</small>
                <button
                  aria-label={`删除 ${novel.title}`}
                  className="material-delete-button novel-delete-button"
                  disabled={deletingMaterialId === novel.id}
                  title="删除整部小说及其全部素材"
                  type="button"
                  onClick={() => onDeleteMaterial(novel.id, novel.title, "novel")}
                >
                  {deletingMaterialId === novel.id ? <LoaderCircle className="spin" size={12} /> : <Trash2 size={12} />}
                </button>
              </div>
              {expanded && renderNodes(novel.id, 1)}
            </div>
          );
        })}
      </div>
    </aside>
  );
}
