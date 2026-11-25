"use client";

import { useState } from "react";
import { levelHint, levelLabel, type Message, type Variant } from "@/lib/types";

type Props = {
  message: Message;
  /** 只有最后一条才给「换个说法」——重生成依赖服务端的会话末尾 */
  canRegenerate: boolean;
  busy: boolean;
  onRegenerate: (variant: Variant) => void;
  onFeedback?: (kind: "up" | "down") => void;
};

export function Slip({ message, canRegenerate, busy, onRegenerate, onFeedback }: Props) {
  const [copied, setCopied] = useState(false);
  const level = message.level;
  const isCrisis = level === "L3";

  async function copy() {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      /* 剪贴板不可用就算了 */
    }
  }

  return (
    <article className="oad-slip" data-level={level ?? "L0"}>
      <header className="oad-slip__head">
        <span className="oad-slip__brand">一日一片</span>
        <span className="oad-slip__tag" title={levelHint(level)}>
          {levelLabel(level)}
        </span>
      </header>

      <p className="oad-slip__body">
        {message.content}
        {message.streaming ? <span className="oad-caret" aria-hidden /> : null}
      </p>

      {!message.streaming && message.content ? (
        <footer className="oad-slip__foot">
          {canRegenerate && !isCrisis ? (
            <>
              <button
                type="button"
                className="oad-chip"
                disabled={busy}
                onClick={() => onRegenerate("spicier")}
              >
                更损一点
              </button>
              <button
                type="button"
                className="oad-chip"
                disabled={busy}
                onClick={() => onRegenerate("warmer")}
              >
                更暖一点
              </button>
            </>
          ) : null}

          <button type="button" className="oad-chip" onClick={copy} data-done={copied ? "1" : "0"}>
            {copied ? "已复制" : "复制"}
          </button>

          {onFeedback ? (
            <>
              <button
                type="button"
                className="oad-chip"
                aria-label="这句像"
                title="像他，记一笔"
                onClick={() => onFeedback("up")}
              >
                ◎ 像
              </button>
              <button
                type="button"
                className="oad-chip"
                aria-label="这句不像"
                title="不像，记一笔"
                onClick={() => onFeedback("down")}
              >
                ○ 不像
              </button>
            </>
          ) : null}
        </footer>
      ) : null}
    </article>
  );
}
