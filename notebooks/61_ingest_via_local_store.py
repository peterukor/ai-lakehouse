# Week 3's "local storage" flow: pulls a NEW batch of COCO images (not
# the same 60 from before), saves them as real files on disk in
# local-store/ first, then loads from those local files into raw as a
# genuine incremental snapshot (INSERT, not replace). This is the piece
# the earlier ingestion scripts skipped -- they streamed straight from
# HF into RustFS, never actually touching local Docker host storage.

import io
import json
import os

import boto3
import duckdb
import pandas as pd
from datasets import load_dataset

N_NEW_IMAGES = 20
ALREADY_HAVE = 60   # skip the first 60, since those are already in raw.coco_annotations
BUCKET = "lakehouse"
S3_PREFIX = "assets/coco/images"
LOCAL_DIR = "/data/local/coco_staging"   # the actual local-store/ bind mount


def make_s3_client():
    return boto3.client(
        "s3",
        endpoint_url="http://rustfs:9000",
        aws_access_key_id="rustfsadmin",
        aws_secret_access_key="rustfsadmin",
    )


def attach_lakehouse():
    con = duckdb.connect()
    con.execute(open("sql/00_attach.sql").read())
    return con


# step 1: download from HF and save as real files on local disk --
# nothing touches RustFS or DuckLake yet, this is purely local staging
def download_to_local_store():
    os.makedirs(LOCAL_DIR, exist_ok=True)
    print(f"Streaming {N_NEW_IMAGES} NEW images (skipping the first {ALREADY_HAVE} already ingested)...")

    ds = load_dataset("ariG23498/coco2017", split="validation", streaming=True)
    ds = ds.skip(ALREADY_HAVE)   # get past the images we already have

    saved = []
    for i, sample in enumerate(ds):
        if i >= N_NEW_IMAGES:
            break

        img = sample["image"].convert("RGB")
        objects = sample["objects"]
        global_index = ALREADY_HAVE + i   # keeps filenames unique against the earlier batch

        local_path = f"{LOCAL_DIR}/{global_index:04d}.jpg"
        img.save(local_path, format="JPEG", quality=90)

        saved.append({
            "local_path": local_path,
            "global_index": global_index,
            "width": img.width,
            "height": img.height,
            "bbox_json": json.dumps(objects.get("bbox", [])),
            "segmentation_json": json.dumps(objects.get("segmentation", [])),
            "categories_json": json.dumps(objects.get("categories", [])),
        })

    print(f"Saved {len(saved)} images to {LOCAL_DIR} (visible in your local-store/ folder)")
    return saved


# step 2: now actually move the staged local files into the lakehouse --
# upload each to RustFS, then INSERT (not replace) the metadata into raw
def load_from_local_store_into_lakehouse(s3, con, staged):
    rows = []
    for item in staged:
        key = f"{S3_PREFIX}/{item['global_index']:04d}.jpg"
        with open(item["local_path"], "rb") as f:
            s3.put_object(Bucket=BUCKET, Key=key, Body=f, ContentType="image/jpeg")

        rows.append({
            "image_uri": f"s3://{BUCKET}/{key}",
            "width": item["width"],
            "height": item["height"],
            "bbox_json": item["bbox_json"],
            "segmentation_json": item["segmentation_json"],
            "categories_json": item["categories_json"],
        })

    df = pd.DataFrame(rows)
    con.register("new_coco_df", df)

    # INSERT, not CREATE OR REPLACE -- this is what makes it genuinely
    # incremental, growing the table instead of rebuilding it
    con.execute("INSERT INTO raw.coco_annotations SELECT * FROM new_coco_df")

    total = con.sql("SELECT COUNT(*) FROM raw.coco_annotations").fetchone()[0]
    print(f"raw.coco_annotations now has {total} rows (added {len(rows)} via local storage)")


def main():
    s3 = make_s3_client()
    con = attach_lakehouse()

    staged = download_to_local_store()
    load_from_local_store_into_lakehouse(s3, con, staged)

    print("\nMost recent snapshots:")
    con.sql("FROM ducklake_snapshots('lake') ORDER BY snapshot_id DESC LIMIT 5").show()


if __name__ == "__main__":
    main()
