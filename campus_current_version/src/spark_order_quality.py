"""Deterministic duplicate handling over schema-parsed, otherwise valid orders.

Exact duplicates collapse; all rows for IDs with conflicting payloads are quarantined.
Invalid rows must be classified by the caller before invoking this helper.
"""
def resolve_order_ids(valid, id_column):
    from pyspark.sql import functions as F
    columns = valid.columns
    variants = valid.dropDuplicates(columns)
    conflicts = (variants.groupBy(id_column).count()
                 .filter(F.col('count') > 1).select(id_column))
    quarantine = valid.join(conflicts, id_column, 'inner').select(*columns)
    clean = variants.join(conflicts, id_column, 'left_anti').select(*columns)
    return clean, quarantine
