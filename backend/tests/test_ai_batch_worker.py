from chatroom_api import config
from chatroom_api.ai_batch import worker
import pytest


def _participants(count: int) -> list[dict]:
    return [
        {
            "role": "ai",
            "ai_participant_id": f"ai-{index}",
            "nickname": f"AI {index}",
        }
        for index in range(count)
    ]


def _batch(**overrides) -> dict:
    return {
        "batch_job_id": "batch",
        "chatroom_id": "room",
        "owner_id": "9",
        "status": "running",
        "deadline_at": 100_000,
        "settings_snapshot": {
            "model_id": "model",
            "temperature": 0.7,
            "mimic_human": True,
            "max_message_chars": 400,
            "max_total_chars": 20_000,
            "max_turns": 4,
        },
        **overrides,
    }


def _conversation(count: int = 2, **overrides) -> dict:
    return {
        "conversation_id": "conversation",
        "batch_job_id": "batch",
        "status": "queued",
        "next_turn": 0,
        "message_count": 0,
        "total_chars": 0,
        "participants": _participants(count),
        **overrides,
    }


def test_two_ai_choose_message_requires_the_alternating_candidate(monkeypatch) -> None:
    conversation = _conversation(last_speaker_id="ai-0")
    calls = []

    def invoke(_batch, _conversation, participant, _history, **kwargs):
        calls.append((participant["ai_participant_id"], kwargs["require_message"]))
        return "hello"

    monkeypatch.setattr(worker, "_invoke_candidate", invoke)
    participant, text = worker._choose_message(_batch(), conversation, [])

    assert (participant["ai_participant_id"], text) == ("ai-1", "hello")
    assert calls == [("ai-1", True)]


def test_three_ai_force_path_has_at_most_ai_count_invocations(monkeypatch) -> None:
    calls = []

    def invoke(_batch, _conversation, participant, _history, **kwargs):
        calls.append((participant["ai_participant_id"], kwargs["require_message"]))
        return "forced" if kwargs["require_message"] else None

    monkeypatch.setattr(worker, "_invoke_candidate", invoke)
    _participant, text = worker._choose_message(
        _batch(),
        _conversation(3, last_speaker_id="ai-0"),
        [],
    )

    assert text == "forced"
    assert len(calls) == 3
    assert calls[-1][1] is True


@pytest.mark.parametrize('max_messages,max_chars,expected_count', [(4, 20000, 4), (100, 6, 2)])
def test_process_work_keeps_advancing_persisted_progress(monkeypatch, max_messages, max_chars, expected_count) -> None:
    batch = _batch()
    batch['settings_snapshot'].update(max_turns=max_messages, max_total_chars=max_chars)
    conversation = _conversation()
    conversation['participants'][0]['avatar'] = {'emojiText': 'legacy'}
    committed = []
    monkeypatch.setattr(config, "AI_BATCH_ENABLED", True)
    monkeypatch.setattr(worker.store, "get_batch", lambda _id: batch)
    monkeypatch.setattr(worker.store, "get_conversation", lambda _id: conversation)
    monkeypatch.setattr(worker.store, "now_ms", lambda: 1000)
    monkeypatch.setattr(worker.store, "now_iso", lambda: "now")
    monkeypatch.setattr(worker.store, "start_conversation", lambda *args: True)
    monkeypatch.setattr(worker.store, "query_history", lambda _id: [])
    monkeypatch.setattr(worker, "_choose_message", lambda *args, **kw: (conversation["participants"][0], "hello"))
    monkeypatch.setattr(worker.store, "finalize_batch_if_done", lambda *args: None)

    def commit(conv, event, *, terminal_status):
        committed.append(event)
        conversation.update(next_turn=conv["next_turn"] + 1, message_count=conv["message_count"] + 1,
                            total_chars=conv["total_chars"] + len(event["content"]),
                            execution_state="terminal" if terminal_status else "running",
                            outcome="succeeded" if terminal_status else None)

    monkeypatch.setattr(worker.store, "commit_turn", commit)

    result = worker._process_work({
        "batch_job_id": "batch",
        "conversation_id": "conversation",
        "expected_turn": 0,
    })

    assert result == {"terminal": True, "outcome": "succeeded"}
    assert len(committed) == expected_count
    assert all(event['content'] == 'hello' for event in committed)
    assert committed[0]["content"] == "hello"
    assert committed[0]["timestamp"] == 1000
    assert "authored_at" not in committed[0]
    assert all('avatar' not in event for event in committed)


