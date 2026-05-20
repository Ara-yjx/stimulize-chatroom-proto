/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_MOCK_MGMT_URL?: string
  readonly VITE_MOCK_MGMT_TOKEN?: string
  readonly VITE_CHATROOM_API_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
