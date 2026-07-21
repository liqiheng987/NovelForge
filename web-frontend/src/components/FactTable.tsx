import { BrainCircuit, Plus, Trash2 } from "lucide-react";
import { api } from "../api/client";
import type { Fact } from "../types";

export default function FactTable({ projectId, facts, onRefresh }: { projectId: string | null; facts: Fact[]; onRefresh: () => Promise<void> }) {
  const add = async () => {
    if (!projectId) return;
    const key = window.prompt("事实名称（如：林舟当前状态）");
    if (!key) return;
    const value = window.prompt("事实值");
    if (!value) return;
    await api("/facts", { method: "POST", body: JSON.stringify({ project_id: projectId, category: "character", key, value, source: "user" }) });
    await onRefresh();
  };
  const remove = async (id: string) => { await api(`/facts/${encodeURIComponent(id)}`, { method: "DELETE" }); await onRefresh(); };
  return <section className="fact-table"><header><div><BrainCircuit size={14} /><strong>结构化记忆</strong><span>{facts.length}/200</span></div><button disabled={!projectId} type="button" onClick={() => void add()}><Plus size={13} /></button></header><div>{facts.slice(0, 20).map((fact) => <article key={fact.id}><div><strong>{fact.key}</strong><p>{fact.value}</p></div><button type="button" onClick={() => void remove(fact.id)}><Trash2 size={12} /></button></article>)}{!facts.length && <p>确认的人物状态、世界规则与开放情节会持续注入对话。</p>}</div></section>;
}
