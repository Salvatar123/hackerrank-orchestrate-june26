from __future__ import annotations
import json
import time
from pathlib import Path

import openai

from .schema import ClaimDecision, ScreenerResult
from .prompts import EVALUATOR_SYSTEM, EVALUATOR_TOOL, build_evaluator_user_prompt
from .screener import encode_image

_client: openai.OpenAI | None = None


def _get_client() -> openai.OpenAI:
    global _client
    if _client is None:
        _client = openai.OpenAI()
    return _client


def evaluate_claim(
    row: dict,
    screener_results: list[ScreenerResult],
    user_history: dict | None,
    evidence_reqs: list[dict],
    image_paths: list[Path],
    max_retries: int = 3,
    initial_delay: float = 2.0,
) -> ClaimDecision:
    """Stage 2: evaluate a full damage claim with all images and context."""
    client = _get_client()

    # Build multimodal user content: images first, then the structured text prompt
    user_content: list[dict] = []
    for ip in image_paths:
        if ip.exists():
            b64, media_type = encode_image(ip)
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{media_type};base64,{b64}",
                        "detail": "high",
                    },
                }
            )

    user_content.append(
        {
            "type": "text",
            "text": build_evaluator_user_prompt(row, screener_results, user_history, evidence_reqs),
        }
    )

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                max_tokens=1024,
                tools=[EVALUATOR_TOOL],
                tool_choice={"type": "function", "function": {"name": "evaluate_claim"}},
                messages=[
                    {"role": "system", "content": EVALUATOR_SYSTEM},
                    {"role": "user", "content": user_content},
                ],
            )
            break
        except (openai.RateLimitError, openai.APIStatusError) as e:
            if attempt == max_retries - 1:
                raise
            delay = initial_delay * (2**attempt)
            print(f"\n  [evaluator] Rate limit / API error, retrying in {delay:.0f}s: {e}")
            time.sleep(delay)

    tool_call = response.choices[0].message.tool_calls[0]
    result = json.loads(tool_call.function.arguments)

    return ClaimDecision(
        evidence_standard_met=result["evidence_standard_met"],
        evidence_standard_met_reason=result["evidence_standard_met_reason"],
        risk_flags=result.get("risk_flags", ["none"]),
        issue_type=result.get("issue_type", "unknown"),
        object_part=result.get("object_part", "unknown"),
        claim_status=result["claim_status"],
        claim_status_justification=result["claim_status_justification"],
        supporting_image_ids=result.get("supporting_image_ids", ["none"]),
        valid_image=result["valid_image"],
        severity=result.get("severity", "unknown"),
    )
