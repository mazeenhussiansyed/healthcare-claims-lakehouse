# Databricks notebook source
display(spark.sql("SHOW CATALOGS"))

# COMMAND ----------

CATALOG = "workspace"
SCHEMA = "healthcare_claims"

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")
spark.sql(f"USE CATALOG {CATALOG}")
spark.sql(f"USE SCHEMA {SCHEMA}")

display(spark.sql(f"SHOW SCHEMAS IN {CATALOG}"))

# COMMAND ----------

VOLUME = "raw_files"

spark.sql(
    f"CREATE VOLUME IF NOT EXISTS {CATALOG}.{SCHEMA}.{VOLUME}"
)

display(
    spark.sql(f"SHOW VOLUMES IN {CATALOG}.{SCHEMA}")
)

# COMMAND ----------

CATALOG = "workspace"
SCHEMA = "healthcare_claims"
VOLUME = "raw_files"

RAW_VOLUME_PATH = f"/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}"

display(dbutils.fs.ls(RAW_VOLUME_PATH))

# COMMAND ----------

patients_raw = spark.read.parquet(f"{RAW_VOLUME_PATH}/patients.parquet")
providers_raw = spark.read.parquet(f"{RAW_VOLUME_PATH}/providers.parquet")
claims_raw = spark.read.parquet(f"{RAW_VOLUME_PATH}/claims.parquet")

row_counts = [
    ("patients", patients_raw.count()),
    ("providers", providers_raw.count()),
    ("claims", claims_raw.count()),
]

display(spark.createDataFrame(row_counts, ["dataset", "row_count"]))

# COMMAND ----------

BRONZE_PATIENTS = f"{CATALOG}.{SCHEMA}.bronze_patients"
BRONZE_PROVIDERS = f"{CATALOG}.{SCHEMA}.bronze_providers"
BRONZE_CLAIMS = f"{CATALOG}.{SCHEMA}.bronze_claims"

patients_raw.write.format("delta").mode("overwrite").saveAsTable(BRONZE_PATIENTS)
providers_raw.write.format("delta").mode("overwrite").saveAsTable(BRONZE_PROVIDERS)
claims_raw.write.format("delta").mode("overwrite").saveAsTable(BRONZE_CLAIMS)

bronze_row_counts = [
    ("bronze_patients", spark.table(BRONZE_PATIENTS).count()),
    ("bronze_providers", spark.table(BRONZE_PROVIDERS).count()),
    ("bronze_claims", spark.table(BRONZE_CLAIMS).count()),
]

display(spark.createDataFrame(bronze_row_counts, ["table_name", "row_count"]))

# COMMAND ----------

from pyspark.sql import functions as F

duplicate_claim_rows = (
    claims_raw.groupBy("claim_id")
    .count()
    .where(F.col("count") > 1)
    .agg(F.sum(F.col("count") - 1).alias("duplicate_rows"))
    .first()["duplicate_rows"]
)

quality_rows = [
    (
        "missing_diagnosis_code",
        claims_raw.where(F.col("diagnosis_code").isNull()).count(),
    ),
    (
        "negative_paid_amount",
        claims_raw.where(F.col("paid_amount") < 0).count(),
    ),
    (
        "invalid_discharge_date",
        claims_raw.where(F.col("discharge_date") < F.col("service_date")).count(),
    ),
    ("duplicate_claim_rows", duplicate_claim_rows),
    (
        "unknown_patient_id",
        claims_raw.join(
            patients_raw.select("patient_id"),
            on="patient_id",
            how="left_anti",
        ).count(),
    ),
    (
        "unknown_provider_id",
        claims_raw.join(
            providers_raw.select("provider_id"),
            on="provider_id",
            how="left_anti",
        ).count(),
    ),
]

display(
    spark.createDataFrame(
        quality_rows,
        ["quality_check", "observed_count"],
    )
)

# COMMAND ----------

from pyspark.sql.window import Window

SILVER_PATIENTS = f"{CATALOG}.{SCHEMA}.silver_patients"
SILVER_PROVIDERS = f"{CATALOG}.{SCHEMA}.silver_providers"
SILVER_CLAIMS = f"{CATALOG}.{SCHEMA}.silver_claims"
QUARANTINE_CLAIMS = f"{CATALOG}.{SCHEMA}.quarantined_claims"

bronze_claims = spark.table(BRONZE_CLAIMS)

duplicate_window = Window.partitionBy("claim_id").orderBy(
    F.col("ingestion_timestamp_utc").asc_nulls_last()
)

