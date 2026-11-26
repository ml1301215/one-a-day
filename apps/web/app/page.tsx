"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Composer } from "@/components/Composer";
import { EmptyState, UserBubble } from "@/components/EmptyState";
import { Slip } from "@/components/Slip";
import { streamChat } from "@/lib/api";
import { clearSessionId, getDeviceId, getSessionId, setSessionId } from "@/lib/device";
import type { Message, Variant } from "@/lib/types";

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
  const [scrolled, setScrolled] = useState(false);
  const [atBottom, setAtBottom] = useState(true);

  const abortRef = useRef<AbortController | null>(null);
  const mainRef = useRef<HTMLDivElement | null>(null);
  const threadRef = useRef<HTMLDivElement | null>(null);
  /** 用户是否还在「跟随最新消息」。滚开超过 80px 就停 */
  const followRef = useRef(true);

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
          messages: Array<{ id: string; role: "user" | "assistant"; content: string }>;
        };
        if (cancelled) return;
        setSid(sid);
        setMessages(
          data.messages.map((m) => ({
            id: m.id,
            role: m.role,
            content: m.content,
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

  // ---- 滚动：只在用户本来就在底部附近时跟随 ----
  const syncScroll = useCallback(() => {
    const el = mainRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    const nearBottom = distance <= 80;
    followRef.current = nearBottom;
    setAtBottom(nearBottom);
    setScrolled(el.scrollTop > 4);
  }, []);

  useEffect(() => {
    const el = mainRef.current;
    if (!el) return;

    el.addEventListener("scroll", syncScroll, { passive: true });

    // 内容长高时才滚，不用定时器
    const ro = new ResizeObserver(() => {
      if (followRef.current) el.scrollTop = el.scrollHeight;
      else syncScroll();
    });
    ro.observe(el);
    if (threadRef.current) ro.observe(threadRef.current);

    return () => {
      el.removeEventListener("scroll", syncScroll);
      ro.disconnect();
    };
  }, [syncScroll]);

  useEffect(() => {
    const el = mainRef.current;
    if (!el || !followRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [messages]);

  const jumpToBottom = useCallback(() => {
    const el = mainRef.current;
    if (!el) return;
    followRef.current = true;
    setAtBottom(true);
    el.scrollTop = el.scrollHeight;
  }, []);

  // ---- 一次生成 ----
  const run = useCallback(
    async (text: string, variant: Variant = "default", replaceId?: string) => {
      setBusy(true);
      setError(null);

      const asstId = replaceId ?? uid();

      setMessages((prev) =>
        replaceId
          ? prev.map((m) =>
              m.id === replaceId ? { ...m, content: "", streaming: true } : m
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
            // 没吐出任何字就把占位删掉，别留一条空回复
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
    followRef.current = true;
    setAtBottom(true);
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
    followRef.current = true;
    setAtBottom(true);
  }

  const empty = messages.length === 0;

  return (
    <div className="oad-shell">
      <header className="oad-header" data-scrolled={scrolled ? "true" : "false"}>
        <div className="oad-header__left">
          <h1 className="oad-brand">一日一片</h1>
          <span className="oad-status">
            <span
              className="oad-dot"
              data-state={status === "live" ? "live" : status === "down" ? "down" : ""}
              aria-hidden="true"
            />
            {status === "live" ? "在线" : status === "down" ? "未连上" : "连接中"}
          </span>
        </div>
        <button
          type="button"
          className="oad-new"
          onClick={reset}
          disabled={empty && !busy}
        >
          新对话
        </button>
      </header>

      <div className="oad-scroll">
        <main className="oad-main" ref={mainRef}>
          <div
            className="oad-thread"
            role="log"
            aria-live="polite"
            aria-relevant="additions text"
            aria-label="对话"
            ref={threadRef}
          >
            {empty ? (
              <EmptyState
                onPick={(t) => {
                  followRef.current = true;
                  setAtBottom(true);
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
                    canRegenerate={i === messages.length - 1 && !busy && !m.streaming}
                    busy={busy}
                    onRegenerate={regenerate}
                  />
                )
              )
            )}

            {error ? <div className="oad-error">{error}</div> : null}
          </div>
        </main>

        {!empty && !atBottom ? (
          <button
            type="button"
            className="oad-jump"
            onClick={jumpToBottom}
            aria-label="回到最新消息"
          >
            ↓
          </button>
        ) : null}
      </div>

      <footer className="oad-footer">
        <Composer
          value={input}
          onChange={setInput}
          onSubmit={submit}
          onStop={stop}
          busy={busy}
        />
        <p className="oad-footnote">角色扮演 · 非官方项目 · 不提供医疗建议</p>
      </footer>
    </div>
  );
}
