// --- Shared interfaces ---

export interface Avatar {
  emojiText: string;
  emojiImg?: string;
}

export interface ChatMessage {
  sender: string;
  content: string;
  role: "user" | "ai" | "system";
  timestamp: number;
  session_id?: string;
  internal_name?: string | null;
  avatar?: Avatar;
}

export interface ConversationEvent {
  type: "message" | "system" | "error";
  session_id: string;
  sender: string;
  role: "human" | "ai" | "system";
  content: string;
  timestamp: number;
  visible_at?: number;
  avatar?: Avatar;
  internal_name?: string | null;
}

export interface ChatroomSetting {
  mode: "one_on_one" | "group";
  topic_instruction: string;
  additional_prompt?: string;
  ai_personas?: string[];
  model_id: string;
  mimic_human?: boolean;
  temperature?: number;
  simulate_pairing_seconds: number;
  timer_min_minutes?: number;
  timer_max_minutes?: number;
  max_duration_seconds?: number;
  target_human_count?: number;
  ai_join_strategy?: "fixed_ai_count" | "total_participant_count";
  ai_strategy_value?: number;
  human_count?: number;
  ai_count?: number;
  replace_human_with_ai?: boolean;
  max_wait_seconds?: number;
}

export interface LobbyState {
  status: "open" | "closing" | "closed" | "aborted";
  actual_human_count: number;
  target_human_count: number;
  deadline_at: number;
}

export interface InitOptions {
  element?: string | HTMLElement;
  parentElement?: string | HTMLElement;
  elementStyle?: Partial<CSSStyleDeclaration>;
  chatroomId: string;
  apiBaseUrl?: string;
  beta?: boolean;
}

export interface SessionInfo {
  token: string;
  sessionId: string;
  conversationId: string;
  nickname: string;
  avatar: Avatar;
  chatroomSetting: ChatroomSetting;
}

export interface ExchangeTokenResponse {
  token: string;
  session_id: string;
  conversation_id: string;
  nickname: string;
  avatar: Avatar;
  chatroom_setting: ChatroomSetting;
}

export interface SendMessageResponse {
  ok?: boolean;
}

export interface PollMessagesResponse {
  events: ConversationEvent[];
  lobby?: LobbyState;
  conversation_status?: "active" | "ended";
}
