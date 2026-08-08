from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clean Bronze healthcare claims data into a Silver layer."
    )
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--bronze-dir", default="data/bronze")
    parser.add_argument("--silver-dir", default="data/silver")
    args = parser.parse_args()

    bronze_batch_path = Path(args.bronze_dir) / f"batch_id={args.batch_id}"
    silver_batch_path = Path(args.silver_dir) / f"batch_id={args.batch_id}"

    if not bronze_batch_path.exists():
        raise FileNotFoundError(f"Bronze batch not found: {bronze_batch_path}")

    if silver_batch_path.exists():
        raise FileExistsError(
            f"Silver batch already exists: {silver_batch_path}. "
            "Use a new batch ID rather than overwriting it."
        )

    patients = pd.read_parquet(bronze_batch_path / "patients.parquet")
    providers = pd.read_parquet(bronze_batch_path / "providers.parquet")
    claims = pd.read_parquet(bronze_batch_path / "claims.parquet")

    input_claim_rows = len(claims)

    # 1. Remove duplicate business records, retaining the first occurrence.
    claims = claims.drop_duplicates(subset=["claim_id"], keep="first").copy()
    duplicate_claim_rows_removed = input_claim_rows - len(claims)

    # 2. Standardise field types and text.
    for column in ["service_date", "admission_date", "discharge_date"]:
        claims[column] = pd.to_datetime(claims[column], errors="coerce")

    claims["claim_type"] = claims["claim_type"].astype("string").str.strip().str.lower()
    claims["claim_status"] = (
        claims["claim_status"].astype("string").str.strip().str.lower()
    )

    # 3. Identify records that are not safe for the analytics layer.
    negative_payment_mask = claims["paid_amount"] < 0
    invalid_discharge_mask = (
        claims["discharge_date"].notna()
        & (claims["discharge_date"] < claims["service_date"])
    )
    invalid_claim_mask = negative_payment_mask | invalid_discharge_mask

    quarantine_claims = claims.loc[invalid_claim_mask].copy()

    if not quarantine_claims.empty:
        quarantine_claims["quarantine_reason"] = [
            ";".join(
                reason
                for reason in [
                    "negative_paid_amount" if is_negative else "",
                    "invalid_discharge_date" if is_invalid_date else "",
                ]
                if reason
            )
            for is_negative, is_invalid_date in zip(
                negative_payment_mask.loc[invalid_claim_mask],
                invalid_discharge_mask.loc[invalid_claim_mask],
                strict=True,
            )
        ]

    silver_claims = claims.loc[~invalid_claim_mask].copy()

    # 4. Impute only safe missing values in records that passed validation.
    missing_diagnosis_mask = (
        silver_claims["diagnosis_code"].isna()
        | silver_claims["diagnosis_code"].astype("string").str.strip().eq("")
    )
    missing_diagnosis_imputed = int(missing_diagnosis_mask.sum())

    silver_claims.loc[missing_diagnosis_mask, "diagnosis_code"] = "UNKNOWN"
    silver_claims.loc[
        missing_diagnosis_mask, "diagnosis_description"
    ] = "Unknown diagnosis"

    # 5. Add analytics-ready derived fields.
    silver_claims["service_year"] = silver_claims["service_date"].dt.year
    silver_claims["service_month"] = silver_claims["service_date"].dt.month

    silver_claims["length_of_stay_days"] = pd.Series(
        pd.NA,
        index=silver_claims.index,
        dtype="Int64",
    )

    inpatient_mask = (
        silver_claims["claim_type"].eq("inpatient")
        & silver_claims["admission_date"].notna()
        & silver_claims["discharge_date"].notna()
    )

    silver_claims.loc[inpatient_mask, "length_of_stay_days"] = (
        silver_claims.loc[inpatient_mask, "discharge_date"]
        - silver_claims.loc[inpatient_mask, "admission_date"]
    ).dt.days

    silver_processed_at = datetime.now(UTC).isoformat()

    for frame in (patients, providers, silver_claims):
        frame["silver_processed_at_utc"] = silver_processed_at

    # 6. Enforce referential integrity before publishing Silver claims.
    unknown_patient_count = int(
        (~silver_claims["patient_id"].isin(patients["patient_id"])).sum()
    )
    unknown_provider_count = int(
        (~silver_claims["provider_id"].isin(providers["provider_id"])).sum()
    )

    if unknown_patient_count or unknown_provider_count:
        raise ValueError(
            "Referential-integrity failure: "
            f"{unknown_patient_count} unknown patients and "
            f"{unknown_provider_count} unknown providers."
        )

    # 7. Publish curated tables and quarantined records.
    silver_batch_path.mkdir(parents=True)

    patients.to_parquet(silver_batch_path / "patients.parquet", index=False)
    providers.to_parquet(silver_batch_path / "providers.parquet", index=False)
    silver_claims.to_parquet(silver_batch_path / "claims.parquet", index=False)
    quarantine_claims.to_parquet(
        silver_batch_path / "quarantine_claims.parquet",
        index=False,
    )

    audit = {
        "batch_id": args.batch_id,
        "silver_processed_at_utc": silver_processed_at,
        "input_claim_rows": input_claim_rows,
        "duplicate_claim_rows_removed": duplicate_claim_rows_removed,
        "invalid_claim_rows_quarantined": len(quarantine_claims),
        "missing_diagnosis_imputed": missing_diagnosis_imputed,
        "silver_claim_rows_published": len(silver_claims),
        "unknown_patient_count": unknown_patient_count,
        "unknown_provider_count": unknown_provider_count,
    }

    (silver_batch_path / "silver_transformation_audit.json").write_text(
        json.dumps(audit, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(audit, indent=2))
    print(f"\nSilver batch created: {silver_batch_path}")


if __name__ == "__main__":
    main()
    