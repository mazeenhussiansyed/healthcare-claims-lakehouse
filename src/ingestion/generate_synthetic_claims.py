from __future__ import annotations

import argparse
import json
import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
from faker import Faker


STATES = ["CA", "TX", "FL", "NY", "IL", "PA", "OH", "GA"]

SPECIALTIES = [
    "Internal Medicine",
    "Cardiology",
    "Orthopedics",
    "Endocrinology",
    "Pulmonology",
    "Family Medicine",
]

DIAGNOSES = {
    "I10": "Essential hypertension",
    "E11.9": "Type 2 diabetes mellitus",
    "I50.9": "Heart failure",
    "J44.9": "Chronic obstructive pulmonary disease",
    "M54.5": "Low back pain",
    "Z00.0": "General medical examination",
}

PROCEDURES = ["99213", "99214", "93000", "71046", "80053", "36415"]

CLAIM_RULES = {
    "inpatient": (9000, 45000),
    "outpatient": (800, 9000),
    "professional": (120, 1800),
    "pharmacy": (20, 1200),
}


def choose_indices(frame: pd.DataFrame, rate: float, seed: int) -> list[int]:
    """Return a reproducible sample of row indexes."""
    count = max(1, round(len(frame) * rate))
    return frame.sample(n=count, random_state=seed).index.tolist()


def build_patients(fake: Faker, patient_count: int) -> pd.DataFrame:
    records = []

    for number in range(1, patient_count + 1):
        records.append(
            {
                "patient_id": f"PT{number:07d}",
                "date_of_birth": fake.date_of_birth(minimum_age=65, maximum_age=95),
                "gender": random.choice(["F", "M"]),
                "state": random.choice(STATES),
                "enrollment_start_date": fake.date_between(
                    start_date="-8y",
                    end_date="-1y",
                ),
                "risk_score": round(random.uniform(0.10, 2.50), 2),
            }
        )

    return pd.DataFrame(records)


def build_providers(fake: Faker, provider_count: int) -> pd.DataFrame:
    records = []

    for number in range(1, provider_count + 1):
        records.append(
            {
                "provider_id": f"PR{number:06d}",
                "provider_name": f"Dr. {fake.last_name()}",
                "specialty": random.choice(SPECIALTIES),
                "state": random.choice(STATES),
                "synthetic_npi": f"999{number:07d}",
            }
        )

    return pd.DataFrame(records)


def build_claims(
    patients: pd.DataFrame,
    providers: pd.DataFrame,
    claim_count: int,
    batch_id: str,
) -> pd.DataFrame:
    records = []
    today = date.today()
    start_date = today - timedelta(days=730)

    patient_ids = patients["patient_id"].tolist()
    provider_ids = providers["provider_id"].tolist()

    for number in range(1, claim_count + 1):
        claim_type = random.choices(
            population=["inpatient", "outpatient", "professional", "pharmacy"],
            weights=[0.10, 0.30, 0.45, 0.15],
            k=1,
        )[0]

        service_date = start_date + timedelta(
            days=random.randint(0, (today - start_date).days)
        )

        admission_date = None
        discharge_date = None

        if claim_type == "inpatient":
            admission_date = service_date
            discharge_date = service_date + timedelta(days=random.randint(1, 10))

        minimum, maximum = CLAIM_RULES[claim_type]
        billed_amount = round(random.uniform(minimum, maximum), 2)
        allowed_amount = round(billed_amount * random.uniform(0.55, 0.90), 2)

        status = random.choices(
            population=["paid", "denied", "pending"],
            weights=[0.86, 0.08, 0.06],
            k=1,
        )[0]

        paid_amount = (
            round(allowed_amount * random.uniform(0.85, 1.00), 2)
            if status == "paid"
            else 0.00
        )

        diagnosis_code = random.choice(list(DIAGNOSES.keys()))

        records.append(
            {
                "claim_id": f"CLM{number:09d}",
                "patient_id": random.choice(patient_ids),
                "provider_id": random.choice(provider_ids),
                "payer": "Medicare",
                "claim_type": claim_type,
                "service_date": service_date,
                "admission_date": admission_date,
                "discharge_date": discharge_date,
                "diagnosis_code": diagnosis_code,
                "diagnosis_description": DIAGNOSES[diagnosis_code],
                "procedure_code": random.choice(PROCEDURES),
                "billed_amount": billed_amount,
                "allowed_amount": allowed_amount,
                "paid_amount": paid_amount,
                "claim_status": status,
                "place_of_service": random.choice(
                    ["office", "hospital", "outpatient clinic", "pharmacy"]
                ),
                "source_file_name": f"medicare_claims_{batch_id}.csv",
            }
        )

    return pd.DataFrame(records)


