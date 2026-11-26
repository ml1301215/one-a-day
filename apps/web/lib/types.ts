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
  streaming?: boolean;
  failed?: boolean;
  /** 服务端消息 id。流式生成时前端先有本地 id，done 事件来了才拿到它 */
  serverId?: string;
};

export type Variant = "default" | "spicier" | "warmer";
