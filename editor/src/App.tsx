import { Routes, Route, Navigate, useNavigate, useLocation, useParams } from 'react-router-dom'
import { useState } from 'react'
import { Layout, Menu, Input, Button, Message } from '@arco-design/web-react'
import ChatroomList from './pages/ChatroomList'
import ChatroomEditor from './pages/ChatroomEditor'
import ChatroomUsage from './pages/ChatroomUsage'
import { CHATROOM_LIST_ROUTE } from './routes'
import {
  getAuthenticatedUsername,
  hasManagementToken,
  loginWithCredentials,
  logoutManagement,
} from './api/managementAuth'

const { Header, Content } = Layout

function LegacyChatroomDetailRedirect() {
  const { id = '' } = useParams<{ id: string }>()
  return <Navigate to={`${CHATROOM_LIST_ROUTE}/${id}`} replace />
}

export default function App() {
  const navigate = useNavigate()
  const location = useLocation()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [loggingIn, setLoggingIn] = useState(false)
  const [loggedInUsername, setLoggedInUsername] = useState(getAuthenticatedUsername())

  const selectedKey = location.pathname.startsWith(CHATROOM_LIST_ROUTE) ? 'chatroom' : 'chatroom'

  const handleLogin = async () => {
    if (!username.trim() || !password) {
      Message.warning('Enter username and password')
      return
    }
    setLoggingIn(true)
    try {
      await loginWithCredentials(username, password)
      setLoggedInUsername(username.trim())
      Message.success('Logged in')
      window.location.reload()
    } catch (error: unknown) {
      Message.error(error instanceof Error ? error.message : 'Login failed')
    } finally {
      setLoggingIn(false)
    }
  }

  const handleLogout = () => {
    logoutManagement()
    setLoggedInUsername('')
    setUsername('')
    setPassword('')
    Message.success('Logged out')
    window.location.reload()
  }

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ borderBottom: '1px solid #e5e6eb', padding: '0 24px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, height: '100%' }}>
          <div style={{ fontSize: 16, fontWeight: 600, marginRight: 24, whiteSpace: 'nowrap' }}>
            Stimulize Chatroom (Beta)
          </div>
          <Menu
            mode="horizontal"
            selectedKeys={[selectedKey]}
            onClickMenuItem={(key) => {
              if (key === 'chatroom') navigate(CHATROOM_LIST_ROUTE)
            }}
            style={{ flex: 1 }}
          >
            <Menu.Item key="chatroom">Chatroom</Menu.Item>
          </Menu>
          {hasManagementToken() ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 12, color: '#4e5969', whiteSpace: 'nowrap' }}>
                {loggedInUsername || 'Logged in'}
              </span>
              <Button size="small" onClick={handleLogout}>
                Logout
              </Button>
            </div>
          ) : (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Input
                size="small"
                placeholder="Username"
                value={username}
                onChange={setUsername}
                style={{ width: 140 }}
              />
              <Input.Password
                size="small"
                placeholder="Password"
                value={password}
                onChange={setPassword}
                onPressEnter={handleLogin}
                style={{ width: 160 }}
              />
              <Button size="small" type="primary" loading={loggingIn} onClick={handleLogin}>
                Login
              </Button>
            </div>
          )}
        </div>
      </Header>
      <Content style={{ background: '#f7f8fa' }}>
        <Routes>
          <Route path={CHATROOM_LIST_ROUTE} element={<ChatroomList />} />
          <Route path={`${CHATROOM_LIST_ROUTE}/:id/usage`} element={<ChatroomUsage />} />
          <Route path={`${CHATROOM_LIST_ROUTE}/:id`} element={<ChatroomEditor />} />
          <Route path="/chatrooms" element={<Navigate to={CHATROOM_LIST_ROUTE} replace />} />
          <Route path="/chatrooms/:id" element={<LegacyChatroomDetailRedirect />} />
          <Route path="*" element={<Navigate to={CHATROOM_LIST_ROUTE} replace />} />
        </Routes>
      </Content>
    </Layout>
  )
}
