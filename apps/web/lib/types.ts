export type Level = "L0" | "L1" | "L2" | "L3";

export type ChatMeta = {
  session_id: string;
  level: Level;
  categories: string[];
  humor_scale: number;
  hard_block: boolean;
  has_template: boolean;
  template?: string | null;
};

export type Role = "user" | "assistant";

export type Message = {
  id: string;
  role: Role;
  content: string;
  level?: Level;
  streaming?: boolean;
  failed?: boolean;
  /** 服务端消息 id。流式生成时前端先有本地 id，done 事件来了才拿到它 */
  serverId?: string;
};

export type Variant = "default" | "spicier" | "warmer";

const LEVEL_LABEL: Record<Level, string> = {
  L0: "常规",
  L1: "半量",
  L2: "停梗",
  L3: "急症",
};

export function levelLabel(level?: Level): string {
  return level ? LEVEL_LABEL[level] : "常规";
}

export function levelHint(level?: Level): string {
  switch (level) {
    case "L0":
      return "正常剂量，可以损";
    case "L1":
      return "减半，先接住情绪";
    case "L2":
      return "不玩梗，认真说话";
    case "L3":
      return "这不是玩笑场景";
    default:
      return "";
  }
}
