import { useMemo, useState } from "react";
import {
  Check,
  CircleCheck,
  Database,
  Eye,
  EyeOff,
  LoaderCircle,
  Plus,
  Server,
  Settings2,
  Trash2,
  TriangleAlert,
  X,
} from "lucide-react";
import { agentFetch } from "../api/client";

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
    return payload.detail || `测试失败（HTTP ${response.status}）`;
  } catch {
    return `测试失败（HTTP ${response.status}）`;
  }
};

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
      setBackupState({ kind: "success", message: `备份完成：${result.backup.name}（${(result.backup.size / 1024 / 1024).toFixed(1)} MB）` });
    } catch (error) {
      setBackupState({ kind: "error", message: error instanceof Error ? error.message : "备份失败，请稍后重试" });
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
            <div className={`settings-backup-card ${backupState.kind}`}>
              <div><Database size={16} /><span><strong>创作数据保护</strong><small>{backupState.message}</small></span></div>
              <button disabled={backupState.kind === "running"} type="button" onClick={() => void createBackup()}>{backupState.kind === "running" ? "备份中…" : "立即备份"}</button>
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
