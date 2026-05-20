import { describe, it, expect } from 'vitest'
import {
  ChatroomSetting,
  defaultSettingForMode,
  denormalizeForSave,
  ONE_ON_ONE_FIXED,
  validateChatroomSetting,
} from '../chatroomSetting'

const baseGroupSetting = (): ChatroomSetting => ({
  ...defaultSettingForMode('group'),
  // override with a known-good full set
  target_human_count: 2,
  ai_join_strategy: 'fixed_ai_count',
  ai_strategy_value: 1,
  max_wait_seconds: 60,
  max_duration_seconds: 600,
})

describe('validateChatroomSetting', () => {
  it('returns ok=true for a valid group setting', () => {
    expect(validateChatroomSetting(baseGroupSetting())).toEqual({ ok: true, errors: {} })
  })

  it('returns ok=true for a valid one_on_one setting (group fields are ignored)', () => {
    const setting: ChatroomSetting = {
      ...defaultSettingForMode('one_on_one'),
      // pretend bad group fields — they should be ignored for one_on_one
      target_human_count: 0,
      ai_strategy_value: 99,
      max_wait_seconds: 9999,
    }
    const result = validateChatroomSetting(setting)
    expect(result.ok).toBe(true)
  })

  it('rejects target_human_count = 0 in group mode', () => {
    const setting = { ...baseGroupSetting(), target_human_count: 0 }
    const result = validateChatroomSetting(setting)
    expect(result.ok).toBe(false)
    expect(result.errors.target_human_count).toBeDefined()
  })

  it('rejects total_participant_count strategy with ai_strategy_value < target_human_count', () => {
    const setting: ChatroomSetting = {
      ...baseGroupSetting(),
      ai_join_strategy: 'total_participant_count',
      target_human_count: 4,
      ai_strategy_value: 2,
    }
    const result = validateChatroomSetting(setting)
    expect(result.ok).toBe(false)
    expect(result.errors.ai_strategy_value).toBeDefined()
  })

  it('accepts total_participant_count strategy with ai_strategy_value >= target_human_count', () => {
    const setting: ChatroomSetting = {
      ...baseGroupSetting(),
      ai_join_strategy: 'total_participant_count',
      target_human_count: 2,
      ai_strategy_value: 5,
    }
    expect(validateChatroomSetting(setting).ok).toBe(true)
  })

  it('rejects max_wait_seconds = 700 (cap is 600)', () => {
    const setting = { ...baseGroupSetting(), max_wait_seconds: 700 }
    const result = validateChatroomSetting(setting)
    expect(result.ok).toBe(false)
    expect(result.errors.max_wait_seconds).toBeDefined()
  })

  it('rejects negative max_wait_seconds', () => {
    const setting = { ...baseGroupSetting(), max_wait_seconds: -1 }
    const result = validateChatroomSetting(setting)
    expect(result.ok).toBe(false)
    expect(result.errors.max_wait_seconds).toBeDefined()
  })

  it('rejects max_duration_seconds = 5000 (cap is 3600)', () => {
    const setting = { ...baseGroupSetting(), max_duration_seconds: 5000 }
    const result = validateChatroomSetting(setting)
    expect(result.ok).toBe(false)
    expect(result.errors.max_duration_seconds).toBeDefined()
  })

  it('enforces max_duration_seconds in one_on_one mode too', () => {
    const setting: ChatroomSetting = {
      ...defaultSettingForMode('one_on_one'),
      max_duration_seconds: 5000,
    }
    const result = validateChatroomSetting(setting)
    expect(result.ok).toBe(false)
    expect(result.errors.max_duration_seconds).toBeDefined()
  })

  it('rejects ai_strategy_value = 8 (cap is 7)', () => {
    const setting = { ...baseGroupSetting(), ai_strategy_value: 8 }
    const result = validateChatroomSetting(setting)
    expect(result.ok).toBe(false)
    expect(result.errors.ai_strategy_value).toBeDefined()
  })

  it('rejects ai_strategy_value = -1', () => {
    const setting = { ...baseGroupSetting(), ai_strategy_value: -1 }
    const result = validateChatroomSetting(setting)
    expect(result.ok).toBe(false)
    expect(result.errors.ai_strategy_value).toBeDefined()
  })

  it('accepts ai_strategy_value = 0 and = 7 (boundaries)', () => {
    const a = { ...baseGroupSetting(), ai_strategy_value: 0 }
    const b = { ...baseGroupSetting(), ai_strategy_value: 7 }
    expect(validateChatroomSetting(a).ok).toBe(true)
    expect(validateChatroomSetting(b).ok).toBe(true)
  })
})

describe('denormalizeForSave', () => {
  it('forces fixed group values when mode = one_on_one and preserves max_duration_seconds', () => {
    const input: ChatroomSetting = {
      ...defaultSettingForMode('one_on_one'),
      // arbitrary bad group field values to verify they are overwritten
      target_human_count: 99,
      ai_join_strategy: 'total_participant_count',
      ai_strategy_value: 7,
      max_wait_seconds: 600,
      max_duration_seconds: 1200,
    }
    const out = denormalizeForSave(input)
    expect(out.target_human_count).toBe(ONE_ON_ONE_FIXED.target_human_count)
    expect(out.ai_join_strategy).toBe(ONE_ON_ONE_FIXED.ai_join_strategy)
    expect(out.ai_strategy_value).toBe(ONE_ON_ONE_FIXED.ai_strategy_value)
    expect(out.max_wait_seconds).toBe(ONE_ON_ONE_FIXED.max_wait_seconds)
    // preserved across both modes
    expect(out.max_duration_seconds).toBe(1200)
  })

  it('preserves all group fields when mode = group', () => {
    const input: ChatroomSetting = {
      ...defaultSettingForMode('group'),
      target_human_count: 4,
      ai_join_strategy: 'total_participant_count',
      ai_strategy_value: 6,
      max_wait_seconds: 120,
      max_duration_seconds: 1800,
    }
    const out = denormalizeForSave(input)
    expect(out).toEqual(input)
  })

  it('does not mutate the input', () => {
    const input: ChatroomSetting = {
      ...defaultSettingForMode('one_on_one'),
      target_human_count: 99,
    }
    const before = JSON.stringify(input)
    denormalizeForSave(input)
    expect(JSON.stringify(input)).toBe(before)
  })
})

describe('defaultSettingForMode', () => {
  it('one_on_one returns the denormalized fixed values', () => {
    const setting = defaultSettingForMode('one_on_one')
    expect(setting.mode).toBe('one_on_one')
    expect(setting.target_human_count).toBe(ONE_ON_ONE_FIXED.target_human_count)
    expect(setting.ai_join_strategy).toBe(ONE_ON_ONE_FIXED.ai_join_strategy)
    expect(setting.ai_strategy_value).toBe(ONE_ON_ONE_FIXED.ai_strategy_value)
    expect(setting.max_wait_seconds).toBe(ONE_ON_ONE_FIXED.max_wait_seconds)
    // round-trip through validate
    expect(validateChatroomSetting(setting).ok).toBe(true)
  })

  it('group returns sensible defaults that pass validation', () => {
    const setting = defaultSettingForMode('group')
    expect(setting.mode).toBe('group')
    expect(setting.target_human_count).toBe(2)
    expect(setting.ai_join_strategy).toBe('fixed_ai_count')
    expect(setting.ai_strategy_value).toBe(1)
    expect(setting.max_wait_seconds).toBe(60)
    expect(setting.max_duration_seconds).toBe(600)
    expect(validateChatroomSetting(setting).ok).toBe(true)
  })
})
