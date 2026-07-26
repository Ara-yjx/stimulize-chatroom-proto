import type {
  ExchangeTokenResponse,
  SendMessageResponse,
  PollMessagesResponse,
  HistoryPageResponse,
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
  after: string | number | null
): Promise<PollMessagesResponse> {
  const value = after ?? 0;
  return _$.ajax({
    url: `${apiBaseUrl}/chat/messages?after=${encodeURIComponent(String(value))}`,
    method: "GET",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export async function fetchHistory(
  apiBaseUrl: string,
  token: string,
  before: string | null,
  limit = 50
): Promise<HistoryPageResponse> {
  const query = before
    ? `?before=${encodeURIComponent(before)}&limit=${limit}`
    : `?limit=${limit}`;
  return _$.ajax({
    url: `${apiBaseUrl}/chat/history${query}`,
    method: "GET",
    headers: { Authorization: `Bearer ${token}` },
  });
}
