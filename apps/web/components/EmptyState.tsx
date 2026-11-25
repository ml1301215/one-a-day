"use client";

import type { Message } from "@/lib/types";

export function EmptyState({ onPick }: { onPick: (text: string) => void }) {
  const starters: Array<{ tag: string; text: string }> = [
    { tag: "职场", text: "今天又被领导当众说，我的方案没有灵魂。" },
    { tag: "家里", text: "我妈又催婚了，说再不结婚就没人要了。" },
    { tag: "路口", text: "我想辞职，但不敢。" },
  ];

  return (
    <div className="oad-empty">
      <h2>
        说点具体的。
        <br />
        抽象的话我接不住。
      </h2>
      <p>
        这里不提供安慰，也不提供人生建议。你说一件今天真发生的事，我给你一句真话，
        加一个今晚就能做的小动作。想听长的，说「展开讲讲」。
      </p>
      <div className="oad-starters">
        {starters.map((s) => (
          <button
            key={s.tag}
            type="button"
            className="oad-starter"
            onClick={() => onPick(s.text)}
          >
            <span>{s.tag}</span>
            {s.text}
          </button>
        ))}
      </div>
    </div>
  );
}

export function UserBubble({ message }: { message: Message }) {
  return <div className="oad-user">{message.content}</div>;
}
