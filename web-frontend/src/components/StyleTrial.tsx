import { useState } from "react";
import { Palette } from "lucide-react";
import { api } from "../api/client";

export default function StyleTrial({ apiConfig, projectId }: { apiConfig: Record<string, string>; projectId: string | null }) {
  const [scene, setScene] = useState("");
  const [trials, setTrials] = useState<Array<{ style?: string; text?: string }>>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const run = async () => { if (!scene.trim()) return; setBusy(true); setError(""); try { const result = await api<{ trials: Array<{ style?: string; text?: string }> }>("/style/trial", { method: "POST", body: JSON.stringify({ scene, project_id: projectId, styles: ["cinematic", "literary", "web_novel"], api_config: apiConfig }) }); setTrials(result.trials); } catch (caught) { setError(caught instanceof Error ? caught.message : "风格试写失败，请稍后重试"); } finally { setBusy(false); } };
  return <section className="style-trial"><header><Palette size={14} /><strong>风格试写</strong></header><textarea value={scene} onChange={(event) => setScene(event.target.value)} placeholder="输入同一场景" /><button disabled={busy || !scene.trim()} type="button" onClick={() => void run()}>{busy ? "试写中…" : "并列试写"}</button>{error && <p className="tool-error" role="alert">{error}</p>}{trials.map((trial, index) => <article key={index}><strong>{trial.style || `风格 ${index + 1}`}</strong><p>{trial.text}</p></article>)}</section>;
}
