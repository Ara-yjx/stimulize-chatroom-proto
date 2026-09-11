import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import {
  Button, Descriptions, Message, Space, Spin, Table, Tag, Typography,
} from '@arco-design/web-react'
import { IconDownload, IconRefresh } from '@arco-design/web-react/icon'
import type { ColumnProps } from '@arco-design/web-react/es/Table'
import { mgmtFetchJson } from '../api/management'

const { Paragraph, Text } = Typography
const TERMINAL = new Set(['completed', 'partial_failure', 'failed', 'timed_out'])

interface ConversationSummary {
  conversation_id: string
  batch_index: number
  status: string
  message_count?: number
  total_chars?: number
  last_error?: string
}

interface BatchDetail {
  batch_job_id: string
  chatroom_id: string
  status: string
  batch_count: number
  queued_count: number
  running_count: number
  unfinished_count: number
  completed_count: number
  failed_count: number
  timed_out_count: number
  created_at: string
  deadline_at: number
  export_status: string
  export_url?: string
  conversations: ConversationSummary[]
}

interface HistoryEvent {
  event_key: string
  type: string
  sender?: string
  internal_name?: string
  content: string
  timestamp: number
}

interface HistoryResponse {
  events: HistoryEvent[]
  next_after?: string
  has_more: boolean
}

function statusColor(status: string): string {
  if (status === 'completed') return 'green'
  if (status === 'failed' || status === 'timed_out') return 'red'
  if (status === 'partial_failure') return 'orange'
  return 'blue'
}

