import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { CircleCheck, LoaderCircle, RefreshCw, ShieldAlert, TriangleAlert, XCircle } from "lucide-react";
import { agentFetch } from "../api/client";
import type { Chapter, PublicationReadiness } from "../types";

type ExportFormat = "epub" | "txt" | "pdf";
type ExportEvent = { progress?: number; message?: string; content_base64?: string; extension?: string; error?: string };

type Props = {
  chapters: Chapter[];
  projectId: string;
  sessionId: string;
  onClose: () => void;
};

export default function ExportDialog({ chapters, projectId, sessionId, onClose }: Props) {
  const [format, setFormat] = useState<ExportFormat>("epub");
  const [fileName, setFileName] = useState("NovelForge-小说");
  const [targetDirectory, setTargetDirectory] = useState("");
  const [progress, setProgress] = useState(0);
  const [progressMessage, setProgressMessage] = useState("等待开始导出");
  const [error, setError] = useState("");
  const [exporting, setExporting] = useState(false);
  const [completedPath, setCompletedPath] = useState("");
  const [readiness, setReadiness] = useState<PublicationReadiness | null>(null);
  const [readinessLoading, setReadinessLoading] = useState(true);
  const [readinessError, setReadinessError] = useState("");
  const [attentionAccepted, setAttentionAccepted] = useState(false);
  const ordered = useMemo(() => [...chapters].sort((a, b) => a.sort_order - b.sort_order), [chapters]);

  const loadReadiness = useCallback(async () => {
    setReadinessLoading(true);
    setReadinessError("");
    setAttentionAccepted(false);
    try {
      const response = await agentFetch(`/projects/${encodeURIComponent(projectId)}/publication-readiness`);
      if (!response.ok) throw new Error("出版前检查失败，请重试");
      setReadiness(await response.json() as PublicationReadiness);
    } catch (caught) {
      setReadiness(null);
      setReadinessError(caught instanceof Error ? caught.message : "出版前检查失败，请重试");
    } finally {
      setReadinessLoading(false);
    }
  }, [projectId]);

  useEffect(() => { void loadReadiness(); }, [loadReadiness]);

  const chooseDirectory = async () => {
    const selected = await invoke<string | null>("open_dialog");
    if (selected) setTargetDirectory(selected);
  };

  const startExport = async () => {
    if (exporting || !targetDirectory || !fileName.trim() || !ordered.length || !readiness?.can_export) return;
    if (readiness.status === "attention" && !attentionAccepted) return;
    setExporting(true);
    setError("");
    setProgress(0);
    setProgressMessage("正在连接导出服务");
    try {
      const response = await agentFetch("/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ format, file_name: fileName.trim(), session_id: sessionId, project_id: projectId, acknowledge_warnings: attentionAccepted }),
      });
      if (!response.ok || !response.body) throw new Error("导出失败，请重试");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let finalContent = "";
      while (true) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value, { stream: !done });
        const blocks = buffer.split(/\r?\n\r?\n/);
        buffer = blocks.pop() ?? "";
        for (const block of blocks) {
          const data = block.split(/\r?\n/).find((line) => line.startsWith("data:"))?.slice(5).trim();
          if (!data) continue;
          const event = JSON.parse(data) as ExportEvent;
          if (event.error) throw new Error(event.error);
          if (typeof event.progress === "number") setProgress(event.progress);
          if (event.message) setProgressMessage(event.message);
          if (event.content_base64) finalContent = event.content_base64;
        }
        if (done) break;
      }
      if (!finalContent) throw new Error("导出失败，请重试");
      setProgressMessage("正在由桌面端写入目标文件");
      const path = await invoke<string>("write_file", { targetDirectory, fileName, format, contentBase64: finalContent });
      setCompletedPath(path);
      setProgress(100);
      setProgressMessage("导出完成，目标文件夹已打开");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "导出失败，请重试");
    } finally {
      setExporting(false);
    }
  };

  const needsAttention = readiness?.status === "attention";
  const exportDisabled = exporting
    || Boolean(completedPath)
    || !targetDirectory
    || !fileName.trim()
    || !ordered.length
    || readinessLoading
    || !readiness?.can_export
    || Boolean(readinessError)
    || Boolean(needsAttention && !attentionAccepted);

  return (
    <div className="export-dialog-backdrop">
      <section aria-label="导出小说" aria-modal="true" className="export-dialog glass" role="dialog">
        <header className="export-dialog-header"><div><span className="eyebrow">NovelForge Export</span><h2>导出小说</h2></div><button disabled={exporting} type="button" onClick={onClose}>关闭</button></header>
        <div className="export-dialog-body">
          <section className="export-preview-pane"><header><strong>完整文本预览</strong><span>{ordered.length} 章</span></header><div className="export-preview-text">{ordered.map((chapter, index) => <article key={chapter.id}><h3>第{index + 1}章 {chapter.title}</h3>{chapter.content.split(/\n+/).filter(Boolean).map((paragraph, paragraphIndex) => <p key={`${chapter.id}-${paragraphIndex}`}>{paragraph}</p>)}</article>)}</div></section>
          <aside className="export-settings-pane">
            <section className={`publication-readiness ${readiness?.status ?? "loading"}`} aria-live="polite">
              <header>
                <div><ShieldAlert size={15} /><span><strong>出版前检查</strong><small>{readinessLoading ? "正在核对整部作品…" : readiness?.status === "ready" ? "可以交付" : readiness?.status === "attention" ? "有事项需要确认" : "暂时无法导出"}</small></span></div>
                <button aria-label="重新检查" disabled={readinessLoading || exporting} title="重新检查" type="button" onClick={() => void loadReadiness()}>{readinessLoading ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />}</button>
              </header>
              {readiness && <div className="publication-summary"><span><strong>{readiness.summary.chapter_count}</strong>章</span><span><strong>{readiness.summary.total_words.toLocaleString("zh-CN")}</strong>字</span><span><strong>{readiness.summary.progress_percent}%</strong>目标</span></div>}
              {readinessError && <p className="publication-readiness-error">{readinessError}</p>}
              {readiness && <div className="publication-checks">{readiness.checks.map((check) => <div className={check.level} key={check.id}>{check.level === "ok" ? <CircleCheck size={13} /> : check.level === "warning" ? <TriangleAlert size={13} /> : <XCircle size={13} />}<span><strong>{check.title}</strong><small>{check.detail}</small></span></div>)}</div>}
              {needsAttention && <label className="publication-acknowledgement"><input checked={attentionAccepted} type="checkbox" onChange={(event) => setAttentionAccepted(event.target.checked)} /><span>我已检查这些提醒，仍导出当前已保存版本</span></label>}
            </section>
            <div className="export-chapter-sequence"><span>篇章顺序</span><div>{ordered.map((chapter, index) => <div key={chapter.id}><strong>{String(index + 1).padStart(2, "0")}</strong><span>{chapter.title}</span></div>)}</div></div>
            <label><span>导出格式</span><select disabled={exporting || Boolean(completedPath)} value={format} onChange={(event) => setFormat(event.target.value as ExportFormat)}><option value="epub">EPUB</option><option value="txt">TXT</option><option value="pdf">PDF</option></select></label>
            <label><span>文件名</span><input disabled={exporting || Boolean(completedPath)} value={fileName} onChange={(event) => setFileName(event.target.value)} /></label>
            <div className="export-path-field"><span>保存路径</span><button disabled={exporting || Boolean(completedPath)} type="button" onClick={() => void chooseDirectory()}>选择路径</button><small>{targetDirectory || "尚未选择目标文件夹"}</small></div>
            <div className="export-progress"><div className="export-progress-track"><div style={{ width: `${progress}%` }} /></div><div><span>{progressMessage}</span><strong>{progress}%</strong></div></div>
            {completedPath && <div className="export-success">{completedPath}</div>}{error && <div className="export-error">{error}</div>}
            <button className="export-confirm" disabled={exportDisabled} type="button" onClick={() => void startExport()}>{completedPath ? "导出完成" : exporting ? "正在导出…" : readiness?.status === "blocked" ? "请先补全正式篇章" : needsAttention && !attentionAccepted ? "请先确认检查提醒" : "确定导出"}</button>
          </aside>
        </div>
      </section>
    </div>
  );
}
