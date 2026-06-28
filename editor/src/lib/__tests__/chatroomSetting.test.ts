import { describe, it, expect } from 'vitest'
import {
  ChatroomSetting,
  defaultChatroomSetting,
  defaultSettingForMode,
  deriveChatroomMode,
  denormalizeForSave,
  ONE_ON_ONE_FIXED,
  validateChatroomSetting,
} from '../chatroomSetting'

const baseGroupSetting = (): ChatroomSetting => ({
  ...defaultSettingForMode('group'),
  // override with a known-good full set
  human_count: 2,
  ai_count: 1,
  replace_human_with_ai: false,
  max_wait_seconds: 60,
  max_duration_seconds: 600,
})

describe('validateChatroomSetting', () => {
  it('returns ok=true for a valid group setting', () => {
    expect(validateChatroomSetting(baseGroupSetting())).toEqual({ ok: true, errors: {} })
  })

  it('returns ok=true for a valid one-human one-ai setting', () => {
    const setting: ChatroomSetting = {
      ...defaultChatroomSetting(),
    }
    const result = validateChatroomSetting(setting)
    expect(result.ok).toBe(true)
  })

  it('rejects human_count = 0', () => {
    const setting = { ...baseGroupSetting(), human_count: 0 }
    const result = validateChatroomSetting(setting)
    expect(result.ok).toBe(false)
    expect(result.errors.human_count).toBeDefined()
  })

  it('accepts replace_human_with_ai because total participants are derived from human_count + ai_count', () => {
    const setting: ChatroomSetting = {
      ...baseGroupSetting(),
      replace_human_with_ai: true,
      human_count: 4,
      ai_count: 2,
    }
    expect(validateChatroomSetting(setting).ok).toBe(true)
  })

  it('denormalizes replace_human_with_ai into total_participant_count', () => {
    const setting: ChatroomSetting = {
      ...baseGroupSetting(),
      replace_human_with_ai: true,
      human_count: 2,
      ai_count: 3,
    }
    const out = denormalizeForSave(setting)
    expect(out.ai_join_strategy).toBe('total_participant_count')
    expect(out.ai_strategy_value).toBe(5)
    expect(out.target_human_count).toBe(2)
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

  it('enforces max_duration_seconds for one-human defaults too', () => {
    const setting: ChatroomSetting = {
      ...defaultChatroomSetting(),
      max_duration_seconds: 5000,
    }
    const result = validateChatroomSetting(setting)
    expect(result.ok).toBe(false)
    expect(result.errors.max_duration_seconds).toBeDefined()
  })

  it('rejects ai_count = 8 (cap is 7)', () => {
    const setting = { ...baseGroupSetting(), ai_count: 8 }
    const result = validateChatroomSetting(setting)
    expect(result.ok).toBe(false)
    expect(result.errors.ai_count).toBeDefined()
  })

  it('rejects ai_count = -1', () => {
    const setting = { ...baseGroupSetting(), ai_count: -1 }
    const result = validateChatroomSetting(setting)
    expect(result.ok).toBe(false)
    expect(result.errors.ai_count).toBeDefined()
  })

  it('accepts ai_count = 0 and = 7 (boundaries)', () => {
    const a = { ...baseGroupSetting(), ai_count: 0 }
    const b = { ...baseGroupSetting(), ai_count: 7 }
    expect(validateChatroomSetting(a).ok).toBe(true)
    expect(validateChatroomSetting(b).ok).toBe(true)
  })
})

describe('denormalizeForSave', () => {
  it('derives fixed runtime values for one-human one-ai and preserves max_duration_seconds', () => {
    const input: ChatroomSetting = {
      ...defaultChatroomSetting(),
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
    expect(out.max_wait_seconds).toBe(input.simulate_pairing_seconds)
    // preserved across both modes
    expect(out.max_duration_seconds).toBe(1200)
  })

  it('ignores simulated pairing when one-human mimic_human is off', () => {
    const input: ChatroomSetting = {
      ...defaultChatroomSetting(),
      mimic_human: false,
      simulate_pairing_seconds: 15,
    }
    const out = denormalizeForSave(input)
    expect(out.simulate_pairing_seconds).toBe(0)
    expect(out.max_wait_seconds).toBe(0)
  })

  it('derives runtime group fields from participant counts', () => {
    const input: ChatroomSetting = {
      ...defaultSettingForMode('group'),
      human_count: 4,
      ai_count: 2,
      replace_human_with_ai: true,
      max_wait_seconds: 120,
      max_duration_seconds: 1800,
    }
    const out = denormalizeForSave(input)
    expect(out.human_count).toBe(4)
    expect(out.ai_count).toBe(2)
    expect(out.replace_human_with_ai).toBe(true)
    expect(out.target_human_count).toBe(4)
    expect(out.ai_join_strategy).toBe('total_participant_count')
    expect(out.ai_strategy_value).toBe(6)
  })

  it('does not mutate the input', () => {
    const input: ChatroomSetting = {
      ...defaultChatroomSetting(),
      target_human_count: 99,
    }
    const before = JSON.stringify(input)
    denormalizeForSave(input)
    expect(JSON.stringify(input)).toBe(before)
  })
})

describe('defaultSettingForMode', () => {
  it('defaultChatroomSetting returns one-human one-ai values', () => {
    const setting = defaultChatroomSetting()
    expect(deriveChatroomMode(setting)).toBe('one_on_one')
    expect(setting.target_human_count).toBe(ONE_ON_ONE_FIXED.target_human_count)
    expect(setting.ai_join_strategy).toBe(ONE_ON_ONE_FIXED.ai_join_strategy)
    expect(setting.ai_strategy_value).toBe(ONE_ON_ONE_FIXED.ai_strategy_value)
    expect(setting.max_wait_seconds).toBe(ONE_ON_ONE_FIXED.max_wait_seconds)
    // round-trip through validate
    expect(validateChatroomSetting(setting).ok).toBe(true)
  })

  it('group returns sensible defaults that pass validation', () => {
    const setting = defaultSettingForMode('group')
    expect(deriveChatroomMode(setting)).toBe('group')
    expect(setting.human_count).toBe(2)
    expect(setting.ai_count).toBe(1)
    expect(setting.replace_human_with_ai).toBe(false)
    expect(setting.target_human_count).toBe(2)
    expect(setting.ai_join_strategy).toBe('fixed_ai_count')
    expect(setting.ai_strategy_value).toBe(1)
    expect(setting.max_wait_seconds).toBe(60)
    expect(setting.max_duration_seconds).toBe(360)
    expect(validateChatroomSetting(setting).ok).toBe(true)
  })
})