export default function AiConversationBatch() {
  const { batchId = '' } = useParams<{ batchId: string }>()
  const [batch, setBatch] = useState<BatchDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [exporting, setExporting] = useState(false)
  const [selectedConversationId, setSelectedConversationId] = useState('')
  const [events, setEvents] = useState<HistoryEvent[]>([])
  const [historyCursor, setHistoryCursor] = useState<string | undefined>()

  const fetchBatch = useCallback(async () => {
    const value = await mgmtFetchJson<BatchDetail>(`/api/getAiConversationBatch/${batchId}`, {
      method: 'POST',
      body: JSON.stringify({ offset: 0, limit: 100 }),
    })
    setBatch(value)
    const first = value.conversations?.[0]?.conversation_id
    if (first) setSelectedConversationId((current) => current || first)
    return value
  }, [batchId])

  useEffect(() => {
    let cancelled = false
    const poll = async () => {
      try {
        const value = await fetchBatch()
        if (!cancelled && !TERMINAL.has(value.status)) {
          window.setTimeout(poll, 3000)
        }
      } catch (error: unknown) {
        if (!cancelled) Message.error(error instanceof Error ? error.message : 'Failed to load batch')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void poll()
    return () => { cancelled = true }
  }, [fetchBatch])

  useEffect(() => {
    setEvents([])
    setHistoryCursor(undefined)
  }, [selectedConversationId])

  useEffect(() => {
    if (!selectedConversationId) return
    let cancelled = false
    let cursor = historyCursor
    const poll = async () => {
      try {
        const value = await mgmtFetchJson<HistoryResponse>(
          `/api/getAiConversationHistory/${selectedConversationId}`,
          {
            method: 'POST',
            body: JSON.stringify({ after: cursor, limit: 200 }),
          },
        )
        if (cancelled) return
        if (value.events.length) {
          setEvents((current) => {
            const seen = new Set(current.map((event) => event.event_key))
            return [...current, ...value.events.filter((event) => !seen.has(event.event_key))]
          })
        }
        cursor = value.next_after
        setHistoryCursor(value.next_after)
      } catch (error: unknown) {
        if (!cancelled) Message.error(error instanceof Error ? error.message : 'Failed to load history')
      }
      if (!cancelled) {
        window.setTimeout(poll, 2000)
      }
    }
    void poll()
    return () => { cancelled = true }
    // Restart only when the selected conversation changes. The local cursor is
    // intentionally owned by this polling loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedConversationId])

  const requestExport = async () => {
    setExporting(true)
    try {
      await mgmtFetchJson(`/api/exportAiConversationBatch/${batchId}`, { method: 'POST' })
      Message.success('Export requested')
      await fetchBatch()
    } catch (error: unknown) {
      Message.error(error instanceof Error ? error.message : 'Failed to request export')
    } finally {
      setExporting(false)
    }
  }

  const columns = useMemo<ColumnProps<ConversationSummary>[]>(() => [
    { title: '#', dataIndex: 'batch_index', width: 70, render: (value) => Number(value) + 1 },
    {
      title: 'Status', dataIndex: 'status', width: 140,
      render: (value) => <Tag color={statusColor(String(value))}>{String(value)}</Tag>,
    },
    { title: 'Messages', dataIndex: 'message_count', width: 100 },
    { title: 'Characters', dataIndex: 'total_chars', width: 110 },
    { title: 'Error', dataIndex: 'last_error', ellipsis: true },
  ], [])

  if (loading && !batch) return <Spin style={{ display: 'block', margin: '80px auto' }} />
  if (!batch) return <div style={{ padding: 24 }}>Batch not found</div>

  return (
    <div style={{ padding: 24, maxWidth: 1120, margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'center' }}>
        <div>
          <h2 style={{ margin: 0 }}>AI Conversation Batch</h2>
          <Text type="secondary">{batch.batch_job_id}</Text>
        </div>
        <Space>
          <Button icon={<IconRefresh />} onClick={() => void fetchBatch()}>Refresh</Button>
          {batch.export_url ? (
            <Button type="primary" icon={<IconDownload />} href={batch.export_url}>Download ZIP</Button>
          ) : (
            <Button
              type="primary"
              icon={<IconDownload />}
              loading={exporting}
              disabled={!TERMINAL.has(batch.status)}
              onClick={() => void requestExport()}
            >
              {batch.export_status === 'building' || batch.export_status === 'requested'
                ? 'Preparing export'
                : 'Export completed conversations'}
            </Button>
          )}
        </Space>
      </div>

      <Descriptions
        style={{ marginTop: 24 }}
        column={4}
        data={[
          { label: 'Status', value: <Tag color={statusColor(batch.status)}>{batch.status}</Tag> },
          { label: 'Total', value: batch.batch_count },
          { label: 'Completed', value: batch.completed_count },
          { label: 'Failed', value: batch.failed_count + batch.timed_out_count },
          { label: 'Running', value: batch.running_count },
          { label: 'Queued', value: batch.queued_count },
          { label: 'Started', value: new Date(batch.created_at).toLocaleString() },
          { label: 'Deadline', value: new Date(batch.deadline_at).toLocaleString() },
        ]}
      />

      <h3 style={{ marginTop: 32 }}>Conversations</h3>
      <Table
        rowKey="conversation_id"
        columns={columns}
        data={batch.conversations}
        pagination={false}
        rowClassName={(record) => record.conversation_id === selectedConversationId ? 'arco-table-tr-checked' : ''}
        onRow={(record) => ({
          style: { cursor: 'pointer' },
          onClick: () => setSelectedConversationId(record.conversation_id),
        })}
      />

      <h3 style={{ marginTop: 32 }}>Conversation History</h3>
      {events.length === 0 ? (
        <Text type="secondary">Waiting for messages...</Text>
      ) : events.map((event) => (
        <div key={event.event_key} style={{ padding: '8px 0', borderBottom: '1px solid #f0f0f0' }}>
          <Text bold>{event.type === 'message' ? event.sender : 'System'}</Text>
          {event.internal_name && <Text type="secondary"> ({event.internal_name})</Text>}
          <Paragraph style={{ margin: '4px 0 0', whiteSpace: 'pre-wrap' }}>{event.content}</Paragraph>
        </div>
      ))}
    </div>
  )
}
