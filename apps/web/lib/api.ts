import type { ChatMeta, Level, Variant } from "./types";

export type StreamPayload = {
  message: string;
  session_id?: string | null;
  device_id: string;
  variant?: Variant;
  expand?: boolean;
};

export type StreamHandlers = {
  onMeta?: (meta: ChatMeta) => void;
  onDelta?: (text: string) => void;
  onDone?: (info: { message_id?: string; level?: Level }) => void;
  onError?: (err: Error) => void;
};

function parseBlock(block: string): { event: string; data: string } | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (!dataLines.length) return null;
  return { event, data: dataLines.join("\n") };
}

/**
 * 消费后端 SSE。用 fetch + ReadableStream 而不是 EventSource，
 * 因为 EventSource 只能 GET，带不了 body。
 */
export async function streamChat(
  payload: StreamPayload,
  handlers: StreamHandlers,
  signal?: AbortSignal
): Promise<void> {
  let res: Response;
  try {
    res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
  } catch (err) {
    if ((err as Error).name === "AbortError") return;
    handlers.onError?.(new Error("连不上后端。确认 services/api 已经启动。"));
    return;
  }

  if (!res.ok || !res.body) {
    let detail = `请求失败（${res.status}）`;
    try {
      const j = await res.json();
      if (j?.detail) detail = String(j.detail);
    } catch {
      /* 保持默认信息 */
    }
    handlers.onError?.(new Error(detail));
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const blocks = buffer.split("\n\n");
      buffer = blocks.pop() ?? "";

      for (const block of blocks) {
        if (!block.trim()) continue;
        const parsed = parseBlock(block);
        if (!parsed) continue;
        let obj: unknown;
        try {
          obj = JSON.parse(parsed.data);
        } catch {
          continue;
        }
        if (parsed.event === "meta") handlers.onMeta?.(obj as ChatMeta);
        else if (parsed.event === "delta") {
          const t = (obj as { text?: string }).text ?? "";
          if (t) handlers.onDelta?.(t);
        } else if (parsed.event === "done") {
          handlers.onDone?.(obj as { message_id?: string; level?: Level });
        }
      }
    }
  } catch (err) {
    if ((err as Error).name !== "AbortError") {
      handlers.onError?.(new Error("连接中断了，可能是后端重启。"));
    }
  }
}
