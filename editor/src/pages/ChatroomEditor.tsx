import { useState, useEffect, useCallback } from 'react'
import { useParams } from 'react-router-dom'
import {
  Form, Input, InputNumber, Switch, Select, Button, Message, Spin, Radio, Space, Popover,
} from '@arco-design/web-react'
import { IconDelete, IconPlus, IconQuestionCircle } from '@arco-design/web-react/icon'
import { mgmtFetchJson } from '../api/management'
import { hasManagementToken } from '../api/managementAuth'
import {
  ChatroomSetting,
  ChatroomMode,
  defaultSettingForMode,
  denormalizeForSave,
  deriveMaxDurationSeconds,
  validateChatroomSetting,
  VALIDATION_LIMITS,
} from '../lib/chatroomSetting'
import ScriptGenerator from '../components/ScriptGenerator'
import WidgetPreview from '../components/WidgetPreview'
import { chatroomUsageRoute } from '../routes'

const TextArea = Input.TextArea
const FormItem = Form.Item
const RadioGroup = Radio.Group
const Option = Select.Option
const OptGroup = Select.OptGroup


/** Section heading inside the form. Compact, uppercase tracking, separator above. */
function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <h4 style={{
      margin: '24px 0 12px',
      paddingTop: 16,
      borderTop: '1px solid #f0f0f0',
      fontSize: 13,
      fontWeight: 600,
      color: '#4e5969',
      letterSpacing: 0.3,
    }}>
      {children}
    </h4>
  )
}

/** Side-by-side flex row for short fields. Wraps on narrow screens. */
function Row({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
      {children}
    </div>
  )
}

/**
 * One row of the AI Join Strategy widget: Radio + paired number input.
 * The number input only edits the form's ``ai_strategy_value`` when its
 * row's radio is the selected one. Both rows display the current value
 * when active so switching strategies preserves the picked count.
 */
function StrategyRow({
  value,
  label,
  hint,
  isActive,
}: {
  value: 'fixed_ai_count' | 'total_participant_count'
  label: string
  hint: string
  isActive: boolean
}) {
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: 12,
      padding: '8px 0',
      opacity: isActive ? 1 : 0.55,
    }}>
      <Radio value={value} style={{ minWidth: 170 }}>
        <span style={{ fontWeight: 500 }}>{label}</span>
      </Radio>
      <FormItem
        field="ai_strategy_value"
        rules={isActive ? [{
          required: true,
          type: 'number',
          min: VALIDATION_LIMITS.aiStrategyValueMin,
          max: VALIDATION_LIMITS.aiStrategyValueMax,
        }] : undefined}
        noStyle
      >
        <InputNumber
          min={VALIDATION_LIMITS.aiStrategyValueMin}
          max={VALIDATION_LIMITS.aiStrategyValueMax}
          disabled={!isActive}
          style={{ width: 100 }}
        />
      </FormItem>
      <span style={{ color: '#86909c', fontSize: 12, flex: 1 }}>{hint}</span>
    </div>
  )
}


/**
 * List-of-textareas editor used by the form's "AI Personas" field. Plays
 * the FormItem custom-component contract: receives ``value`` + ``onChange``
 * from Arco's Form. Each entry is a free-form persona string.
 */
function PersonaListEditor({
  value,
  onChange,
}: {
  value?: string[]
  onChange?: (next: string[]) => void
}) {
  const personas = value ?? []
  const update = (next: string[]) => onChange?.(next)
  return (
    <div>
      {personas.map((p, i) => (
        <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 8, alignItems: 'flex-start' }}>
          <TextArea
            value={p}
            onChange={(v) => update(personas.map((x, j) => (j === i ? v : x)))}
            autoSize={{ minRows: 2, maxRows: 6 }}
            placeholder={`Instruction to persona ${i + 1}`}
            style={{ flex: 1 }}
          />
          <Button
            shape="circle"
            type="text"
            icon={<IconDelete />}
            onClick={() => update(personas.filter((_, j) => j !== i))}
            aria-label="Remove persona"
          />
        </div>
      ))}
      <Space>
        <Button size="small" icon={<IconPlus />} onClick={() => update([...personas, ''])}>
          Add persona
        </Button>
        {personas.length > 0 && (
          <span style={{ color: '#86909c', fontSize: 12 }}>
            {personas.length} persona{personas.length === 1 ? '' : 's'}
          </span>
        )}
      </Space>
    </div>
  )
}

