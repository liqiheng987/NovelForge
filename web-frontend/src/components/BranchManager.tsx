import { useState } from "react";
import { GitBranch, GitMerge, GitCompareArrows, Plus, X } from "lucide-react";
import type { BranchComparison, ChatSession } from "../types";

type Props = {
  sessions: ChatSession[];
  currentSessionId: string | null;
  disabled?: boolean;
  onCreate: (name: string) => Promise<void>;
  onSwitch: (id: string) => Promise<void>;
  onCompare: (a: string, b: string) => Promise<BranchComparison | null>;
  onMerge: (source: string, target: string) => Promise<void>;
};

export default function BranchManager({ sessions, currentSessionId, disabled, onCreate, onSwitch, onCompare, onMerge }: Props) {
  const branches = sessions.filter((session) => session.branch_of || session.branch_name !== "主分支");
  const [comparison, setComparison] = useState<BranchComparison | null>(null);
  const compare = async () => {
    const candidates = sessions.slice(0, 2);
    if (candidates.length < 2) return;
    const result = await onCompare(candidates[0].id, candidates[1].id);
    if (result) setComparison(result);
  };
  return (
    <div className="branch-manager">
      <div className="branch-manager-title"><GitBranch size={14} />版本分支 <span>{branches.length}</span></div>
      <select disabled={disabled} value={currentSessionId ?? ""} onChange={(event) => void onSwitch(event.target.value)}>
        {sessions.map((session) => <option key={session.id} value={session.id}>{session.branch_name || "主分支"} · {session.title}</option>)}
      </select>
      <div className="branch-actions">
        <button disabled={disabled || !currentSessionId} type="button" onClick={() => { const name = window.prompt("分支名称", "新方案"); if (name) void onCreate(name); }}><Plus size={13} />分叉</button>
        <button disabled={disabled || sessions.length < 2} type="button" onClick={() => void compare()}><GitCompareArrows size={13} />对比</button>
        <button disabled={disabled || sessions.length < 2} type="button" onClick={() => { const source = sessions.find((session) => session.id !== currentSessionId); if (source && currentSessionId) void onMerge(source.id, currentSessionId); }}><GitMerge size={13} />合并</button>
      </div>
      {comparison && <section className="branch-comparison"><header><strong>版本差异</strong><button type="button" onClick={() => setComparison(null)}><X size={12} /></button></header><div className="branch-diff-summary"><span className="added">新增 {comparison.added.length}</span><span className="deleted">删除 {comparison.deleted.length}</span><span className="modified">修改 {comparison.modified.length}</span></div>{comparison.modified.map((item) => <article key={item.id}><div><strong>原版本</strong><p>{item.old}</p></div><div><strong>新版本</strong><p>{item.new}</p></div></article>)}</section>}
    </div>
  );
}