deduplicated_claims = (
    bronze_claims
    .withColumn("_duplicate_rank", F.row_number().over(duplicate_window))
    .where(F.col("_duplicate_rank") == 1)
    .drop("_duplicate_rank")
)

invalid_claim_condition = (
    (F.col("paid_amount") < 0)
    | (
        F.col("discharge_date").isNotNull()
        & (F.col("discharge_date") < F.col("service_date"))
    )
)

quarantined_claims = (
    deduplicated_claims
    .where(invalid_claim_condition)
    .withColumn(
        "quarantine_reason",
        F.when(
            (F.col("paid_amount") < 0)
            & (F.col("discharge_date") < F.col("service_date")),
            F.lit("negative_paid_amount_and_invalid_discharge_date"),
        )
        .when(
            F.col("paid_amount") < 0,
            F.lit("negative_paid_amount"),
        )
        .otherwise(F.lit("invalid_discharge_date")),
    )
    .withColumn("quarantined_at_utc", F.current_timestamp())
)

claims_safe_for_silver = deduplicated_claims.where(~invalid_claim_condition)

missing_diagnoses_imputed = (
    claims_safe_for_silver
    .where(F.col("diagnosis_code").isNull())
    .count()
)

silver_claims_df = (
    claims_safe_for_silver
    .withColumn(
        "diagnosis_code",
        F.coalesce(F.col("diagnosis_code"), F.lit("UNKNOWN")),
    )
    .withColumn(
        "diagnosis_description",
        F.coalesce(
            F.col("diagnosis_description"),
            F.lit("Diagnosis unavailable in source"),
        ),
    )
    .withColumn("silver_processed_at_utc", F.current_timestamp())
)

spark.table(BRONZE_PATIENTS).write.format("delta").mode("overwrite").saveAsTable(
    SILVER_PATIENTS
)
spark.table(BRONZE_PROVIDERS).write.format("delta").mode("overwrite").saveAsTable(
    SILVER_PROVIDERS
)
quarantined_claims.write.format("delta").mode("overwrite").saveAsTable(
    QUARANTINE_CLAIMS
)
silver_claims_df.write.format("delta").mode("overwrite").saveAsTable(SILVER_CLAIMS)

silver_results = [
    ("input_claim_rows", bronze_claims.count()),
    ("duplicate_claim_rows_removed", bronze_claims.count() - deduplicated_claims.count()),
    ("invalid_claim_rows_quarantined", quarantined_claims.count()),
    ("missing_diagnosis_imputed", missing_diagnoses_imputed),
    ("silver_claim_rows_published", silver_claims_df.count()),
]

display(spark.createDataFrame(silver_results, ["measure", "value"]))

# COMMAND ----------

GOLD_MONTHLY = f"{CATALOG}.{SCHEMA}.gold_monthly_cost_utilization"
GOLD_PROVIDER = f"{CATALOG}.{SCHEMA}.gold_provider_performance"
GOLD_DIAGNOSIS = f"{CATALOG}.{SCHEMA}.gold_diagnosis_cost_utilization"
GOLD_PATIENT = f"{CATALOG}.{SCHEMA}.gold_patient_utilization"

silver_claims = spark.table(SILVER_CLAIMS)
silver_patients = spark.table(SILVER_PATIENTS)
silver_providers = spark.table(SILVER_PROVIDERS)

monthly_kpis = (
    silver_claims
    .withColumn("service_year", F.year("service_date"))
    .withColumn("service_month", F.month("service_date"))
    .groupBy("service_year", "service_month")
    .agg(
        F.count("*").alias("claim_count"),
        F.countDistinct("patient_id").alias("unique_patient_count"),
        F.countDistinct("provider_id").alias("unique_provider_count"),
        F.round(F.sum("billed_amount"), 2).alias("total_billed_amount"),
        F.round(F.sum("allowed_amount"), 2).alias("total_allowed_amount"),
        F.round(F.sum("paid_amount"), 2).alias("total_paid_amount"),
        F.round(F.avg("paid_amount"), 2).alias("average_paid_amount"),
        F.sum(F.when(F.col("claim_type") == "inpatient", 1).otherwise(0)).alias(
            "inpatient_claim_count"
        ),
        F.round(
            F.avg(
                F.when(
                    F.col("admission_date").isNotNull(),
                    F.datediff("discharge_date", "admission_date"),
                )
            ),
            2,
        ).alias("average_length_of_stay_days"),
        F.round(
            F.sum("paid_amount") / F.sum("billed_amount"),
            4,
        ).alias("paid_to_billed_ratio"),
    )
    .withColumn("gold_published_at_utc", F.current_timestamp())
)

