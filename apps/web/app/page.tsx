"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Composer } from "@/components/Composer";
import { EmptyState, UserBubble } from "@/components/EmptyState";
import { Slip } from "@/components/Slip";
import { sendFeedback, streamChat } from "@/lib/api";
import { clearSessionId, getDeviceId, getSessionId, setSessionId } from "@/lib/device";
import type { Level, Message, Variant } from "@/lib/types";

const uid = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;

type Status = "checking" | "live" | "down";

export default function Page() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<Status>("checking");
  const [sessionId, setSid] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const mainRef = useRef<HTMLDivElement | null>(null);

  // ---- 启动：探活 + 恢复上次会话 ----
  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const r = await fetch("/api/health");
        if (!r.ok) throw new Error("bad status");
        if (!cancelled) setStatus("live");
      } catch {
        if (!cancelled) setStatus("down");
        return;
      }

      const sid = getSessionId();
      if (!sid) {
        if (!cancelled) setSid(null);
        return;
      }
      try {
        const r = await fetch(`/api/sessions/${sid}/messages`);
        if (!r.ok) throw new Error("no session");
        const data = (await r.json()) as {
          messages: Array<{
            id: string;
            role: "user" | "assistant";
            content: string;
            level?: Level | null;
          }>;
        };
        if (cancelled) return;
        setSid(sid);
        setMessages(
          data.messages.map((m) => ({
            id: m.id,
            role: m.role,
            content: m.content,
            level: m.level ?? undefined,
          }))
        );
      } catch {
        clearSessionId();
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  // ---- 自动滚到底 ----
  const scrollToBottom = useCallback(() => {
    const el = mainRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  // ---- 一次生成 ----
  const run = useCallback(
    async (text: string, variant: Variant = "default", replaceId?: string) => {
      setBusy(true);
      setError(null);

      const asstId = replaceId ?? uid();

      setMessages((prev) =>
        replaceId
          ? prev.map((m) =>
              m.id === replaceId
                ? { ...m, content: "", streaming: true, level: undefined }
                : m
            )
          : [...prev, { id: asstId, role: "assistant", content: "", streaming: true }]
      );

      const ctrl = new AbortController();
      abortRef.current = ctrl;

      await streamChat(
        {
          message: text,
          session_id: sessionId,
          device_id: getDeviceId(),
          variant,
        },
        {
          onMeta: (meta) => {
            setSid(meta.session_id);
            setSessionId(meta.session_id);
            setStatus("live");
            setMessages((prev) =>
              prev.map((m) => (m.id === asstId ? { ...m, level: meta.level } : m))
            );
          },
          onDelta: (t) => {
            setMessages((prev) =>
              prev.map((m) => (m.id === asstId ? { ...m, content: m.content + t } : m))
            );
          },
          onDone: (info) => {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === asstId
                  ? { ...m, streaming: false, serverId: info.message_id ?? m.serverId }
                  : m
              )
            );
          },
          onError: (e) => {
            setError(e.message);
            setStatus("down");
            // 没吐出任何字就把占位删掉，别留一个空药袋
            setMessages((prev) =>
              prev.filter((m) => m.id !== asstId || m.content.trim().length > 0)
            );
          },
        },
        ctrl.signal
      );

      abortRef.current = null;
      setBusy(false);
    },
    [sessionId]
  );

  function submit() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setMessages((prev) => [...prev, { id: uid(), role: "user", content: text }]);
    void run(text);
  }

  function stop() {
    abortRef.current?.abort();
    abortRef.current = null;
    setBusy(false);
    setMessages((prev) => prev.map((m) => (m.streaming ? { ...m, streaming: false } : m)));
  }

  function regenerate(variant: Variant) {
    if (busy) return;
    const last = messages[messages.length - 1];
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    if (!last || last.role !== "assistant" || !lastUser) return;
    void run(lastUser.content, variant, last.id);
  }

  function reset() {
    if (busy) stop();
    clearSessionId();
    setSid(null);
    setMessages([]);
    setError(null);
  }

  const empty = messages.length === 0;

  return (
    <div className="oad-shell">
      <header className="oad-header">
        <div className="oad-header__row">
          <h1 className="oad-brand">
            一日一片<small>ONE A DAY</small>
          </h1>
          <span className="oad-status">
            <span
              className="oad-dot"
              data-state={status === "live" ? "live" : status === "down" ? "down" : ""}
            />
            {status === "live" ? "在线" : status === "down" ? "后端未连上" : "连接中"}
          </span>
        </div>
        <p className="oad-sub">
          一个一本正经讲荒诞的搭子。<em>名字是玩笑，这里不提供任何医疗建议。</em>
        </p>
      </header>

      <main className="oad-main" ref={mainRef}>
        <div className="oad-thread">
          {empty ? (
            <EmptyState
              onPick={(t) => {
                setMessages([{ id: uid(), role: "user", content: t }]);
                void run(t);
              }}
            />
          ) : (
            messages.map((m, i) =>
              m.role === "user" ? (
                <UserBubble key={m.id} message={m} />
              ) : (
                <Slip
                  key={m.id}
                  message={m}
                  canRegenerate={i === messages.length - 1 && !busy}
                  busy={busy}
                  onRegenerate={regenerate}
                  onFeedback={(kind) => sendFeedback(m.serverId ?? m.id, kind)}
                />
              )
            )
          )}

          {error ? <div className="oad-error">{error}</div> : null}

          {!empty && messages.length > 6 ? (
            <div className="oad-notice">
              这是角色扮演，不是心理咨询。真撑不住的时候，找个具体的人说一句。
            </div>
          ) : null}
        </div>
      </main>

      <footer className="oad-footer">
        <Composer
          value={input}
          onChange={setInput}
          onSubmit={submit}
          onStop={stop}
          busy={busy}
        />
        <div className="oad-hint">
          <span>Enter 发送 · Shift+Enter 换行 · 说「展开讲讲」能听长的</span>
          <button type="button" onClick={reset}>
            重新开始
          </button>
        </div>
      </footer>
    </div>
  );
}