def test_terminal_reinvocation_does_not_infer(monkeypatch) -> None:
    monkeypatch.setattr(config, "AI_BATCH_ENABLED", True)
    monkeypatch.setattr(worker.store, "get_batch", lambda _id: _batch())
    monkeypatch.setattr(
        worker.store,
        "get_conversation",
        lambda _id: _conversation(status="completed", execution_state="terminal", outcome="succeeded"),
    )
    monkeypatch.setattr(worker.store, "finalize_batch_if_done", lambda *args: None)
    monkeypatch.setattr(worker, "_choose_message", lambda *args: pytest.fail("must not infer"))

    result = worker._process_work({
        "batch_job_id": "batch",
        "conversation_id": "conversation",
        "expected_turn": 1,
    })

    assert result == {"terminal": True, "outcome": "succeeded"}


@pytest.mark.parametrize('guidance', [None, 3])
def test_message_length_is_prompt_only_without_correction(monkeypatch, guidance):
    batch = _batch()
    batch['settings_snapshot']['max_message_chars'] = guidance
    calls = []
    monkeypatch.setattr(worker, 'credits_allow', lambda _: True)
    monkeypatch.setattr(worker, '_build_request', lambda *a, **kw: ('model', 0.7, [], []))
    monkeypatch.setattr(worker.store, 'now_ms', lambda: 1000)
    monkeypatch.setattr(worker, 'record_bedrock_usage', lambda **kw: None)
    def invoke(*args, **kwargs):
        calls.append(kwargs)
        return {'messages': ['This message deliberately exceeds the suggested length.']}
    monkeypatch.setattr(worker, 'invoke_speak_tool', invoke)
    text = worker._invoke_candidate(batch, _conversation(), _participants(2)[0], [], require_message=True, attempt=0)
    assert text == 'This message deliberately exceeds the suggested length.'
    assert len(calls) == 1
    assert calls[0].get('max_message_chars') is None
    assert calls[0]['max_messages'] == 1


def test_progress_is_dynamic_after_cache_and_absent_guidance_is_omitted(monkeypatch):
    import json
    monkeypatch.setattr(worker, 'build_bedrock_system_blocks', lambda *a, **kw: [{'text': 'static'}])
    monkeypatch.setattr(worker, 'build_bedrock_messages', lambda *a, **kw: [])
    monkeypatch.setattr(worker, 'supports_bedrock_prompt_cache', lambda _: True)
    monkeypatch.setattr(worker, 'build_bedrock_cache_prefix_message', lambda *a, **kw: {
        'role': 'user', 'content': [{'text': 'cached rules'}, {'cachePoint': {'type': 'default'}}]})
    monkeypatch.setattr(worker, 'attach_for_inference', lambda messages, *a: messages)
    setting = {**_batch()['settings_snapshot'], 'max_message_chars': None, 'max_turns': 100, 'max_total_chars': 10000}
    _, _, system, messages = worker._build_request(_conversation(message_count=12, total_chars=3000), setting, _participants(2)[0], [], require_message=True)
    contents = [block for msg in messages for block in msg['content']]
    cache_index = next(i for i, b in enumerate(contents) if 'cachePoint' in b)
    progress_index = next(i for i, b in enumerate(contents) if 'Conversation progress' in b.get('text', ''))
    assert progress_index > cache_index
    assert '12/100 messages; 3000/10000 characters used' in contents[progress_index]['text']
    assert 'Aim for' not in json.dumps(messages)
    assert 'progress' not in json.dumps(system)
    setting['max_message_chars'] = 50
    _, _, _, messages = worker._build_request(_conversation(), setting, _participants(2)[0], [], require_message=True)
    assert 'Aim for at most 50 characters' in json.dumps(messages)


@pytest.mark.parametrize('mimic,required', [(True, True), (False, True), (False, False)])
def test_ai_only_scaffold_includes_conclusion_guidance(mimic, required):
    from chatroom_api.prompts.speech_scaffold import get_scaffold_for_mode
    assert get_scaffold_for_mode('ai_only', mimic_human=mimic, require_response=required).startswith('As either conversation limit approaches')
    assert 'As either conversation limit approaches' not in get_scaffold_for_mode('group', mimic_human=mimic, require_response=required)
