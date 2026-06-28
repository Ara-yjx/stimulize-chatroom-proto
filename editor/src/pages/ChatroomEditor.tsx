import { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Form, Input, InputNumber, Switch, Select, Button, Message, Spin, Space, Popover, Modal, Card,
} from '@arco-design/web-react'
import { IconDelete, IconPlus, IconQuestionCircle } from '@arco-design/web-react/icon'
import { isManagementAuthExpiredError, mgmtFetchJson } from '../api/management'
import { hasManagementToken, logoutManagement } from '../api/managementAuth'
import {
  ChatroomSetting,
  AiPersonaSetting,
  defaultChatroomSetting,
  defaultSettingForMode,
  denormalizeForSave,
  deriveMaxDurationSeconds,
  normalizeAiPersonas,
  validateChatroomSetting,
  VALIDATION_LIMITS,
} from '../lib/chatroomSetting'
import ScriptGenerator from '../components/ScriptGenerator'
import WidgetPreview from '../components/WidgetPreview'
import { CHATROOM_LIST_ROUTE, chatroomUsageRoute } from '../routes'

const TextArea = Input.TextArea
const FormItem = Form.Item
const Option = Select.Option
const OptGroup = Select.OptGroup
const SAME_MODEL_AS_DEFAULT = '__CHATROOM_DEFAULT__'
const CACHE_SUPPORTED_MODEL_IDS = new Set([
  'global.anthropic.claude-sonnet-4-6',
])

function formatModelOptionLabel(option: { label: string; value: string }): string {
  return CACHE_SUPPORTED_MODEL_IDS.has(option.value)
    ? `${option.label} (supports caching)`
    : option.label
}


