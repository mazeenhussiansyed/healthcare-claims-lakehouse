from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


GOLD_TABLES = [
    "monthly_cost_utilization",
    "provider_performance",
    "diagnosis_cost_utilization",
    "patient_utilization",
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export Gold KPI Parquet tables as Power BI-ready CSV files."
    )
    parser.add_argument(
        "--batch-id",
        default="orchestration_demo_001",
        help="Gold batch identifier to export.",
    )
    parser.add_argument(
        "--input-dir",
        default="data/gold",
        help="Root folder containing Gold batch folders.",
    )
    parser.add_argument(
        "--output-dir",
        default="/mnt/c/Users/mazee/Documents/healthcare_claims_power_bi_data",
        help="Windows-accessible folder for the Power BI CSV files.",
    )
    args = parser.parse_args()

    gold_batch_directory = Path(args.input_dir) / f"batch_id={args.batch_id}"
    output_directory = Path(args.output_dir)

    if not gold_batch_directory.exists():
        raise FileNotFoundError(
            f"Gold batch not found: {gold_batch_directory}. "
            "Run the pipeline first or provide a valid --batch-id."
        )

    output_directory.mkdir(parents=True, exist_ok=True)

    exported_tables: list[dict[str, object]] = []

    for table_name in GOLD_TABLES:
        source_file = gold_batch_directory / f"{table_name}.parquet"
        destination_file = output_directory / f"{table_name}.csv"

        if not source_file.exists():
            raise FileNotFoundError(f"Required Gold table not found: {source_file}")

        table = pd.read_parquet(source_file)
        table.to_csv(destination_file, index=False)

        exported_tables.append(
            {
                "table_name": table_name,
                "source_file": str(source_file),
                "csv_file": str(destination_file),
                "row_count": len(table),
                "column_count": len(table.columns),
            }
        )

        print(f"Exported {table_name}: {len(table):,} rows")

    manifest = {
        "batch_id": args.batch_id,
        "exported_at_utc": datetime.now(UTC).isoformat(),
        "purpose": "Power BI dashboard source files",
        "tables": exported_tables,
    }

    manifest_file = output_directory / "export_manifest.json"
    manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print("\nPower BI export completed successfully.")
    print("Open this folder in Windows File Explorer:")
    print(r"C:\Users\mazee\Documents\healthcare_claims_power_bi_data")


if __name__ == "__main__":
    main()