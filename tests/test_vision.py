import json
import sys
from pathlib import Path

from PIL import Image
from pytest import MonkeyPatch

import nimbledesk.creative.vision as vision


def test_semantic_vision_uses_bounded_contact_sheets_and_confidence_gate(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    frames = tuple(tmp_path / f"frame-{index:02d}.jpg" for index in range(13))
    for index, frame in enumerate(frames):
        Image.new("RGB", (640, 360), (index * 10, 20, 30)).save(frame)
    worker = tmp_path / "worker.py"
    worker.write_text(
        """
import json
import sys
request = json.load(open(sys.argv[1], encoding='utf-8'))
assert len(request['sheets']) == 2
json.dump({'events': [
    {'time_seconds': 12, 'event_type': 'grenade_kill', 'label': 'Grenade double kill',
     'confidence': 0.91, 'evidence': 'throw, explosion, and elimination markers'},
    {'time_seconds': 20, 'event_type': 'kill', 'label': 'Uncertain kill',
     'confidence': 0.4, 'evidence': 'ambiguous marker'}
]}, open(sys.argv[2], 'w', encoding='utf-8'))
""".strip(),
        encoding="utf-8",
    )
    config = tmp_path / "provider.json"
    config.write_text(
        json.dumps(
            {
                "provider_id": "fixture-vision",
                "model": "fixture-model",
                "command": [sys.executable, str(worker), "{request}", "{response}"],
                "sample_interval_seconds": 4,
                "maximum_frames": 48,
                "timeout_seconds": 30,
                "minimum_confidence": 0.65,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(vision, "_extract_frames", lambda *_args: frames)

    analysis = vision.analyze_with_vision_provider(
        tmp_path / "source.mp4",
        tmp_path / "analysis",
        config,
        "gameplay",
    )

    assert analysis.sampled_frames == 13
    assert len(analysis.contact_sheets) == 2
    assert [event.event_type for event in analysis.events] == ["grenade_kill"]
    assert analysis.timeline_events()[0].importance == 0.91
    assert analysis.timeline_events()[0].provenance == (
        "vision:fixture-vision:fixture-model",
    )
    assert analysis.timeline_events()[0].evidence == (
        "throw, explosion, and elimination markers",
    )
    assert (tmp_path / "analysis" / "request.json").is_file()
    assert (tmp_path / "analysis" / "analysis.json").is_file()
