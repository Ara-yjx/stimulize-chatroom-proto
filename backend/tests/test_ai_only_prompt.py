import pytest
from chatroom_api.prompts.speech_scaffold import get_scaffold_for_mode
from chatroom_api.prompts.construction import build_bedrock_system_blocks


@pytest.mark.parametrize('count', [2, 3, 7])
@pytest.mark.parametrize('required', [False, True])
def test_discussion_scaffold_is_independent_of_mimic_human(count, required):
    text = get_scaffold_for_mode('ai_only', ai_count=count, require_response=required)
    assert text == get_scaffold_for_mode('ai_only', ai_count=count, require_response=required, mimic_human=False)
    assert 'semi-formal' in text and 'not an assistant' in text
    assert 'Never split' in text and 'Example 4' not in text and 'mid-thought' not in text
    assert ('Example 1' in text) == (count > 2)
    assert ('"messages": []' in text) == (count > 2)
    if count > 2 and required:
        assert 'For this call' in text


@pytest.mark.parametrize('model', ['global.anthropic.claude-sonnet-4-6', 'no-cache-model'])
def test_request_construction_passes_ai_count_to_static_rules(model):
    for count in (2, 3):
        blocks = build_bedrock_system_blocks('ai_only', {'ai_count': count}, '', 'Participant_001', '', model_id=model)
        assert ('Example 1' in blocks[0]['text']) == (count == 3)
