import { useState } from "react";
import { BrainCircuit, Plus, Trash2 } from "lucide-react";
import { api } from "../api/client";
import type { Fact } from "../types";

export default function FactTable({ projectId, facts, onRefresh }: { projectId: string | null; facts: Fact[]; onRefresh: () => Promise<void> }) {
  const [error, setError] = useState("");
  const add = async () => {
    if (!projectId) return;
    const key = window.prompt("事实名称（如：林舟当前状态）");
    if (!key) return;
    const value = window.prompt("事实值");
    if (!value) return;
    setError("");
    try {
      await api("/facts", { method: "POST", body: JSON.stringify({ project_id: projectId, category: "character", key, value, source: "user" }) });
      await onRefresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "结构化记忆创建失败");
    }
  };
  const remove = async (id: string) => { setError(""); try { await api(`/facts/${encodeURIComponent(id)}`, { method: "DELETE" }); await onRefresh(); } catch (caught) { setError(caught instanceof Error ? caught.message : "结构化记忆删除失败"); } };
  return <section className="fact-table"><header><div><BrainCircuit size={14} /><strong>结构化记忆</strong><span>{facts.length}/200</span></div><button disabled={!projectId} type="button" onClick={() => void add()}><Plus size={13} /></button></header>{error && <p className="tool-error" role="alert">{error}</p>}<div>{facts.slice(0, 20).map((fact) => <article key={fact.id}><div><strong>{fact.key}</strong><p>{fact.value}</p></div><button type="button" onClick={() => void remove(fact.id)}><Trash2 size={12} /></button></article>)}{!facts.length && <p>确认的人物状态、世界规则与开放情节会持续注入对话。</p>}</div></section>;
}
