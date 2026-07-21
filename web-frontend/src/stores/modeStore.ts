import { create } from "zustand";
import { api } from "../api/client";
import type { Mode } from "../types";

const descriptions: Record<Mode, string> = {
  guided: "主动提问、给建议、搭骨架",
  collaborative: "共同讨论、多方案选择",
  silent: "按明确指令直接执行",
  traceable: "关键结论附来源标注",
  teaching: "执行同时讲解写作方法",
};

type ModeStore = {
  mode: Mode;
  description: string;
  setLocalMode: (mode: Mode) => void;
  setMode: (sessionId: string, mode: Mode) => Promise<void>;
  getModePrefix: () => string;
};

export const useModeStore = create<ModeStore>((set, get) => ({
  mode: "guided",
  description: descriptions.guided,
  setLocalMode: (mode) => set({ mode, description: descriptions[mode] }),
  setMode: async (sessionId, mode) => {
    const result = await api<{ description: string }>("/mode/switch", { method: "POST", body: JSON.stringify({ session_id: sessionId, mode }) });
    set({ mode, description: result.description });
  },
  getModePrefix: () => `[${get().mode}] ${get().description}`,
}));
