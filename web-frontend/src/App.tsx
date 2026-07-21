import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { BookOpen, Settings } from "lucide-react";
import { invoke } from "@tauri-apps/api/core";
import CenterPanel from "./components/CenterPanel";
import ExportDialog from "./components/ExportDialog";
import LeftPanel from "./components/LeftPanel";
import RightPanel from "./components/RightPanel";
import SettingsDialog from "./components/SettingsDialog";
import type { ApiProfile, ApiProfilesState } from "./components/SettingsDialog";
import { AGENT_URL, api, errorDetail, readSse } from "./api/client";
import { useChapterStore } from "./stores/chapterStore";
import { useChatStore } from "./stores/chatStore";
import { useMaterialSelectionStore } from "./stores/materialStore";
import { useModeStore } from "./stores/modeStore";
import type { BranchComparison, ChatMessage, ChatSession, Chapter, CreationAction, Fact, FileInfo, ImpactHighlight, Mode, NovelMaterial, Paper, Project, ProjectSettings as ProjectSettingsConfig, StoryNode, Toast, UniverseRule } from "./types";

const SUPPORTED_EXTENSIONS = new Set([".docx", ".pdf", ".txt", ".epub"]);
const DEFAULT_API_PROFILES: ApiProfilesState = { version: 1, activeProfileId: "openai-default", profiles: [{ id: "openai-default", name: "OpenAI", provider: "openai", apiKey: "", baseUrl: "https://api.openai.com/v1", model: "" }] };

function parseApiProfiles(value: string): ApiProfilesState | null {
  try {
    const parsed = JSON.parse(value) as Partial<ApiProfilesState>;
    if (parsed.version !== 1 || !Array.isArray(parsed.profiles)) return null;
    const profiles = parsed.profiles.filter((profile): profile is ApiProfile => Boolean(profile && typeof profile.id === "string" && typeof profile.name === "string" && (profile.provider === "openai" || profile.provider === "compatible") && typeof profile.apiKey === "string" && typeof profile.baseUrl === "string" && typeof profile.model === "string"));
    if (!profiles.length) return null;
    return { version: 1, profiles, activeProfileId: profiles.some((profile) => profile.id === parsed.activeProfileId) ? String(parsed.activeProfileId) : profiles[0].id };
  } catch { return null; }
}

