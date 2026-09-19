from chatroom_api.prompts.speech_scaffold import get_scaffold_for_mode
from chatroom_api.prompts.construction import build_semi_static_setup_blocks


def test_mimic_and_group_prompts_define_room_silence_and_participant_invitation():
    for mode, mimic in [('one_on_one', True), ('group', True), ('group', False)]:
        text = get_scaffold_for_mode(mode, mimic_human=mimic)
        assert 'entire chatroom has been silent for roughly 30 seconds' in text
        assert 'invite them to share' in text


def test_single_assistant_idle_policy_still_uses_sixty_seconds():
    blocks = build_semi_static_setup_blocks(
        {'human_count': 1, 'ai_count': 1, 'mimic_human': False}, '', 'AI')
    assert 'roughly 60 seconds' in '\n'.join(blocks)
