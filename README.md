# ai-lakehouse

Small versioned lakehouse: DuckLake (catalog) over RustFS (S3-compatible storage),
built with DuckDB, populated from COCO (images) and VisDrone-MOT (video), with a
round-trip to the Hugging Face Hub.

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
  