provider_kpis = (
    silver_claims
    .groupBy("provider_id")
    .agg(
        F.count("*").alias("claim_count"),
        F.countDistinct("patient_id").alias("unique_patient_count"),
        F.round(F.sum("paid_amount"), 2).alias("total_paid_amount"),
        F.round(F.avg("paid_amount"), 2).alias("average_paid_amount"),
        F.sum(F.when(F.col("claim_type") == "inpatient", 1).otherwise(0)).alias(
            "inpatient_claim_count"
        ),
    )
    .join(
        silver_providers.select("provider_id", "provider_name", "specialty", "state"),
        on="provider_id",
        how="left",
    )
    .withColumn(
        "paid_amount_per_claim",
        F.round(F.col("total_paid_amount") / F.col("claim_count"), 2),
    )
    .withColumn("gold_published_at_utc", F.current_timestamp())
)

diagnosis_kpis = (
    silver_claims
    .groupBy("diagnosis_code", "diagnosis_description")
    .agg(
        F.count("*").alias("claim_count"),
        F.countDistinct("patient_id").alias("unique_patient_count"),
        F.round(F.sum("paid_amount"), 2).alias("total_paid_amount"),
        F.round(F.avg("paid_amount"), 2).alias("average_paid_amount"),
        F.sum(F.when(F.col("claim_type") == "inpatient", 1).otherwise(0)).alias(
            "inpatient_claim_count"
        ),
    )
    .withColumn("gold_published_at_utc", F.current_timestamp())
)

patient_kpis_base = (
    silver_claims
    .groupBy("patient_id")
    .agg(
        F.count("*").alias("claim_count"),
        F.round(F.sum("paid_amount"), 2).alias("total_paid_amount"),
        F.round(F.avg("paid_amount"), 2).alias("average_paid_amount"),
        F.sum(F.when(F.col("claim_type") == "inpatient", 1).otherwise(0)).alias(
            "inpatient_claim_count"
        ),
        F.max("service_date").alias("latest_service_date"),
    )
    .join(
        silver_patients.select(
            "patient_id", "date_of_birth", "gender", "state", "risk_score"
        ),
        on="patient_id",
        how="left",
    )
    .withColumn(
        "age_years",
        F.floor(F.months_between(F.current_date(), F.col("date_of_birth")) / 12),
    )
)

high_cost_threshold = patient_kpis_base.approxQuantile(
    "total_paid_amount",
    [0.95],
    0.0,
)[0]

patient_kpis = (
    patient_kpis_base
    .withColumn(
        "high_cost_flag",
        F.col("total_paid_amount") >= F.lit(high_cost_threshold),
    )
    .withColumn("gold_published_at_utc", F.current_timestamp())
)

monthly_kpis.write.format("delta").mode("overwrite").saveAsTable(GOLD_MONTHLY)
provider_kpis.write.format("delta").mode("overwrite").saveAsTable(GOLD_PROVIDER)
diagnosis_kpis.write.format("delta").mode("overwrite").saveAsTable(GOLD_DIAGNOSIS)
patient_kpis.write.format("delta").mode("overwrite").saveAsTable(GOLD_PATIENT)

gold_results = [
    ("gold_monthly_cost_utilization", monthly_kpis.count()),
    ("gold_provider_performance", provider_kpis.count()),
    ("gold_diagnosis_cost_utilization", diagnosis_kpis.count()),
    ("gold_patient_utilization", patient_kpis.count()),
]

display(spark.createDataFrame(gold_results, ["table_name", "row_count"]))

# COMMAND ----------

gold_monthly = spark.table(GOLD_MONTHLY)
gold_patients = spark.table(GOLD_PATIENT)

total_paid_amount = gold_monthly.agg(
    F.round(F.sum("total_paid_amount"), 2).alias("value")
).first()["value"]

verification_results = [
    ("silver_claim_count", f"{spark.table(SILVER_CLAIMS).count():,}"),
    ("total_paid_amount", f"{float(total_paid_amount):,.2f}"),
    ("high_cost_patient_threshold", f"{float(high_cost_threshold):,.2f}"),
    (
        "high_cost_patient_count",
        f"{gold_patients.where(F.col('high_cost_flag')).count():,}",
    ),
]

display(
    spark.createDataFrame(
        verification_results,
        ["measure", "value"],
    )
)

# COMMAND ----------

continuous_high_cost_threshold = spark.sql(
    f"""
    SELECT percentile(total_paid_amount, 0.95) AS threshold
    FROM {GOLD_PATIENT}
    """
).first()["threshold"]

continuous_high_cost_count = (
    spark.table(GOLD_PATIENT)
    .where(F.col("total_paid_amount") >= F.lit(continuous_high_cost_threshold))
    .count()
)

