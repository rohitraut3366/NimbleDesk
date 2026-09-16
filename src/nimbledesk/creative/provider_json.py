from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ProviderResponseError(ValueError):
    pass


def load_provider_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_non_finite_number,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProviderResponseError("provider response is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise ProviderResponseError("provider response must contain a JSON object")
    return payload


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProviderResponseError(f"provider response contains duplicate key: {key}")
        result[key] = value
    return result


def _reject_non_finite_number(value: str) -> None:
    raise ProviderResponseError(f"provider response contains non-finite number: {value}")
