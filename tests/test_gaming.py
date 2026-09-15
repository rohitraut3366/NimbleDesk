from nimbledesk.creative.gaming import DEFAULT_GAME_PACK, detect_events_from_ocr_samples


def test_game_pack_recognizes_kill_feed_patterns_and_infers_multi_kill() -> None:
    events = detect_events_from_ocr_samples(
        (
            (10, "rohit eliminated opponent_one"),
            (13, "opponent_two was killed by rohit"),
        ),
        DEFAULT_GAME_PACK,
    )

    assert [event.event_type for event in events].count("kill") == 2
    inferred = next(event for event in events if event.event_type == "multi_kill")
    assert inferred.time_seconds == 13
    assert inferred.label == "Inferred 2-kill streak"


def test_game_pack_infers_clutch_from_critical_health_and_payoff() -> None:
    events = detect_events_from_ocr_samples(
        (
            (40, "7 hp"),
            (46, "you killed final_enemy"),
            (48, "round won"),
        ),
        DEFAULT_GAME_PACK,
    )

    assert {event.event_type for event in events} >= {
        "narrow_survival",
        "kill",
        "victory",
        "clutch",
    }
    clutch = next(event for event in events if event.event_type == "clutch")
    assert clutch.time_seconds == 46
    assert clutch.importance == 1


def test_game_pack_cooldown_suppresses_duplicate_ocr_frames() -> None:
    events = detect_events_from_ocr_samples(
        (
            (1, "you killed player_one"),
            (2, "you killed player_one"),
            (3, "you killed player_two"),
        ),
        DEFAULT_GAME_PACK,
    )

    kills = [event for event in events if event.event_type == "kill"]
    assert [event.time_seconds for event in kills] == [1, 3]
