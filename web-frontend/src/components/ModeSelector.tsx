import type { Mode } from "../types";

const modes: Array<[Mode, string]> = [
  ["guided", "引导"],
  ["collaborative", "共创"],
  ["silent", "静默"],
  ["traceable", "溯源"],
  ["teaching", "教学"],
];

export default function ModeSelector({ mode, disabled, onChange }: { mode: Mode; disabled?: boolean; onChange: (mode: Mode) => void }) {
  return (
    <label className="mode-selector">
      <span>创作模式</span>
      <select disabled={disabled} value={mode} onChange={(event) => onChange(event.target.value as Mode)}>
        {modes.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select>
    </label>
  );
}
