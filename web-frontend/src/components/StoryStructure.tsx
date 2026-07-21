import { useMemo, useState } from "react";
import { FolderTree, Lock, LockOpen, Plus, Trash2, Pencil, Copy } from "lucide-react";
import { api } from "../api/client";
import type { Project, StoryNode } from "../types";

const layers: Array<[StoryNode["layer"], string]> = [
  ["premise", "设定卡"],
  ["volume_outline", "卷大纲"],
  ["chapter_beat", "章节细纲"],
  ["content", "正文节点"],
  ["attachment", "附加表"],
];

type Props = {
  projectId: string | null;
  sessionId: string | null;
  projects: Project[];
  nodes: StoryNode[];
  onRefresh: () => Promise<void>;
  onToast: (message: string) => void;
};

export default function StoryStructure({ projectId, sessionId, projects, nodes, onRefresh, onToast }: Props) {
  const [layer, setLayer] = useState<StoryNode["layer"]>("premise");
  const [copyTargetProjectId, setCopyTargetProjectId] = useState("");
  const visible = useMemo(() => nodes.filter((node) => node.layer === layer), [layer, nodes]);
  const add = async () => {
    if (!projectId) return;
    const title = window.prompt(`新建${layers.find(([value]) => value === layer)?.[1] ?? "节点"}标题`);
    if (!title) return;
    const content = window.prompt("节点内容（可稍后编辑）", "") ?? "";
    await api("/story/nodes", { method: "POST", body: JSON.stringify({ project_id: projectId, session_id: sessionId, layer, title, content }) });
    await onRefresh();
  };
  const edit = async (node: StoryNode) => {
    const title = window.prompt("节点标题", node.title);
    if (!title) return;
    const content = window.prompt("节点内容", node.content);
    if (content === null) return;
    await api(`/story/nodes/${encodeURIComponent(node.id)}`, { method: "PUT", body: JSON.stringify({ title, content }) });
    await onRefresh();
  };
  const toggleLock = async (node: StoryNode) => {
    await api(`/story/nodes/${encodeURIComponent(node.id)}`, { method: "PUT", body: JSON.stringify({ locked: !node.locked }) });
    await onRefresh();
  };
  const remove = async (node: StoryNode) => {
    if (!window.confirm(`删除“${node.title}”及其子节点？`)) return;
    await api(`/story/nodes/${encodeURIComponent(node.id)}`, { method: "DELETE" });
    await onRefresh();
  };
  const copyToProject = async (node: StoryNode) => {
    const targetId = copyTargetProjectId || projects.find((project) => project.id !== projectId)?.id;
    if (!targetId) return onToast("请先选择复制目标作品");
    try {
      await api(`/story/nodes/${encodeURIComponent(node.id)}/copy`, { method: "POST", body: JSON.stringify({ target_project_id: targetId }) });
      onToast("结构节点已复制到目标作品");
    } catch (error) { onToast(error instanceof Error ? error.message : "复制失败"); }
  };
  return (
    <section className="story-structure">
      <header><div><FolderTree size={15} /><strong>四层创作结构</strong></div><div className="story-header-actions"><select aria-label="跨作品复制目标" value={copyTargetProjectId} onChange={(event) => setCopyTargetProjectId(event.target.value)}><option value="">复制目标…</option>{projects.filter((project) => project.id !== projectId).map((project) => <option key={project.id} value={project.id}>{project.title}</option>)}</select></div><button disabled={!projectId} type="button" onClick={() => void add()}><Plus size={13} />新建</button></header>
      <div className="story-layer-tabs">{layers.map(([value, label]) => <button className={layer === value ? "active" : ""} key={value} type="button" onClick={() => setLayer(value)}>{label}</button>)}</div>
      <div className="story-node-list">
        {!visible.length && <p>当前层级暂无节点，可手动建立骨架。</p>}
        {visible.map((node) => <article key={node.id}><div><strong>{node.title}</strong><p>{node.content || "暂无内容"}</p></div><div><button title={node.locked ? "解锁" : "锁定"} type="button" onClick={() => void toggleLock(node)}>{node.locked ? <Lock size={12} /> : <LockOpen size={12} />}</button><button title="编辑" type="button" onClick={() => void edit(node)}><Pencil size={12} /></button><button title="跨作品复制" type="button" onClick={() => void copyToProject(node)}><Copy size={12} /></button><button title="删除" type="button" onClick={() => void remove(node)}><Trash2 size={12} /></button></div></article>)}
      </div>
    </section>
  );
}
