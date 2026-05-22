/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_MOCK_MGMT_URL?: string
  readonly VITE_MOCK_MGMT_TOKEN?: string
  readonly VITE_MOCK_MGMT_USERNAME?: string
  readonly VITE_MOCK_MGMT_PASSWORD?: string
  readonly VITE_CHATROOM_API_URL?: string
  readonly VITE_CHATROOM_WIDGET_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
