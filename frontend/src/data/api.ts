import type {
  ExchangeTokenResponse,
  SendMessageResponse,
  PollMessagesResponse,
} from "./types";
import { _$ } from "../lib/jquery";

export async function exchangeToken(
  apiBaseUrl: string,
  chatroomId: string
): Promise<ExchangeTokenResponse> {
  return _$.ajax({
    url: `${apiBaseUrl}/auth/token`,
    method: "POST",
    contentType: "application/json",
    data: JSON.stringify({ chatroom_id: chatroomId }),
  });
}

export async function sendMessage(
  apiBaseUrl: string,
  token: string,
  message: string
): Promise<SendMessageResponse> {
  return _$.ajax({
    url: `${apiBaseUrl}/chat/send`,
    method: "POST",
    contentType: "application/json",
    headers: { Authorization: `Bearer ${token}` },
    data: JSON.stringify({ message }),
  });
}

export async function pollMessages(
  apiBaseUrl: string,
  token: string,
  after: number
): Promise<PollMessagesResponse> {
  return _$.ajax({
    url: `${apiBaseUrl}/chat/messages?after=${after}`,
    method: "GET",
    headers: { Authorization: `Bearer ${token}` },
  });
}
