from __future__ import annotations
import csv
import hashlib
from pathlib import Path

import pandas as pd
from tqdm import tqdm

from .schema import ScreenerResult, OUTPUT_COLUMNS
from .screener import screen_image
from .evaluator import evaluate_claim

CHECKPOINT_EVERY = 5


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_data(repo_root: Path) -> tuple[pd.DataFrame, dict, list[dict]]:
    claims = pd.read_csv(repo_root / "dataset" / "claims.csv")
    user_history = pd.read_csv(repo_root / "dataset" / "user_history.csv")
    evidence_reqs = pd.read_csv(repo_root / "dataset" / "evidence_requirements.csv")
    history_map = {r["user_id"]: r.to_dict() for _, r in user_history.iterrows()}
    return claims, history_map, evidence_reqs.to_dict("records")


# ---------------------------------------------------------------------------
# Image path resolution
# ---------------------------------------------------------------------------

def resolve_image_paths(image_paths_str: str, repo_root: Path, images_dir: Path) -> list[Path]:
    """Resolve semicolon-separated image path string to absolute Path objects."""
    paths = []
    for p in image_paths_str.split(";"):
        p = p.strip()
        candidate = repo_root / p
        if candidate.exists():
            paths.append(candidate)
            continue
        # Fallback: try the provided images_dir with the relative path tail
        rel = Path(p)
        alt = images_dir / rel.parent.name / rel.name
        paths.append(alt if alt.exists() else candidate)
    return paths


# ---------------------------------------------------------------------------
# Image-level cache (keyed by file content hash so rename-safe)
# ---------------------------------------------------------------------------

def _cache_key(path: Path) -> str:
    if not path.exists():
        return f"missing:{path}"
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


# ---------------------------------------------------------------------------
# Single claim processing
# ---------------------------------------------------------------------------

def process_claim(
    row: dict,
    history_map: dict,
    evidence_reqs: list[dict],
    repo_root: Path,
    images_dir: Path,
    screener_cache: dict[str, ScreenerResult],
) -> dict:
    image_paths = resolve_image_paths(row["image_paths"], repo_root, images_dir)

    # Stage 1 — screen each image (cached by content hash)
    screener_results: list[ScreenerResult] = []
    for ip in image_paths:
        key = _cache_key(ip)
        if key not in screener_cache:
            screener_cache[key] = screen_image(ip, row["claim_object"])
        screener_results.append(screener_cache[key])

    # Filter evidence requirements to this claim_object
    obj = row["claim_object"]
    relevant_reqs = [r for r in evidence_reqs if r["claim_object"] in (obj, "all")]

    # Stage 2 — evaluate
    user_hist = history_map.get(row["user_id"])
    decision = evaluate_claim(row, screener_results, user_hist, relevant_reqs, image_paths)

    # Format multi-value fields for CSV
    flags = decision.risk_flags
    if not flags or flags == ["none"]:
        risk_str = "none"
    else:
        # deduplicate, keep "none" out if other flags present
        deduped = list(dict.fromkeys(f for f in flags if f != "none"))
        risk_str = ";".join(deduped) if deduped else "none"

    ids = decision.supporting_image_ids
    if not ids or ids == ["none"]:
        ids_str = "none"
    else:
        ids_str = ";".join(i for i in ids if i != "none") or "none"

    return {
        "user_id": row["user_id"],
        "image_paths": row["image_paths"],
        "user_claim": row["user_claim"],
        "claim_object": row["claim_object"],
        "evidence_standard_met": str(decision.evidence_standard_met).lower(),
        "evidence_standard_met_reason": decision.evidence_standard_met_reason,
        "risk_flags": risk_str,
        "issue_type": decision.issue_type,
        "object_part": decision.object_part,
        "claim_status": decision.claim_status,
        "claim_status_justification": decision.claim_status_justification,
        "supporting_image_ids": ids_str,
        "valid_image": str(decision.valid_image).lower(),
        "severity": decision.severity,
    }


def _fallback_row(row: dict, error: Exception) -> dict:
    return {
        "user_id": row.get("user_id", ""),
        "image_paths": row.get("image_paths", ""),
        "user_claim": row.get("user_claim", ""),
        "claim_object": row.get("claim_object", ""),
        "evidence_standard_met": "false",
        "evidence_standard_met_reason": f"Processing error: {error}",
        "risk_flags": "manual_review_required",
        "issue_type": "unknown",
        "object_part": "unknown",
        "claim_status": "not_enough_information",
        "claim_status_justification": "An error occurred during automated processing.",
        "supporting_image_ids": "none",
        "valid_image": "false",
        "severity": "unknown",
    }


# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------

def _write_csv(results: list[dict], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(results)


# ---------------------------------------------------------------------------
# Main run function
# ---------------------------------------------------------------------------

def run(
    claims_csv: Path,
    output_csv: Path,
    images_dir: Path,
    repo_root: Path,
    verbose: bool = True,
) -> list[dict]:
    claims, history_map, evidence_reqs = load_data(repo_root)

    results: list[dict] = []
    screener_cache: dict[str, ScreenerResult] = {}

    iterator = tqdm(claims.iterrows(), total=len(claims), desc="Processing claims") if verbose else claims.iterrows()

    for i, (_, row) in enumerate(iterator):
        row_dict = row.to_dict()
        try:
            result = process_claim(
                row_dict, history_map, evidence_reqs, repo_root, images_dir, screener_cache
            )
            results.append(result)
        except Exception as e:
            print(f"\n[runner] Error on row {i} ({row_dict.get('user_id')}): {e}")
            results.append(_fallback_row(row_dict, e))

        if (i + 1) % CHECKPOINT_EVERY == 0:
            _write_csv(results, output_csv)
            if verbose:
                print(f"\n  Checkpoint: {i + 1} rows saved to {output_csv}")

    _write_csv(results, output_csv)
    if verbose:
        print(f"\nDone. {len(results)} rows written to {output_csv}")

    return results