/** Section heading inside the form. Compact, uppercase tracking, separator above. */
function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <h4 style={{
      margin: '24px 0 12px',
      paddingTop: 16,
      borderTop: '1px solid #f0f0f0',
      fontSize: 16,
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

function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 4, color: '#4e5969', fontSize: 13, fontWeight: 500 }}>
      {children}
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
  value?: AiPersonaSetting[]
  onChange?: (next: AiPersonaSetting[]) => void
}) {
  const personas = value ?? []
  const update = (next: AiPersonaSetting[]) => onChange?.(next)
  return (
    <div>
      {personas.map((p, i) => (
        <Card
          key={i}
          bordered
          style={{
            marginBottom: 16,
            borderRadius: 8,
          }}
          bodyStyle={{ padding: 16 }}
        >
          <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <Row>
                <div style={{ flex: 1, minWidth: 180 }}>
                  <FieldLabel>Internal name</FieldLabel>
                  <Input
                    value={p.internal_name}
                    onChange={(v) => update(personas.map((x, j) => (j === i ? { ...x, internal_name: v } : x)))}
                    placeholder={`condition_${i + 1}`}
                  />
                  <div style={{ marginTop: 4, color: '#86909c', fontSize: 12 }}>
                    Invisible to participants. Used for logs and result analysis.
                  </div>
                </div>
                <div style={{ flex: 1, minWidth: 180 }}>
                  <FieldLabel>Display name</FieldLabel>
                  <Input
                    value={p.nickname}
                    onChange={(v) => update(personas.map((x, j) => (j === i ? { ...x, nickname: v } : x)))}
                    placeholder="Participant display name"
                  />
                  <div style={{ marginTop: 4, color: '#86909c', fontSize: 12 }}>
                    Shown to chat participants as this AI's name.
                  </div>
                </div>
              </Row>
              <Row>
                <div style={{ flex: 2, minWidth: 260 }}>
                  <FieldLabel>🤖 Model</FieldLabel>
                  <Select
                    value={p.model_id ?? SAME_MODEL_AS_DEFAULT}
                    onChange={(v) => update(
                      personas.map((x, j) => (
                        j === i
                          ? { ...x, model_id: v === SAME_MODEL_AS_DEFAULT ? null : String(v) }
                          : x
                      )),
                    )}
                    placeholder="Select model"
                    style={{ width: '100%' }}
                  >
                    <Option value={SAME_MODEL_AS_DEFAULT}>(same model as chatroom default)</Option>
                    {MODEL_GROUPS.map((group) => (
                      <OptGroup key={group.label} label={group.label}>
                        {group.options.map((opt) => (
                          <Option key={opt.value} value={opt.value}>{formatModelOptionLabel(opt)}</Option>
                        ))}
                      </OptGroup>
                    ))}
                  </Select>
                </div>
                <div style={{ flex: 1, minWidth: 160 }}>
                  <FieldLabel>🌡️ Temperature</FieldLabel>
                  <InputNumber
                    value={p.temperature ?? undefined}
                    min={VALIDATION_LIMITS.temperatureMin}
                    max={VALIDATION_LIMITS.temperatureMax}
                    step={0.1}
                    precision={2}
                    onChange={(v) => update(
                      personas.map((x, j) => (
                        j === i
                          ? { ...x, temperature: typeof v === 'number' ? v : null }
                          : x
                      )),
                    )}
                    placeholder="Same as default"
                    style={{ width: '100%' }}
                  />
                </div>
              </Row>
              <div style={{ marginTop: 8 }}>
                <FieldLabel>Additional prompt</FieldLabel>
                <TextArea
                  value={p.persona}
                  onChange={(v) => update(personas.map((x, j) => (j === i ? { ...x, persona: v } : x)))}
                  autoSize={{ minRows: 2, maxRows: 6 }}
                  placeholder={`Instruction to persona ${i + 1}`}
                  style={{ flex: 1 }}
                />
              </div>
            </div>
            <Button
              shape="circle"
              type="text"
              icon={<IconDelete />}
              onClick={() => update(personas.filter((_, j) => j !== i))}
              aria-label="Remove persona"
            />
          </div>
        </Card>
      ))}
      <Space>
        <Button
          size="small"
          icon={<IconPlus />}
          onClick={() => update([...personas, {
            internal_name: '',
            nickname: '',
            persona: '',
            model_id: null,
            temperature: null,
          }])}
        >
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

interface SaveOptions {
  activate?: boolean
}

/**
 * Older v2 chatrooms may not have all group / max_duration fields. Fill in
 * sensible defaults so the form doesn't crash.
 */
function normalizeLoadedSetting(setting: Partial<ChatroomSetting> | undefined): ChatroomSetting {
  const legacyMode = (setting as { mode?: unknown } | undefined)?.mode
  const defaults = legacyMode === 'group' ? defaultSettingForMode('group') : defaultChatroomSetting()
  const timerMaxMinutes =
    typeof setting?.timer_max_minutes === 'number' ? setting.timer_max_minutes : defaults.timer_max_minutes
  const humanCount =
    typeof setting?.human_count === 'number'
      ? setting.human_count
      : typeof setting?.target_human_count === 'number'
        ? setting.target_human_count
        : defaults.human_count
  const replaceHumanWithAi =
    typeof setting?.replace_human_with_ai === 'boolean'
      ? setting.replace_human_with_ai
      : setting?.ai_join_strategy === 'total_participant_count'
  const aiCount =
    typeof setting?.ai_count === 'number'
      ? setting.ai_count
      : replaceHumanWithAi && typeof setting?.ai_strategy_value === 'number'
        ? Math.max(0, setting.ai_strategy_value - humanCount)
        : typeof setting?.ai_strategy_value === 'number'
          ? setting.ai_strategy_value
          : defaults.ai_count
  return {
    ...defaults,
    ...setting,
    ai_personas: normalizeAiPersonas(setting?.ai_personas),
    mimic_human: typeof setting?.mimic_human === 'boolean' ? setting.mimic_human : defaults.mimic_human,
    temperature: typeof setting?.temperature === 'number' ? setting.temperature : defaults.temperature,
    human_count: humanCount,
    ai_count: aiCount,
    replace_human_with_ai: replaceHumanWithAi,
    max_duration_seconds: deriveMaxDurationSeconds(timerMaxMinutes ?? null),
  }
}

export default function ChatroomEditor() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [chatroom, setChatroom] = useState<Chatroom | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm<FormValues>()
  const sessionExpiredModalShownRef = useRef(false)
  const watchedHumanCount = Form.useWatch('human_count', form) as number | undefined
  const watchedMimicHuman = Form.useWatch('mimic_human', form) as boolean | undefined
  const watchedTimerMaxMinutes = Form.useWatch('timer_max_minutes', form) as number | null | undefined

  useEffect(() => {
    if (typeof watchedTimerMaxMinutes === 'undefined') return
    form.setFieldValue('max_duration_seconds', deriveMaxDurationSeconds(watchedTimerMaxMinutes ?? null))
  }, [form, watchedTimerMaxMinutes])

  useEffect(() => {
    if (typeof watchedHumanCount === 'number' && watchedHumanCount <= 1) {
      form.setFieldValue('replace_human_with_ai', false)
    }
  }, [form, watchedHumanCount])

  useEffect(() => {
    if (watchedHumanCount === 1 && watchedMimicHuman === true) return
    form.setFieldValue('simulate_pairing_seconds', 0)
  }, [form, watchedHumanCount, watchedMimicHuman])

  const showSessionExpiredModal = useCallback(() => {
    if (sessionExpiredModalShownRef.current) return
    sessionExpiredModalShownRef.current = true
    logoutManagement()
    Modal.confirm({
      title: '登录已过期',
      content: '请重新登录。',
      okText: '确定',
      hideCancel: true,
      onOk: () => {
        sessionExpiredModalShownRef.current = false
        navigate(CHATROOM_LIST_ROUTE, { replace: true })
      },
      onCancel: () => {
        sessionExpiredModalShownRef.current = false
      },
      afterClose: () => {
        sessionExpiredModalShownRef.current = false
      },
    })
  }, [navigate])

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

  const handleSave = async (options: SaveOptions = {}) => {
    if (!hasManagementToken()) {
      showSessionExpiredModal()
      return
    }
    const values = await form.validate()
    const nextStatus = options.activate ? 'active' : values.status ? 'active' : 'inactive'

    // Layer custom validation on top of Form's built-in rules.
    const settingToValidate: ChatroomSetting = {
      topic_instruction: values.topic_instruction,
      additional_prompt: values.additional_prompt,
      ai_personas: normalizeAiPersonas(values.ai_personas),
      model_id: values.model_id,
      mimic_human: values.mimic_human,
      temperature: values.temperature,
      simulate_pairing_seconds: values.simulate_pairing_seconds,
      timer_min_minutes: values.timer_min_minutes ?? null,
      timer_max_minutes: values.timer_max_minutes ?? null,
      max_duration_seconds: deriveMaxDurationSeconds(values.timer_max_minutes ?? null),
      human_count: values.human_count,
      ai_count: values.ai_count,
      replace_human_with_ai: values.replace_human_with_ai,
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
          status: nextStatus,
          setting: finalSetting,
        }),
      })
      setChatroom(updated)
      if (options.activate) {
        form.setFieldValue('status', true)
      }
      Message.success(options.activate ? 'Saved and activated' : 'Saved')
    } finally {
      setSaving(false)
    }
  }

  const handleSaveSafe = async () => {
    try {
      await handleSave()
    } catch (e: unknown) {
      if (isManagementAuthExpiredError(e)) {
        showSessionExpiredModal()
        return
      }
      // Already surfaced via Message.error in handleSave / form.validate.
      if (e instanceof Error && !e.message.startsWith('Validation')) {
        Message.error(e.message)
      }
    }
  }

  const handleSaveAndActivate = async () => {
    await handleSave({ activate: true })
  }

  const handleOpenUsage = () => {
    const url = `${window.location.origin}${import.meta.env.BASE_URL}#${chatroomUsageRoute(id ?? '')}`
    window.open(url, '_blank', 'noopener,noreferrer')
  }

  if (loading) return <Spin style={{ display: 'block', margin: '80px auto' }} />
  if (!chatroom) return <div style={{ padding: 24 }}>Chatroom not found</div>

  const enableSimulatePairing = (watchedHumanCount ?? 1) === 1 && watchedMimicHuman === true

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

          {/* ─── Participants ─────────────────────────────────────── */}
          <SectionHeader>👥 Participants</SectionHeader>

          <Row>
            <FormItem
              label="Human Count"
              field="human_count"
              rules={[{ required: true, type: 'number', min: VALIDATION_LIMITS.targetHumanCountMin }]}
              style={{ flex: 1, minWidth: 200 }}
            >
              <InputNumber min={VALIDATION_LIMITS.targetHumanCountMin} style={{ width: '100%' }} />
            </FormItem>
            <FormItem
              label="AI Count"
              field="ai_count"
              rules={[{
                required: true,
                type: 'number',
                min: VALIDATION_LIMITS.aiStrategyValueMin,
                max: VALIDATION_LIMITS.aiStrategyValueMax,
              }]}
              style={{ flex: 1, minWidth: 200 }}
            >
              <InputNumber
                min={VALIDATION_LIMITS.aiStrategyValueMin}
                max={VALIDATION_LIMITS.aiStrategyValueMax}
                style={{ width: '100%' }}
              />
            </FormItem>
          </Row>

          <Row>
            <FormItem
              label="Max wait time (sec)"
              field="max_wait_seconds"
              rules={[{
                required: true,
                type: 'number',
                min: 0,
                max: VALIDATION_LIMITS.maxWaitSecondsMax,
              }]}
              extra="How long the lobby waits for humans before starting or filling missing seats."
              style={{ flex: 1, minWidth: 220 }}
            >
              <InputNumber
                min={0}
                max={VALIDATION_LIMITS.maxWaitSecondsMax}
                disabled={(watchedHumanCount ?? 1) <= 1}
                style={{ width: '100%' }}
              />
            </FormItem>
            <FormItem
              label="Replace missing humans with AI"
              field="replace_human_with_ai"
              triggerPropName="checked"
              extra={
                (watchedHumanCount ?? 1) <= 1
                  ? 'Only applies when Human Count is greater than 1.'
                  : 'When on, missing human seats are filled by additional AIs at the wait deadline.'
              }
              style={{ flex: 1, minWidth: 260 }}
            >
              <Switch
                checkedText="On"
                uncheckedText="Off"
                disabled={(watchedHumanCount ?? 1) <= 1}
              />
            </FormItem>
          </Row>

          {/* ─── Model and Prompt ──────────────────────────────────── */}
          <SectionHeader>⚙️ Model and Prompt</SectionHeader>

          <Row>
            <FormItem
              label="🤖 Model"
              field="model_id"
              extra="Models marked 'supports caching' can save about 80% of repeated input prompt tokens after the first tick."
              style={{ flex: 2, minWidth: 280 }}
            >
              <Select showSearch placeholder="Select a model">
                {MODEL_GROUPS.map((group) => (
                  <OptGroup key={group.label} label={group.label}>
                    {group.options.map((opt) => (
                      <Option key={opt.value} value={opt.value}>{formatModelOptionLabel(opt)}</Option>
                    ))}
                  </OptGroup>
                ))}
              </Select>
            </FormItem>

            <FormItem
              label="🌡️ Temperature"
              field="temperature"
              rules={[{
                required: true,
                type: 'number',
                min: VALIDATION_LIMITS.temperatureMin,
                max: VALIDATION_LIMITS.temperatureMax,
              }]}
              extra="Bedrock beta range is 0.0-1.0. OpenAI and Anthropic direct API limits may differ later."
              style={{ flex: 1, minWidth: 180 }}
            >
              <InputNumber
                min={VALIDATION_LIMITS.temperatureMin}
                max={VALIDATION_LIMITS.temperatureMax}
                step={0.1}
                precision={2}
                style={{ width: '100%' }}
              />
            </FormItem>
          </Row>

          <FormItem
            label="Mimic human"
            field="mimic_human"
            triggerPropName="checked"
            extra="When off, the backend uses a generic AI-assistant prompt instead of human-mimic instructions and examples."
          >
            <Switch checkedText="On" uncheckedText="Off" />
          </FormItem>

          <FormItem
            label="📝 Chatroom Topic"
            field="topic_instruction"
            extra="Describe the topic the participants should chat about. (You also need to tell human participants about the topic in your Qualtrics survey.)"
          >
            <TextArea autoSize={{ minRows: 4, maxRows: 12 }} placeholder="Anything about your college life." />
          </FormItem>

          <FormItem
            label="📝 Additional Prompt"
            field="additional_prompt"
            extra="The backend already provides general instructions for online conversation and, when enabled, human mimicry. Use this optional prompt to fine-tune this chatroom's AI behavior."
          >
            <TextArea autoSize={{ minRows: 3, maxRows: 10 }} placeholder="(optional)" />
          </FormItem>

          {/* ─── AI Personas ───────────────────────────────────────── */}
          <SectionHeader>
            <Space size={4}>
              AI Personas
              <Popover
                position="right"
                trigger="click"
                content={
                  <div style={{ maxWidth: 360, fontSize: 13, lineHeight: 1.6 }}>
                    <p style={{ marginTop: 0 }}>
                      Pool of per-AI persona instructions. When the lobby closes,
                      the backend assigns one entry per AI in round-robin-style
                      batches, and each persona can optionally override the
                      chatroom default model.
                    </p>
                    <p>
                      Each entry is free-form — describe the persona AND any
                      dos/don'ts, then optionally pick a model just for that
                      persona. For example:
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
          </SectionHeader>

          <FormItem field="ai_personas">
            <PersonaListEditor />
          </FormItem>

          {/* ─── Misc ──────────────────────────────────────────────── */}
          <SectionHeader>Misc</SectionHeader>

          <Row>
            <FormItem
              label="🕒 Timer Min"
              field="timer_min_minutes"
              extra="When set, Qualtrics 'Next' button will be hidden until this time has elapsed"
              style={{ flex: 1, minWidth: 180 }}
            >
              <InputNumber min={0} suffix="minutes" style={{ width: '100%' }} />
            </FormItem>
            <FormItem
              label="🕒 Timer Max"
              field="timer_max_minutes"
              style={{ flex: 1, minWidth: 180 }}
              extra="Chatroom will force-close when this time has elapsed, even if participants are still chatting. Too long conversation (>15 minutes) will cause extra AI cost. "
            >
              <InputNumber min={0} suffix="minutes" style={{ width: '100%' }} />
            </FormItem>
          </Row>

          <FormItem
            label="⏱️ Simulate Pairing (sec)"
            field="simulate_pairing_seconds"
            hidden={!enableSimulatePairing}
            extra="Server-managed lobby duration before a one-human mimic-human chat starts."
          >
            <InputNumber min={0} style={{ width: '100%' }} />
          </FormItem>
        </Form>

        <div style={{ borderTop: '1px solid #e5e6eb', margin: '24px 0' }} />
        <ScriptGenerator chatroomId={chatroom.id} />
      </div>

      <div style={{ borderTop: '1px solid #e5e6eb', margin: '24px 0' }} />
      <WidgetPreview chatroomId={chatroom.id} onSaveBeforeLaunch={handleSaveAndActivate} />
    </div>
  )
}
