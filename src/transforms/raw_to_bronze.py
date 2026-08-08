from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


TABLES = ("patients", "providers", "claims")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Promote an immutable raw batch into the Bronze layer."
    )
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--raw-dir", default="data/raw")
    parser.add_argument("--bronze-dir", default="data/bronze")
    args = parser.parse_args()

    raw_batch_path = Path(args.raw_dir) / f"batch_id={args.batch_id}"
    bronze_batch_path = Path(args.bronze_dir) / f"batch_id={args.batch_id}"

    if not raw_batch_path.exists():
        raise FileNotFoundError(f"Raw batch not found: {raw_batch_path}")

    if bronze_batch_path.exists():
        raise FileExistsError(
            f"Bronze batch already exists: {bronze_batch_path}. "
            "Bronze data is immutable and should not be overwritten."
        )

    bronze_batch_path.mkdir(parents=True)
    bronze_loaded_at = datetime.now(UTC).isoformat()
    record_counts = {}

    for table_name in TABLES:
        source_path = raw_batch_path / f"{table_name}.parquet"

        if not source_path.exists():
            raise FileNotFoundError(f"Missing source table: {source_path}")

        source_frame = pd.read_parquet(source_path)
        bronze_frame = source_frame.copy()

        bronze_frame["source_batch_id"] = args.batch_id
        bronze_frame["bronze_loaded_at_utc"] = bronze_loaded_at

        bronze_frame.to_parquet(
            bronze_batch_path / f"{table_name}.parquet",
            index=False,
        )

        record_counts[table_name] = {
            "raw_rows": len(source_frame),
            "bronze_rows": len(bronze_frame),
        }

    audit = {
        "batch_id": args.batch_id,
        "bronze_loaded_at_utc": bronze_loaded_at,
        "transformation_policy": (
            "No source values were modified, removed, or deduplicated in Bronze."
        ),
        "record_counts": record_counts,
    }

    audit_path = bronze_batch_path / "bronze_load_audit.json"
    audit_path.write_text(json.dumps(audit, indent=2), encoding="utf-8")

    print(json.dumps(audit, indent=2))
    print(f"\nBronze batch created: {bronze_batch_path}")


if __name__ == "__main__":
    main()