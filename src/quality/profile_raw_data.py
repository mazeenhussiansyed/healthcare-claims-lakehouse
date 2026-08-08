from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Profile a raw synthetic healthcare claims batch."
    )
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--raw-dir", default="data/raw")
    parser.add_argument("--output-dir", default="data/quality")
    args = parser.parse_args()

    raw_batch_path = Path(args.raw_dir) / f"batch_id={args.batch_id}"
    if not raw_batch_path.exists():
        raise FileNotFoundError(f"Raw batch not found: {raw_batch_path}")

    patients = pd.read_parquet(raw_batch_path / "patients.parquet")
    providers = pd.read_parquet(raw_batch_path / "providers.parquet")
    claims = pd.read_parquet(raw_batch_path / "claims.parquet")
    metadata = json.loads((raw_batch_path / "metadata.json").read_text(encoding="utf-8"))

    service_dates = pd.to_datetime(claims["service_date"], errors="coerce")
    discharge_dates = pd.to_datetime(claims["discharge_date"], errors="coerce")

    observed_issues = {
        "missing_diagnosis_code": int(claims["diagnosis_code"].isna().sum()),
        "negative_paid_amount": int((claims["paid_amount"] < 0).sum()),
        "invalid_discharge_date": int(
            ((discharge_dates.notna()) & (discharge_dates < service_dates)).sum()
        ),
        "duplicate_claim_rows": int(claims.duplicated().sum()),
        "unknown_patient_id": int((~claims["patient_id"].isin(patients["patient_id"])).sum()),
        "unknown_provider_id": int(
            (~claims["provider_id"].isin(providers["provider_id"])).sum()
        ),
    }

    expected_issues = metadata["intentional_quality_issues"]
    issue_reconciliation = {
        issue_name: {
            "expected": expected_issues[issue_name],
            "observed": observed_issues[issue_name],
            "matches_expected": expected_issues[issue_name] == observed_issues[issue_name],
        }
        for issue_name in expected_issues
    }

    report = {
        "batch_id": args.batch_id,
        "profiled_at_utc": datetime.now(UTC).isoformat(),
        "record_counts": {
            "patients": len(patients),
            "providers": len(providers),
            "claims": len(claims),
        },
        "observed_quality_issues": observed_issues,
        "expected_issue_reconciliation": issue_reconciliation,
        "quality_gate_passed": all(
            result["matches_expected"] for result in issue_reconciliation.values()
        )
        and observed_issues["unknown_patient_id"] == 0
        and observed_issues["unknown_provider_id"] == 0,
    }

    output_path = (
        Path(args.output_dir)
        / f"batch_id={args.batch_id}"
        / "raw_claims_profile.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(json.dumps(report, indent=2))
    print(f"\nQuality profile saved: {output_path}")


if __name__ == "__main__":
    main()
    