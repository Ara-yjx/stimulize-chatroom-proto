from chatroom_api.settings import (
    derive_runtime_mode,
    is_single_human_single_ai_assistant_room,
    normalize_ai_nickname,
    resolve_runtime_setting,
)


def test_ai_only_setting_preserves_zero_humans_and_batch_limits() -> None:
    setting = resolve_runtime_setting({
        "human_count": 0,
        "ai_count": 3,
        "resumable": True,
    })

    assert derive_runtime_mode(setting) == "ai_only"
    assert setting["human_count"] == 0
    assert setting["target_human_count"] == 0
    assert setting["ai_strategy_value"] == 3
    assert setting["max_wait_seconds"] == 0
    assert setting["resumable"] is False
    assert setting["max_message_chars"] is None
    assert resolve_runtime_setting({'max_message_chars': None})['max_message_chars'] is None
    assert resolve_runtime_setting({'max_message_chars': 400})['max_message_chars'] == 400
    assert setting["max_total_chars"] == 20_000
    assert setting["max_turns"] == 100


def test_show_avatars_defaults_on_and_preserves_false() -> None:
    assert resolve_runtime_setting({})["show_avatars"] is True
    assert resolve_runtime_setting({"show_avatars": False})["show_avatars"] is False
    assert resolve_runtime_setting({"show_avatars": "false"})["show_avatars"] is True


def test_assistant_room_detection_is_exact() -> None:
    assert is_single_human_single_ai_assistant_room({
        "mimic_human": False,
        "human_count": 1,
        "ai_count": 1,
    })
    assert not is_single_human_single_ai_assistant_room({
        "mimic_human": True,
        "human_count": 1,
        "ai_count": 1,
    })
    assert not is_single_human_single_ai_assistant_room({
        "mimic_human": False,
        "human_count": 2,
        "ai_count": 1,
    })


def test_runtime_ai_nickname_filters_reserved_names_case_insensitively() -> None:
    assert normalize_ai_nickname(" Alex ") == "Alex"
    assert normalize_ai_nickname("YOU") == ""
    assert normalize_ai_nickname(" Participant ") == ""
    assert resolve_runtime_setting({"ai_nickname": " Helper "})["ai_nickname"] == "Helper"
