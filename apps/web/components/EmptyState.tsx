"use client";

import type { Message } from "@/lib/types";

const STARTERS = [
  "今天又被领导当众说，方案没有灵魂。",
  "我妈又催婚了。",
  "我想辞职，但不敢。",
];

export function EmptyState({ onPick }: { onPick: (text: string) => void }) {
  return (
    <div className="oad-empty">
      <h2 className="oad-empty__title">说点具体的。</h2>
      <p className="oad-empty__hint">说一件今天真发生的事。想听长的，说「展开讲讲」。</p>
      <div className="oad-starters">
        {STARTERS.map((text) => (
          <button
            key={text}
            type="button"
            className="oad-starter"
            onClick={() => onPick(text)}
          >
            {text}
          </button>
        ))}
      </div>
    </div>
  );
}

export function UserBubble({ message }: { message: Message }) {
  return <div className="oad-user">{message.content}</div>;
}
