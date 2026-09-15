from pathlib import Path

from nimbledesk.creative.models import BrandRules, CreativeBrief
from nimbledesk.creative.style import (
    StyleDefaults,
    StyleProfile,
    StyleProfileStore,
    resolve_brief,
)


def test_style_profile_applies_defaults_without_overriding_brief(tmp_path: Path) -> None:
    logo = tmp_path / "logo.png"
    profile = StyleProfile(
        profile_id="rohit-gaming",
        name="Rohit gaming",
        defaults=StyleDefaults(
            audience="competitive players",
            platform="youtube_shorts",
            aspect_ratio="9:16",
            pace="fast",
            mood="tense exciting",
            color_look="vivid",
            brand=BrandRules(logo_path=logo, primary_color="#ff5500"),
        ),
    )

    brief = resolve_brief({"title": "Final round", "pace": "calm"}, profile)

    assert brief.audience == "competitive players"
    assert brief.aspect_ratio.value == "9:16"
    assert brief.pace.value == "calm"
    assert brief.brand.logo_path == logo


def test_style_profile_store_is_atomic_and_records_explicit_feedback(tmp_path: Path) -> None:
    store = StyleProfileStore(tmp_path / "profiles")
    profile = StyleProfile(profile_id="main", name="Main channel")

    path = store.save(profile)
    updated = store.record_feedback("main", "context-first", "Preferred clearer setup")

    assert path.is_file()
    assert not path.with_suffix(".json.tmp").exists()
    assert store.load("main") == updated
    assert updated.feedback[0].selected_variant_id == "context-first"
    assert updated.feedback[0].notes == "Preferred clearer setup"
    assert store.list() == (updated,)


def test_creative_brief_keeps_remote_media_private_by_default() -> None:
    brief = CreativeBrief()

    assert not brief.data_policy.allow_remote_frames
    assert not brief.data_policy.allow_remote_audio
    assert not brief.data_policy.allow_remote_transcript
