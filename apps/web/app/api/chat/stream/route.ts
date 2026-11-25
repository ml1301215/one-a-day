/**
 * SSE 透传。
 *
 * 为什么要有这个文件：next.config 里的 `rewrites` 会把整个响应缓冲完再吐给浏览器，
 * 实测经反代的 32 个 delta 块到达时间跨度是 0.00s（直连 FastAPI 是 0.71s）。
 * 结果是「逐字浮现」在浏览器里根本不成立——用户干等十几秒，然后整段蹦出来。
 *
 * 这里用 Route Handler 直接转发上游 ReadableStream，不经过 rewrites，
 * 字节到一块就往下写一块。
 */

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const API_ORIGIN = process.env.API_ORIGIN || "http://127.0.0.1:8000";

export async function POST(req: Request) {
  const body = await req.text();

  let upstream: Response;
  try {
    upstream = await fetch(`${API_ORIGIN}/api/chat/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body,
      cache: "no-store",
      // 浏览器断开时，顺带把上游也断掉，别让后端空转
      signal: req.signal,
    });
  } catch {
    return Response.json(
      { detail: "连不上后端。确认 services/api 已经启动。" },
      { status: 502 }
    );
  }

  if (!upstream.ok || !upstream.body) {
    const detail = await upstream.text().catch(() => "");
    return new Response(detail || `上游返回 ${upstream.status}`, {
      status: upstream.status || 502,
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      "X-Accel-Buffering": "no",
      Connection: "keep-alive",
    },
  });
}
