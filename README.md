# ai-lakehouse

Small versioned lakehouse: DuckLake (catalog) over RustFS (S3-compatible storage),
built with DuckDB, populated from COCO (images) and VisDrone-MOT (real video
sequences -- real scene/frame ordering and real cross-frame object tracking, see
notes below), with a round-trip to the Hugging Face Hub.

## Setup (do this once)

```bash
cp .env.example .env
# edit .env, paste your HF token in (no quotes around the value)

docker compose up -d
```

This brings up three containers: `rustfs` (S3-compatible object storage),
`mongo` (required by the `fiftyone` library -- see notes below), and `lab`
(where everything actually runs).

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

Fair warning: this install is noticeably heavier than it looks, since `fiftyone`
(needed to correctly read the real VisDrone-MOT data -- see notes below) pulls in
matplotlib, scikit-learn, scikit-image, and a handful of other sizeable packages
as dependencies. Budget a few extra minutes on the first install.

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

There's also a `fiftyone-cache` named volume, mounted at `/root/fiftyone` in the
`lab` container. Without it, `./rebuild.sh` would re-download the VisDrone-MOT
dataset from Hugging Face from scratch on every single rebuild, since `rebuild.sh`
destroys and recreates the `lab` container each time. The named volume lets that
download persist across rebuilds instead.

## Attach the lakehouse

From inside the `lab` container:
```bash
python3
>>> import duckdb
>>> con = duckdb.connect()
>>> con.execute(open("sql/00_attach.sql").read())
```

Or from the DuckDB CLI directly if it's installed.

## Running the pipeline

Every pipeline step is a Jupyter notebook (`notebooks/*.ipynb`), run non-interactively
with `papermill`. Run these in order from inside the `lab` container (or from the host
with `docker compose exec -T lab ...` in front of each, which is what `rebuild.sh` does):

```bash
papermill notebooks/10_ingest_coco.ipynb notebooks/10_ingest_coco.ipynb --cwd /workspace --kernel python3
papermill notebooks/61_ingest_via_local_store.ipynb notebooks/61_ingest_via_local_store.ipynb --cwd /workspace --kernel python3
papermill notebooks/11_ingest_visdrone.ipynb notebooks/11_ingest_visdrone.ipynb --cwd /workspace --kernel python3
python3 -c "import duckdb; con = duckdb.connect(); con.execute(open('sql/00_attach.sql').read()); con.execute(open('sql/20_silver.sql').read())"   # silver
python3 -c "import duckdb; con = duckdb.connect(); con.execute(open('sql/00_attach.sql').read()); con.execute(open('sql/20_silver.sql').read()); con.execute(open('sql/30_gold.sql').read())"   # gold
papermill notebooks/40_fragment_query_demo.ipynb notebooks/40_fragment_query_demo.ipynb --cwd /workspace --kernel python3
papermill notebooks/50_versioning_demo.ipynb notebooks/50_versioning_demo.ipynb --cwd /workspace --kernel python3
```

`--cwd /workspace` matters: it makes the notebook execute as if run from the project
root, so relative paths like `sql/00_attach.sql` resolve correctly (papermill's default
execution directory is wherever the notebook *file* lives, i.e. `notebooks/`, not the
project root). Passing the same path as both input and output writes the executed
cell outputs back into the same `.ipynb` file, so you can open it afterward in
Jupyter/VS Code and see exactly what ran and printed at each step.

To push the gold tables to Hugging Face (Week 3), edit `HF_USERNAME` in
`notebooks/60_push_to_hub.ipynb`, then run it manually -- not run automatically
by anything else, since it creates real repos on your HF account.

### Rebuilding from scratch

`./rebuild.sh` automates all of the above from a completely empty state: tears
down and recreates the containers, wipes RustFS's storage and the local
DuckLake catalog, then reruns the entire pipeline in order via `papermill`
(everything above except the HF push, which stays manual on purpose). Run it
from the project root: `chmod +x rebuild.sh && ./rebuild.sh`. Verified working
end-to-end, including a real detection count on `raw.visdrone_frames` (not zero).

## Published gold datasets

Both gold tables were pushed back to the Hugging Face Hub via `notebooks/60_push_to_hub.ipynb`:

- https://huggingface.co/datasets/shalyyy/ai-lakehouse-coco
- https://huggingface.co/datasets/shalyyy/ai-lakehouse-visdrone

