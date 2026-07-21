export const AGENT_URL = "http://127.0.0.1:8000";

export const errorDetail = async (response: Response, fallback: string) => {
  try {
    const value = (await response.json()) as { detail?: string };
    return value.detail || fallback;
  } catch {
    return fallback;
  }
};

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${AGENT_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) throw new Error(await errorDetail(response, "请求失败"));
  return (await response.json()) as T;
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
