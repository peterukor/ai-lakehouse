# Pulls a handful of COCO images from Hugging Face and lands them in raw.
# Photos go straight to RustFS as objects, and a small table describing
# them (where each photo is, its boxes/labels) goes into DuckLake.

import io
import json

import boto3
import duckdb
import pandas as pd
from datasets import load_dataset

N_IMAGES = 60   # keeping this small, see README
BUCKET = "lakehouse"
S3_PREFIX = "assets/coco/images"


# connects to RustFS. RustFS speaks the S3 protocol, so boto3 (normally
# used for real AWS) works here too, just pointed at our local endpoint
def make_s3_client():
    return boto3.client(
        "s3",
        endpoint_url="http://rustfs:9000",
        aws_access_key_id="rustfsadmin",
        aws_secret_access_key="rustfsadmin",
    )


# turns on DuckLake, connects it to RustFS, opens/creates the catalog
# with its raw/silver/gold schemas -- same attach script every time
def attach_lakehouse():
    con = duckdb.connect()
    con.execute(open("sql/00_attach.sql").read())
    return con


# handles one image: uploads it to RustFS, returns a row describing it
def upload_image_and_build_row(s3, index, sample):
    img = sample["image"].convert("RGB")
    objects = sample["objects"]

    # give this image a filename inside the bucket, e.g. "0007.jpg"
    key = f"{S3_PREFIX}/{index:04d}.jpg"

    # turn the PIL image into raw JPEG bytes so we can upload it
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    buf.seek(0)
    s3.put_object(Bucket=BUCKET, Key=key, Body=buf, ContentType="image/jpeg")

    # bbox/segmentation/categories stay as plain JSON strings here --
    # parsing them into real typed columns is silver's job, not raw's
    return {
        "image_uri": f"s3://{BUCKET}/{key}",
        "width": img.width,
        "height": img.height,
        "bbox_json": json.dumps(objects.get("bbox", [])),
        "segmentation_json": json.dumps(objects.get("segmentation", [])),
        "categories_json": json.dumps(objects.get("categories", [])),
    }


def main():
    s3 = make_s3_client()
    con = attach_lakehouse()

    print(f"Streaming {N_IMAGES} images from ariG23498/coco2017 (validation split)...")

    # streaming=True means we don't download the full 20GB dataset,
    # just the images we actually ask for
    ds = load_dataset("ariG23498/coco2017", split="validation", streaming=True)

    rows = []
    for i, sample in enumerate(ds):
        if i >= N_IMAGES:
            break   # got enough, stop early

        rows.append(upload_image_and_build_row(s3, i, sample))

        if (i + 1) % 10 == 0:
            print(f"  ...{i + 1}/{N_IMAGES} images uploaded")

    print(f"Uploaded {len(rows)} images to s3://{BUCKET}/{S3_PREFIX}/")

    # turn our list of dicts into a table and save it into DuckLake --
    # this is the moment raw.coco_annotations actually becomes real
    df = pd.DataFrame(rows)
    con.register("coco_df", df)
    con.execute("CREATE OR REPLACE TABLE raw.coco_annotations AS SELECT * FROM coco_df")

    count = con.sql("SELECT COUNT(*) FROM raw.coco_annotations").fetchone()[0]
    print(f"raw.coco_annotations now has {count} rows")

    # show the last few snapshots -- proof this created a new catalog version
    print("\nMost recent snapshots:")
    con.sql("FROM ducklake_snapshots('lake') ORDER BY snapshot_id DESC LIMIT 5").show()


if __name__ == "__main__":
    main()