## Structure

```
docker-compose.yml   # RustFS + Mongo + lab containers
rebuild.sh            # recreates the whole lakehouse from an empty bucket, via papermill
.env                  # HF_TOKEN (not committed)
sql/
  00_attach.sql         # extensions + S3 secret + ATTACH DuckLake
  20_silver.sql         # raw -> silver transforms (typed tables, schema evolution demo)
  30_gold.sql           # silver -> gold: ML-ready tables + the VisDrone fragment index
notebooks/
  10_ingest_coco.ipynb       # lands COCO images + annotations into raw
  61_ingest_via_local_store.ipynb  # incremental: 20 more COCO images, staged through local-store/ first
  11_ingest_visdrone.ipynb   # lands real VisDrone-MOT video frames + real detections into raw
  40_fragment_query_demo.ipynb  # COCO crowded-scenes query + selective VisDrone fragment fetch
  50_versioning_demo.ipynb      # time travel, snapshot comparison, rollback demo
  60_push_to_hub.ipynb          # pushes both gold tables back to Hugging Face
local-store/          # local Docker host storage -- 61_ingest_via_local_store.ipynb stages files here
```

## Notes / deviations from the assignment brief

- **VisDrone-VID itself (the exact split named in the brief) couldn't be used.**
  It's only distributed via Google Drive/Baidu, no direct download URL. A direct
  `gdown` attempt at the smallest official split (valset) was blocked by Google
  itself ("too many users have downloaded this file recently, try again in 24
  hours") -- outside our control, not a bug on our end.

- **What we ended up using instead: VisDrone-MOT** (`Voxel51/visdrone-mot` on
  Hugging Face) -- the multi-object-tracking track of the same VisDrone
  benchmark, built from much of the same underlying drone footage as VID. This
  is a real video dataset: real named sequences (e.g. `uav0000086_00000_v`),
  real sequential `frame_number`s within each sequence, and real per-object
  detections that include a genuine cross-frame **tracking ID** (VisDrone-VID's
  own annotation format includes an equivalent tracking field, so MOT data is
  a reasonable stand-in, not a stretch). One honest caveat: this is the MOT
  track, not the literally-named VID track -- worth stating plainly rather than
  implying it's the exact same split.

- **Getting to that real data took several wrong turns, worth documenting
  honestly:**
  - A first attempt at this exact dataset failed when read through Hugging
    Face's **auto-converted Parquet mirror** -- that conversion silently drops
    FiftyOne's nested `scene_id`/`frame_number`/`detections` fields down to a
    bare `image` column. Confirmed by querying the raw Parquet directly with
    DuckDB and seeing nothing else.
  - The actual fix: load the dataset through the real **`fiftyone`** Python
    library instead (`fiftyone.utils.huggingface.load_from_hub`), which reads
    the dataset in its native format with every field intact.
  - `fiftyone` requires a real MongoDB to store its metadata. Its bundled
    auto-installer (`fiftyone-db`) doesn't ship a `mongod` binary for
    `linux/aarch64` -- exactly the platform this container runs on under
    Docker Desktop on Apple Silicon. Fixed by running a real `mongo` container
    (`docker-compose.yml`) and pointing `fiftyone` at it via
    `FIFTYONE_DATABASE_URI`.
  - Downloading the full dataset with no limit (~2,847 images across all 7
    real scenes) triggered a `429 Too Many Requests` from Hugging Face's Xet
    storage backend. Fixed by bounding the download (`max_samples`) and
    retrying with backoff on failure, and by picking whichever real scenes
    actually land inside that bounded download rather than hardcoding scene
    names that might get cut off.
  - `sample.detections`' actual shape varies -- sometimes a `Detections`
    wrapper object, sometimes a bare list of `Detection` objects directly. An
    early version of the ingestion code mishandled the bare-list case and
    silently discarded every real detection (`raw.visdrone_frames` landed with
    0 total detections despite real data existing). Fixed by handling both
    shapes explicitly; confirmed fixed by rerunning and seeing a real,
    non-zero detection count and a populated fragment index.

- `notebooks/11_ingest_visdrone.ipynb` pulls 2 real scenes, 30 contiguous real
  frames each (60 frames total, matching the original scale of this project),
  selected by real `frame_number` ordering rather than an arbitrary slice --
  see the notebook for the exact logic.
