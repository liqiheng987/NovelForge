import { useEffect, useState } from "react";
import { Archive, Check, RotateCcw, SlidersHorizontal, Trash2 } from "lucide-react";
import type { Project, ProjectSettings as Settings } from "../types";

type ProjectSettingsProps = {
  project: Project | null;
  disabled: boolean;
  canDelete: boolean;
  onDelete: () => Promise<void>;
  onRename: (title: string) => Promise<void>;
  onUpdate: (settings: Settings) => Promise<void>;
  onStatus: (status: Project["status"]) => Promise<void>;
};

export default function ProjectSettings({ project, disabled, canDelete, onDelete, onRename, onUpdate, onStatus }: ProjectSettingsProps) {
  const [title, setTitle] = useState(project?.title ?? "");
  const [busyAction, setBusyAction] = useState<"rename" | "delete" | null>(null);

  useEffect(() => setTitle(project?.title ?? ""), [project?.id, project?.title]);
  if (!project) return null;
  const settings = project.settings ?? {};
  const titleChanged = title.trim() !== project.title;

  const rename = async () => {
    if (disabled || busyAction || !title.trim() || !titleChanged) return;
    setBusyAction("rename");
    try { await onRename(title.trim()); } finally { setBusyAction(null); }
  };

  const remove = async () => {
    if (disabled || busyAction || !canDelete) return;
    const confirmation = window.prompt(`永久删除《${project.title}》及其全部会话和篇章。删除前会自动创建安全备份。\n\n请输入完整作品名称确认：`);
    if (confirmation === null) return;
    if (confirmation.trim() !== project.title) { window.alert("作品名称不匹配，未执行删除。"); return; }
    setBusyAction("delete");
    try { await onDelete(); } finally { setBusyAction(null); }
  };

  return (
    <section className="project-settings-bar">
      <div><SlidersHorizontal size={14} /><strong>创作配置</strong></div>
      <label className="project-title-setting">作品名<input disabled={disabled || Boolean(busyAction)} maxLength={120} value={title} onChange={(event) => setTitle(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); void rename(); } }} /></label>
      <button disabled={disabled || Boolean(busyAction) || !title.trim() || !titleChanged} type="button" onClick={() => void rename()}><Check size={13} />{busyAction === "rename" ? "保存中" : "保存名称"}</button>
      <span className="project-settings-divider" />
      <label>流程<select disabled={disabled} value={settings.workflow ?? "standard"} onChange={(event) => void onUpdate({ workflow: event.target.value as Settings["workflow"] })}><option value="standard">标准长篇</option><option value="short">短篇压缩</option><option value="serial">连载</option><option value="collection">短篇合集</option><option value="fanfiction">同人衍生</option><option value="adaptation">改编</option></select></label>
      <label>目标字数<input disabled={disabled} min={100} max={5000000} type="number" value={settings.target_words ?? 80000} onChange={(event) => void onUpdate({ target_words: Number(event.target.value) })} /></label>
      <label>语言<input disabled={disabled} value={settings.target_language ?? "zh"} onChange={(event) => void onUpdate({ target_language: event.target.value })} /></label>
      <label>风格强度<input disabled={disabled} min={0} max={5} type="range" value={settings.style_intensity ?? 3} onChange={(event) => void onUpdate({ style_intensity: Number(event.target.value) })} /></label>
      <label>隐私<select disabled={disabled} value={settings.privacy_mode ?? "standard"} onChange={(event) => void onUpdate({ privacy_mode: event.target.value as Settings["privacy_mode"] })}><option value="standard">标准模式</option><option value="local">纯本地模型</option></select></label>
      <label>合规<select disabled={disabled} value={settings.compliance_level ?? "off"} onChange={(event) => void onUpdate({ compliance_level: event.target.value as Settings["compliance_level"] })}><option value="off">关闭</option><option value="publication">出版检查</option><option value="custom">自定义</option></select></label>
      <span className="project-settings-divider" />
      <button disabled={disabled || Boolean(busyAction)} type="button" onClick={() => void onStatus(project.status === "archived" ? "active" : "archived")}>{project.status === "archived" ? <RotateCcw size={13} /> : <Archive size={13} />}{project.status === "archived" ? "恢复" : "归档"}</button>
      <button className="project-delete-button" disabled={disabled || Boolean(busyAction) || !canDelete} title={canDelete ? "永久删除当前作品" : "至少保留一个作品"} type="button" onClick={() => void remove()}><Trash2 size={13} />{busyAction === "delete" ? "删除中" : "删除作品"}</button>
    </section>
  );
}
