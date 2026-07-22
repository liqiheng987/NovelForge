import { invoke } from "@tauri-apps/api/core";

export type AgentConnection = {
  baseUrl: string;
  token: string;
  instanceId: string;
};

export type AgentServiceStatus = {
  status: "online" | "recovering" | "offline";
  detail: string;
  restartCount: number;
  startedAt: number | null;
  instanceId: string | null;
};

const developmentConnection: AgentConnection = {
  baseUrl: "http://127.0.0.1:8000",
  token: "",
  instanceId: "development",
};

let connectionPromise: Promise<AgentConnection> | null = null;

export function initializeAgentConnection(): Promise<AgentConnection> {
  if (!("__TAURI_INTERNALS__" in window)) return Promise.resolve(developmentConnection);
  if (!connectionPromise) {
    connectionPromise = invoke<AgentConnection>("get_agent_connection").catch((error) => {
      connectionPromise = null;
      throw error;
    });
  }
  return connectionPromise;
}

export function resetAgentConnection() {
  connectionPromise = null;
}

export function refreshAgentConnection(): Promise<AgentConnection> {
  resetAgentConnection();
  return initializeAgentConnection();
}

export const errorDetail = async (response: Response, fallback: string) => {
  try {
    const value = (await response.json()) as { detail?: string };
    return value.detail || fallback;
  } catch {
    return fallback;
  }
};

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await agentFetch(path, init);
  if (!response.ok) throw new Error(await errorDetail(response, "请求失败"));
  return (await response.json()) as T;
}

export async function agentFetch(path: string, init?: RequestInit): Promise<Response> {
  const request = async (connection: AgentConnection) => {
    const headers = new Headers(init?.headers);
    if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    if (connection.token) headers.set("Authorization", `Bearer ${connection.token}`);
    return fetch(`${connection.baseUrl}${path}`, { ...init, headers });
  };
  const connection = await initializeAgentConnection();
  try {
    return await request(connection);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    if (!("__TAURI_INTERNALS__" in window)) throw error;
    const refreshed = await refreshAgentConnection();
    const method = (init?.method || "GET").toUpperCase();
    if (method === "GET" || method === "HEAD") return request(refreshed);
    throw new Error("Agent 连接已恢复，请重新执行刚才的操作");
  }
}

export type SseHandler = (event: string, data: Record<string, unknown>) => void;

export async function readSse(response: Response, handler: SseHandler) {
  if (!response.body) throw new Error("Agent 没有返回数据流");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() ?? "";
    for (const block of blocks) {
      const lines = block.split(/\r?\n/);
      const event = lines.find((line) => line.startsWith("event:"))?.slice(6).trim() ?? "message";
      const dataText = lines.filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trim()).join("\n");
      if (dataText) handler(event, JSON.parse(dataText) as Record<string, unknown>);
    }
    if (done) break;
  }
}
