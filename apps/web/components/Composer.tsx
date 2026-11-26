"use client";

import { useEffect, useRef, type KeyboardEvent } from "react";

type Props = {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  onStop: () => void;
  busy: boolean;
};

export function Composer({ value, onChange, onSubmit, onStop, busy }: Props) {
  const ref = useRef<HTMLTextAreaElement | null>(null);

  // 跟随内容长高，最多约 5 行
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 140)}px`;
  }, [value]);

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      if (!busy) onSubmit();
    }
  }

  return (
    <form
      className="oad-composer"
      onSubmit={(e) => {
        e.preventDefault();
        if (!busy) onSubmit();
      }}
    >
      <textarea
        ref={ref}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={busy ? "正在想……" : "今天发生了什么？"}
        rows={1}
        maxLength={2000}
        aria-label="输入内容"
      />
      {busy ? (
        <button
          type="button"
          className="oad-send"
          data-mode="stop"
          onClick={onStop}
          aria-label="停止生成"
        >
          停
        </button>
      ) : (
        <button
          type="submit"
          className="oad-send"
          data-mode="send"
          disabled={!value.trim()}
          aria-label="发送"
        >
          发
        </button>
      )}
    </form>
  );
}
