from chatroom_api.settings import (
    is_single_human_single_ai_assistant_room,
    normalize_ai_nickname,
    resolve_runtime_setting,
)


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
