# Healthcare Claims Intelligence Lakehouse

An end-to-end healthcare claims data platform that converts synthetic Medicare-style claims into trusted analytics-ready datasets and operational cost/utilisation KPIs.

## Business problem

Healthcare claims data is high-volume, inconsistent, and difficult for analysts to use directly. This project builds a reproducible Medallion Architecture pipeline that improves data quality, preserves auditability, and delivers reliable reporting datasets.

## What this project delivers

- Synthetic, privacy-safe claims data with intentional real-world data-quality issues.
- Bronze, Silver, and Gold data layers in Parquet/Delta-style design.
- Automated data-quality checks for missing values, duplicates, invalid dates, and referential integrity.
- Cost, utilisation, provider, diagnosis, and readmission-risk KPIs.
- Databricks and PySpark implementation for scalable processing.
- AWS S3-style raw-data storage design and Docker-based reproducible local development.
- A Power BI dashboard for healthcare operations insights.

## Architecture

```text
Synthetic Claims Data
        ↓
Bronze: raw immutable records
        ↓
Silver: cleaned and conformed tables
        ↓
Gold: analytics-ready KPI tables
        ↓
Power BI / stakeholder dashboard