const MODEL_GROUPS: { label: string; options: { label: string; value: string }[] }[] = [
  { label: 'Anthropic', options: [
    { label: 'Claude Sonnet 4.6', value: 'global.anthropic.claude-sonnet-4-6' },
    { label: 'Claude Sonnet 4.5', value: 'global.anthropic.claude-sonnet-4-5-20250929-v1:0' },
    { label: 'Claude Sonnet 4', value: 'global.anthropic.claude-sonnet-4-20250514-v1:0' },
    { label: 'Claude Haiku 4.5', value: 'global.anthropic.claude-haiku-4-5-20251001-v1:0' },
    { label: 'Claude Opus 4.7', value: 'global.anthropic.claude-opus-4-7' },
    { label: 'Claude Opus 4.6', value: 'global.anthropic.claude-opus-4-6-v1' },
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
]

interface Chatroom {
  id: string
  name: string
  status: string
  setting: Partial<ChatroomSetting>
  created_at: string
  updated_at: string
}

interface FormValues extends ChatroomSetting {
  name: string
  status: boolean
}

/**
 * Older v2 chatrooms may not have all group / max_duration fields. Fill in
 * sensible defaults so the form doesn't crash.
 */
function normalizeLoadedSetting(setting: Partial<ChatroomSetting> | undefined): ChatroomSetting {
  const mode: ChatroomMode = setting?.mode === 'group' ? 'group' : 'one_on_one'
  const defaults = defaultSettingForMode(mode)
  const timerMaxMinutes =
    typeof setting?.timer_max_minutes === 'number' ? setting.timer_max_minutes : defaults.timer_max_minutes
  return {
    ...defaults,
    ...setting,
    mode,
    max_duration_seconds: deriveMaxDurationSeconds(timerMaxMinutes ?? null),
  }
}

export default function ChatroomEditor() {
  const { id } = useParams<{ id: string }>()
  const [chatroom, setChatroom] = useState<Chatroom | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm<FormValues>()
  const watchedMode = Form.useWatch('mode', form) as ChatroomMode | undefined
  const watchedTargetHumanCount = Form.useWatch('target_human_count', form) as number | undefined
  const watchedAiStrategy = Form.useWatch('ai_join_strategy', form) as string | undefined
  const watchedTimerMaxMinutes = Form.useWatch('timer_max_minutes', form) as number | null | undefined

  useEffect(() => {
    if (typeof watchedTimerMaxMinutes === 'undefined') return
    form.setFieldValue('max_duration_seconds', deriveMaxDurationSeconds(watchedTimerMaxMinutes ?? null))
  }, [form, watchedTimerMaxMinutes])

  const fetchChatroom = useCallback(async () => {
    if (!hasManagementToken()) {
      setChatroom(null)
      setLoading(false)
      return
    }
    try {
      const data = await mgmtFetchJson<Chatroom>(`/api/getChatroom/${id}`, {
        method: 'POST',
      })
      setChatroom(data)
      const setting = normalizeLoadedSetting(data.setting)
      form.setFieldsValue({
        name: data.name,
        status: data.status === 'active',
        ...setting,
      })
    } catch (e: unknown) {
      Message.error(e instanceof Error ? e.message : 'Failed to load chatroom')
    } finally {
      setLoading(false)
    }
  }, [id, form])

  useEffect(() => { fetchChatroom() }, [fetchChatroom])

  const handleSave = async () => {
    if (!hasManagementToken()) {
      Message.warning('Please log in first')
      return
    }
    const values = await form.validate()

    // Layer custom validation on top of Form's built-in rules.
    const settingToValidate: ChatroomSetting = {
      mode: values.mode,
      topic_instruction: values.topic_instruction,
      additional_prompt: values.additional_prompt,
      ai_personas: values.ai_personas ?? [],
      model_id: values.model_id,
      simulate_pairing_seconds: values.simulate_pairing_seconds,
      timer_min_minutes: values.timer_min_minutes ?? null,
      timer_max_minutes: values.timer_max_minutes ?? null,
      max_duration_seconds: deriveMaxDurationSeconds(values.timer_max_minutes ?? null),
      target_human_count: values.target_human_count,
      ai_join_strategy: values.ai_join_strategy,
      ai_strategy_value: values.ai_strategy_value,
      max_wait_seconds: values.max_wait_seconds,
    }
    const result = validateChatroomSetting(settingToValidate)
    if (!result.ok) {
      const fields: Record<string, { value: unknown; errors: string[] }> = {}
      for (const [field, msg] of Object.entries(result.errors)) {
        fields[field] = {
          value: (values as unknown as Record<string, unknown>)[field],
          errors: [msg],
        }
      }
      form.setFields(fields)
      const firstError = Object.values(result.errors)[0] || 'Validation failed'
      Message.error(firstError)
      throw new Error(firstError)
    }

    const finalSetting = denormalizeForSave(settingToValidate)

    setSaving(true)
    try {
      const updated = await mgmtFetchJson<Chatroom>(`/api/updateChatroom/${id}`, {
        method: 'POST',
        body: JSON.stringify({
          name: values.name,
          status: values.status ? 'active' : 'inactive',
          setting: finalSetting,
        }),
      })
      setChatroom(updated)
      Message.success('Saved')
    } finally {
      setSaving(false)
    }
  }

  const handleSaveSafe = async () => {
    try {
      await handleSave()
    } catch (e: unknown) {
      // Already surfaced via Message.error in handleSave / form.validate.
      if (e instanceof Error && !e.message.startsWith('Validation')) {
        Message.error(e.message)
      }
    }
  }

  const handleOpenUsage = () => {
    const url = `${window.location.origin}${import.meta.env.BASE_URL}#${chatroomUsageRoute(id ?? '')}`
    window.open(url, '_blank', 'noopener,noreferrer')
  }

  if (loading) return <Spin style={{ display: 'block', margin: '80px auto' }} />
  if (!chatroom) return <div style={{ padding: 24 }}>Chatroom not found</div>

  const isGroup = watchedMode === 'group'
  // Hide simulated pairing wait when there are real other humans to wait for.
  // 1-on-1 mode and group-with-target-1 still show the field — both are
  // single-human flows where the wait is purely cosmetic.
  const hideSimulatePairing = isGroup && (watchedTargetHumanCount ?? 0) > 1

  return (
    <div style={{ padding: 24 }}>
      {/* Form area is intentionally narrow — long lines on a 1600px display
          look bad in a vertical form layout. The widget preview below
          breaks out to full width so multiple iframes can sit side-by-side. */}
      <div style={{ maxWidth: 800 }}>
        <div style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 16,
          // Sticky header so Save stays reachable while editing long forms.
          position: 'sticky',
          top: 0,
          zIndex: 10,
          background: '#fff',
          padding: '12px 0',
          borderBottom: '1px solid #f0f0f0',
        }}>
          <h2 style={{ margin: 0 }}>Edit Chatroom</h2>
          <Space>
            <Button onClick={handleOpenUsage}>
              Token Usage
            </Button>
            <Button type="primary" loading={saving} onClick={handleSaveSafe}>
              Save
            </Button>
          </Space>
        </div>

        <div style={{ marginBottom: 8, color: '#86909c', fontSize: 13 }}>
          ID: {chatroom.id}
        </div>

        <Form form={form} layout="vertical">
          {/* ─── Basics ─────────────────────────────────────────────── */}
          <SectionHeader>📋 Basics</SectionHeader>

          <Row>
            <FormItem label="Name" field="name" rules={[{ required: true, message: 'Name is required' }]} style={{ flex: 2, minWidth: 220 }}>
              <Input placeholder="Chatroom name" />
            </FormItem>
            <FormItem label="Status" field="status" triggerPropName="checked" style={{ flex: 0, minWidth: 140 }}>
              <Switch checkedText="Active" uncheckedText="Inactive" />
            </FormItem>
          </Row>

          <Row>
            <FormItem label="Mode" field="mode" rules={[{ required: true }]} style={{ flex: 0, minWidth: 160 }}>
              <Select options={[
                { label: '1 Human x 1 AI', value: 'one_on_one' },
                { label: 'Group', value: 'group' },
              ]} />
            </FormItem>
          </Row>

          {/* ─── Prompt ─────────────────────────────────────────────── */}
          <SectionHeader>💬 Prompt</SectionHeader>

          <FormItem
            label="📝 Chatroom Topic"
            field="topic_instruction"
            extra="Just describe the topic the AI should chat about. The human-mimicry rules, tool-use mechanics, and examples are managed by the backend."
          >
            <TextArea autoSize={{ minRows: 4, maxRows: 12 }} placeholder="Anything about your college life." />
          </FormItem>

          <FormItem
            label="📝 Additional Prompt"
            field="additional_prompt"
            extra="Free-form text appended after the conversation history. Use for last-mile reminders the AI sees right before deciding what to say. Optional."
          >
            <TextArea autoSize={{ minRows: 3, maxRows: 10 }} placeholder="(optional)" />
          </FormItem>

          <FormItem
            label={
              <Space size={4}>
                🎭 AI Personas
                <Popover
                  position="right"
                  trigger="click"
                  content={
                    <div style={{ maxWidth: 360, fontSize: 13, lineHeight: 1.6 }}>
                      <p style={{ marginTop: 0 }}>
                        Pool of per-AI persona instructions. When the lobby closes,
                        the backend randomly picks one entry per AI (distinct when
                        the pool is big enough; with replacement once it overflows).
                      </p>
                      <p>
                        Each entry is free-form — describe the persona AND any
                        dos/don'ts. For example:
                      </p>
                      <pre style={{
                        background: '#f2f3f5', padding: 8, borderRadius: 4,
                        fontSize: 12, whiteSpace: 'pre-wrap', margin: '4px 0',
                      }}>
upenn sophomore, asian studies minor, casual tone.
avoid talking about politics; keep messages under 12 words.
                      </pre>
                      <p>
                        The chosen entry is injected into that AI's system prompt
                        as{' '}
                        <code style={{ background: '#f2f3f5', padding: '0 4px', borderRadius: 3 }}>
                          {'<your-persona>...</your-persona>'}
                        </code>{' '}
                        right before the conversation history; the speech scaffold
                        tells the AI to "stay strictly within those facts."
                      </p>
                      <p style={{ marginBottom: 0 }}>
                        Leave empty to let the AI build its own identity from the topic.
                      </p>
                    </div>
                  }
                >
                  <IconQuestionCircle
                    style={{ color: '#86909c', cursor: 'pointer' }}
                    aria-label="What are AI Personas?"
                  />
                </Popover>
              </Space>
            }
            field="ai_personas"
          >
            <PersonaListEditor />
          </FormItem>

          {/* ─── Model & timing ────────────────────────────────────── */}
          <SectionHeader>⚙️ Model &amp; timing</SectionHeader>

          <FormItem label="🤖 Model" field="model_id">
            <Select showSearch placeholder="Select a model">
              {MODEL_GROUPS.map((group) => (
                <OptGroup key={group.label} label={group.label}>
                  {group.options.map((opt) => (
                    <Option key={opt.value} value={opt.value}>{opt.label}</Option>
                  ))}
                </OptGroup>
              ))}
            </Select>
          </FormItem>

          <Row>
            <FormItem label="⏱️ Simulate Pairing (sec)" field="simulate_pairing_seconds" hidden={hideSimulatePairing} style={{ flex: 1, minWidth: 180 }}>
              <InputNumber min={0} style={{ width: '100%' }} />
            </FormItem>
          </Row>

          <Row>
            <FormItem label="🕒 Timer Min (min)" field="timer_min_minutes" style={{ flex: 1, minWidth: 140 }}>
              <InputNumber min={0} style={{ width: '100%' }} />
            </FormItem>
            <FormItem
              label="🕒 Timer Max (min)"
              field="timer_max_minutes"
              style={{ flex: 1, minWidth: 140 }}
              extra={
                typeof watchedTimerMaxMinutes === 'number' && watchedTimerMaxMinutes > 15
                  ? 'Too long conversation will cause extra cost.'
                  : undefined
              }
            >
              <InputNumber min={0} style={{ width: '100%' }} />
            </FormItem>
          </Row>

          {/* ─── Group mode ────────────────────────────────────────── */}
          {isGroup && (
            <>
              <SectionHeader>👥 Group mode</SectionHeader>

              <Row>
                <FormItem
                  label="💀 Target Human Count"
                  field="target_human_count"
                  rules={[{ required: true, type: 'number', min: VALIDATION_LIMITS.targetHumanCountMin }]}
                  style={{ flex: 1, minWidth: 200 }}
                >
                  <InputNumber min={VALIDATION_LIMITS.targetHumanCountMin} style={{ width: '100%' }} />
                </FormItem>
                <FormItem
                  label="⏰ Max Wait (sec)"
                  field="max_wait_seconds"
                  extra="Lobby cap before starting with as-many-humans-as-possible."
                  rules={[{ required: true, type: 'number', min: 0, max: VALIDATION_LIMITS.maxWaitSecondsMax }]}
                  style={{ flex: 1, minWidth: 200 }}
                >
                  <InputNumber min={0} max={VALIDATION_LIMITS.maxWaitSecondsMax} style={{ width: '100%' }} />
                </FormItem>
              </Row>

              {/* AI Join Strategy: radio + paired number input on the same row.
                  Two rows total; only the picked strategy's number input is
                  active. Both numbers are kept on the form so switching back
                  doesn't lose the previous value. */}
              <FormItem label="🤖 AI Join Strategy" required>
                <FormItem
                  field="ai_join_strategy"
                  rules={[{ required: true }]}
                  noStyle
                >
                  <RadioGroup direction="vertical" style={{ width: '100%' }}>
                    <StrategyRow
                      value="fixed_ai_count"
                      label="Fixed AIs"
                      hint="exactly this many AIs join, regardless of human count"
                      isActive={watchedAiStrategy === 'fixed_ai_count'}
                    />
                    <StrategyRow
                      value="total_participant_count"
                      label="Total Participants"
                      hint="fill up to this many participants total (humans + AIs); must be ≥ Target Human Count"
                      isActive={watchedAiStrategy === 'total_participant_count'}
                    />
                  </RadioGroup>
                </FormItem>
              </FormItem>
            </>
          )}
        </Form>

        <div style={{ borderTop: '1px solid #e5e6eb', margin: '24px 0' }} />
        <ScriptGenerator chatroomId={chatroom.id} />
      </div>

      <div style={{ borderTop: '1px solid #e5e6eb', margin: '24px 0' }} />
      <WidgetPreview chatroomId={chatroom.id} onSaveBeforeLaunch={handleSave} />
    </div>
  )
}
