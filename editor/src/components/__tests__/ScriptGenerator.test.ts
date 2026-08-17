import { describe, expect, it } from 'vitest'
import { buildEmbedScript } from '../ScriptGenerator'

describe('buildEmbedScript', () => {
  it('omits the resume hint for a non-resumable chatroom', () => {
    const script = buildEmbedScript('scid_non_resume', false)

    expect(script).toContain('chatroomId: "scid_non_resume"')
    expect(script).not.toContain('resumable:')
  })

  it('includes the resume hint only for a resumable chatroom', () => {
    const script = buildEmbedScript('scid_resume', true)

    expect(script).toContain('chatroomId: "scid_resume"')
    expect(script).toContain('resumable: true')
  })
})
