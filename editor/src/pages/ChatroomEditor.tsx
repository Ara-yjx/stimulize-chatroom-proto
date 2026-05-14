import { useState, useEffect, useCallback } from 'react'
import { useParams } from 'react-router-dom'
import {
  Form, Input, InputNumber, Switch, Select, Button, Message, Spin,
} from '@arco-design/web-react'
import { MANAGEMENT_API_URL } from '../config'
import ScriptGenerator from '../components/ScriptGenerator'
import WidgetPreview from '../components/WidgetPreview'

const TextArea = Input.TextArea
const FormItem = Form.Item

interface ChatroomSetting {
  mode: string
  mimic_human: boolean
  system_prompt: string
  model_id: string
  simulate_pairing_seconds: number
  timer_min_minutes: number | null
  timer_max_minutes: number | null
}

interface Chatroom {
  id: string
  name: string
  status: string
  setting: ChatroomSetting
  created_at: string
  updated_at: string
}

export default function ChatroomEditor() {
  const { id } = useParams<{ id: string }>()
  const [chatroom, setChatroom] = useState<Chatroom | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()

  const fetchChatroom = useCallback(async () => {
    try {
      const resp = await fetch(`${MANAGEMENT_API_URL}/chatrooms/${id}`)
      if (!resp.ok) throw new Error('Chatroom not found')
      const data: Chatroom = await resp.json()
      setChatroom(data)
      form.setFieldsValue({
        name: data.name,
        status: data.status === 'active',
        mode: data.setting.mode,
        mimic_human: data.setting.mimic_human,
        system_prompt: data.setting.system_prompt,
        model_id: data.setting.model_id,
        simulate_pairing_seconds: data.setting.simulate_pairing_seconds,
        timer_min_minutes: data.setting.timer_min_minutes,
        timer_max_minutes: data.setting.timer_max_minutes,
      })
    } catch (e: unknown) {
      Message.error(e instanceof Error ? e.message : 'Failed to load chatroom')
    } finally {
      setLoading(false)
    }
  }, [id, form])

  useEffect(() => { fetchChatroom() }, [fetchChatroom])

  const handleSave = async () => {
    try {
      const values = await form.validate()
      setSaving(true)
      const resp = await fetch(`${MANAGEMENT_API_URL}/chatrooms/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: values.name,
          status: values.status ? 'active' : 'inactive',
          setting: {
            mode: values.mode,
            mimic_human: values.mimic_human,
            system_prompt: values.system_prompt,
            model_id: values.model_id,
            simulate_pairing_seconds: values.simulate_pairing_seconds,
            timer_min_minutes: values.timer_min_minutes ?? null,
            timer_max_minutes: values.timer_max_minutes ?? null,
          },
        }),
      })
      if (!resp.ok) throw new Error('Failed to save')
      const updated: Chatroom = await resp.json()
      setChatroom(updated)
      Message.success('Saved')
    } catch (e: unknown) {
      if (e instanceof Error) Message.error(e.message)
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <Spin style={{ display: 'block', margin: '80px auto' }} />
  if (!chatroom) return <div style={{ padding: 24 }}>Chatroom not found</div>

  return (
    <div style={{ padding: 24, maxWidth: 800 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h2 style={{ margin: 0 }}>Edit Chatroom</h2>
        <Button type="primary" loading={saving} onClick={handleSave}>
          Save
        </Button>
      </div>

      <div style={{ marginBottom: 8, color: '#86909c', fontSize: 13 }}>
        ID: {chatroom.id}
      </div>

      <Form form={form} layout="vertical">
        <FormItem label="Name" field="name" rules={[{ required: true, message: 'Name is required' }]}>
          <Input placeholder="Chatroom name" />
        </FormItem>

        <FormItem label="Status" field="status" triggerPropName="checked">
          <Switch checkedText="Active" uncheckedText="Inactive" />
        </FormItem>

        {/* Mode hidden — defaults to one_on_one. Uncomment for Phase 2 group mode.
        <FormItem label="Mode" field="mode">
          <Select options={[
            { label: '1-on-1', value: 'one_on_one' },
            { label: 'Group', value: 'group' },
          ]} />
        </FormItem>
        */}

        <FormItem label="Mimic Human" field="mimic_human" triggerPropName="checked">
          <Switch />
        </FormItem>

        <FormItem label="System Prompt" field="system_prompt">
          <TextArea autoSize={{ minRows: 8, maxRows: 20 }} />
        </FormItem>

        <FormItem label="Model" field="model_id">
          <Select
            showSearch
            placeholder="Select a model"
            options={[
              { label: 'Anthropic', options: [
                { label: 'Claude Sonnet 4.6', value: 'global.anthropic.claude-sonnet-4-6' },
                { label: 'Claude Sonnet 4.5', value: 'global.anthropic.claude-sonnet-4-5-20250929-v1:0' },
                { label: 'Claude Sonnet 4', value: 'global.anthropic.claude-sonnet-4-20250514-v1:0' },
                { label: 'Claude Haiku 4.5', value: 'global.anthropic.claude-haiku-4-5-20251001-v1:0' },
                { label: 'Claude Opus 4.7', value: 'global.anthropic.claude-opus-4-7' },
                { label: 'Claude Opus 4.6', value: 'global.anthropic.claude-opus-4-6-v1' },
                { label: 'Claude 3.7 Sonnet', value: 'us.anthropic.claude-3-7-sonnet-20250219-v1:0' },
                { label: 'Claude 3.5 Haiku', value: 'us.anthropic.claude-3-5-haiku-20241022-v1:0' },
              ]},
              { label: 'Amazon Nova', options: [
                { label: 'Nova Pro', value: 'us.amazon.nova-pro-v1:0' },
                { label: 'Nova Lite', value: 'us.amazon.nova-lite-v1:0' },
                { label: 'Nova Micro', value: 'us.amazon.nova-micro-v1:0' },
                { label: 'Nova Premier', value: 'us.amazon.nova-premier-v1:0' },
                { label: 'Nova 2 Lite', value: 'global.amazon.nova-2-lite-v1:0' },
              ]},
              { label: 'Meta Llama', options: [
                { label: 'Llama 4 Maverick 17B', value: 'us.meta.llama4-maverick-17b-instruct-v1:0' },
                { label: 'Llama 4 Scout 17B', value: 'us.meta.llama4-scout-17b-instruct-v1:0' },
                { label: 'Llama 3.3 70B', value: 'us.meta.llama3-3-70b-instruct-v1:0' },
                { label: 'Llama 3.1 70B', value: 'us.meta.llama3-1-70b-instruct-v1:0' },
                { label: 'Llama 3.1 8B', value: 'us.meta.llama3-1-8b-instruct-v1:0' },
              ]},
              { label: 'DeepSeek', options: [
                { label: 'DeepSeek R1', value: 'us.deepseek.r1-v1:0' },
                { label: 'DeepSeek V3', value: 'deepseek.v3-v1:0' },
                { label: 'DeepSeek V3.2', value: 'deepseek.v3.2' },
              ]},
              { label: 'Qwen', options: [
                { label: 'Qwen3 235B', value: 'qwen.qwen3-235b-a22b-2507-v1:0' },
                { label: 'Qwen3 32B', value: 'qwen.qwen3-32b-v1:0' },
                { label: 'Qwen3 Next 80B', value: 'qwen.qwen3-next-80b-a3b' },
              ]},
              { label: 'Google', options: [
                { label: 'Gemma 3 27B', value: 'google.gemma-3-27b-it' },
                { label: 'Gemma 3 12B', value: 'google.gemma-3-12b-it' },
                { label: 'Gemma 3 4B', value: 'google.gemma-3-4b-it' },
              ]},
              { label: 'Mistral', options: [
                { label: 'Mistral Large 3 675B', value: 'mistral.mistral-large-3-675b-instruct' },
                { label: 'Devstral 2 123B', value: 'mistral.devstral-2-123b' },
                { label: 'Ministral 3 14B', value: 'mistral.ministral-3-14b-instruct' },
              ]},
            ]}
          />
        </FormItem>

        <FormItem label="Simulate Pairing Seconds" field="simulate_pairing_seconds">
          <InputNumber min={0} style={{ width: 200 }} />
        </FormItem>

        <FormItem label="Timer Min Minutes" field="timer_min_minutes">
          <InputNumber min={0} style={{ width: 200 }} />
        </FormItem>

        <FormItem label="Timer Max Minutes" field="timer_max_minutes">
          <InputNumber min={0} style={{ width: 200 }} />
        </FormItem>
      </Form>

      <div style={{ borderTop: '1px solid #e5e6eb', margin: '24px 0' }} />
      <ScriptGenerator chatroomId={chatroom.id} />

      <div style={{ borderTop: '1px solid #e5e6eb', margin: '24px 0' }} />
      <WidgetPreview chatroomId={chatroom.id} />
    </div>
  )
}
