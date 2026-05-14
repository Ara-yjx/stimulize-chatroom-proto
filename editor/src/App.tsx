import { Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom'
import { Layout, Menu } from '@arco-design/web-react'
import ChatroomList from './pages/ChatroomList'
import ChatroomEditor from './pages/ChatroomEditor'

const { Header, Content } = Layout

export default function App() {
  const navigate = useNavigate()
  const location = useLocation()

  const selectedKey = location.pathname.startsWith('/chatrooms') ? 'chatrooms' : 'chatrooms'

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ borderBottom: '1px solid #e5e6eb' }}>
        <div style={{ display: 'flex', alignItems: 'center', height: '100%' }}>
          <div style={{ fontSize: 16, fontWeight: 600, marginRight: 24, whiteSpace: 'nowrap' }}>
            Stimulize Editor
          </div>
          <Menu
            mode="horizontal"
            selectedKeys={[selectedKey]}
            onClickMenuItem={(key) => {
              if (key === 'chatrooms') navigate('/chatrooms')
            }}
            style={{ flex: 1 }}
          >
            <Menu.Item key="chatrooms">Chatrooms</Menu.Item>
          </Menu>
        </div>
      </Header>
      <Content style={{ background: '#f7f8fa' }}>
        <Routes>
          <Route path="/chatrooms" element={<ChatroomList />} />
          <Route path="/chatrooms/:id" element={<ChatroomEditor />} />
          <Route path="*" element={<Navigate to="/chatrooms" replace />} />
        </Routes>
      </Content>
    </Layout>
  )
}
