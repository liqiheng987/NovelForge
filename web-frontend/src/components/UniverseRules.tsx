import { useState } from "react";
import { Gavel, Plus, Trash2 } from "lucide-react";
import type { UniverseRule } from "../types";

type Props = { rules: UniverseRule[]; disabled?: boolean; onCreate: (key: string, value: string, category: UniverseRule["category"]) => Promise<void>; onDelete: (id: string) => Promise<void> };

export default function UniverseRules({ rules, disabled, onCreate, onDelete }: Props) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  const create = async () => {
    const key = window.prompt("铁律名称");
    if (!key) return;
    const value = window.prompt("铁律内容");
    if (!value) return;
    setError("");
    try {
      await onCreate(key, value, "world");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "宇宙铁律创建失败");
    }
  };
  const remove = async (id: string) => {
    setError("");
    try {
      await onDelete(id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "宇宙铁律删除失败");
    }
  };
  return (
    <section className="universe-rules">
      <header><div><Gavel size={15} /><strong>宇宙铁律</strong><span>{rules.length}/100</span></div><button disabled={disabled} type="button" onClick={() => void create()}><Plus size={14} /></button></header>
      <button className="universe-toggle" type="button" onClick={() => setOpen((value) => !value)}>{open ? "收起规则" : "查看规则"}</button>
      {error && <p className="tool-error" role="alert">{error}</p>}
      {open && <div className="universe-rule-list">{rules.map((rule) => <article key={rule.id}><div><strong>{rule.key}</strong><p>{rule.value}</p></div>{!rule.immutable && <button type="button" onClick={() => void remove(rule.id)}><Trash2 size={12} /></button>}</article>)}</div>}
    </section>
  );
}
