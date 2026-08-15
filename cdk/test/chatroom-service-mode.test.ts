import { parseChatroomServiceMode } from "../lib/chatroom-service-mode";

describe("parseChatroomServiceMode", () => {
  it("defaults to normal", () => {
    expect(parseChatroomServiceMode(undefined)).toBe("normal");
  });

  it.each(["normal", "drain", "maintenance"])("accepts %s", (mode) => {
    expect(parseChatroomServiceMode(mode)).toBe(mode);
  });

  it.each(["", "paused", true])("rejects invalid mode %p", (mode) => {
    expect(() => parseChatroomServiceMode(mode)).toThrow(
      "chatroomServiceMode must be one of: normal, drain, maintenance",
    );
  });
});
