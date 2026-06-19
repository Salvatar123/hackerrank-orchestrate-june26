from __future__ import annotations
import base64
import json
import time
from pathlib import Path

import openai

from .schema import ScreenerResult
from .prompts import SCREENER_SYSTEM, SCREENER_TOOL, build_screener_user_prompt

_client: openai.OpenAI | None = None


def _get_client() -> openai.OpenAI:
    global _client
    if _client is None:
        _client = openai.OpenAI()
    return _client


def encode_image(path: Path) -> tuple[str, str]:
    """Return (base64_data, media_type) for a local image file."""
    suffix = path.suffix.lower()
    media_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    media_type = media_map.get(suffix, "image/jpeg")
    with open(path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8"), media_type


def screen_image(
    image_path: Path,
    claim_object: str,
    max_retries: int = 3,
    initial_delay: float = 2.0,
) -> ScreenerResult:
    """Stage 1: examine a single image for quality and describe what is visible."""
    client = _get_client()
    image_id = image_path.stem

    if not image_path.exists():
        return ScreenerResult(
            image_id=image_id,
            image_path=str(image_path),
            quality_flags=["cropped_or_obstructed"],
            description="Image file not found.",
            is_usable=False,
        )

    b64, media_type = encode_image(image_path)

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                max_tokens=512,
                tools=[SCREENER_TOOL],
                tool_choice={"type": "function", "function": {"name": "screen_image"}},
                messages=[
                    {"role": "system", "content": SCREENER_SYSTEM},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{media_type};base64,{b64}",
                                    "detail": "high",
                                },
                            },
                            {
                                "type": "text",
                                "text": build_screener_user_prompt(claim_object),
                            },
                        ],
                    },
                ],
            )
            break
        except (openai.RateLimitError, openai.APIStatusError) as e:
            if attempt == max_retries - 1:
                raise
            delay = initial_delay * (2**attempt)
            print(f"\n  [screener] Rate limit / API error, retrying in {delay:.0f}s: {e}")
            time.sleep(delay)

    tool_call = response.choices[0].message.tool_calls[0]
    result = json.loads(tool_call.function.arguments)

    return ScreenerResult(
        image_id=image_id,
        image_path=str(image_path),
        quality_flags=result.get("quality_flags", ["none"]),
        description=result.get("description", ""),
        is_usable=result.get("is_usable", True),
    )
