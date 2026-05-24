/**
 * Pure logic for chatroom settings: types, validation, and denormalization.
 *
 * Kept free of React / Arco / network imports so it can be unit-tested
 * without a DOM or network. See:
 * - docs/low-level-design.md "Form validation" / "Mode UX"
 * - docs/api-management.yml "ChatroomSetting"
 */

export type ChatroomMode = 'one_on_one' | 'group'

export type AiJoinStrategy = 'fixed_ai_count' | 'total_participant_count'

export interface ChatroomSetting {
  mode: ChatroomMode
  /**
   * Researcher-supplied topic. Just the topic — the human-mimicry speech
   * scaffold (rules, tool-use mechanics, examples) lives in the backend
   * and is wrapped around this string at runtime.
   * E.g. "Anything about your college life."
   */
  topic_instruction: string
  /**
   * Free-form text appended after the conversation history block in the
   * per-tick system prompt. Use this for last-mile reminders the AI must
   * see right before deciding what to say. Optional.
   */
  additional_prompt: string
  /**
   * Pool of researcher-supplied per-AI personas. When the lobby closes,
   * the backend randomly picks one persona per AI (without replacement
   * when the pool has enough entries; with replacement on overflow).
   * Each entry is a free-form string injected into the AI's system
   * prompt as ``<your-persona>...</your-persona>``. Empty pool → no
   * persona block; the scaffold's "build an identity as the conversation
   * goes" rule takes over.
   */
  ai_personas: string[]
  model_id: string
  simulate_pairing_seconds: number
  timer_min_minutes: number | null
  timer_max_minutes: number | null
  /** Cap on total conversation duration (seconds). Applies to both modes. */
  max_duration_seconds: number
  /** Group fields. For one_on_one, these are stored denormalized. */
  target_human_count: number
  ai_join_strategy: AiJoinStrategy
  ai_strategy_value: number
  max_wait_seconds: number
}

/** One-on-one denormalized fixed values per low-level design. */
export const ONE_ON_ONE_FIXED = {
  target_human_count: 1,
  ai_join_strategy: 'fixed_ai_count' as const,
  ai_strategy_value: 1,
  max_wait_seconds: 0,
}

/** Validation caps per docs/low-level-design.md "Form validation". */
export const VALIDATION_LIMITS = {
  maxWaitSecondsMax: 600,
  maxDurationSecondsMax: 3600,
  aiStrategyValueMin: 0,
  aiStrategyValueMax: 7,
  targetHumanCountMin: 1,
}

export interface ValidationResult {
  ok: boolean
  errors: Record<string, string>
}

export function deriveMaxDurationSeconds(timerMaxMinutes: number | null): number {
  const effectiveMaxMinutes =
    typeof timerMaxMinutes === 'number' && Number.isFinite(timerMaxMinutes) && timerMaxMinutes >= 0
      ? timerMaxMinutes
      : 0
  return (effectiveMaxMinutes + 1) * 60
}

/**
 * Validate a chatroom setting per the rules in docs/low-level-design.md.
 *
 * Rules (only enforced for `mode === "group"`; one_on_one denormalizes on save):
 * - target_human_count >= 1
 * - if ai_join_strategy = total_participant_count: ai_strategy_value >= target_human_count
 * - 0 <= max_wait_seconds <= 600
 * - 0 <= ai_strategy_value <= 7
 *
 * Always enforced (both modes):
 * - 0 <= max_duration_seconds <= 3600
 */
export function validateChatroomSetting(setting: ChatroomSetting): ValidationResult {
  const errors: Record<string, string> = {}

  // Always validate max_duration_seconds (applies to both modes).
  if (
    !Number.isFinite(setting.max_duration_seconds) ||
    setting.max_duration_seconds < 0 ||
    setting.max_duration_seconds > VALIDATION_LIMITS.maxDurationSecondsMax
  ) {
    errors.max_duration_seconds = `max_duration_seconds must be between 0 and ${VALIDATION_LIMITS.maxDurationSecondsMax}`
  }

  if (setting.mode === 'group') {
    if (
      !Number.isFinite(setting.target_human_count) ||
      setting.target_human_count < VALIDATION_LIMITS.targetHumanCountMin
    ) {
      errors.target_human_count = `target_human_count must be >= ${VALIDATION_LIMITS.targetHumanCountMin}`
    }

    if (
      !Number.isFinite(setting.ai_strategy_value) ||
      setting.ai_strategy_value < VALIDATION_LIMITS.aiStrategyValueMin ||
      setting.ai_strategy_value > VALIDATION_LIMITS.aiStrategyValueMax
    ) {
      errors.ai_strategy_value = `ai_strategy_value must be between ${VALIDATION_LIMITS.aiStrategyValueMin} and ${VALIDATION_LIMITS.aiStrategyValueMax}`
    }

    if (
      setting.ai_join_strategy === 'total_participant_count' &&
      Number.isFinite(setting.ai_strategy_value) &&
      Number.isFinite(setting.target_human_count) &&
      setting.ai_strategy_value < setting.target_human_count
    ) {
      errors.ai_strategy_value =
        'ai_strategy_value must be >= target_human_count when strategy is total_participant_count'
    }

    if (
      !Number.isFinite(setting.max_wait_seconds) ||
      setting.max_wait_seconds < 0 ||
      setting.max_wait_seconds > VALIDATION_LIMITS.maxWaitSecondsMax
    ) {
      errors.max_wait_seconds = `max_wait_seconds must be between 0 and ${VALIDATION_LIMITS.maxWaitSecondsMax}`
    }
  }

  return { ok: Object.keys(errors).length === 0, errors }
}

/**
 * Compute the on-save setting. For one_on_one, group fields are forced to
 * fixed values so the chat Lambda can branch on group fields uniformly
 * without re-deriving from `mode`. max_duration_seconds is preserved in
 * both modes.
 */
export function denormalizeForSave(values: ChatroomSetting): ChatroomSetting {
  if (values.mode === 'one_on_one') {
    return {
      ...values,
      ...ONE_ON_ONE_FIXED,
    }
  }
  return { ...values }
}

/**
 * Default setting for a freshly-created chatroom in the given mode. Used by
 * the create-chatroom modal.
 */
export function defaultSettingForMode(mode: ChatroomMode): ChatroomSetting {
  const base: Omit<
    ChatroomSetting,
    'target_human_count' | 'ai_join_strategy' | 'ai_strategy_value' | 'max_wait_seconds'
  > = {
    mode,
    topic_instruction: 'Anything about your college life.',
    additional_prompt: '',
    ai_personas: [],
    model_id: 'global.anthropic.claude-sonnet-4-6',
    simulate_pairing_seconds: 15,
    timer_min_minutes: 1,
    timer_max_minutes: 5,
    max_duration_seconds: deriveMaxDurationSeconds(5),
  }

  if (mode === 'one_on_one') {
    return { ...base, ...ONE_ON_ONE_FIXED }
  }
  return {
    ...base,
    target_human_count: 2,
    ai_join_strategy: 'fixed_ai_count',
    ai_strategy_value: 1,
    max_wait_seconds: 60,
  }
}
