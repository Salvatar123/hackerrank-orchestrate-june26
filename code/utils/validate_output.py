"""Validate output.csv against the required schema before submission."""
from __future__ import annotations
import sys
from pathlib import Path
import pandas as pd

CODE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(CODE_DIR))

from agent.schema import (
    ALLOWED_RISK_FLAGS, ALLOWED_ISSUE_TYPES, ALLOWED_SEVERITIES,
    ALLOWED_CLAIM_STATUSES, OBJECT_PARTS, OUTPUT_COLUMNS,
)

ALL_OBJECT_PARTS = {p for parts in OBJECT_PARTS.values() for p in parts}


def validate(output_csv: Path) -> bool:
    df = pd.read_csv(output_csv)
    errors: list[str] = []

    # Column presence and order
    missing = [c for c in OUTPUT_COLUMNS if c not in df.columns]
    if missing:
        errors.append(f"Missing columns: {missing}")

    extra = [c for c in df.columns if c not in OUTPUT_COLUMNS]
    if extra:
        errors.append(f"Unexpected columns: {extra}")

    if errors:
        _report(errors)
        return False

    # Per-row validation
    for i, row in df.iterrows():
        prefix = f"Row {i} ({row.get('user_id', '?')})"

        # Boolean fields stored as "true"/"false"
        for bool_field in ("evidence_standard_met", "valid_image"):
            v = str(row[bool_field]).strip().lower()
            if v not in ("true", "false"):
                errors.append(f"{prefix}: {bool_field}={row[bool_field]!r} must be 'true' or 'false'")

        # Enum fields
        claim_status = str(row["claim_status"]).strip()
        if claim_status not in ALLOWED_CLAIM_STATUSES:
            errors.append(f"{prefix}: claim_status={claim_status!r} not in allowed values")

        issue_type = str(row["issue_type"]).strip()
        if issue_type not in ALLOWED_ISSUE_TYPES:
            errors.append(f"{prefix}: issue_type={issue_type!r} not in allowed values")

        severity = str(row["severity"]).strip()
        if severity not in ALLOWED_SEVERITIES:
            errors.append(f"{prefix}: severity={severity!r} not in allowed values")

        obj_part = str(row["object_part"]).strip()
        if obj_part not in ALL_OBJECT_PARTS:
            errors.append(f"{prefix}: object_part={obj_part!r} not in any allowed object_part list")

        # Risk flags (semicolon-separated)
        risk_str = str(row["risk_flags"]).strip()
        if risk_str != "none":
            for flag in risk_str.split(";"):
                flag = flag.strip()
                if flag not in ALLOWED_RISK_FLAGS:
                    errors.append(f"{prefix}: risk_flag={flag!r} not in allowed values")

        # No blank required fields
        for col in OUTPUT_COLUMNS:
            v = row[col]
            if pd.isna(v) or str(v).strip() == "":
                errors.append(f"{prefix}: column {col!r} is blank or NaN")

    if errors:
        _report(errors)
        return False

    print(f"✓ {output_csv} passed all checks ({len(df)} rows, {len(OUTPUT_COLUMNS)} columns).")
    return True


def _report(errors: list[str]) -> None:
    print(f"VALIDATION FAILED — {len(errors)} error(s):")
    for e in errors:
        print(f"  • {e}")


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent.parent.parent / "output.csv"
    ok = validate(path)
    sys.exit(0 if ok else 1)
