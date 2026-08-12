from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st


st.set_page_config(
    page_title="Healthcare Claims Intelligence",
    page_icon="🏥",
    layout="wide",
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLD_ROOT = PROJECT_ROOT / "data" / "gold"
QUALITY_ROOT = PROJECT_ROOT / "data" / "quality"

st.markdown(
    """
    <style>
        .block-container {padding-top: 2rem; padding-bottom: 2rem;}
        [data-testid="stMetricValue"] {font-size: 1.7rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


def currency(value: float) -> str:
    """Format a number as US dollars."""
    return f"${value:,.0f}"


def list_batch_ids() -> list[str]:
    """Return available Gold batch IDs, newest first."""
    if not GOLD_ROOT.exists():
        return []

    return sorted(
        [
            directory.name.split("=", maxsplit=1)[1]
            for directory in GOLD_ROOT.glob("batch_id=*")
            if directory.is_dir()
        ],
        reverse=True,
    )


@st.cache_data
def load_gold_data(batch_id: str) -> tuple[pd.DataFrame, ...]:
    """Load the four Gold datasets for one pipeline batch."""
    batch_directory = GOLD_ROOT / f"batch_id={batch_id}"

    monthly = pd.read_parquet(batch_directory / "monthly_cost_utilization.parquet")
    diagnosis = pd.read_parquet(
        batch_directory / "diagnosis_cost_utilization.parquet"
    )
    patients = pd.read_parquet(batch_directory / "patient_utilization.parquet")
    providers = pd.read_parquet(batch_directory / "provider_performance.parquet")

    monthly["period"] = pd.to_datetime(
        monthly["service_year"].astype(str)
        + "-"
        + monthly["service_month"].astype(str).str.zfill(2)
        + "-01"
    )
    monthly = monthly.sort_values("period")

    return monthly, diagnosis, patients, providers


def load_quality_profile(batch_id: str) -> dict:
    """Load the raw-data quality profile if it exists."""
    profile_path = (
        QUALITY_ROOT
        / f"batch_id={batch_id}"
        / "raw_claims_profile.json"
    )

    if not profile_path.exists():
        return {}

    return json.loads(profile_path.read_text(encoding="utf-8"))


def main() -> None:
    batch_ids = list_batch_ids()

    st.title("🏥 Healthcare Claims Intelligence Dashboard")
    st.caption(
        "Interactive analysis of synthetic Medicare-style claims data "
        "produced by the Medallion pipeline."
    )

    if not batch_ids:
        st.error(
            "No Gold KPI batch was found. Run the pipeline before opening "
            "the dashboard."
        )
        st.stop()

    with st.sidebar:
        st.header("Dashboard Controls")
        selected_batch = st.selectbox(
            "Pipeline batch",
            batch_ids,
            help="Choose a completed Gold-layer pipeline batch.",
        )
        st.divider()
        st.caption("Synthetic data only — no PHI or real patient data.")

    try:
        monthly, diagnosis, patients, providers = load_gold_data(selected_batch)
    except FileNotFoundError as error:
        st.error(f"A required Gold file is missing: {error}")
        st.stop()

    quality_profile = load_quality_profile(selected_batch)

    total_paid = monthly["total_paid_amount"].sum()
    total_billed = monthly["total_billed_amount"].sum()
    total_claims = int(monthly["claim_count"].sum())
    total_patients = int(patients["patient_id"].nunique())
    total_providers = int(providers["provider_id"].nunique())
    high_cost_patients = int(patients["high_cost_flag"].astype(bool).sum())
    paid_to_billed_ratio = total_paid / total_billed if total_billed else 0

    st.caption(f"Selected batch: `{selected_batch}`")

    tab_overview, tab_providers, tab_risk_quality = st.tabs(
        ["Executive Overview", "Providers & Diagnoses", "Patient Risk & Quality"]
    )

    with tab_overview:
        metric_1, metric_2, metric_3, metric_4 = st.columns(4)
        metric_1.metric("Total Paid Claims", currency(total_paid))
        metric_2.metric("Total Claims", f"{total_claims:,}")
        metric_3.metric("Unique Patients", f"{total_patients:,}")
        metric_4.metric("Paid-to-Billed Ratio", f"{paid_to_billed_ratio:.1%}")

        st.subheader("Monthly Claims Cost Trend")

        trend = monthly.melt(
            id_vars="period",
            value_vars=["total_billed_amount", "total_paid_amount"],
            var_name="metric",
            value_name="amount",
        )
        trend["metric"] = trend["metric"].replace(
            {
                "total_billed_amount": "Billed Amount",
                "total_paid_amount": "Paid Amount",
            }
        )

        trend_chart = px.line(
            trend,
            x="period",
            y="amount",
            color="metric",
            markers=True,
            labels={
                "period": "Service Month",
                "amount": "Amount (USD)",
                "metric": "Metric",
            },
            color_discrete_map={
                "Billed Amount": "#94a3b8",
                "Paid Amount": "#0f766e",
            },
        )
        trend_chart.update_layout(legend_title_text="")
        st.plotly_chart(trend_chart, width="stretch")

        left, right = st.columns(2)

        with left:
            st.subheader("Monthly Claim Volume")
            volume_chart = px.bar(
                monthly,
                x="period",
                y="claim_count",
                labels={
                    "period": "Service Month",
                    "claim_count": "Claims",
                },
                color_discrete_sequence=["#2563eb"],
            )
            st.plotly_chart(volume_chart, width="stretch")

        with right:
            st.subheader("Operational Snapshot")
            st.metric("Unique Providers", f"{total_providers:,}")
            st.metric("High-Cost Patients", f"{high_cost_patients:,}")
            st.metric(
                "Average Paid Amount per Claim",
                currency(total_paid / total_claims),
            )

            top_diagnosis = diagnosis.loc[
                diagnosis["total_paid_amount"].idxmax()
            ]
            st.info(
                f"Highest-cost diagnosis: **{top_diagnosis['diagnosis_description']}** "
                f"at **{currency(top_diagnosis['total_paid_amount'])}**."
            )

    with tab_providers:
        specialty_options = ["All"] + sorted(
            providers["specialty"].dropna().unique().tolist()
        )
        selected_specialty = st.selectbox(
            "Filter provider specialty",
            specialty_options,
        )

        filtered_providers = providers.copy()
        if selected_specialty != "All":
            filtered_providers = filtered_providers[
                filtered_providers["specialty"] == selected_specialty
            ]

        top_providers = (
            filtered_providers.sort_values(
                "total_paid_amount",
                ascending=False,
            )
            .head(10)
            .sort_values("total_paid_amount")
        )

        provider_chart = px.bar(
            top_providers,
            x="total_paid_amount",
            y="provider_name",
            orientation="h",
            color="specialty",
            labels={
                "total_paid_amount": "Total Paid Amount (USD)",
                "provider_name": "Provider",
                "specialty": "Specialty",
            },
            title="Top 10 Providers by Paid Claims Amount",
        )
        st.plotly_chart(provider_chart, width="stretch")

        st.subheader("Diagnosis Cost and Utilization")
        diagnosis_chart_data = (
            diagnosis.sort_values("total_paid_amount", ascending=False)
            .head(10)
            .sort_values("total_paid_amount")
        )

        diagnosis_chart = px.bar(
            diagnosis_chart_data,
            x="total_paid_amount",
            y="diagnosis_description",
            orientation="h",
            color="claim_count",
            color_continuous_scale="Teal",
            labels={
                "total_paid_amount": "Total Paid Amount (USD)",
                "diagnosis_description": "Diagnosis",
                "claim_count": "Claim Count",
            },
            title="Top Diagnoses by Paid Claims Amount",
        )
        st.plotly_chart(diagnosis_chart, width="stretch")

        st.subheader("Provider Performance Table")
        provider_table = filtered_providers[
            [
                "provider_name",
                "specialty",
                "state",
                "claim_count",
                "unique_patient_count",
                "total_paid_amount",
                "average_paid_amount",
            ]
        ].sort_values("total_paid_amount", ascending=False)

        st.dataframe(
            provider_table.style.format(
                {
                    "total_paid_amount": "${:,.2f}",
                    "average_paid_amount": "${:,.2f}",
                }
            ),
            width="stretch",
            hide_index=True,
        )

    with tab_risk_quality:
        left, right = st.columns(2)

        with left:
            st.subheader("High-Cost Patients by State")
            state_summary = (
                patients.groupby("state", as_index=False)
                .agg(
                    patient_count=("patient_id", "count"),
                    high_cost_patient_count=("high_cost_flag", "sum"),
                    total_paid_amount=("total_paid_amount", "sum"),
                )
                .sort_values("total_paid_amount", ascending=False)
            )

            state_chart = px.bar(
                state_summary,
                x="state",
                y="total_paid_amount",
                color="high_cost_patient_count",
                labels={
                    "state": "State",
                    "total_paid_amount": "Total Paid Amount (USD)",
                    "high_cost_patient_count": "High-Cost Patients",
                },
                color_continuous_scale="Oranges",
            )
            st.plotly_chart(state_chart, width="stretch")

        with right:
            st.subheader("Patient Age Distribution")
            age_chart = px.histogram(
                patients,
                x="age_years",
                nbins=12,
                color="gender",
                labels={
                    "age_years": "Age",
                    "gender": "Gender",
                },
                barmode="overlay",
                opacity=0.75,
            )
            st.plotly_chart(age_chart, width="stretch")

        st.subheader("Highest-Cost Patient Cohort")
        high_cost_table = (
            patients[patients["high_cost_flag"].astype(bool)][
                [
                    "patient_id",
                    "state",
                    "gender",
                    "age_years",
                    "risk_score",
                    "claim_count",
                    "inpatient_claim_count",
                    "total_paid_amount",
                ]
            ]
            .sort_values("total_paid_amount", ascending=False)
            .head(20)
        )

        st.dataframe(
            high_cost_table.style.format(
                {
                    "risk_score": "{:.2f}",
                    "total_paid_amount": "${:,.2f}",
                }
            ),
            width="stretch",
            hide_index=True,
        )

        st.subheader("Raw Data Quality Reconciliation")
        observed_issues = quality_profile.get("observed_quality_issues", {})

        if observed_issues:
            quality_table = pd.DataFrame(
                {
                    "Quality Check": [
                        issue.replace("_", " ").title()
                        for issue in observed_issues
                    ],
                    "Observed Count": observed_issues.values(),
                }
            )
            st.dataframe(
                quality_table,
                width="stretch",
                hide_index=True,
            )

            if quality_profile.get("quality_gate_passed"):
                st.success(
                    "Quality gate passed: observed data issues matched "
                    "the expected reconciliation rules."
                )
            else:
                st.warning(
                    "Quality gate did not pass. Review reconciliation results "
                    "before using the batch for decisions."
                )
        else:
            st.info("No quality profile was found for this batch.")

    st.divider()
    st.caption(
        "Portfolio project by Mazeen Hussain | "
        "All values are synthetic and for demonstration only."
    )


if __name__ == "__main__":
    main()