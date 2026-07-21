import { Network } from "lucide-react";
import type { ImpactHighlight } from "../types";

export default function ImpactVisualization({ impacts }: { impacts: ImpactHighlight[] }) {
  if (!impacts.length) return null;
  return <section className="impact-visualization"><header><Network size={14} /><strong>影响范围</strong><span>{impacts.length}</span></header><div>{impacts.slice(0, 8).map((impact) => <span className={`impact-${impact.action_required}`} key={impact.id}>{impact.relation} · {impact.action_required}</span>)}</div></section>;
}
