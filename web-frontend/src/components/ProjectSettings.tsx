import { Archive, RotateCcw, SlidersHorizontal } from "lucide-react";
import type { Project, ProjectSettings as Settings } from "../types";

export default function ProjectSettings({ project, onUpdate, onStatus }: { project: Project | null; onUpdate: (settings: Settings) => Promise<void>; onStatus: (status: Project["status"]) => Promise<void> }) {
  if (!project) return null;
  const settings = project.settings ?? {};
  return (
    <section className="project-settings-bar">
      <div><SlidersHorizontal size={14} /><strong>创作配置</strong></div>
      <label>流程<select value={settings.workflow ?? "standard"} onChange={(event) => void onUpdate({ workflow: event.target.value as Settings["workflow"] })}><option value="standard">标准长篇</option><option value="short">短篇压缩</option><option value="serial">连载</option><option value="collection">短篇合集</option><option value="fanfiction">同人衍生</option><option value="adaptation">改编</option></select></label>
      <label>目标字数<input min={100} max={5000000} type="number" value={settings.target_words ?? 80000} onChange={(event) => void onUpdate({ target_words: Number(event.target.value) })} /></label>
      <label>语言<input value={settings.target_language ?? "zh"} onChange={(event) => void onUpdate({ target_language: event.target.value })} /></label>
      <label>风格强度<input min={0} max={5} type="range" value={settings.style_intensity ?? 3} onChange={(event) => void onUpdate({ style_intensity: Number(event.target.value) })} /></label>
      <label>隐私<select value={settings.privacy_mode ?? "standard"} onChange={(event) => void onUpdate({ privacy_mode: event.target.value as Settings["privacy_mode"] })}><option value="standard">标准模式</option><option value="local">纯本地模型</option></select></label>
      <label>合规<select value={settings.compliance_level ?? "off"} onChange={(event) => void onUpdate({ compliance_level: event.target.value as Settings["compliance_level"] })}><option value="off">关闭</option><option value="publication">出版检查</option><option value="custom">自定义</option></select></label>
      <button type="button" onClick={() => void onStatus(project.status === "archived" ? "active" : "archived")}>{project.status === "archived" ? <RotateCcw size={13} /> : <Archive size={13} />}{project.status === "archived" ? "恢复" : "归档"}</button>
    </section>
  );
}
