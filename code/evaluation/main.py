"""Evaluation entry point: runs the agent on sample_claims.csv and scores against expected outputs."""
from __future__ import annotations
import argparse
import csv
import sys
from pathlib import Path

CODE_DIR = Path(__file__).parent.parent
REPO_ROOT = CODE_DIR.parent
sys.path.insert(0, str(CODE_DIR))

from dotenv import load_dotenv
load_dotenv(REPO_ROOT / ".env")

import pandas as pd
from agent.runner import run as run_agent

# Fields where exact match is meaningful
EXACT_MATCH_FIELDS = [
    "evidence_standard_met",
    "claim_status",
    "issue_type",
    "object_part",
    "severity",
    "valid_image",
]

# Fields scored as set F1 (semicolon-separated multi-value)
SET_MATCH_FIELDS = ["risk_flags", "supporting_image_ids"]


def set_f1(pred: str, gold: str) -> float:
    p_set = set(pred.split(";")) if pred and pred != "none" else set()
    g_set = set(gold.split(";")) if gold and gold != "none" else set()
    if not p_set and not g_set:
        return 1.0
    if not p_set or not g_set:
        return 0.0
    tp = len(p_set & g_set)
    precision = tp / len(p_set)
    recall = tp / len(g_set)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def evaluate(pred_rows: list[dict], gold_df: pd.DataFrame) -> dict:
    gold_rows = gold_df.to_dict("records")
    assert len(pred_rows) == len(gold_rows), "Row count mismatch between predictions and gold"

    scores: dict[str, list] = {f: [] for f in EXACT_MATCH_FIELDS + SET_MATCH_FIELDS}

    mismatches = []
    for i, (pred, gold) in enumerate(zip(pred_rows, gold_rows)):
        row_id = gold.get("user_id", i)
        row_wrong = {}

        for field in EXACT_MATCH_FIELDS:
            p = str(pred.get(field, "")).strip().lower()
            g = str(gold.get(field, "")).strip().lower()
            match = int(p == g)
            scores[field].append(match)
            if not match:
                row_wrong[field] = {"pred": p, "gold": g}

        for field in SET_MATCH_FIELDS:
            f1 = set_f1(str(pred.get(field, "")), str(gold.get(field, "")))
            scores[field].append(f1)
            if f1 < 1.0:
                row_wrong[field] = {
                    "pred": pred.get(field, ""),
                    "gold": gold.get(field, ""),
                    "f1": round(f1, 2),
                }

        if row_wrong:
            mismatches.append({"user_id": row_id, "fields": row_wrong})

    summary = {f: round(sum(v) / len(v), 3) for f, v in scores.items()}
    return {"per_field": summary, "mismatches": mismatches}


def write_report(results: dict, output_path: Path, pred_path: Path) -> None:
    pf = results["per_field"]
    mismatches = results["mismatches"]

    lines = [
        "# Evaluation Report\n",
        "## Per-Field Accuracy\n",
        "| Field | Score |",
        "|---|---|",
    ]
    for field, score in pf.items():
        metric = "accuracy" if field in EXACT_MATCH_FIELDS else "F1"
        lines.append(f"| {field} | {score} ({metric}) |")

    overall = round(sum(pf[f] for f in EXACT_MATCH_FIELDS) / len(EXACT_MATCH_FIELDS), 3)
    lines += [
        f"\n**Overall exact-match accuracy (core fields):** {overall}\n",
        f"\n## Mismatches ({len(mismatches)} rows with at least one wrong field)\n",
    ]

    for m in mismatches:
        lines.append(f"### {m['user_id']}")
        for field, info in m["fields"].items():
            if "f1" in info:
                lines.append(f"- **{field}**: pred=`{info['pred']}` gold=`{info['gold']}` F1={info['f1']}")
            else:
                lines.append(f"- **{field}**: pred=`{info['pred']}` gold=`{info['gold']}`")
        lines.append("")

    lines += [
        "## Operational Analysis\n",
        "| Metric | Value |",
        "|---|---|",
        "| Sample rows processed | 20 |",
        "| Model | claude-sonnet-4-6 |",
        "| Stage 1 calls (screener) | ~1 per image |",
        "| Stage 2 calls (evaluator) | 1 per claim row |",
        "| Approx input tokens | ~1500–2500 per claim (images + text) |",
        "| Approx output tokens | ~200–400 per claim |",
        "| Est. cost per sample run | ~$0.20–$0.60 |",
        "| Est. cost per test run (46 rows) | ~$0.50–$1.50 |",
        "| Pricing assumption | claude-sonnet-4-6: $3/MTok input, $15/MTok output |",
        "| Rate limit strategy | Exponential backoff (max 3 retries, 2s initial delay) |",
        "| Caching strategy | Stage 1 results cached by image content hash |",
        "| Checkpointing | Output CSV saved every 5 rows |",
        f"\nPredictions written to: `{pred_path}`\n",
    ]

    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report written to {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate agent on sample_claims.csv")
    parser.add_argument("--sample", type=Path, default=REPO_ROOT / "dataset" / "sample_claims.csv")
    parser.add_argument("--images", type=Path, default=REPO_ROOT / "dataset" / "images" / "sample")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "evaluation" / "eval_output.csv")
    parser.add_argument("--report", type=Path, default=REPO_ROOT / "evaluation" / "evaluation_report.md")
    args = parser.parse_args()

    # Load sample CSV — input columns only (runner expects just input fields)
    gold_df = pd.read_csv(args.sample)
    input_cols = ["user_id", "image_paths", "user_claim", "claim_object"]
    input_df = gold_df[input_cols].copy()

    # Write a temporary claims CSV for the runner
    tmp_claims = args.output.parent / "_tmp_sample_claims.csv"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    input_df.to_csv(tmp_claims, index=False)

    print(f"Running agent on {len(input_df)} sample rows...")
    pred_rows = run_agent(
        claims_csv=tmp_claims,
        output_csv=args.output,
        images_dir=args.images,
        repo_root=REPO_ROOT,
    )

    tmp_claims.unlink(missing_ok=True)

    print("Scoring predictions...")
    results = evaluate(pred_rows, gold_df)

    pf = results["per_field"]
    print("\n--- Per-field scores ---")
    for field, score in pf.items():
        print(f"  {field}: {score}")

    write_report(results, args.report, args.output)


if __name__ == "__main__":
    main()
