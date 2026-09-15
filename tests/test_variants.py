from pathlib import Path

from nimbledesk.creative.models import CreativeBrief
from nimbledesk.creative.variants import generate_variant_comparison
from nimbledesk.media.models import (
    AnalysisConfig,
    HighlightCandidate,
    HighlightManifest,
    MediaMetadata,
)


def test_variant_comparison_produces_valid_meaningful_tradeoffs(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    manifest = HighlightManifest(
        source=MediaMetadata(
            path=source,
            duration_seconds=90,
            width=1920,
            height=1080,
            frame_rate=30,
            has_audio=True,
            video_codec="h264",
            audio_codec="aac",
        ),
        config=AnalysisConfig(),
        candidates=(
            HighlightCandidate(
                rank=1,
                start_seconds=40,
                end_seconds=55,
                peak_seconds=48,
                score=1,
                reasons=("clutch payoff",),
                event_labels=("clutch",),
            ),
            HighlightCandidate(
                rank=2,
                start_seconds=5,
                end_seconds=17,
                peak_seconds=10,
                score=0.75,
                reasons=("match setup",),
            ),
            HighlightCandidate(
                rank=3,
                start_seconds=65,
                end_seconds=77,
                peak_seconds=70,
                score=0.7,
                reasons=("victory reaction",),
                event_labels=("victory",),
            ),
        ),
        clips=(),
    )
    brief = CreativeBrief(
        title="Variant fixture",
        target_duration_seconds=35,
        clip_count=3,
        music=False,
        captions=False,
    )

    comparison = generate_variant_comparison(manifest, brief, tmp_path / "variants")

    assert {variant.variant_id for variant in comparison.variants} == {
        "strongest-hook",
        "energetic-short",
        "context-first",
    }
    context = next(item for item in comparison.variants if item.variant_id == "context-first")
    energetic = next(item for item in comparison.variants if item.variant_id == "energetic-short")
    assert context.strategy == "chronological"
    assert "improves clarity" in context.tradeoff
    assert energetic.plan_path.is_file()
    assert all(metric.evidence for variant in comparison.variants for metric in variant.metrics)
    assert "do not predict or guarantee" in comparison.disclaimer
    assert (tmp_path / "variants" / "variant_comparison.json").is_file()