export default function App() {
  const [rustStatus, setRustStatus] = useState("连接中");
  const [agentStatus, setAgentStatus] = useState("连接中");
  const [novels, setNovels] = useState<NovelMaterial[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [currentProjectId, setCurrentProjectId] = useState<string | null>(null);
  const [pendingFiles, setPendingFiles] = useState<FileInfo[]>([]);
  const [pinnedIds, setPinnedIds] = useState<string[]>([]);
  const [universeRules, setUniverseRules] = useState<UniverseRule[]>([]);
  const [impactHighlights, setImpactHighlights] = useState<ImpactHighlight[]>([]);
  const [storyNodes, setStoryNodes] = useState<StoryNode[]>([]);
  const [facts, setFacts] = useState<Fact[]>([]);
  const [materialsLoading, setMaterialsLoading] = useState(true);
  const [analyzing, setAnalyzing] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [generationStage, setGenerationStage] = useState<string | null>(null);
  const [deletingMaterialId, setDeletingMaterialId] = useState<string | null>(null);
  const [paperActionId, setPaperActionId] = useState<string | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [showExport, setShowExport] = useState(false);
  const [toast, setToast] = useState<Toast | null>(null);
  const [apiProfiles, setApiProfiles] = useState<ApiProfilesState>(DEFAULT_API_PROFILES);
  const abortRef = useRef<AbortController | null>(null);

  const sessions = useChatStore((state) => state.sessions);
  const activeSessionId = useChatStore((state) => state.activeSessionId);
  const messages = useChatStore((state) => state.messages);
  const chapters = useChapterStore((state) => state.chapters);
  const selectedChapterId = useChapterStore((state) => state.selectedChapterId);
  const selectedIds = useMaterialSelectionStore((state) => state.selectedIds);
  const clearSelectedIds = useMaterialSelectionStore((state) => state.clearSelectedIds);
  const mode = useModeStore((state) => state.mode);
  const activeProfile = useMemo(() => apiProfiles.profiles.find((profile) => profile.id === apiProfiles.activeProfileId) ?? apiProfiles.profiles[0], [apiProfiles]);
  const apiReady = Boolean(activeProfile?.baseUrl.trim() && activeProfile?.model.trim() && (activeProfile.provider === "compatible" || activeProfile.apiKey.trim()));
  const apiConfig = useCallback(() => ({ provider: activeProfile.provider, api_key: activeProfile.apiKey, base_url: activeProfile.baseUrl, model: activeProfile.model }), [activeProfile]);
  const notify = useCallback((message: string, kind: Toast["kind"] = "error") => setToast({ message, kind }), []);

  useEffect(() => { if (!toast) return; const timer = window.setTimeout(() => setToast(null), 4200); return () => window.clearTimeout(timer); }, [toast]);

  const loadMaterials = useCallback(async () => { setMaterialsLoading(true); try { setNovels(await api<NovelMaterial[]>("/materials")); } finally { setMaterialsLoading(false); } }, []);
  const loadProjectSettings = useCallback(async (projectId: string) => {
    const [pinned, rules, story, memoryFacts] = await Promise.all([api<Array<{ material_id: string }>>(`/pin/material?project_id=${encodeURIComponent(projectId)}`), api<UniverseRule[]>(`/universe/rules?project_id=${encodeURIComponent(projectId)}`), api<StoryNode[]>(`/story/nodes?project_id=${encodeURIComponent(projectId)}`), api<Fact[]>(`/facts?project_id=${encodeURIComponent(projectId)}`)]);
    setPinnedIds(pinned.map((item) => item.material_id)); setUniverseRules(rules); setStoryNodes(story); setFacts(memoryFacts);
  }, []);
  const loadChapters = useCallback(async (projectId: string | null) => { useChapterStore.getState().setChapters(projectId ? await api<Chapter[]>(`/chapters?project_id=${encodeURIComponent(projectId)}`) : []); }, []);
  const refreshProjects = useCallback(async () => { const value = await api<Project[]>("/projects"); setProjects(value); return value; }, []);
  const refreshSessions = useCallback(async (projectId: string) => { const value = await api<ChatSession[]>(`/sessions?project_id=${encodeURIComponent(projectId)}`); useChatStore.getState().setSessions(value); return value; }, []);

  const switchSession = useCallback(async (sessionId: string) => {
    const result = await api<{ project_id: string; session: ChatSession; messages: ChatMessage[] }>("/session/switch", { method: "POST", body: JSON.stringify({ session_id: sessionId }) });
    setCurrentProjectId(result.project_id); useChatStore.getState().setActiveSession(sessionId, result.messages);
    await refreshSessions(result.project_id); await loadProjectSettings(result.project_id); await loadChapters(result.project_id); useModeStore.getState().setLocalMode(result.session.mode || "guided");
  }, [loadChapters, loadProjectSettings, refreshSessions]);

  useEffect(() => {
    if ("__TAURI_INTERNALS__" in window) { invoke<string | null>("load_api_profiles").then((value) => { const restored = value ? parseApiProfiles(value) : null; if (restored) setApiProfiles(restored); }).catch(() => undefined); invoke<string>("ping").then((value) => setRustStatus(value === "pong" ? "在线" : value)).catch(() => setRustStatus("离线")); }
    let active = true;
    const connect = async () => {
      for (let attempt = 0; attempt < 60 && active; attempt += 1) {
        try {
          await api("/health"); setAgentStatus("在线"); await loadMaterials();
          const availableProjects = await refreshProjects(); const current = availableProjects.find((project) => project.active) ?? availableProjects[0];
          if (current) { setCurrentProjectId(current.id); const availableSessions = await refreshSessions(current.id); const session = availableSessions.find((item) => item.active) ?? availableSessions[0]; if (session) await switchSession(session.id); }
          return;
        } catch { await new Promise((resolve) => window.setTimeout(resolve, 500)); }
      }
      if (active) { setAgentStatus("离线"); setMaterialsLoading(false); notify("AI Agent 未就绪，请重启应用"); }
    };
    void connect(); return () => { active = false; abortRef.current?.abort(); };
  }, [loadMaterials, notify, refreshProjects, refreshSessions, switchSession]);

  const stagePaths = useCallback(async (paths: string[]) => {
    const unique = [...new Set(paths)].filter((path) => SUPPORTED_EXTENSIONS.has(path.slice(path.lastIndexOf(".")).toLowerCase())).slice(0, 5);
    if (!unique.length || unique.length + pendingFiles.length > 5) return notify("最多支持 5 个素材文件");
    try { const metadata = await invoke<FileInfo[]>("get_file_metadata", { paths: unique }); setPendingFiles((current) => [...current, ...metadata.map((file) => ({ ...file, genre_hint: "" }))]); } catch { notify("无法读取素材文件"); }
  }, [notify, pendingFiles.length]);

  const analyzeFiles = async () => {
    if (!pendingFiles.length || analyzing) return; if (!apiReady) { setShowSettings(true); return; } setAnalyzing(true);
    try {
      const response = await fetch(`${AGENT_URL}/analyze`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ paths: pendingFiles.map((file) => file.path), genre_hints: Object.fromEntries(pendingFiles.filter((file) => file.genre_hint).map((file) => [file.path, file.genre_hint])), api_config: apiConfig(), project_id: currentProjectId }) });
      if (!response.ok) throw new Error(await errorDetail(response, "素材分析失败"));
      let result: { materials: NovelMaterial[]; imports: Array<{ warnings: string[] }>; errors: Array<{ file_name: string; message: string }> } | null = null;
      await readSse(response, (event, data) => { if (event === "progress") { const dimension = String(data.dimension ?? ""); const longProgress = ["长篇区域", "章节建档", "重要章精读", "全书区域", "区域重试"].some((prefix) => dimension.startsWith(prefix)); const finished = data.status === "done" && longProgress; if (data.status === "analyzing" || finished) notify(`${finished ? "已完成" : "正在分析"} ${String(data.file ?? "素材")} · ${dimension}`, "info"); } if (event === "done") result = data as unknown as typeof result; if (event === "error") throw new Error(String(data.message ?? "素材分析失败")); });
      if (!result) throw new Error("分析服务未返回结果"); const parsedResult = result as { materials: NovelMaterial[]; imports: Array<{ warnings: string[] }>; errors: Array<{ file_name: string; message: string }> }; setNovels(parsedResult.materials); const failed = new Set(parsedResult.errors.map((item) => item.file_name)); setPendingFiles((current) => current.filter((file) => failed.has(file.name))); notify(parsedResult.errors.length ? `已导入 ${parsedResult.imports.length} 个文件，${parsedResult.errors.length} 个失败` : "素材分析完成", parsedResult.errors.length ? "info" : "success");
    } catch (error) { notify(error instanceof Error ? error.message : "素材分析失败"); } finally { setAnalyzing(false); }
  };

  const togglePinned = async (materialId: string) => {
    if (!currentProjectId) return;
    try { if (pinnedIds.includes(materialId)) await api(`/pin/material/${encodeURIComponent(materialId)}?project_id=${encodeURIComponent(currentProjectId)}`, { method: "DELETE" }); else await api("/pin/material", { method: "POST", body: JSON.stringify({ project_id: currentProjectId, material_id: materialId }) }); await loadProjectSettings(currentProjectId); } catch (error) { notify(error instanceof Error ? error.message : "常驻素材更新失败"); }
  };

  const runChat = async (question: string, paperSourceMessageId: string | null, regenerateAssistantId?: string, selectedSnapshot?: string[], creationAction: CreationAction = "auto", chapterTargetWords?: number) => {
    if (!activeSessionId || generating) return false; if (!apiReady) { setShowSettings(true); return false; }
    const snapshot = selectedSnapshot ?? [...selectedIds]; const state = useChatStore.getState(); const assistantPending = `pending-${crypto.randomUUID()}`; const userPending = `pending-user-${crypto.randomUUID()}`; const timestamp = new Date().toISOString();
    if (regenerateAssistantId) state.setMessages(state.messages.map((message) => message.id === regenerateAssistantId ? { ...message, id: assistantPending, content: "", paper: null, has_paper: false } : message));
    else { state.setMessages([...state.messages, { id: userPending, session_id: activeSessionId, role: "user", content: question, selected_material_ids: snapshot, has_paper: false, paper: null, created_at: timestamp }, { id: assistantPending, session_id: activeSessionId, role: "assistant", content: "", selected_material_ids: [], has_paper: false, paper: null, created_at: timestamp }]); clearSelectedIds(); }
    setGenerating(true); setGenerationStage("正在准备创作上下文…"); const controller = new AbortController(); abortRef.current = controller; let assistantId = assistantPending; let completed = false; let content = ""; let autoCollectedCount = 0; let autoCollectTotal = 0; let autoCollectPartial = ""; const collectedTitles: string[] = [];
    try {
      const response = await fetch(`${AGENT_URL}/chat`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_id: activeSessionId, project_id: currentProjectId, mode, message: question, selected_material_ids: snapshot, api_config: apiConfig(), regenerate_assistant_id: regenerateAssistantId ?? null, paper_source_message_id: paperSourceMessageId, creation_action: creationAction, chapter_target_words: chapterTargetWords ?? null }), signal: controller.signal });
      if (!response.ok) throw new Error(await errorDetail(response, "对话生成失败"));
      await readSse(response, (event, data) => {
        if (event === "start") {
          assistantId = String(data.assistant_message_id);
          state.updateMessage(assistantPending, { id: assistantId });
          if (data.user_message) state.updateMessage(userPending, data.user_message as Partial<ChatMessage>);
        } else if (event === "stage") setGenerationStage(String(data.message ?? "正在创作…"));
        else if (event === "delta") { content += String(data.content ?? ""); state.updateMessage(assistantId, { content }); }
        else if (event === "paper") state.updateMessage(assistantId, { paper: data.paper as Paper, has_paper: true });
        else if (event === "auto_collect_start") {
          autoCollectTotal = Number(data.total ?? 1);
          setGenerationStage(`准备连续生成并收录 ${autoCollectTotal} 章…`);
        } else if (event === "auto_collected") {
          autoCollectedCount = Number(data.index ?? autoCollectedCount + 1);
          autoCollectTotal = Number(data.total ?? autoCollectTotal);
          collectedTitles.push(`第 ${autoCollectedCount}/${autoCollectTotal} 章《${String(data.title ?? "未命名篇章")}》已收录`);
          state.updateMessage(assistantId, { content: collectedTitles.join("\n") });
          setGenerationStage(`已生成并收录 ${autoCollectedCount}/${autoCollectTotal} 章`);
        } else if (event === "auto_collect_partial") {
          autoCollectPartial = String(data.message ?? "后续篇章生成中断");
        } else if (event === "impact") setImpactHighlights((data.affected_nodes as ImpactHighlight[] | undefined) ?? []);
        else if (event === "workflow") void refreshProjects();
        else if (event === "compliance") { const findings = data.findings as unknown[] | undefined; if (findings?.length) notify(`出版合规检查发现 ${findings.length} 项，请在稿纸中审阅`, "info"); }
        else if (event === "done") { completed = true; if (!autoCollectedCount) state.updateMessage(assistantId, data.message as Partial<ChatMessage>); }
        else if (event === "error") throw new Error(String(data.message ?? "对话生成失败"));
      });
      if (!completed) throw new Error("AI 服务连接中断");
      if (autoCollectedCount) {
        await switchSession(activeSessionId);
        if (currentProjectId) await loadChapters(currentProjectId);
        notify(autoCollectPartial ? `已收录 ${autoCollectedCount}/${autoCollectTotal} 章；${autoCollectPartial}` : `已连续生成并收录 ${autoCollectedCount} 章`, autoCollectPartial ? "info" : "success");
      } else await refreshSessions(currentProjectId ?? "");
      return true;
    } catch (error) { const aborted = error instanceof DOMException && error.name === "AbortError"; notify(aborted ? "已停止本轮生成，输入和素材选择已恢复" : error instanceof Error ? error.message : "对话生成失败", aborted ? "info" : "error"); try { await switchSession(activeSessionId); } catch { state.setMessages(state.messages.filter((item) => item.id !== assistantId && item.id !== assistantPending)); } if (!regenerateAssistantId) useMaterialSelectionStore.getState().setSelectedIds(snapshot); return false; } finally { abortRef.current = null; setGenerating(false); setGenerationStage(null); }
  };

  const cancelGeneration = () => abortRef.current?.abort();

  const createProject = async (title: string) => { try { const result = await api<{ project: Project; sessions: ChatSession[] }>("/projects", { method: "POST", body: JSON.stringify({ title, mode: "guided" }) }); setProjects(await refreshProjects()); setCurrentProjectId(result.project.id); useChatStore.getState().setSessions(result.sessions); await switchSession(result.sessions[0].id); } catch (error) { notify(error instanceof Error ? error.message : "创建作品失败"); } };
  const switchProject = async (projectId: string) => { try { const result = await api<{ session: ChatSession; messages: ChatMessage[] }>("/project/switch", { method: "POST", body: JSON.stringify({ project_id: projectId }) }); setCurrentProjectId(projectId); useChatStore.getState().setActiveSession(result.session.id, result.messages); await refreshSessions(projectId); await loadProjectSettings(projectId); await loadChapters(projectId); useModeStore.getState().setLocalMode(result.session.mode); } catch (error) { notify(error instanceof Error ? error.message : "作品切换失败"); } };
  const createBranch = async (name: string) => { if (!currentProjectId || !activeSessionId) return; try { const result = await api<{ branch: ChatSession }>("/branch/create", { method: "POST", body: JSON.stringify({ project_id: currentProjectId, source_session_id: activeSessionId, name }) }); await refreshSessions(currentProjectId); await switchSession(result.branch.id); } catch (error) { notify(error instanceof Error ? error.message : "创建分支失败"); } };
  const compareBranches = async (a: string, b: string): Promise<BranchComparison | null> => { if (!currentProjectId) return null; try { return await api<BranchComparison>("/branch/compare", { method: "POST", body: JSON.stringify({ project_id: currentProjectId, branch_a_id: a, branch_b_id: b }) }); } catch (error) { notify(error instanceof Error ? error.message : "分支对比失败"); return null; } };
  const mergeBranches = async (source: string, target: string) => { if (!currentProjectId) return; try { await api("/branch/merge", { method: "POST", body: JSON.stringify({ project_id: currentProjectId, source_session_id: source, target_session_id: target }) }); notify("分支已合并", "success"); } catch (error) { notify(error instanceof Error ? error.message : "分支合并失败"); } };
  const changeMode = async (value: Mode) => { if (!activeSessionId) return; try { await useModeStore.getState().setMode(activeSessionId, value); useChatStore.getState().setSessions(useChatStore.getState().sessions.map((session) => session.id === activeSessionId ? { ...session, mode: value } : session)); } catch (error) { notify(error instanceof Error ? error.message : "模式切换失败"); } };
  const deleteSession = async () => { if (!activeSessionId || !window.confirm("确定删除当前会话吗？篇章按作品保留。")) return; try { const result = await api<{ active_session_id: string }>(`/sessions/${encodeURIComponent(activeSessionId)}`, { method: "DELETE" }); await switchSession(result.active_session_id); } catch (error) { notify(error instanceof Error ? error.message : "删除会话失败"); } };
  const updatePaper = async (messageId: string, action: "confirm" | "abandon") => { setPaperActionId(messageId); try { const result = await api<{ paper: Paper; chapter?: Chapter; affected_nodes?: ImpactHighlight[] }>("/chapter/update", { method: "POST", body: JSON.stringify({ action, message_id: messageId }) }); useChatStore.getState().updateMessage(messageId, { paper: result.paper, has_paper: true }); if (result.affected_nodes) setImpactHighlights(result.affected_nodes); if (action === "confirm" && currentProjectId) { await loadChapters(currentProjectId); notify("篇章已确认收录", "success"); } } catch (error) { notify(error instanceof Error ? error.message : "稿件操作失败"); } finally { setPaperActionId(null); } };
  const reorder = async (chapterIds: string[]) => { if (!currentProjectId) return; try { await api("/chapters/reorder", { method: "POST", body: JSON.stringify({ project_id: currentProjectId, chapter_ids: chapterIds }) }); await loadChapters(currentProjectId); } catch (error) { notify(error instanceof Error ? error.message : "篇章排序失败"); } };
  const editChapter = async (chapterId: string, title: string, content: string) => { const result = await api<{ affected_nodes?: ImpactHighlight[] }>("/chapter/update", { method: "POST", body: JSON.stringify({ action: "edit", chapter_id: chapterId, title, content }) }); if (result.affected_nodes) setImpactHighlights(result.affected_nodes); if (currentProjectId) await loadChapters(currentProjectId); };
  const removeChapter = async (chapterId: string) => { await api(`/chapter/delete?chapter_id=${encodeURIComponent(chapterId)}`, { method: "DELETE" }); if (currentProjectId) await loadChapters(currentProjectId); };
  const createRule = async (key: string, value: string, category: UniverseRule["category"]) => { if (!currentProjectId) return; await api("/universe/rule", { method: "POST", body: JSON.stringify({ project_id: currentProjectId, key, value, category, immutable: true }) }); await loadProjectSettings(currentProjectId); };
  const deleteRule = async (id: string) => { await api(`/universe/rule/${encodeURIComponent(id)}`, { method: "DELETE" }); if (currentProjectId) await loadProjectSettings(currentProjectId); };
  const updateSettings = async (settings: ProjectSettingsConfig) => { if (!currentProjectId) return; const updated = await api<Project>(`/projects/${encodeURIComponent(currentProjectId)}/settings`, { method: "PATCH", body: JSON.stringify(settings) }); setProjects((current) => current.map((project) => project.id === updated.id ? updated : project)); };
  const updateProjectStatus = async (status: Project["status"]) => { if (!currentProjectId) return; const updated = await api<Project>(`/projects/${encodeURIComponent(currentProjectId)}/status`, { method: "POST", body: JSON.stringify({ status }) }); setProjects((current) => current.map((project) => project.id === updated.id ? updated : project)); notify(status === "archived" ? "作品已归档，可随时恢复" : "作品已恢复", "success"); };
  const refreshStory = async () => { if (currentProjectId) setStoryNodes(await api<StoryNode[]>(`/story/nodes?project_id=${encodeURIComponent(currentProjectId)}`)); };
  const refreshFacts = async () => { if (currentProjectId) setFacts(await api<Fact[]>(`/facts?project_id=${encodeURIComponent(currentProjectId)}`)); };

  const activeProjectTitle = projects.find((project) => project.id === currentProjectId)?.title ?? "当前作品";
  return <div className="app-shell"><header className="app-topbar glass"><div className="brand-block"><span className="brand-logo"><BookOpen size={20} /></span><div><strong>NovelForge</strong><span>AI 小说创作工作台</span></div></div><div className="topbar-actions"><div className="service-health"><span className={rustStatus === "在线" ? "online" : "offline"} />桌面端 <span className={agentStatus === "在线" ? "online" : "offline"} />Agent</div><button className="settings-trigger" type="button" onClick={() => setShowSettings(true)}><Settings size={17} />设置</button></div></header><PanelGroup className="workspace" direction="horizontal"><Panel className="panel-slot" defaultSize={28} minSize={20}><LeftPanel apiConfig={apiConfig()} analyzing={analyzing} deletingMaterialId={deletingMaterialId} loading={materialsLoading} novels={novels} pendingFiles={pendingFiles} pinnedIds={pinnedIds} onTogglePinned={togglePinned} onAnalyze={() => void analyzeFiles()} onClearPending={() => setPendingFiles([])} onDeleteMaterial={async (id) => { setDeletingMaterialId(id); try { await api(`/materials/${encodeURIComponent(id)}`, { method: "DELETE" }); await loadMaterials(); } catch (error) { notify(error instanceof Error ? error.message : "素材删除失败"); } finally { setDeletingMaterialId(null); } }} onPickFiles={async () => { try { await stagePaths(await invoke<string[]>("open_files")); } catch { notify("无法打开文件选择器"); } }} onRemovePending={(path) => setPendingFiles((current) => current.filter((file) => file.path !== path))} onSetGenreHint={(path, genreHint) => setPendingFiles((current) => current.map((file) => file.path === path ? { ...file, genre_hint: genreHint } : file))} onStagePaths={(paths) => void stagePaths(paths)} onToast={(message) => notify(message)} /></Panel><PanelResizeHandle className="resize-handle" /><Panel className="panel-slot" defaultSize={50} minSize={38}><CenterPanel activeModelLabel={apiReady ? `${activeProfile.name} · ${activeProfile.model}` : "请在设置中配置并测试模型"} activeSessionId={activeSessionId} projects={projects} currentProjectId={currentProjectId} currentProject={projects.find((project) => project.id === currentProjectId) ?? null} projectId={currentProjectId} apiConfig={apiConfig()} mode={mode} generating={generating} generationStage={generationStage} messages={messages} paperActionId={paperActionId} sessions={sessions} onAbandonPaper={(id) => updatePaper(id, "abandon")} onCancelGeneration={cancelGeneration} onConfirmPaper={(id) => updatePaper(id, "confirm")} onDeleteSession={deleteSession} onNewSession={createProject} onRegenerate={(question, ids, assistantId) => runChat(question, null, assistantId, ids).then(() => undefined)} onSend={(message, sourceId, options) => runChat(message, sourceId, undefined, undefined, options?.creationAction, options?.chapterTargetWords)} onSwitchSession={switchSession} onSwitchProject={switchProject} onModeChange={changeMode} onCreateBranch={createBranch} onCompareBranches={compareBranches} onMergeBranches={mergeBranches} onUpdateProjectSettings={updateSettings} onProjectStatus={updateProjectStatus} /></Panel><PanelResizeHandle className="resize-handle" /><Panel className="panel-slot" defaultSize={22} minSize={17}><RightPanel facts={facts} onRefreshFacts={refreshFacts} projects={projects} sessionId={activeSessionId} storyNodes={storyNodes} onRefreshStory={refreshStory} onToast={(message) => notify(message, "success")} chapters={chapters} projectTitle={activeProjectTitle} selectedChapterId={selectedChapterId} projectId={currentProjectId} apiConfig={apiConfig()} rules={universeRules} impacts={impactHighlights} onCreateRule={createRule} onDeleteRule={deleteRule} onDelete={removeChapter} onEdit={editChapter} onExport={() => setShowExport(true)} onReorder={reorder} onSelect={(id) => useChapterStore.getState().selectChapter(id)} /></Panel></PanelGroup>{showSettings && <SettingsDialog settings={apiProfiles} onClose={() => setShowSettings(false)} onSave={async (settings) => { if ("__TAURI_INTERNALS__" in window) await invoke("save_api_profiles", { value: JSON.stringify(settings) }); setApiProfiles(settings); setShowSettings(false); }} />}{showExport && activeSessionId && <ExportDialog chapters={chapters} sessionId={activeSessionId} onClose={() => setShowExport(false)} />}{toast && <div className={`toast ${toast.kind}`} role="status">{toast.message}</div>}</div>;
}
