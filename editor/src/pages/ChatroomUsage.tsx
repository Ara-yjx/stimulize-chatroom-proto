import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import {
  Button,
  Card,
  Empty,
  Message,
  Radio,
  Spin,
  Statistic,
  Table,
} from '@arco-design/web-react'
import type { ColumnProps } from '@arco-design/web-react/es/Table'
import { mgmtFetchJson } from '../api/management'
import { hasManagementToken } from '../api/managementAuth'

type UsagePeriod = 'day' | 'week' | 'month'

interface UsageTotals {
  input_tokens: number
  output_tokens: number
  estimated_cost_usd: number
}

interface UsageSeriesBucket extends UsageTotals {
  period_start: string
}

interface UsageEnvelope {
  scope: 'chatroom'
  chatroom_id: string
  period: UsagePeriod | null
  from: string | null
  to: string | null
  totals: UsageTotals
  series: UsageSeriesBucket[]
}

interface UsageResponseData {
  usage: UsageEnvelope
}

const PERIOD_OPTIONS: { label: string; value: UsagePeriod }[] = [
  { label: 'Daily', value: 'day' },
  { label: 'Weekly', value: 'week' },
  { label: 'Monthly', value: 'month' },
]

function formatUsd(value: number): string {
  return `$${value.toFixed(6)}`
}

function formatPeriodLabel(period: UsagePeriod, iso: string): string {
  const date = new Date(iso)
  if (period === 'day') return date.toLocaleDateString()
  if (period === 'week') return `Week of ${date.toLocaleDateString()}`
  return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short' })
}

function UsageBarChart({
  rows,
  period,
}: {
  rows: UsageSeriesBucket[]
  period: UsagePeriod
}) {
  const maxValue = Math.max(...rows.map((row) => row.input_tokens + row.output_tokens), 0)

  if (rows.length === 0) {
    return (
      <div style={{
        height: 240,
        border: '1px solid #e5e6eb',
        borderRadius: 8,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#fff',
      }}>
        <Empty description="No usage yet" />
      </div>
    )
  }

  return (
    <div style={{
      height: 240,
      border: '1px solid #e5e6eb',
      borderRadius: 8,
      padding: '16px 20px 12px',
      background: '#fff',
      display: 'flex',
      alignItems: 'stretch',
      gap: 16,
      overflowX: 'auto',
    }}>
      {rows.map((row) => {
        const totalTokens = row.input_tokens + row.output_tokens
        const height = maxValue > 0 ? Math.max(8, Math.round((totalTokens / maxValue) * 150)) : 8
        return (
          <div key={row.period_start} style={{
            minWidth: 96,
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'flex-end',
            alignItems: 'center',
            gap: 8,
          }}>
            <div style={{ fontSize: 12, color: '#4e5969' }}>{totalTokens.toLocaleString()}</div>
            <div style={{
              width: 48,
              height,
              borderRadius: 6,
              background: 'linear-gradient(180deg, #165dff 0%, #69b1ff 100%)',
              boxShadow: 'inset 0 -1px 0 rgba(255,255,255,0.24)',
            }} />
            <div style={{
              fontSize: 12,
              color: '#86909c',
              textAlign: 'center',
              lineHeight: 1.4,
            }}>
              {formatPeriodLabel(period, row.period_start)}
            </div>
          </div>
        )
      })}
    </div>
  )
}

export default function ChatroomUsage() {
  const { id = '' } = useParams<{ id: string }>()
  const [period, setPeriod] = useState<UsagePeriod>('day')
  const [loading, setLoading] = useState(true)
  const [usage, setUsage] = useState<UsageEnvelope | null>(null)

  const fetchUsage = useCallback(async (nextPeriod: UsagePeriod) => {
    if (!hasManagementToken()) {
      setUsage(null)
      setLoading(false)
      return
    }

    setLoading(true)
    try {
      const data = await mgmtFetchJson<UsageResponseData>(`/api/getChatroomUsage/${id}`, {
        method: 'POST',
        body: JSON.stringify({ period: nextPeriod }),
      })
      setUsage(data.usage)
    } catch (error: unknown) {
      Message.error(error instanceof Error ? error.message : 'Failed to load token usage')
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    fetchUsage(period)
  }, [fetchUsage, period])

  const totalTokens = useMemo(() => {
    if (!usage) return 0
    return usage.totals.input_tokens + usage.totals.output_tokens
  }, [usage])

  const columns: ColumnProps<UsageSeriesBucket>[] = [
    {
      title: 'Period',
      dataIndex: 'period_start',
      render: (_, record) => formatPeriodLabel(period, record.period_start),
    },
    {
      title: 'Input Tokens',
      dataIndex: 'input_tokens',
      render: (_, record) => record.input_tokens.toLocaleString(),
    },
    {
      title: 'Output Tokens',
      dataIndex: 'output_tokens',
      render: (_, record) => record.output_tokens.toLocaleString(),
    },
    {
      title: 'Total Tokens',
      render: (_, record) => (record.input_tokens + record.output_tokens).toLocaleString(),
    },
    {
      title: 'Approx. USD',
      dataIndex: 'estimated_cost_usd',
      render: (_, record) => formatUsd(record.estimated_cost_usd),
    },
  ]

  if (loading) {
    return <Spin style={{ display: 'block', margin: '80px auto' }} />
  }

  if (!hasManagementToken()) {
    return <div style={{ padding: 24 }}>Please log in first</div>
  }

  if (!usage) {
    return <div style={{ padding: 24 }}>Usage data unavailable</div>
  }

  return (
    <div style={{ padding: 24 }}>
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        marginBottom: 20,
      }}>
        <div>
          <h2 style={{ margin: 0 }}>Token Usage</h2>
          <div style={{ marginTop: 6, color: '#86909c', fontSize: 13 }}>
            Chatroom ID: {usage.chatroom_id}
          </div>
        </div>
        <Button onClick={() => window.close()}>Close Tab</Button>
      </div>

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
        gap: 16,
        marginBottom: 20,
      }}>
        <Card bordered>
          <Statistic title="Total Token" value={totalTokens} groupSeparator />
        </Card>
        <Card bordered>
          <Statistic title="Approx. Total USD" value={usage.totals.estimated_cost_usd} precision={6} prefix="$" />
        </Card>
      </div>

      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        marginBottom: 16,
        gap: 16,
        flexWrap: 'wrap',
      }}>
        <Radio.Group
          type="button"
          name="usage-period"
          value={period}
          options={PERIOD_OPTIONS}
          onChange={(value) => setPeriod(value as UsagePeriod)}
        />
      </div>

      <div style={{ marginBottom: 20 }}>
        <UsageBarChart rows={usage.series} period={period} />
      </div>

      <Table
        columns={columns}
        data={usage.series}
        rowKey="period_start"
        pagination={{ pageSize: 10 }}
        noDataElement={<Empty description="No usage yet" />}
      />
    </div>
  )
}
