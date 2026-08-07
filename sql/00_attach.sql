-- Run this first in every session. It installs the extensions, points DuckDB
-- at RustFS via an S3 secret, and attaches the DuckLake catalog.
--
-- Catalog (metadata: schemas, snapshots) lives in a local DuckDB file (metadata.ducklake).
-- Actual data (Parquet bytes) lives in the RustFS bucket. That split is the whole point
-- of a lakehouse: the catalog is small and transactional, the data is cheap and immutable.

INSTALL ducklake; LOAD ducklake;
INSTALL httpfs;   LOAD httpfs;

CREATE OR REPLACE SECRET rustfs (
    TYPE s3,
    KEY_ID 'rustfsadmin',
    SECRET 'rustfsadmin',
    ENDPOINT 'rustfs:9000',   -- use 'localhost:9000' if connecting from the host instead of the lab container
    URL_STYLE 'path',
    USE_SSL false
);

ATTACH 'ducklake:metadata.ducklake' AS lake (DATA_PATH 's3://lakehouse/');
USE lake;

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;