def inject_quality_issues(
    claims: pd.DataFrame,
    seed: int,
) -> tuple[pd.DataFrame, dict]:
    """
    Deliberately simulates common raw-data issues.
    The Silver layer will later detect, quarantine, or correct them.
    """
    claims = claims.copy()

    missing_diagnosis = choose_indices(claims, 0.015, seed + 1)
    negative_payment = choose_indices(claims, 0.010, seed + 2)

    inpatient_indexes = claims.index[claims["claim_type"].eq("inpatient")].tolist()
    invalid_date_count = max(1, round(len(claims) * 0.005))

    invalid_discharge = (
        random.Random(seed + 3).sample(
            inpatient_indexes,
            min(invalid_date_count, len(inpatient_indexes)),
        )
        if inpatient_indexes
        else []
    )

    claims.loc[missing_diagnosis, ["diagnosis_code", "diagnosis_description"]] = None
    claims.loc[negative_payment, "paid_amount"] = -50.00
    claims.loc[invalid_discharge, "discharge_date"] = claims.loc[
        invalid_discharge,
        "service_date",
    ] - pd.Timedelta(days=1)

    # Choose duplicates only from clean rows, so duplicate creation does not
    # accidentally increase another quality-issue count.
    duplicate_count = max(1, round(len(claims) * 0.010))
    quality_issue_indexes = (
        set(missing_diagnosis)
        | set(negative_payment)
        | set(invalid_discharge)
    )

    eligible_duplicate_rows = claims.loc[
        ~claims.index.isin(quality_issue_indexes)
    ]

    if len(eligible_duplicate_rows) < duplicate_count:
        raise ValueError("Not enough clean rows available to create duplicates.")

    duplicate_rows = eligible_duplicate_rows.sample(
        n=duplicate_count,
        random_state=seed + 4,
    )

    claims = pd.concat([claims, duplicate_rows], ignore_index=True)

    issue_counts = {
        "missing_diagnosis_code": len(missing_diagnosis),
        "negative_paid_amount": len(negative_payment),
        "invalid_discharge_date": len(invalid_discharge),
        "duplicate_claim_rows": duplicate_count,
    }

    return claims, issue_counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate synthetic Medicare-style claims data."
    )

    parser.add_argument("--patients", type=int, default=1_000)
    parser.add_argument("--providers", type=int, default=150)
    parser.add_argument("--claims", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", default="data/raw")
    parser.add_argument(
        "--batch-id",
        default=f"synthetic_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
    )

    args = parser.parse_args()

    random.seed(args.seed)
    Faker.seed(args.seed)
    fake = Faker("en_US")

    batch_directory = Path(args.output_dir) / f"batch_id={args.batch_id}"

    if batch_directory.exists():
        raise FileExistsError(
            f"{batch_directory} already exists. "
            "Use a new --batch-id to preserve raw data."
        )

    batch_directory.mkdir(parents=True)

    patients = build_patients(fake, args.patients)
    providers = build_providers(fake, args.providers)
    claims = build_claims(patients, providers, args.claims, args.batch_id)
    claims, issue_counts = inject_quality_issues(claims, args.seed)

    ingestion_timestamp = datetime.now(UTC).isoformat()

    for frame in (patients, providers, claims):
        frame["ingestion_timestamp_utc"] = ingestion_timestamp

    patients.to_parquet(batch_directory / "patients.parquet", index=False)
    providers.to_parquet(batch_directory / "providers.parquet", index=False)
    claims.to_parquet(batch_directory / "claims.parquet", index=False)

    metadata = {
        "batch_id": args.batch_id,
        "seed": args.seed,
        "created_at_utc": ingestion_timestamp,
        "record_counts": {
            "patients": len(patients),
            "providers": len(providers),
            "claims_including_duplicates": len(claims),
        },
        "intentional_quality_issues": issue_counts,
    }

    (batch_directory / "metadata.json").write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )

    print(f"Raw batch created: {batch_directory}")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()