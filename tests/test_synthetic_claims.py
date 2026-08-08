import random

import pandas as pd
from faker import Faker

from src.ingestion.generate_synthetic_claims import (
    build_claims,
    build_patients,
    build_providers,
    inject_quality_issues,
)


def create_test_data():
    random.seed(42)
    Faker.seed(42)

    fake = Faker("en_US")
    patients = build_patients(fake, patient_count=100)
    providers = build_providers(fake, provider_count=20)
    claims = build_claims(
        patients,
        providers,
        claim_count=1_000,
        batch_id="test_batch",
    )

    return patients, providers, claims


def test_injected_quality_issue_counts_match_metadata():
    _, _, claims = create_test_data()

    claims_with_issues, issue_counts = inject_quality_issues(claims, seed=42)

    service_dates = pd.to_datetime(
        claims_with_issues["service_date"],
        errors="coerce",
    )
    discharge_dates = pd.to_datetime(
        claims_with_issues["discharge_date"],
        errors="coerce",
    )

    observed_missing_diagnosis = int(
        claims_with_issues["diagnosis_code"].isna().sum()
    )
    observed_negative_payments = int(
        (claims_with_issues["paid_amount"] < 0).sum()
    )
    observed_invalid_discharge_dates = int(
        (
            discharge_dates.notna()
            & (discharge_dates < service_dates)
        ).sum()
    )
    observed_duplicate_rows = int(claims_with_issues.duplicated().sum())

    assert observed_missing_diagnosis == issue_counts["missing_diagnosis_code"]
    assert observed_negative_payments == issue_counts["negative_paid_amount"]
    assert (
        observed_invalid_discharge_dates
        == issue_counts["invalid_discharge_date"]
    )
    assert observed_duplicate_rows == issue_counts["duplicate_claim_rows"]


def test_claims_reference_valid_patients_and_providers():
    patients, providers, claims = create_test_data()

    assert set(claims["patient_id"]).issubset(set(patients["patient_id"]))
    assert set(claims["provider_id"]).issubset(set(providers["provider_id"]))
    