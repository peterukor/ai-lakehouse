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

# install deps FIRST -- the bucket creation step below needs boto3.
# requirements.txt now also pulls in papermill + ipykernel, since the
# pipeline runs .ipynb notebooks instead of .py scripts from here on
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

# runs one notebook top-to-bottom in place, writing executed outputs back
# into the same .ipynb file (open it afterward to see exactly what ran).
#
# --cwd /workspace matters: the notebooks live in notebooks/, but sql/
# is a SIBLING of notebooks/, not nested inside it (project root has
# both notebooks/ and sql/ side by side). The code opens "sql/00_attach.sql"
# as a path relative to the project root, so execution has to happen from
# /workspace, not from notebooks/. (nbconvert's --execute doesn't have a
# working --cwd equivalent -- its "cwd" config option silently does nothing,
# which is what broke the first version of this script. papermill's --cwd
# is real and actually works, confirmed against papermill 2.7.0's own
# --help output.)
run_notebook() {
    docker compose exec -T lab papermill \
        "notebooks/$1" "notebooks/$1" \
        --cwd /workspace \
        --kernel python3 \
        --no-progress-bar
}

# run the whole pipeline in order, same sequence as the README, now
# against the .ipynb versions of the three ingestion notebooks
#
# NOTE: the two COCO ingest notebooks sometimes crash on exit with
# "Fatal Python error: PyGILState_Release" -- this is a known, harmless
# cleanup-order issue between duckdb/pyarrow/aiohttp threads that only
# happens AFTER all the real work is already done (confirmed multiple
# times: the row counts print successfully before this happens). Without
# "|| true" here, that crash's exit code would trip set -e and halt the
# whole rebuild even though nothing actually went wrong. Keeping "|| true"
# on all three ingestion notebooks, same as the original script did.
echo "Running the pipeline..."
run_notebook 10_ingest_coco.ipynb || true
run_notebook 61_ingest_via_local_store.ipynb || true
run_notebook 11_ingest_visdrone.ipynb || true
docker compose exec -T lab python3 -c "import duckdb; con = duckdb.connect(); con.execute(open('sql/00_attach.sql').read()); con.execute(open('sql/20_silver.sql').read())"
docker compose exec -T lab python3 -c "import duckdb; con = duckdb.connect(); con.execute(open('sql/00_attach.sql').read()); con.execute(open('sql/20_silver.sql').read()); con.execute(open('sql/30_gold.sql').read())"
run_notebook 40_fragment_query_demo.ipynb
run_notebook 50_versioning_demo.ipynb

# sanity check: confirm the ingestion steps actually landed real data,
# since we just told the script to ignore their exit codes above.
# raw.coco_annotations should be 80 (60 from the first script + 20 from
# the local-storage incremental step), not just "> 0"
echo "Verifying ingestion actually succeeded..."
docker compose exec -T lab python3 -c "
import duckdb
con = duckdb.connect()
con.execute(open('sql/00_attach.sql').read())
coco_count = con.sql('SELECT COUNT(*) FROM raw.coco_annotations').fetchone()[0]
visdrone_count = con.sql('SELECT COUNT(*) FROM raw.visdrone_frames').fetchone()[0]
print(f'raw.coco_annotations: {coco_count} rows')
print(f'raw.visdrone_frames: {visdrone_count} rows')
assert coco_count == 80, f'expected 80 COCO rows (60 + 20 incremental), got {coco_count}!'
assert visdrone_count > 0, 'VisDrone ingestion produced no rows!'
print('Both ingestion steps confirmed successful.')
"

echo ""
echo "== rebuild complete =="
echo "Note: notebooks/60_push_to_hub.ipynb is NOT run automatically (it creates/overwrites"
echo "real Hugging Face repos) -- run it manually if you want to re-push the gold tables."
