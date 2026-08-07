# ai-lakehouse

Small versioned lakehouse: DuckLake (catalog) over RustFS (S3-compatible storage),
built with DuckDB, populated from COCO (images) and VisDrone (real detection images,
grouped into synthetic clips -- see notes below), with a round-trip to the Hugging
Face Hub.

## Setup (do this once)

```bash
cp .env.example .env
# edit .env, paste your HF token in (no quotes around the value)

docker compose up -d
```

Create the `lakehouse` bucket:
- open http://localhost:9001 in a browser (user/pass: rustfsadmin / rustfsadmin)
- create a bucket named `lakehouse`
- leave Object Lock and Versioning off (DuckLake manages versioning at the table
  level itself; turning these on at the storage layer too would just conflict
  with that)

Then get a shell inside the lab container and install Python deps:
```bash
docker compose exec lab bash
pip install -r requirements.txt
```

### Note on storage: named volumes, not bind mounts

RustFS's storage (`/data`, `/logs` inside its container) uses **named Docker
volumes**, not bind-mounted host folders. This was a deliberate fix, not the
original plan: RustFS runs as a non-root user (UID 10001) inside its container,
and on this machine (Docker Desktop on macOS, VirtioFS file sharing), bind-mounting
a host folder into that container consistently failed with a Permission Denied
error — even a separate helper container running as root couldn't chown the
bind-mounted folder, which points to the block happening somewhere in Docker
Desktop's host<->VM file-sharing bridge on macOS, not in ordinary Unix permissions.
Named volumes sidestep this entirely, since Docker creates and owns that storage
inside its own Linux VM and never needs to cross that bridge.

Practical effect: you can't browse RustFS's raw files in Finder anymore. Use the
RustFS web console (http://localhost:9001) to browse the bucket, or DuckDB/DuckLake
queries to read the data -- which is the intended workflow anyway. `local-store/`
is still a normal bind mount and works fine for staging files you do want visible
in Finder (e.g. exporting a table before pushing it to Hugging Face).

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
  20_silver.sql        # raw -> silver transforms (typed tables, schema evolution demo)
  30_gold.sql          # (coming next) silver -> gold tables
notebooks/
  10_ingest_coco.py     # lands COCO images + annotations into raw
  11_ingest_visdrone.py # lands VisDrone images + real detections into raw
local-store/          # local Docker host storage (staging area for HF round-trip)
```

## Notes / deviations from the assignment brief

- **VisDrone-VID (the actual video task) couldn't be used.** It's only distributed via
  Google Drive/Baidu, no direct download URL. A direct `gdown` attempt at the smallest
  official split (valset) was blocked by Google itself ("too many users have downloaded
  this file recently, try again in 24 hours") -- outside our control, not a bug on our end.

- **First substitution attempt failed too.** Tried `Voxel51/visdrone-mot` on Hugging Face
  (a FiftyOne-packaged version) instead. Its metadata (scene_id, frame_number, detections)
  turned out to not survive Hugging Face's automatic Parquet conversion -- confirmed by
  querying the raw Parquet file directly with DuckDB, which showed only an `image` column,
  nothing else. Not fixable from our side; FiftyOne's nested types aren't supported by
  that auto-conversion.

- **What we actually used:** `banu4prasad/VisDrone-Dataset` (YOLO format) instead. This
  gives **real bounding boxes**, parsed from real YOLO label files, for real VisDrone
  imagery. The one honest simplification: this is the VisDrone-**DET** split (individual
  detection images), not real video sequences, so `scene_id`/`frame_number` in
  `raw.visdrone_frames` are **synthetic** -- we group every 10 ingested images into a
  fake "clip" ourselves, purely so there's something to build and demo the fragment-index
  pattern on. The detections themselves are 100% real, only the clip boundaries are
  constructed. See `notebooks/11_ingest_visdrone.py` for the exact logic.
