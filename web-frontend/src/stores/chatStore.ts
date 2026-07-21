import { create } from "zustand";
import type { ChatMessage, ChatSession } from "../types";

type ChatStore = {
  sessions: ChatSession[];
  activeSessionId: string | null;
  messages: ChatMessage[];
  setSessions: (sessions: ChatSession[]) => void;
  setActiveSession: (sessionId: string, messages: ChatMessage[]) => void;
  setMessages: (messages: ChatMessage[]) => void;
  updateMessage: (messageId: string, update: Partial<ChatMessage>) => void;
};

export const useChatStore = create<ChatStore>((set) => ({
  sessions: [],
  activeSessionId: null,
  messages: [],
  setSessions: (sessions) => set({ sessions }),
  setActiveSession: (activeSessionId, messages) => set({ activeSessionId, messages }),
  setMessages: (messages) => set({ messages }),
  updateMessage: (messageId, update) =>
    set((state) => ({
      messages: state.messages.map((message) =>
        message.id === messageId ? { ...message, ...update } : message,
      ),
    })),
}));
