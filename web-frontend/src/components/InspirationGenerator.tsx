import { useState } from "react";
import { Lightbulb } from "lucide-react";
import { api } from "../api/client";

export default function InspirationGenerator({ apiConfig, projectId, onSelect }: { apiConfig: Record<string, string>; projectId: string | null; onSelect: (value: string) => void }) {
  const [premise, setPremise] = useState("");
  const [dilemma, setDilemma] = useState("");
  const [options, setOptions] = useState<Array<{ title?: string; hook?: string; conflict?: string }>>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generate = async () => {
    if (!premise.trim()) return;
    setBusy(true);
    setError("");
    try {
      const result = await api<{ options: Array<{ title?: string; hook?: string; conflict?: string }> }>("/inspiration/generate", { method: "POST", body: JSON.stringify({ premise, dilemma, project_id: projectId, api_config: apiConfig }) });
      setOptions(result.options);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "灵感生成失败，请稍后重试");
    } finally { setBusy(false); }
  };
  return <section className="inspiration-generator"><header><Lightbulb size={14} /><strong>灵感生成器</strong></header><input value={premise} onChange={(event) => setPremise(event.target.value)} placeholder="当前设定或困境" /><input value={dilemma} onChange={(event) => setDilemma(event.target.value)} placeholder="希望解决的冲突" /><button disabled={busy || !premise.trim()} type="button" onClick={() => void generate()}>{busy ? "生成中…" : "生成 10 个方向"}</button>{error && <p className="tool-error" role="alert">{error}</p>}{options.length > 0 && <ol>{options.map((option, index) => { const text = [option.title, option.hook, option.conflict].filter(Boolean).join("："); return <li key={index}><button type="button" onClick={() => onSelect(text)}><strong>{option.title || `方向 ${index + 1}`}</strong><span>{option.hook || option.conflict}</span></button></li>; })}</ol>}</section>;
}
