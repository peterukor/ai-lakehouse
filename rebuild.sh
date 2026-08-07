#!/usr/bin/env bash
# Rebuilds the entire lakehouse from nothing: fresh containers, empty
# RustFS storage, empty DuckLake catalog, then reruns the whole pipeline
# in order. Run this from the project root: ./rebuild.sh

set -euo pipefail

echo "== ai-lakehouse rebuild =="

# tear down containers AND named volumes -- this wipes RustFS's stored
# data completely, not just stopping the containers
echo "Tearing down existing containers and volumes..."
docker compose down -v

# the DuckLake catalog lives as a local file in the project folder (not
# inside a container), so it needs to be removed separately to actually
# start from an empty catalog
rm -f metadata.ducklake metadata.ducklake.wal

echo "Starting fresh containers..."
docker compose up -d

# give RustFS a moment to actually come up before we try to use it
echo "Waiting for RustFS to be ready..."
until curl -s -o /dev/null "http://localhost:9000" 2>/dev/null; do
    sleep 1
done

# install deps FIRST -- the bucket creation step below needs boto3
echo "Installing Python dependencies..."
docker compose exec -T lab pip install -r requirements.txt

# create the bucket via the S3 API directly -- no browser/manual click
# needed, this is what makes the whole thing scriptable
echo "Creating the lakehouse bucket..."
docker compose exec -T lab python3 -c "
import boto3
s3 = boto3.client('s3', endpoint_url='http://rustfs:9000',
                   aws_access_key_id='rustfsadmin', aws_secret_access_key='rustfsadmin')
try:
    s3.create_bucket(Bucket='lakehouse')
    print('bucket created')
except Exception as e:
    print(f'bucket create skipped (probably already exists): {e}')
"

# run the whole pipeline in order, same sequence as the README
#
# NOTE: the two ingest scripts sometimes crash on exit with
# "Fatal Python error: PyGILState_Release" -- this is a known, harmless
# cleanup-order issue between duckdb/pyarrow/aiohttp threads that only
# happens AFTER all the real work is already done (confirmed multiple
# times: the row counts print successfully before this happens). Without
# "|| true" here, that crash's exit code would trip set -e and halt the
# whole rebuild even though nothing actually went wrong.
echo "Running the pipeline..."
docker compose exec -T lab python3 notebooks/10_ingest_coco.py || true
docker compose exec -T lab python3 notebooks/11_ingest_visdrone.py || true
docker compose exec -T lab python3 -c "import duckdb; con = duckdb.connect(); con.execute(open('sql/00_attach.sql').read()); con.execute(open('sql/20_silver.sql').read())"
docker compose exec -T lab python3 -c "import duckdb; con = duckdb.connect(); con.execute(open('sql/00_attach.sql').read()); con.execute(open('sql/20_silver.sql').read()); con.execute(open('sql/30_gold.sql').read())"
docker compose exec -T lab python3 notebooks/40_fragment_query_demo.py
docker compose exec -T lab python3 notebooks/50_versioning_demo.py

# sanity check: confirm the ingestion steps actually landed real data,
# since we just told the script to ignore their exit codes above
echo "Verifying ingestion actually succeeded..."
docker compose exec -T lab python3 -c "
import duckdb
con = duckdb.connect()
con.execute(open('sql/00_attach.sql').read())
coco_count = con.sql('SELECT COUNT(*) FROM raw.coco_annotations').fetchone()[0]
visdrone_count = con.sql('SELECT COUNT(*) FROM raw.visdrone_frames').fetchone()[0]
print(f'raw.coco_annotations: {coco_count} rows')
print(f'raw.visdrone_frames: {visdrone_count} rows')
assert coco_count > 0, 'COCO ingestion produced no rows!'
assert visdrone_count > 0, 'VisDrone ingestion produced no rows!'
print('Both ingestion steps confirmed successful.')
"

echo ""
echo "== rebuild complete =="
echo "Note: notebooks/60_push_to_hub.py is NOT run automatically (it creates/overwrites"
echo "real Hugging Face repos) -- run it manually if you want to re-push the gold tables."
