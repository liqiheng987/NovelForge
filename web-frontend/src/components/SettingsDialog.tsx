import { useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import {
  Check,
  CircleCheck,
  Database,
  Eye,
  EyeOff,
  FileDown,
  FolderOpen,
  LoaderCircle,
  Plus,
  RefreshCw,
  RotateCcw,
  Server,
  Settings2,
  Trash2,
  TriangleAlert,
  X,
} from "lucide-react";
import { agentFetch, refreshAgentConnection } from "../api/client";
import type { AgentServiceStatus } from "../api/client";

export type ApiProfile = {
  id: string;
  name: string;
  provider: "openai" | "compatible";
  apiKey: string;
  baseUrl: string;
  model: string;
};

export type ApiProfilesState = {
  version: 1;
  activeProfileId: string;
  profiles: ApiProfile[];
};

type ApiTestResult = {
  status: "ok";
  model: string;
  latency_ms: number;
  structured_json: boolean;
};

type TestState =
  | { kind: "idle" }
  | { kind: "testing" }
  | { kind: "success"; message: string }
  | { kind: "error"; message: string };

type BackupState =
  | { kind: "idle"; message: string }
  | { kind: "running"; message: string }
  | { kind: "success"; message: string }
  | { kind: "error"; message: string };

type DatabaseBackup = {
  name: string;
  size: number;
  created_at: string;
  valid: boolean;
  error?: string;
};

type ServiceAction = "restart" | "logs" | "diagnostics" | null;

type SettingsDialogProps = {
  settings: ApiProfilesState;
  onClose: () => void;
  onSave: (settings: ApiProfilesState) => Promise<void>;
};

const createProfile = (index: number): ApiProfile => ({
  id: crypto.randomUUID(),
  name: `兼容 API ${index}`,
  provider: "compatible",
  apiKey: "",
  baseUrl: "",
  model: "",
});

const responseError = async (response: Response) => {
  try {
    const payload = (await response.json()) as { detail?: string };
    return payload.detail || `请求失败（HTTP ${response.status}）`;
  } catch {
    return `请求失败（HTTP ${response.status}）`;
  }
};

const formatBackupSize = (size: number) => {
  if (size < 1024 * 1024) return `${Math.max(1, Math.round(size / 1024))} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
};

const formatBackupTime = (value: string) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
};

const actionError = (error: unknown, fallback: string) =>
  error instanceof Error ? error.message : typeof error === "string" ? error : fallback;

export default function SettingsDialog({
  settings,
  onClose,
  onSave,
}: SettingsDialogProps) {
  const [draft, setDraft] = useState<ApiProfilesState>(() => ({
    ...settings,
    profiles: settings.profiles.map((profile) => ({ ...profile })),
  }));
  const [showApiKey, setShowApiKey] = useState(false);
  const [testState, setTestState] = useState<TestState>({ kind: "idle" });
  const [backupState, setBackupState] = useState<BackupState>({ kind: "idle", message: "每天首次启动自动备份，最多保留 7 份。" });
  const [backups, setBackups] = useState<DatabaseBackup[]>([]);
  const [backupsLoading, setBackupsLoading] = useState(true);
  const [restoringBackup, setRestoringBackup] = useState<string | null>(null);
  const [serviceStatus, setServiceStatus] = useState<AgentServiceStatus>({
    status: "recovering",
    detail: "正在读取 Agent 状态",
    restartCount: 0,
    startedAt: null,
    instanceId: null,
  });
  const [serviceAction, setServiceAction] = useState<ServiceAction>(null);
  const [serviceMessage, setServiceMessage] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const activeProfile = useMemo(
    () =>
      draft.profiles.find((profile) => profile.id === draft.activeProfileId) ??
      draft.profiles[0],
    [draft],
  );
  const profileValid = (profile: ApiProfile) =>
    Boolean(profile.name.trim()) &&
    Boolean(profile.baseUrl.trim()) &&
    Boolean(profile.model.trim()) &&
    (profile.provider === "compatible" || Boolean(profile.apiKey.trim()));
  const canSave =
    Boolean(activeProfile) &&
    profileValid(activeProfile) &&
    draft.profiles.every((profile) => Boolean(profile.name.trim()));

  const loadBackups = async (signal?: AbortSignal) => {
    setBackupsLoading(true);
    try {
      const response = await agentFetch("/maintenance/backups", { signal });
      if (!response.ok) throw new Error(await responseError(response));
      const result = (await response.json()) as { backups: DatabaseBackup[] };
      setBackups(result.backups);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setBackupState({ kind: "error", message: error instanceof Error ? error.message : "无法读取备份列表" });
    } finally {
      if (!signal?.aborted) setBackupsLoading(false);
    }
  };

  useEffect(() => {
    const controller = new AbortController();
    void loadBackups(controller.signal);
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!("__TAURI_INTERNALS__" in window)) {
      setServiceStatus({
        status: "online",
        detail: "浏览器预览模式使用开发 Agent",
        restartCount: 0,
        startedAt: null,
        instanceId: "development",
      });
      return;
    }
    let active = true;
    const loadStatus = async () => {
      try {
        const status = await invoke<AgentServiceStatus>("agent_service_status");
        if (active) setServiceStatus(status);
      } catch (error) {
        if (active) {
          setServiceStatus((current) => ({ ...current, status: "offline", detail: actionError(error, "无法读取 Agent 状态") }));
        }
      }
    };
    void loadStatus();
    const timer = window.setInterval(() => void loadStatus(), 2000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  const updateActiveProfile = (changes: Partial<ApiProfile>) => {
    setDraft((current) => ({
      ...current,
      profiles: current.profiles.map((profile) =>
        profile.id === current.activeProfileId
          ? { ...profile, ...changes }
          : profile,
      ),
    }));
    setTestState({ kind: "idle" });
    setSaveError("");
  };

  const selectProvider = (provider: ApiProfile["provider"]) => {
    updateActiveProfile({
      provider,
      baseUrl:
        provider === "openai" && activeProfile.provider !== "openai"
          ? "https://api.openai.com/v1"
          : activeProfile.baseUrl,
    });
  };

  const addProfile = () => {
    const profile = createProfile(draft.profiles.length + 1);
    setDraft((current) => ({
      ...current,
      activeProfileId: profile.id,
      profiles: [...current.profiles, profile],
    }));
    setTestState({ kind: "idle" });
  };

  const deleteActiveProfile = () => {
    if (draft.profiles.length === 1) return;
    setDraft((current) => {
      const profiles = current.profiles.filter(
        (profile) => profile.id !== current.activeProfileId,
      );
      return { ...current, activeProfileId: profiles[0].id, profiles };
    });
    setTestState({ kind: "idle" });
  };

  const testConnection = async () => {
    if (!profileValid(activeProfile)) return;
    setTestState({ kind: "testing" });
    try {
      const response = await agentFetch("/api/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider: activeProfile.provider,
          api_key: activeProfile.apiKey.trim(),
          base_url: activeProfile.baseUrl.trim(),
          model: activeProfile.model.trim(),
        }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      const result = (await response.json()) as ApiTestResult;
      setTestState({
        kind: "success",
        message: `连接与结构化输出成功 · ${result.model} · ${result.latency_ms} ms`,
      });
    } catch (error) {
      setTestState({
        kind: "error",
        message:
          error instanceof Error && !/failed to fetch/i.test(error.message)
            ? error.message
            : "无法连接 Agent，请确认桌面端服务已启动",
      });
    }
  };

  const createBackup = async () => {
    if (backupState.kind === "running") return;
    setBackupState({ kind: "running", message: "正在创建一致性备份…" });
    try {
      const response = await agentFetch("/maintenance/backup", { method: "POST" });
      if (!response.ok) throw new Error(await responseError(response));
      const result = (await response.json()) as { backup: { name: string; size: number } };
      setBackupState({ kind: "success", message: `备份完成：${result.backup.name}（${formatBackupSize(result.backup.size)}）` });
      await loadBackups();
    } catch (error) {
      setBackupState({ kind: "error", message: error instanceof Error ? error.message : "备份失败，请稍后重试" });
    }
  };

  const restoreBackup = async (backup: DatabaseBackup) => {
    if (!backup.valid || restoringBackup || backupState.kind === "running") return;
    const confirmed = window.confirm(
      `确定恢复 ${formatBackupTime(backup.created_at)} 的创作数据吗？\n\n当前数据会先自动备份。恢复完成后，工作台将重新载入。`,
    );
    if (!confirmed) return;
    setRestoringBackup(backup.name);
    setBackupState({ kind: "running", message: "正在校验备份并安全恢复…" });
    try {
      const response = await agentFetch("/maintenance/restore", {
        method: "POST",
        body: JSON.stringify({ name: backup.name }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      setBackupState({ kind: "success", message: "数据恢复完成，正在重新载入工作台…" });
      window.location.reload();
    } catch (error) {
      setBackupState({ kind: "error", message: error instanceof Error ? error.message : "恢复失败，当前数据未改变" });
      setRestoringBackup(null);
    }
  };

  const restartAgent = async () => {
    if (serviceAction) return;
    if (!window.confirm("确定重新启动 Agent 吗？正在进行的生成会暂停，但已保存进度可以继续。")) return;
    setServiceAction("restart");
    setServiceMessage("正在安全重启 Agent…");
    try {
      const status = await invoke<AgentServiceStatus>("restart_agent");
      setServiceStatus(status);
      setServiceMessage("Agent 已恢复，正在重新载入工作台…");
      await refreshAgentConnection();
      window.location.reload();
    } catch (error) {
      setServiceMessage(actionError(error, "Agent 重启失败，请查看日志"));
      setServiceAction(null);
    }
  };

  const openAgentLogs = async () => {
    if (serviceAction) return;
    setServiceAction("logs");
    setServiceMessage("");
    try {
      await invoke<string>("open_agent_logs");
      setServiceMessage("日志目录已打开");
    } catch (error) {
      setServiceMessage(actionError(error, "无法打开日志目录"));
    } finally {
      setServiceAction(null);
    }
  };

  const exportDiagnostics = async () => {
    if (serviceAction) return;
    setServiceAction("diagnostics");
    setServiceMessage("");
    try {
      const path = await invoke<string | null>("export_diagnostics");
      if (path) setServiceMessage("脱敏诊断报告已导出");
    } catch (error) {
      setServiceMessage(actionError(error, "诊断报告导出失败"));
    } finally {
      setServiceAction(null);
    }
  };

  const saveSettings = async () => {
    if (!canSave || isSaving) return;
    setIsSaving(true);
    setSaveError("");
    const normalized: ApiProfilesState = {
      ...draft,
      profiles: draft.profiles.map((profile) => ({
        ...profile,
        name: profile.name.trim(),
        apiKey: profile.apiKey.trim(),
        baseUrl: profile.baseUrl.trim().replace(/\/+$/, ""),
        model: profile.model.trim(),
      })),
    };
    try {
      await onSave(normalized);
      onClose();
    } catch {
      setSaveError("保存失败，无法写入本机加密配置，请稍后重试。");
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="modal-backdrop settings-backdrop">
      <section
        aria-label="API 设置"
        aria-modal="true"
        className="settings-dialog backdrop-blur-2xl"
        role="dialog"
      >
        <header className="dialog-titlebar">
          <div className="dialog-heading">
            <span className="dialog-icon"><Settings2 size={18} /></span>
            <div>
              <h2>模型与 API</h2>
              <p>保存多个模型配置，并选择当前使用项</p>
            </div>
          </div>
          <button aria-label="关闭设置" className="icon-button" type="button" onClick={onClose}>
            <X size={18} />
          </button>
        </header>

        <div className="settings-manager">
          <aside className="profile-sidebar">
            <div className="profile-sidebar-title">
              <span>配置档案</span>
              <button aria-label="新增配置" type="button" onClick={addProfile}><Plus size={15} /></button>
            </div>
            <div className="profile-list">
              {draft.profiles.map((profile) => (
                <button
                  className={profile.id === draft.activeProfileId ? "active" : ""}
                  key={profile.id}
                  type="button"
                  onClick={() => {
                    setDraft((current) => ({ ...current, activeProfileId: profile.id }));
                    setTestState({ kind: "idle" });
                  }}
                >
                  <span className="profile-provider-dot" />
                  <span><strong>{profile.name || "未命名配置"}</strong><small>{profile.model || "尚未设置模型"}</small></span>
                  {profile.id === draft.activeProfileId && <Check size={15} />}
                </button>
              ))}
            </div>
            <div className="profile-security-note">API Key 使用 Windows DPAPI 加密，只能由当前 Windows 用户解密。</div>
          </aside>

          <div className="settings-content">
            <div className="profile-editor-title">
              <label>
                <span>配置名称</span>
                <input value={activeProfile.name} onChange={(event) => updateActiveProfile({ name: event.target.value })} />
              </label>
              <button disabled={draft.profiles.length === 1} type="button" onClick={deleteActiveProfile}>
                <Trash2 size={15} /> 删除
              </button>
            </div>

            <div className="provider-options">
              <button className={activeProfile.provider === "openai" ? "active" : ""} type="button" onClick={() => selectProvider("openai")}>
                <span className="provider-mark openai-mark">AI</span>
                <span><strong>OpenAI</strong><small>标准 Chat Completions</small></span>
                {activeProfile.provider === "openai" && <Check size={17} />}
              </button>
              <button className={activeProfile.provider === "compatible" ? "active" : ""} type="button" onClick={() => selectProvider("compatible")}>
                <span className="provider-mark"><Server size={18} /></span>
                <span><strong>第三方兼容 API</strong><small>OpenAI 兼容协议</small></span>
                {activeProfile.provider === "compatible" && <Check size={17} />}
              </button>
            </div>

            <div className="settings-form">
              <label>
                <span>API 地址</span>
                <input placeholder="https://api.openai.com/v1" value={activeProfile.baseUrl} onChange={(event) => updateActiveProfile({ baseUrl: event.target.value })} />
                <small>可填写以 /v1 结尾的基础地址，或完整的 /chat/completions 地址</small>
              </label>
              <label>
                <span>API Key {activeProfile.provider === "compatible" && <em>（无鉴权服务可留空）</em>}</span>
                <div className="secret-input">
                  <input autoComplete="off" placeholder="sk-..." type={showApiKey ? "text" : "password"} value={activeProfile.apiKey} onChange={(event) => updateActiveProfile({ apiKey: event.target.value })} />
                  <button aria-label={showApiKey ? "隐藏 API Key" : "显示 API Key"} type="button" onClick={() => setShowApiKey((current) => !current)}>
                    {showApiKey ? <EyeOff size={17} /> : <Eye size={17} />}
                  </button>
                </div>
              </label>
              <label>
                <span>模型 ID</span>
                <input placeholder="例如 gpt-4.1-mini" value={activeProfile.model} onChange={(event) => updateActiveProfile({ model: event.target.value })} />
              </label>
            </div>

            <div className={`api-test-result ${testState.kind}`}>
              {testState.kind === "testing" && <><LoaderCircle className="spin" size={15} /><span>正在向模型发送真实测试请求…</span></>}
              {testState.kind === "success" && <><CircleCheck size={15} /><span>{testState.message}</span></>}
              {testState.kind === "error" && <><TriangleAlert size={15} /><span>{testState.message}</span></>}
              {testState.kind === "idle" && <><Server size={15} /><span>测试会同时验证连接、Key、模型 ID 与结构化 JSON 输出。</span></>}
            </div>
            <div className={`settings-service-panel ${serviceStatus.status}`}>
              <div className="settings-service-status">
                <span className="settings-service-indicator" />
                <span>
                  <strong>本地 Agent 服务</strong>
                  <small>{serviceMessage || serviceStatus.detail}{serviceStatus.restartCount > 0 ? ` · 已恢复 ${serviceStatus.restartCount} 次` : ""}</small>
                </span>
              </div>
              <div className="settings-service-actions">
                <button disabled={Boolean(serviceAction) || !("__TAURI_INTERNALS__" in window)} type="button" onClick={() => void restartAgent()}>
                  {serviceAction === "restart" ? <LoaderCircle className="spin" size={13} /> : <RefreshCw size={13} />}重启
                </button>
                <button disabled={Boolean(serviceAction) || !("__TAURI_INTERNALS__" in window)} type="button" onClick={() => void openAgentLogs()}>
                  <FolderOpen size={13} />日志
                </button>
                <button disabled={Boolean(serviceAction) || !("__TAURI_INTERNALS__" in window)} type="button" onClick={() => void exportDiagnostics()}>
                  {serviceAction === "diagnostics" ? <LoaderCircle className="spin" size={13} /> : <FileDown size={13} />}诊断报告
                </button>
              </div>
            </div>
            <div className={`settings-backup-panel ${backupState.kind}`}>
              <div className="settings-backup-toolbar">
                <div><Database size={16} /><span><strong>创作数据保护</strong><small>{backupState.message}</small></span></div>
                <button disabled={backupState.kind === "running" || Boolean(restoringBackup)} type="button" onClick={() => void createBackup()}>{backupState.kind === "running" && !restoringBackup ? "备份中…" : "立即备份"}</button>
              </div>
              <div className="settings-backup-list" aria-label="可用备份">
                {backupsLoading && <div className="settings-backup-empty"><LoaderCircle className="spin" size={14} />正在检查备份…</div>}
                {!backupsLoading && backups.length === 0 && <div className="settings-backup-empty">还没有可恢复的备份</div>}
                {!backupsLoading && backups.map((backup) => (
                  <div className={`settings-backup-row ${backup.valid ? "valid" : "invalid"}`} key={backup.name}>
                    <span className="settings-backup-status" title={backup.valid ? "完整性检查通过" : backup.error || "备份不可用"}>
                      {backup.valid ? <CircleCheck size={14} /> : <TriangleAlert size={14} />}
                    </span>
                    <span className="settings-backup-meta">
                      <strong>{formatBackupTime(backup.created_at)}</strong>
                      <small>{formatBackupSize(backup.size)} · {backup.valid ? "可恢复" : backup.error || "文件损坏"}</small>
                    </span>
                    <button
                      aria-label={`恢复 ${formatBackupTime(backup.created_at)} 的备份`}
                      disabled={!backup.valid || Boolean(restoringBackup) || backupState.kind === "running"}
                      title={backup.valid ? "恢复此备份" : backup.error || "此备份不可恢复"}
                      type="button"
                      onClick={() => void restoreBackup(backup)}
                    >
                      {restoringBackup === backup.name ? <LoaderCircle className="spin" size={13} /> : <RotateCcw size={13} />}
                      {restoringBackup === backup.name ? "恢复中…" : "恢复"}
                    </button>
                  </div>
                ))}
              </div>
            </div>
            {saveError && <div className="settings-save-error">{saveError}</div>}
          </div>
        </div>

        <footer className="dialog-footer">
          <button className="secondary-button" type="button" onClick={onClose}>取消</button>
          <button className="secondary-button test-api-button" disabled={!profileValid(activeProfile) || testState.kind === "testing"} type="button" onClick={() => void testConnection()}>
            {testState.kind === "testing" ? "测试中…" : "测试连接"}
          </button>
          <button className="primary-button" disabled={!canSave || isSaving} type="button" onClick={() => void saveSettings()}>
            {isSaving ? "加密保存中…" : "保存并使用"}
          </button>
        </footer>
      </section>
    </div>
  );
}
