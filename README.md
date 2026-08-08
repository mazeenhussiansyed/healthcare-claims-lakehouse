# Healthcare Claims Lakehouse

An end-to-end, containerized data pipeline that turns synthetic Medicare-style claims data into analytics-ready healthcare cost and utilization KPIs.

> **Data privacy note:** This project uses fully synthetic data only. It contains no real patient information, protected health information (PHI), or restricted healthcare data.

## Recruiter Snapshot

- Built a five-stage Medallion pipeline: Raw → Quality → Bronze → Silver → Gold
- Generated and processed 10,100 synthetic healthcare claim rows per run
- Reconciled intentional raw-data issues before transformation
- Removed 100 duplicate claims and quarantined 150 invalid claims
- Published 9,850 trusted Silver claims and Gold KPI tables for 1,000 patients and 150 providers
- Containerized the pipeline with Docker and Docker Compose
- Added automated regression tests with `pytest`
- Created a one-command Python orchestrator that stops immediately when a stage fails

## Business Problem

Healthcare claims data commonly arrives from multiple source files with duplicate records, missing diagnosis information, invalid payment amounts, and inconsistent date values. If analysts use this data directly, cost and utilization KPIs can become misleading.

This project models a production-style pipeline that preserves source traceability, measures data quality, applies controlled remediation, and publishes clean datasets for reporting and analysis.

## Architecture

```text
Synthetic Claims Generator
          │
          ▼
   Raw Data Layer
          │
          ▼
 Raw Quality Profile
          │
          ▼
 Bronze Layer
(unchanged, auditable copy)
          │
          ▼
 Silver Layer
(deduplicated, validated, cleaned)
          │
          ▼
  Gold Layer
(healthcare cost and utilization KPIs)
```

## Verified Pipeline Outcome

Results below are from the reproducible batch `orchestration_demo_001`.

| Metric | Result |
|---|---:|
| Raw patients | 1,000 |
| Raw providers | 150 |
| Raw claims, including duplicates | 10,100 |
| Duplicate claims removed | 100 |
| Invalid claims quarantined | 150 |
| Missing diagnoses imputed | 147 |
| Silver claims published | 9,850 |
| Patient KPI records | 1,000 |
| Provider KPI records | 150 |
| Diagnosis KPI groups | 7 |
| Monthly KPI rows | 25 |
| Total paid claims amount | $26.03M* |

\*Synthetic value generated for portfolio demonstration only.

## Data Quality Controls

| Raw-data issue | Detection | Silver-layer action |
|---|---|---|
| Duplicate claim rows | Duplicate `claim_id` check | Removed while retaining the original Bronze record |
| Negative paid amounts | Payment validation rule | Quarantined for investigation |
| Discharge before service date | Date sequence validation | Quarantined for investigation |
| Missing diagnosis code | Null-value check | Imputed only for otherwise valid records |
| Unknown patient/provider IDs | Referential-integrity check | Counted and reported |

The pipeline reconciles observed issue counts with the intentionally generated expected counts. The transformation proceeds only when the raw quality gate passes.

## Technology Stack

- **Language:** Python 3.14
- **Data processing:** Pandas, PyArrow, Parquet
- **Synthetic data:** Faker
- **Testing:** pytest
- **Containerization:** Docker and Docker Compose
- **Version control:** Git and GitHub
- **Architecture pattern:** Medallion (Bronze, Silver, Gold)

## Project Structure

```text
.
├── data/
│   ├── raw/
│   ├── bronze/
│   ├── silver/
│   ├── gold/
│   └── quality/
├── src/
│   ├── ingestion/
│   ├── quality/
│   ├── transforms/
│   ├── kpis/
│   └── orchestration/
├── tests/
├── docs/
├── infra/
├── Dockerfile
├── compose.yaml
└── README.md
```

## Run the Project

### 1. Run tests

```bash
python -m pytest -q
```

### 2. Build and test with Docker

```bash
docker compose up --build --abort-on-container-exit
```

### 3. Run the complete pipeline with one command

Use a new batch ID every time to preserve prior raw data.

```bash
docker compose run --build --rm pipeline python src/orchestration/run_pipeline.py --batch-id healthcare_demo_001
```

The orchestration process performs these stages in order:

1. Generate synthetic claims data
2. Profile raw-data quality
3. Load immutable data into Bronze
4. Clean and validate data in Silver
5. Build Gold KPI datasets

## Gold-Layer Analytics

The Gold layer produces datasets that support healthcare operations and analytics, including:

- Monthly claims volume, cost, and utilization KPIs
- Provider-level paid-claims and utilization metrics
- Diagnosis-level cost and claim-frequency analysis
- Patient-level total-cost metrics and high-cost patient flags

## Author

**Mazeen Hussain**

Data Engineering & Data Analytics Portfolio Project