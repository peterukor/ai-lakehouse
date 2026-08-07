# ai-lakehouse

Small versioned lakehouse: DuckLake (catalog) over RustFS (S3-compatible storage),
built with DuckDB, populated from COCO (images) and VisDrone-MOT (video), with a
round-trip to the Hugging Face Hub.

## Setup (do this once)

```bash
cp .env.example .env
# edit .env, paste your HF token in

mkdir -p rustfs-data rustfs-logs
sudo chown -R 10001 rustfs-data   # RustFS runs as a non-root user inside the container

docker compose up -d
```

Create the `lakehouse` bucket:
- open http://localhost:9001 in a browser (user/pass: rustfsadmin / rustfsadmin)
- create a bucket named `lakehouse`

Then get a shell inside the lab container and install Python deps:
```bash
docker compose exec lab bash
pip install -r requirements.txt
```

## Attach the lakehouse

From inside the `lab` container:
```bash
python3
>>> import duckdb
>>> con = duckdb.connect()
>>> con.execute(open("sql/00_attach.sql").read())
```

Or from the DuckDB CLI directly if it's installed.

## Structure

```
docker-compose.yml   # RustFS + lab containers
.env                  # HF_TOKEN (not committed)
sql/
  00_attach.sql       # extensions + S3 secret + ATTACH DuckLake
  10_raw.sql          # (coming next) land datasets in raw
  20_silver.sql       # (coming next) raw -> silver transforms
  30_gold.sql         # (coming next) silver -> gold tables
notebooks/            # ingestion + transform scripts
local-store/          # local Docker host storage (staging area for HF round-trip)
```

## Notes / deviations from the assignment brief

- Substituted **VisDrone-MOT** (`Voxel51/visdrone-mot` on Hugging Face) for the
  VisDrone-VID Task 2 dataset. The original VID task is only distributed via
  Google Drive / Baidu with no direct download URL, which was impractical given
  the project timeline. VisDrone-MOT is the same drone footage with per-frame
  bounding boxes, already grouped by `scene_id` + `frame_number`, which maps
  directly onto the fragment-index pattern the assignment asks for.
