import { useEffect, useRef, useState } from "react";
import {
  Check,
  CircleAlert,
  Clipboard,
  FilePenLine,
  GitBranch,
  Lightbulb,
  LoaderCircle,
  MessageCircle,
  MessageSquarePlus,
  RefreshCcw,
  Send,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  UserRound,
  Volume2,
  X,
} from "lucide-react";
import { useMaterialSelectionStore } from "../stores/materialStore";
import BranchManager from "./BranchManager";
import ModeSelector from "./ModeSelector";
import InspirationGenerator from "./InspirationGenerator";
import ProjectSettings from "./ProjectSettings";
import FeatureGuideCard from "./FeatureGuideCard";
import type { BranchComparison, ChatMessage, ChatSession, CreationAction, GenerationTask, Mode, Paper, Project, ProjectSettings as Settings } from "../types";

type CenterPanelProps = {
  sessions: ChatSession[];
  projects: Project[];
  currentProjectId: string | null;
  mode: Mode;
  projectId: string | null;
  apiConfig: Record<string, string>;
  currentProject: Project | null;
  activeSessionId: string | null;
  messages: ChatMessage[];
  activeModelLabel: string;
  generating: boolean;
  generationStage: string | null;
  recoveryTask: GenerationTask | null;
  paperActionId: string | null;
  onAbandonPaper: (messageId: string) => Promise<void>;
  onConfirmPaper: (messageId: string) => Promise<void>;
  onCancelGeneration: () => void;
  onDeleteSession: () => Promise<void>;
  onDismissRecovery: () => Promise<void>;
  onNewSession: (title: string) => Promise<void>;
  onRegenerate: (question: string, selectedIds: string[], assistantId: string) => Promise<void>;
  onResumeRecovery: () => Promise<void>;
  onSend: (message: string, paperSourceMessageId: string | null, options?: { creationAction: CreationAction; chapterTargetWords?: number }) => Promise<boolean>;
  onSwitchSession: (sessionId: string) => Promise<void>;
  onSwitchProject: (projectId: string) => Promise<void>;
  onModeChange: (mode: Mode) => Promise<void>;
  onCreateBranch: (name: string) => Promise<void>;
  onCompareBranches: (a: string, b: string) => Promise<BranchComparison | null>;
  onMergeBranches: (source: string, target: string) => Promise<void>;
  onUpdateProjectSettings: (settings: Settings) => Promise<void>;
  onProjectStatus: (status: Project["status"]) => Promise<void>;
  onRenameProject: (title: string) => Promise<void>;
  onDeleteProject: () => Promise<void>;
};

const readText = (content: string) => {
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(content);
  utterance.rate = 1;
  window.speechSynthesis.speak(utterance);
};

function PaperWindow({
  messageId,
  paper,
  busy,
  locked,
  onAbandon,
  onConfirm,
  onModify,
  onContinue,
}: {
  messageId: string;
  paper: Paper;
  busy: boolean;
  locked: boolean;
  onAbandon: () => void;
  onConfirm: () => void;
  onModify: () => void;
  onContinue: () => void;
}) {
  const statusLabel = paper.status === "collected" ? "已收录" : paper.status === "abandoned" ? "已放弃" : "待决定";
  return (
    <section className={`paper-window ${paper.status}`} data-message-id={messageId}>
      <header><div><span className="eyebrow">篇章稿纸</span><strong>{paper.title}</strong></div><span className="paper-status">{paper.status === "collected" && <Check size={13} />}{statusLabel}</span></header>
      <div className="paper-content">{paper.content}</div>
      <footer>
        <span>{paper.word_count ?? paper.content.replace(/\s/g, "").length} 字{paper.target_words ? ` / 目标约 ${paper.target_words} 字` : ""}{paper.length_status === "short" ? " · 篇幅偏短" : paper.length_status === "long" ? " · 篇幅偏长" : ""} · 固定在本轮回复下方</span>
        {paper.status === "draft" && (
          <div>
            <button disabled={busy || locked} type="button" onClick={onAbandon}>放弃</button>
            <button disabled={busy || locked} type="button" onClick={onModify}><FilePenLine size={14} /> 修改</button>
            <button className="paper-confirm" disabled={busy || locked} type="button" onClick={onConfirm}>{busy ? <LoaderCircle className="spin" size={14} /> : <Check size={14} />}确认收录</button>
          </div>
        )}
        {paper.status === "collected" && <div><button className="paper-continue" disabled={locked} type="button" onClick={onContinue}><Sparkles size={14} />承接本章继续写</button></div>}
      </footer>
    </section>
  );
}