display(
    spark.createDataFrame(
        [
            ("continuous_high_cost_threshold", f"{continuous_high_cost_threshold:,.2f}"),
            ("continuous_high_cost_patient_count", f"{continuous_high_cost_count:,}"),
        ],
        ["measure", "value"],
    )
)

# COMMAND ----------

patient_cost_audit = spark.sql(
    f"""
    SELECT
        percentile(total_paid_amount, 0.90) AS p90,
        percentile(total_paid_amount, 0.91) AS p91,
        percentile(total_paid_amount, 0.92) AS p92,
        percentile(total_paid_amount, 0.93) AS p93,
        percentile(total_paid_amount, 0.94) AS p94,
        percentile(total_paid_amount, 0.95) AS p95
    FROM {GOLD_PATIENT}
    """
)

display(patient_cost_audit)

# COMMAND ----------

HIGH_COST_PERCENTILE = 0.90

high_cost_threshold_p90 = spark.sql(
    f"""
    SELECT percentile(total_paid_amount, {HIGH_COST_PERCENTILE}) AS threshold
    FROM {GOLD_PATIENT}
    """
).first()["threshold"]

spark.sql(
    f"""
    UPDATE {GOLD_PATIENT}
    SET high_cost_flag = total_paid_amount >= {high_cost_threshold_p90}
    """
)

high_cost_patient_count = (
    spark.table(GOLD_PATIENT)
    .where(F.col("high_cost_flag"))
    .count()
)

display(
    spark.createDataFrame(
        [
            ("high_cost_definition", "P90 — top 10% by total paid amount"),
            ("high_cost_threshold", f"{high_cost_threshold_p90:,.2f}"),
            ("high_cost_patient_count", f"{high_cost_patient_count:,}"),
        ],
        ["measure", "value"],
    )
)

# COMMAND ----------

HIGH_COST_STUDY = f"{CATALOG}.{SCHEMA}.gold_high_cost_utilization_study"

high_cost_study = (
    spark.table(GOLD_PATIENT)
    .groupBy("high_cost_flag")
    .agg(
        F.count("*").alias("patient_count"),
        F.round(F.sum("total_paid_amount"), 2).alias("total_paid_amount"),
        F.round(F.avg("total_paid_amount"), 2).alias("average_paid_amount"),
        F.round(F.avg("claim_count"), 2).alias("average_claim_count"),
        F.round(F.avg("inpatient_claim_count"), 2).alias(
            "average_inpatient_claim_count"
        ),
        F.round(F.avg("risk_score"), 2).alias("average_risk_score"),
    )
)

total_study_paid = high_cost_study.agg(
    F.sum("total_paid_amount").alias("total_paid_amount")
).first()["total_paid_amount"]

high_cost_study = (
    high_cost_study
    .withColumn(
        "share_of_total_paid_amount_pct",
        F.round(
            F.col("total_paid_amount") / F.lit(total_study_paid) * 100,
            2,
        ),
    )
    .withColumn("study_published_at_utc", F.current_timestamp())
)

high_cost_study.write.format("delta").mode("overwrite").saveAsTable(
    HIGH_COST_STUDY
)

display(
    high_cost_study.orderBy(F.desc("high_cost_flag"))
)

# COMMAND ----------

HIGH_COST_DIAGNOSIS_STUDY = (
    f"{CATALOG}.{SCHEMA}.gold_high_cost_diagnosis_drivers"
)

high_cost_diagnosis_drivers = (
    spark.table(SILVER_CLAIMS)
    .join(
        spark.table(GOLD_PATIENT).select("patient_id", "high_cost_flag"),
        on="patient_id",
        how="inner",
    )
    .where(F.col("high_cost_flag"))
    .groupBy("diagnosis_code", "diagnosis_description")
    .agg(
        F.count("*").alias("claim_count"),
        F.countDistinct("patient_id").alias("high_cost_patient_count"),
        F.round(F.sum("paid_amount"), 2).alias("total_paid_amount"),
        F.round(F.avg("paid_amount"), 2).alias("average_paid_amount"),
        F.sum(
            F.when(F.col("claim_type") == "inpatient", 1).otherwise(0)
        ).alias("inpatient_claim_count"),
    )
    .orderBy(F.desc("total_paid_amount"))
    .withColumn("study_published_at_utc", F.current_timestamp())
)

high_cost_diagnosis_drivers.write.format("delta").mode("overwrite").saveAsTable(
    HIGH_COST_DIAGNOSIS_STUDY
)

display(high_cost_diagnosis_drivers)

# COMMAND ----------

