from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def run_step(name: str, command: list[str]) -> None:
    """Run one pipeline stage and stop the pipeline if it fails."""
    print(f"\n{'=' * 72}")
    print(f"RUNNING: {name}")
    print(f"COMMAND: {' '.join(command)}")
    print(f"{'=' * 72}\n")

    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the complete healthcare claims medallion pipeline."
    )
    parser.add_argument(
        "--batch-id",
        default=f"healthcare_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
        help="Unique identifier for this pipeline run.",
    )
    parser.add_argument("--patients", type=int, default=1_000)
    parser.add_argument("--providers", type=int, default=150)
    parser.add_argument("--claims", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    python = sys.executable
    batch_id = args.batch_id

    steps = [
        (
            "1/5 Generate synthetic raw claims",
            [
                python,
                "src/ingestion/generate_synthetic_claims.py",
                "--patients",
                str(args.patients),
                "--providers",
                str(args.providers),
                "--claims",
                str(args.claims),
                "--seed",
                str(args.seed),
                "--batch-id",
                batch_id,
            ],
        ),
        (
            "2/5 Profile raw-data quality",
            [
                python,
                "src/quality/profile_raw_data.py",
                "--batch-id",
                batch_id,
            ],
        ),
        (
            "3/5 Load raw data into Bronze",
            [
                python,
                "src/transforms/raw_to_bronze.py",
                "--batch-id",
                batch_id,
            ],
        ),
        (
            "4/5 Transform Bronze into Silver",
            [
                python,
                "src/transforms/bronze_to_silver.py",
                "--batch-id",
                batch_id,
            ],
        ),
        (
            "5/5 Build Gold KPI tables",
            [
                python,
                "src/kpis/build_gold_kpis.py",
                "--batch-id",
                batch_id,
            ],
        ),
    ]

    try:
        for name, command in steps:
            run_step(name, command)
    except subprocess.CalledProcessError as error:
        print(f"\nPipeline stopped because this stage failed: {error.cmd}")
        raise SystemExit(error.returncode) from error

    print("\n" + "=" * 72)
    print(f"PIPELINE COMPLETED SUCCESSFULLY FOR BATCH: {batch_id}")
    print("=" * 72)


if __name__ == "__main__":
    main()