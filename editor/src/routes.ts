export const CHATROOM_LIST_ROUTE = '/chatroom'

export function chatroomDetailRoute(id: string): string {
  return `${CHATROOM_LIST_ROUTE}/${id}`
}
