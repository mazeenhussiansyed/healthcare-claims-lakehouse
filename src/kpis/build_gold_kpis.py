from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build analytics-ready Gold KPI tables from Silver healthcare data."
    )
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--silver-dir", default="data/silver")
    parser.add_argument("--gold-dir", default="data/gold")
    args = parser.parse_args()

    silver_batch_path = Path(args.silver_dir) / f"batch_id={args.batch_id}"
    gold_batch_path = Path(args.gold_dir) / f"batch_id={args.batch_id}"

    if not silver_batch_path.exists():
        raise FileNotFoundError(f"Silver batch not found: {silver_batch_path}")

    if gold_batch_path.exists():
        raise FileExistsError(
            f"Gold batch already exists: {gold_batch_path}. "
            "Use a new batch ID rather than overwriting it."
        )

    patients = pd.read_parquet(silver_batch_path / "patients.parquet")
    providers = pd.read_parquet(silver_batch_path / "providers.parquet")
    claims = pd.read_parquet(silver_batch_path / "claims.parquet")

    claims["service_date"] = pd.to_datetime(claims["service_date"], errors="coerce")
    claims["paid_amount"] = pd.to_numeric(claims["paid_amount"], errors="coerce")
    claims["allowed_amount"] = pd.to_numeric(claims["allowed_amount"], errors="coerce")
    claims["billed_amount"] = pd.to_numeric(claims["billed_amount"], errors="coerce")

    claims["is_inpatient"] = claims["claim_type"].eq("inpatient")
    claims["service_year"] = claims["service_date"].dt.year
    claims["service_month"] = claims["service_date"].dt.month

    # Monthly healthcare cost and utilisation KPI table.
    monthly_cost_utilization = (
        claims.groupby(["service_year", "service_month"], dropna=False)
        .agg(
            claim_count=("claim_id", "nunique"),
            unique_patient_count=("patient_id", "nunique"),
            unique_provider_count=("provider_id", "nunique"),
            total_billed_amount=("billed_amount", "sum"),
            total_allowed_amount=("allowed_amount", "sum"),
            total_paid_amount=("paid_amount", "sum"),
            average_paid_amount=("paid_amount", "mean"),
            inpatient_claim_count=("is_inpatient", "sum"),
            average_length_of_stay_days=("length_of_stay_days", "mean"),
        )
        .reset_index()
        .sort_values(["service_year", "service_month"])
    )

    monthly_cost_utilization["paid_to_billed_ratio"] = (
        monthly_cost_utilization["total_paid_amount"]
        / monthly_cost_utilization["total_billed_amount"]
    ).round(4)

    # Provider performance KPI table.
    provider_performance = (
        claims.groupby("provider_id")
        .agg(
            claim_count=("claim_id", "nunique"),
            unique_patient_count=("patient_id", "nunique"),
            total_paid_amount=("paid_amount", "sum"),
            average_paid_amount=("paid_amount", "mean"),
            inpatient_claim_count=("is_inpatient", "sum"),
        )
        .reset_index()
        .merge(
            providers[["provider_id", "provider_name", "specialty", "state"]],
            on="provider_id",
            how="left",
            validate="one_to_one",
        )
    )

    provider_performance["paid_amount_per_claim"] = (
        provider_performance["total_paid_amount"]
        / provider_performance["claim_count"]
    ).round(2)

    provider_performance = provider_performance.sort_values(
        "total_paid_amount",
        ascending=False,
    )

    # Diagnosis cost and utilisation KPI table.
    diagnosis_cost_utilization = (
        claims.groupby(
            ["diagnosis_code", "diagnosis_description"],
            dropna=False,
        )
        .agg(
            claim_count=("claim_id", "nunique"),
            unique_patient_count=("patient_id", "nunique"),
            total_paid_amount=("paid_amount", "sum"),
            average_paid_amount=("paid_amount", "mean"),
            inpatient_claim_count=("is_inpatient", "sum"),
        )
        .reset_index()
        .sort_values("total_paid_amount", ascending=False)
    )

    # Patient utilisation and high-cost cohort table.
    patient_utilization = (
        claims.groupby("patient_id")
        .agg(
            claim_count=("claim_id", "nunique"),
            total_paid_amount=("paid_amount", "sum"),
            average_paid_amount=("paid_amount", "mean"),
            inpatient_claim_count=("is_inpatient", "sum"),
            latest_service_date=("service_date", "max"),
        )
        .reset_index()
        .merge(
            patients[
                ["patient_id", "date_of_birth", "gender", "state", "risk_score"]
            ],
            on="patient_id",
            how="left",
            validate="one_to_one",
        )
    )

    patient_utilization["date_of_birth"] = pd.to_datetime(
        patient_utilization["date_of_birth"],
        errors="coerce",
    )

    today = pd.Timestamp.now(tz=None).normalize()
    patient_utilization["age_years"] = (
        (today - patient_utilization["date_of_birth"]).dt.days // 365
    ).astype("Int64")

    high_cost_threshold = patient_utilization["total_paid_amount"].quantile(0.90)
    patient_utilization["high_cost_flag"] = (
        patient_utilization["total_paid_amount"] >= high_cost_threshold
    )

    patient_utilization = patient_utilization.sort_values(
        "total_paid_amount",
        ascending=False,
    )

    gold_published_at = datetime.now(UTC).isoformat()

    for frame in (
        monthly_cost_utilization,
        provider_performance,
        diagnosis_cost_utilization,
        patient_utilization,
    ):
        frame["gold_published_at_utc"] = gold_published_at

    gold_batch_path.mkdir(parents=True)

    monthly_cost_utilization.to_parquet(
        gold_batch_path / "monthly_cost_utilization.parquet",
        index=False,
    )
    provider_performance.to_parquet(
        gold_batch_path / "provider_performance.parquet",
        index=False,
    )
    diagnosis_cost_utilization.to_parquet(
        gold_batch_path / "diagnosis_cost_utilization.parquet",
        index=False,
    )
    patient_utilization.to_parquet(
        gold_batch_path / "patient_utilization.parquet",
        index=False,
    )

    audit = {
        "batch_id": args.batch_id,
        "gold_published_at_utc": gold_published_at,
        "input_silver_claim_rows": len(claims),
        "monthly_kpi_rows": len(monthly_cost_utilization),
        "provider_kpi_rows": len(provider_performance),
        "diagnosis_kpi_rows": len(diagnosis_cost_utilization),
        "patient_kpi_rows": len(patient_utilization),
        "total_paid_amount": round(float(claims["paid_amount"].sum()), 2),
        "high_cost_patient_threshold": round(float(high_cost_threshold), 2),
    }

    (gold_batch_path / "gold_publish_audit.json").write_text(
        json.dumps(audit, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(audit, indent=2))
    print(f"\nGold KPI batch created: {gold_batch_path}")


if __name__ == "__main__":
    main()
    