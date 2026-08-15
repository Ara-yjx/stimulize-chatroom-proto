export const CHATROOM_SERVICE_MODES = ["normal", "drain", "maintenance"] as const;

export type ChatroomServiceMode = (typeof CHATROOM_SERVICE_MODES)[number];

export function parseChatroomServiceMode(value: unknown): ChatroomServiceMode {
  const mode = value ?? "normal";
  if (
    typeof mode !== "string" ||
    !CHATROOM_SERVICE_MODES.includes(mode as ChatroomServiceMode)
  ) {
    throw new Error(
      `chatroomServiceMode must be one of: ${CHATROOM_SERVICE_MODES.join(", ")}`,
    );
  }
  return mode as ChatroomServiceMode;
}
