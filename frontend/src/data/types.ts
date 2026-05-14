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
  avatar?: Avatar;
}

export interface ConversationEvent {
  type: "message" | "system" | "error";
  session_id: string;
  sender: string;
  role: "human" | "ai" | "system";
  content: string;
  timestamp: number;
  avatar?: Avatar;
}

export interface ChatroomSetting {
  mode: "one_on_one" | "group";
  mimic_human: boolean;
  system_prompt: string;
  model_id: string;
  simulate_pairing_seconds: number;
  timer_min_minutes?: number;
  timer_max_minutes?: number;
}

export interface InitOptions {
  element: string | HTMLElement;
  chatroomId: string;
  apiBaseUrl: string;
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
  replies?: Array<{
    nickname: string;
    avatar?: Avatar;
    content: string;
  }>;
  error?: boolean;
}

export interface PollMessagesResponse {
  events: ConversationEvent[];
}
