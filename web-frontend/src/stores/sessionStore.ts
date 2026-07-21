import { create } from "zustand";
import { api } from "../api/client";
import type { ChatMessage, ChatSession, Mode, Project } from "../types";

type SessionStore = {
  projects: Project[];
  currentProjectId: string | null;
  sessions: ChatSession[];
  currentSessionId: string | null;
  setWorkspace: (projects: Project[], projectId: string | null, sessions: ChatSession[], sessionId: string | null) => void;
  refreshProjects: () => Promise<Project[]>;
  createProject: (title: string, mode?: Mode) => Promise<Project>;
  createSession: (projectId: string, title: string, mode?: Mode) => Promise<ChatSession>;
  switchProject: (projectId: string) => Promise<{ session: ChatSession; messages: ChatMessage[] }>;
  switchSession: (sessionId: string) => Promise<{ session: ChatSession; messages: ChatMessage[] }>;
};

export const useSessionStore = create<SessionStore>((set, get) => ({
  projects: [],
  currentProjectId: null,
  sessions: [],
  currentSessionId: null,
  setWorkspace: (projects, currentProjectId, sessions, currentSessionId) => set({ projects, currentProjectId, sessions, currentSessionId }),
  refreshProjects: async () => {
    const projects = await api<Project[]>("/projects");
    set({ projects });
    return projects;
  },
  createProject: async (title, mode = "guided") => {
    const result = await api<{ project: Project; sessions: ChatSession[] }>("/projects", { method: "POST", body: JSON.stringify({ title, mode }) });
    const projects = await get().refreshProjects();
    set({ projects, currentProjectId: result.project.id, sessions: result.sessions, currentSessionId: result.sessions[0]?.id ?? null });
    return result.project;
  },
  createSession: async (projectId, title, mode = "guided") => {
    const result = await api<{ session: ChatSession }>("/sessions", { method: "POST", body: JSON.stringify({ project_id: projectId, title, mode }) });
    const sessions = await api<ChatSession[]>(`/sessions?project_id=${encodeURIComponent(projectId)}`);
    set({ sessions, currentProjectId: projectId, currentSessionId: result.session.id });
    return result.session;
  },
  switchProject: async (projectId) => {
    const result = await api<{ session: ChatSession; messages: ChatMessage[] }>("/project/switch", { method: "POST", body: JSON.stringify({ project_id: projectId }) });
    const sessions = await api<ChatSession[]>(`/sessions?project_id=${encodeURIComponent(projectId)}`);
    set({ currentProjectId: projectId, currentSessionId: result.session.id, sessions });
    return result;
  },
  switchSession: async (sessionId) => {
    const result = await api<{ project_id: string; session: ChatSession; messages: ChatMessage[] }>("/session/switch", { method: "POST", body: JSON.stringify({ session_id: sessionId }) });
    set({ currentProjectId: result.project_id, currentSessionId: sessionId });
    return result;
  },
}));
