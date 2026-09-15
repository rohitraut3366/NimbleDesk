from __future__ import annotations

import argparse
import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from nimbledesk.creative.vision import (
    VisionProviderResponse,
    VisionProviderUsage,
    VisionRequest,
    measure_sheet_usage,
)

MAXIMUM_IMAGE_BYTES = 24 * 1024 * 1024
MAXIMUM_HTTP_RESPONSE_BYTES = 2 * 1024 * 1024


class VisionHttpError(RuntimeError):
    pass


def analyze_request(
    request: VisionRequest,
    endpoint: str,
    model: str,
    api_key: str | None,
    timeout_seconds: float,
) -> VisionProviderResponse:
    if not endpoint.startswith(("http://127.0.0.1", "http://localhost", "https://")):
        raise VisionHttpError("vision endpoint must use HTTPS or local loopback HTTP")
    content: list[dict[str, Any]] = [{"type": "text", "text": _prompt(request)}]
    total_bytes = 0
    for sheet in request.sheets:
        image_bytes = sheet.path.read_bytes()
        total_bytes += len(image_bytes)
        if total_bytes > MAXIMUM_IMAGE_BYTES:
            raise VisionHttpError("vision contact sheets exceeded the 24-megabyte request limit")
        encoded = base64.b64encode(image_bytes).decode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{encoded}", "detail": "low"},
            }
        )
    payload = json.dumps(
        {
            "model": model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": content}],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    headers = {"content-type": "application/json"}
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"
    http_request = urllib.request.Request(endpoint, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(http_request, timeout=timeout_seconds) as response:
            body = response.read(MAXIMUM_HTTP_RESPONSE_BYTES + 1)
    except (OSError, urllib.error.HTTPError, urllib.error.URLError) as error:
        raise VisionHttpError(f"vision endpoint request failed: {error}") from error
    if len(body) > MAXIMUM_HTTP_RESPONSE_BYTES:
        raise VisionHttpError("vision endpoint response exceeded two megabytes")
    try:
        envelope = json.loads(body)
        message = envelope["choices"][0]["message"]["content"]
        if isinstance(message, list):
            message = "".join(
                str(part.get("text", "")) for part in message if isinstance(part, dict)
            )
        decoded = _decode_json_content(str(message))
        response = VisionProviderResponse.model_validate(decoded)
        provider_usage = envelope.get("usage", {})
        measured = measure_sheet_usage(request.sheets)
        usage = VisionProviderUsage(
            image_bytes=measured.image_bytes,
            estimated_512px_tiles=measured.estimated_512px_tiles,
            estimated_image_tokens=measured.estimated_image_tokens,
            provider_input_tokens=_optional_token_count(
                provider_usage, "prompt_tokens", "input_tokens"
            ),
            provider_output_tokens=_optional_token_count(
                provider_usage, "completion_tokens", "output_tokens"
            ),
        )
        return response.model_copy(update={"usage": usage})
    except Exception as error:
        raise VisionHttpError("vision endpoint returned an invalid structured response") from error


def _prompt(request: VisionRequest) -> str:
    events = ", ".join(request.requested_events)
    timestamps = "; ".join(
        f"sheet {index}: {', '.join(f'{value:g}s' for value in sheet.timestamps_seconds)}"
        for index, sheet in enumerate(request.sheets, start=1)
    )
    return (
        f"Analyze these timestamped contact sheets as {request.content_kind} content. "
        f"Detect only well-supported events from: {events}. The timestamp labels correspond to "
        f"frames in reading order. Timestamps are {timestamps}. Return one JSON object with an "
        '"events" array. Every event must contain time_seconds, event_type, label, confidence '
        "from 0 to 1, and concise visible evidence. Use an empty array when evidence is weak. "
        "Do not infer a kill only from motion or excitement; require a kill feed, game state, "
        "telemetry visible in frame, or a clearly linked action and elimination result."
    )


def _decode_json_content(content: str) -> object:
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines)
    return json.loads(stripped)


def _optional_token_count(usage: object, *names: str) -> int | None:
    if not isinstance(usage, dict):
        return None
    for name in names:
        value = usage.get(name)
        if isinstance(value, int) and value >= 0:
            return value
    return None


def parse_args(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run bounded semantic vision through an OpenAI-compatible endpoint"
    )
    parser.add_argument("request", type=Path)
    parser.add_argument("response", type=Path)
    parser.add_argument(
        "--endpoint",
        default=os.getenv("NIMBLEDESK_VISION_ENDPOINT"),
        help="HTTPS chat-completions URL, or local loopback HTTP URL",
    )
    parser.add_argument("--model", default=os.getenv("NIMBLEDESK_VISION_MODEL"))
    parser.add_argument("--api-key", default=os.getenv("NIMBLEDESK_VISION_API_KEY"))
    parser.add_argument("--timeout-seconds", type=float, default=300)
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> None:
    parsed = parse_args(arguments)
    if not parsed.endpoint or not parsed.model:
        raise SystemExit("vision endpoint and model are required")
    request = VisionRequest.model_validate_json(parsed.request.read_text(encoding="utf-8"))
    result = analyze_request(
        request,
        parsed.endpoint,
        parsed.model,
        parsed.api_key,
        parsed.timeout_seconds,
    )
    parsed.response.parent.mkdir(parents=True, exist_ok=True)
    parsed.response.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
