"use client";

import { useState } from "react";
import type { Message, Variant } from "@/lib/types";

type Props = {
  message: Message;
  /** 只有最后一条才给「更损 / 更暖」——重生成依赖服务端的会话末尾 */
  canRegenerate: boolean;
  busy: boolean;
  onRegenerate: (variant: Variant) => void;
};

export function Slip({ message, canRegenerate, busy, onRegenerate }: Props) {
  const [copied, setCopied] = useState(false);
  const done = !message.streaming && message.content.length > 0;

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
    <article className="oad-reply">
      <p className="oad-reply__body">
        {message.content}
        {message.streaming ? <span className="oad-caret" aria-hidden="true" /> : null}
      </p>

      {done ? (
        <div className="oad-actions">
          {canRegenerate ? (
            <>
              <button
                type="button"
                className="oad-action"
                disabled={busy}
                aria-label="换个更损的说法"
                onClick={() => onRegenerate("spicier")}
              >
                更损
              </button>
              <button
                type="button"
                className="oad-action"
                disabled={busy}
                aria-label="换个更暖的说法"
                onClick={() => onRegenerate("warmer")}
              >
                更暖
              </button>
            </>
          ) : null}

          <button
            type="button"
            className="oad-action"
            onClick={copy}
            data-done={copied ? "1" : "0"}
            aria-label="复制这条回复"
          >
            {copied ? "已复制" : "复制"}
          </button>
        </div>
      ) : null}
    </article>
  );
}
