"""Application-level error types shared across the chatroom API."""


class ChatroomNotFoundException(Exception):
    """Raised when a chatroom ID is not found in RDS."""

    def __init__(self, chatroom_id: str):
        self.chatroom_id = chatroom_id
        super().__init__(f"Chatroom not found: {chatroom_id}")


class InactiveChatroomException(Exception):
    """Raised when a chatroom exists but its status is not active."""

    def __init__(self, chatroom_id: str):
        self.chatroom_id = chatroom_id
        super().__init__(f"Chatroom is inactive: {chatroom_id}")
