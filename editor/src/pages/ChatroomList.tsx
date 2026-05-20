import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { Table, Button, Modal, Input, Tag, Message } from '@arco-design/web-react'
import type { ColumnProps } from '@arco-design/web-react/es/Table'
import { mgmtFetch } from '../api/management'
import { defaultSettingForMode } from '../lib/chatroomSetting'

interface ChatroomSummary {
  id: string
  name: string
  status: string
  created_at: string
  updated_at: string
}

export default function ChatroomList() {
  const [chatrooms, setChatrooms] = useState<ChatroomSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [modalVisible, setModalVisible] = useState(false)
  const [newName, setNewName] = useState('')
  const [creating, setCreating] = useState(false)
  const navigate = useNavigate()

  const fetchChatrooms = useCallback(async () => {
    try {
      const resp = await mgmtFetch('/chatrooms')
      if (!resp.ok) throw new Error('Failed to fetch chatrooms')
      const data: ChatroomSummary[] = await resp.json()
      setChatrooms(data)
    } catch (e: unknown) {
      Message.error(e instanceof Error ? e.message : 'Failed to load chatrooms')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchChatrooms() }, [fetchChatrooms])

  const createChatroom = async () => {
    if (!newName.trim()) {
      Message.warning('Please enter a chatroom name')
      return
    }
    setCreating(true)
    try {
      const setting = defaultSettingForMode('one_on_one')
      const resp = await mgmtFetch('/chatrooms', {
        method: 'POST',
        body: JSON.stringify({
          name: newName.trim(),
          setting,
        }),
      })
      if (!resp.ok) throw new Error('Failed to create chatroom')
      Message.success('Chatroom created')
      setModalVisible(false)
      setNewName('')
      await fetchChatrooms()
    } catch (e: unknown) {
      Message.error(e instanceof Error ? e.message : 'Failed to create chatroom')
    } finally {
      setCreating(false)
    }
  }

  const columns: ColumnProps<ChatroomSummary>[] = [
    { title: 'Name', dataIndex: 'name' },
    {
      title: 'Status',
      dataIndex: 'status',
      render: (_, record) => (
        <Tag color={record.status === 'active' ? 'green' : 'red'}>
          {record.status}
        </Tag>
      ),
    },
    {
      title: 'Created',
      dataIndex: 'created_at',
      render: (_, record) => new Date(record.created_at).toLocaleDateString(),
    },
  ]

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h2 style={{ margin: 0 }}>Chatrooms</h2>
        <Button type="primary" onClick={() => setModalVisible(true)}>
          Create Chatroom
        </Button>
      </div>

      <Table
        columns={columns}
        data={chatrooms}
        rowKey="id"
        loading={loading}
        onRow={(record) => ({
          style: { cursor: 'pointer' },
          onClick: () => navigate(`/chatrooms/${record.id}`),
        })}
        pagination={{ pageSize: 20 }}
      />

      <Modal
        title="Create Chatroom"
        visible={modalVisible}
        onOk={createChatroom}
        onCancel={() => { setModalVisible(false); setNewName('') }}
        confirmLoading={creating}
      >
        <Input
          placeholder="Chatroom name"
          value={newName}
          onChange={setNewName}
          onPressEnter={createChatroom}
        />
      </Modal>
    </div>
  )
}
