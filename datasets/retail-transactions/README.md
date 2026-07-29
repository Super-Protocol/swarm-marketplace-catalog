# Retail Transactions 2024

Anonymized point-of-sale transactions from a European retail chain, updated daily.

## Contents

A single table, `transactions`, with one row per purchased line item. See the `schema` section of [`data.yaml`](data.yaml) for column-level documentation.

## Access

Visibility, usage terms, and grants are configured by the owner on the marketplace listing, not in this definition. Request access through the listing.

## Interfaces

- `clickhouse-native/v1` — live queries against the owner's ClickHouse cluster.
- `s3-parquet/v1` — daily Parquet snapshots in an S3-compatible bucket.