export default function CenterPanel({
  sessions,
  projects,
  currentProjectId,
  mode,
  projectId,
  apiConfig,
  currentProject,
  activeSessionId,
  messages,
  activeModelLabel,
  generating,
  generationStage,
  recoveryTask,
  paperActionId,
  onAbandonPaper,
  onConfirmPaper,
  onCancelGeneration,
  onDeleteSession,
  onDismissRecovery,
  onNewSession,
  onRegenerate,
  onResumeRecovery,
  onSend,
  onSwitchSession,
  onSwitchProject,
  onModeChange,
  onCreateBranch,
  onCompareBranches,
  onMergeBranches,
  onUpdateProjectSettings,
  onProjectStatus,
  onRenameProject,
  onDeleteProject,
}: CenterPanelProps) {
  const selectedIds = useMaterialSelectionStore((state) => state.selectedIds);
  const [input, setInput] = useState("");
  const [paperSourceMessageId, setPaperSourceMessageId] = useState<string | null>(null);
  const [creationAction, setCreationAction] = useState<CreationAction>("auto");
  const [chapterTargetWords, setChapterTargetWords] = useState(3000);
  const [activeTool, setActiveTool] = useState<"settings" | "branches" | "inspiration" | null>(null);
  const messageEndRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const generationLocked = generating || Boolean(recoveryTask);

  useEffect(() => {
    setInput(activeSessionId ? window.localStorage.getItem(`novelforge.chat-draft.${activeSessionId}`) ?? "" : "");
    setPaperSourceMessageId(null);
    setCreationAction("auto");
  }, [activeSessionId]);

  useEffect(() => {
    messageEndRef.current?.scrollIntoView({ behavior: generating ? "auto" : "smooth" });
  }, [generating, messages]);

  const submit = async () => {
    const pendingMessage = input;
    const value = pendingMessage.trim();
    if (!value || generating || recoveryTask || !activeSessionId) return;
    setInput("");
    const action = paperSourceMessageId ? "modify" : creationAction;
    if (await onSend(value, paperSourceMessageId, { creationAction: action, chapterTargetWords: ["create", "continue", "modify"].includes(action) ? chapterTargetWords : undefined })) {
      if (activeSessionId) window.localStorage.removeItem(`novelforge.chat-draft.${activeSessionId}`);
      setPaperSourceMessageId(null);
      setCreationAction("auto");
    } else {
      setInput(pendingMessage);
    }
  };

  const createProject = () => {
    const title = window.prompt("新作品名称", "未命名新小说");
    if (title === null) return;
    void onNewSession(title.trim() || "未命名新小说");
  };

  const beginModify = (messageId: string, paper: Paper) => {
    if (recoveryTask) return;
    setPaperSourceMessageId(messageId);
    setCreationAction("modify");
    setChapterTargetWords(Math.max(500, Math.min(12000, paper.word_count ?? paper.content.replace(/\s/g, "").length)));
    setInput(`修改《${paper.title}》：`);
    window.setTimeout(() => inputRef.current?.focus(), 0);
  };

  const continueFromPaper = (paper: Paper) => {
    if (recoveryTask) return;
    setPaperSourceMessageId(null);
    setCreationAction("continue");
    setInput(`承接《${paper.title}》的结尾状态，生成下一章正式篇章。推进一个核心事件，保持人物动机和世界规则连续，并在结尾留下自然钩子。`);
    window.setTimeout(() => inputRef.current?.focus(), 0);
  };

  const chooseTemplate = (action: CreationAction, template: string) => {
    if (recoveryTask) return;
    setPaperSourceMessageId(null);
    setCreationAction(action);
    setInput(template);
    if (activeSessionId) window.localStorage.setItem(`novelforge.chat-draft.${activeSessionId}`, template);
    window.setTimeout(() => inputRef.current?.focus(), 0);
  };

  return (
    <main className="workspace-panel chat-panel glass">
      <header className="chat-header">
        <div className="section-title"><MessageCircle size={18} /><div><h1>作品共创</h1><p>{activeModelLabel} · 每个作品独立管理对话与篇章</p></div></div>
        <div className="chat-header-actions">
          <button aria-label="新建作品" disabled={generating} title="新建作品" type="button" onClick={createProject}><MessageSquarePlus size={15} /><span>新建作品</span></button>
          <button aria-label="删除当前会话" disabled={generating || sessions.length <= 1} title="删除当前会话" type="button" onClick={() => void onDeleteSession()}><Trash2 size={14} /></button>
        </div>
        <div className="session-controls">
          <label className="session-picker"><span>作品</span><select disabled={generating} value={currentProjectId ?? ""} onChange={(event) => void onSwitchProject(event.target.value)}>
            {projects.map((project) => <option key={project.id} value={project.id}>{project.status === "archived" ? "[归档] " : ""}{project.title}</option>)}
          </select></label>
          <label className="session-picker"><span>会话</span><select disabled={generating} value={activeSessionId ?? ""} onChange={(event) => void onSwitchSession(event.target.value)}>
            {sessions.map((session) => <option key={session.id} value={session.id}>{session.branch_name || "主分支"} · {session.title}</option>)}
          </select></label>
          <ModeSelector disabled={generationLocked || !activeSessionId} mode={mode} onChange={(value) => void onModeChange(value)} />
        </div>
      </header>

      <FeatureGuideCard
        title="从新小说规划到连续成章"
        description="选择明确动作，不必再猜关键词"
        items={["先用“规划新小说”形成设定、主线和前三章任务，再生成正式开篇。", "明确写出“连续生成后面 N 章并直接收录、无需确认”可自动完成 1–10 章；每章都会先校验再成为下一章记忆。", "普通生成仍保留稿纸确认；网络短暂断开时自动恢复重试，已经成功收录的篇章不会丢失。"]}
      />

      <section className={`creator-toolbox ${activeTool ? "expanded" : ""}`}>
        <div className="creator-toolbox-bar">
          <div className="creator-toolbox-context"><span>{currentProject?.settings?.workflow === "short" ? "短篇压缩" : currentProject?.settings?.workflow === "serial" ? "连载创作" : currentProject?.settings?.workflow === "collection" ? "短篇合集" : currentProject?.settings?.workflow === "fanfiction" ? "同人衍生" : currentProject?.settings?.workflow === "adaptation" ? "改编创作" : "标准长篇"}</span><i /><span>{sessions.length} 个会话</span></div>
          <div className="creator-tool-buttons">
            <button className={activeTool === "settings" ? "active" : ""} type="button" onClick={() => setActiveTool((tool) => tool === "settings" ? null : "settings")}><SlidersHorizontal size={14} />创作配置</button>
            <button className={activeTool === "branches" ? "active" : ""} type="button" onClick={() => setActiveTool((tool) => tool === "branches" ? null : "branches")}><GitBranch size={14} />版本管理</button>
            <button className={activeTool === "inspiration" ? "active" : ""} type="button" onClick={() => setActiveTool((tool) => tool === "inspiration" ? null : "inspiration")}><Lightbulb size={14} />找灵感</button>
          </div>
        </div>
        {activeTool && <div className="creator-toolbox-content">
          {activeTool === "settings" && <ProjectSettings canDelete={projects.length > 1} disabled={generationLocked} project={currentProject} onDelete={onDeleteProject} onRename={onRenameProject} onUpdate={onUpdateProjectSettings} onStatus={onProjectStatus} />}
          {activeTool === "branches" && <BranchManager disabled={generationLocked} sessions={sessions} currentSessionId={activeSessionId} onCreate={onCreateBranch} onSwitch={onSwitchSession} onCompare={onCompareBranches} onMerge={onMergeBranches} />}
          {activeTool === "inspiration" && <InspirationGenerator projectId={projectId} apiConfig={apiConfig} onSelect={(value) => { setInput(value); setActiveTool(null); }} />}
        </div>}
      </section>

      <div className="message-list">
        {messages.length === 0 && <div className="empty-chat"><Sparkles size={28} /><h3>开始这部作品的创作对话</h3><p>勾选左侧素材只会服务于下一条消息；本作品的篇章独立收录。</p></div>}
        {messages.map((message, index) => {
          const previousUser = message.role === "assistant" ? [...messages.slice(0, index)].reverse().find((item) => item.role === "user") : undefined;
          const isLastAssistant = message.role === "assistant" && index === messages.length - 1;
          return (
            <article className={`message-row ${message.role}`} key={message.id}>
              <div className="message-avatar">{message.role === "user" ? <UserRound size={17} /> : <Sparkles size={17} />}</div>
              <div className="message-body">
                <div className="message-author"><strong>{message.role === "user" ? "你" : "NovelForge Agent"}</strong>{message.selected_material_ids.length > 0 && <span>本轮引用 {message.selected_material_ids.length} 个素材</span>}</div>
                <div className={`message-content ${generating && isLastAssistant ? "streaming" : ""}`}>{message.content || generationStage || "正在组织创作思路…"}</div>
                <div className="message-actions">
                  <button type="button" onClick={() => void navigator.clipboard.writeText(message.content)}><Clipboard size={13} />复制</button>
                  <button type="button" onClick={() => readText(message.content)}><Volume2 size={13} />朗读</button>
                  {isLastAssistant && previousUser && message.paper?.status !== "collected" && (
                    <button disabled={generationLocked} type="button" onClick={() => void onRegenerate(previousUser.content, previousUser.selected_material_ids, message.id)}><RefreshCcw size={13} />重新生成</button>
                  )}
                </div>
                {message.paper && (
                  <PaperWindow
                    busy={paperActionId === message.id}
                    locked={generationLocked}
                    messageId={message.id}
                    paper={message.paper}
                    onAbandon={() => void onAbandonPaper(message.id)}
                    onConfirm={() => void onConfirmPaper(message.id)}
                    onModify={() => beginModify(message.id, message.paper!)}
                    onContinue={() => continueFromPaper(message.paper!)}
                  />
                )}
              </div>
            </article>
          );
        })}
        <div ref={messageEndRef} />
      </div>

      <div className="chat-composer">
        {recoveryTask && (
          <div className="generation-recovery-banner">
            <CircleAlert size={17} />
            <div><strong>{recoveryTask.completed_count > 0 ? `发现未完成任务：已安全收录 ${recoveryTask.completed_count}/${recoveryTask.batch_total} 章` : "发现可继续的生成任务"}</strong><span>{recoveryTask.error || "上次生成在完成前中断，可以从安全进度继续。"}</span></div>
            <button disabled={generating} type="button" onClick={() => void onDismissRecovery()}>放弃</button>
            <button className="resume-generation-button" disabled={generating} type="button" onClick={() => void onResumeRecovery()}><RefreshCcw size={13} />继续任务</button>
          </div>
        )}
        {paperSourceMessageId && (
          <div className="paper-modify-context"><FilePenLine size={13} /><span>正在修改指定稿纸；确认后会更新对应已收录篇章，原稿仍保留在聊天记录中。</span><button aria-label="取消修改" type="button" onClick={() => { setPaperSourceMessageId(null); setCreationAction("auto"); }}><X size={13} /></button></div>
        )}
        <div className="composer-workflow-actions">
          <button className={creationAction === "discuss" ? "active" : ""} disabled={generationLocked} type="button" onClick={() => chooseTemplate("discuss", "帮我梳理这部新小说的核心设定、主角目标、主要冲突、整体阶段和前三章推进方案。只做规划，先不要生成正文。")}>规划新小说</button>
          <button className={creationAction === "create" ? "active" : ""} disabled={generationLocked} type="button" onClick={() => chooseTemplate("create", "根据当前设定、素材和讨论，生成一篇正式开篇。建立主角困境、核心冲突与行动选择，结尾留下可继续发展的钩子。")}>生成正式开篇</button>
          <button className={creationAction === "continue" ? "active" : ""} disabled={generationLocked} type="button" onClick={() => chooseTemplate("continue", "承接上一章结尾，生成下一章正式篇章。保持人物状态、时间顺序和未解线索连续，推进一个核心事件。")}>续写下一章</button>
          <button disabled={generationLocked} type="button" onClick={() => chooseTemplate("continue", "直接承接已收录的最后一章，连续生成后面10章，并将每章通过宇宙铁律、连续性和篇幅检查后直接收入篇章，无需逐章确认。")}>连续生成10章</button>
          {(creationAction === "create" || creationAction === "continue" || paperSourceMessageId) && <label><span>目标</span><select disabled={generationLocked} value={chapterTargetWords} onChange={(event) => setChapterTargetWords(Number(event.target.value))}><option value={1500}>约 1500 字</option><option value={3000}>约 3000 字</option><option value={5000}>约 5000 字</option><option value={8000}>约 8000 字</option></select></label>}
        </div>
        <div className="composer-shortcuts">
          <span><Sparkles size={12} />本轮素材 {selectedIds.length}</span>
          <span>{creationAction === "discuss" ? "本轮只讨论，不生成稿纸" : creationAction === "create" ? "本轮生成新篇章" : creationAction === "continue" ? "本轮承接已收录篇章" : paperSourceMessageId ? "本轮修改指定稿纸" : "自动识别讨论或创作意图"}</span>
        </div>
        <textarea
          ref={inputRef}
          disabled={generating || Boolean(recoveryTask) || !activeSessionId}
          placeholder="讨论创意，或明确命令 Agent 生成正式篇章…"
          value={input}
          onChange={(event) => { setInput(event.target.value); if (activeSessionId) window.localStorage.setItem(`novelforge.chat-draft.${activeSessionId}`, event.target.value); }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void submit();
            }
          }}
        />
        <div className="composer-footer"><span>{generating ? generationStage || "正在创作，可随时停止" : recoveryTask ? "请先继续或放弃未完成任务" : "Enter 发送 · Shift+Enter 换行 · 草稿自动保存"}</span><button aria-label={generating ? "停止生成" : "发送"} className={`send-button ${generating ? "cancel" : ""}`} disabled={!activeSessionId || (!generating && (Boolean(recoveryTask) || !input.trim()))} type="button" onClick={() => generating ? onCancelGeneration() : void submit()}>{generating ? <X size={16} /> : <Send size={16} />}</button></div>
      </div>
    </main>
  );
}
