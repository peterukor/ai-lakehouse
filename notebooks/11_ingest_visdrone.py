# Pulls VisDrone images and real bounding boxes from Hugging Face and lands them in raw.
# Uses banu4prasad/VisDrone-Dataset (YOLO format, real boxes) instead of the
# FiftyOne-packaged version, which turned out to have no usable metadata -- see README.
#
# Note: these are static images, not real video frames (VisDrone-VID is Google-Drive-only,
# no direct link -- see README). scene_id/frame_number below are SYNTHETIC, grouped by us
# every 10 images, just so there's something to build the fragment-index pattern on later.

import io
import json

import boto3
import duckdb
import pandas as pd
from huggingface_hub import HfApi, hf_hub_download
from PIL import Image

N_IMAGES = 60
BUCKET = "lakehouse"
S3_PREFIX = "assets/visdrone/images"
REPO_ID = "banu4prasad/VisDrone-Dataset"
SPLIT_DIR = "VisDrone2019-DET-val"   # smallest split, 548 images
FRAMES_PER_CLIP = 10                  # synthetic clip size, see note above

CLASS_NAMES = [
    "pedestrian", "people", "bicycle", "car", "van", "truck",
    "tricycle", "awning-tricycle", "bus", "motor", "others",
]


# connects to RustFS the same way the COCO script does
def make_s3_client():
    return boto3.client(
        "s3",
        endpoint_url="http://rustfs:9000",
        aws_access_key_id="rustfsadmin",
        aws_secret_access_key="rustfsadmin",
    )


# turns on DuckLake, connects it to RustFS, opens the catalog
def attach_lakehouse():
    con = duckdb.connect()
    con.execute(open("sql/00_attach.sql").read())
    return con


# this repo has no dataset script or parquet conversion, so we just list
# the raw files ourselves and filter down to the split/folder we want
def list_image_files(api):
    all_files = api.list_repo_files(REPO_ID, repo_type="dataset")
    images = sorted(
        f for f in all_files
        if f.startswith(f"{SPLIT_DIR}/images/") and f.endswith(".jpg")
    )
    return images[:N_IMAGES]


# YOLO format: one line per object, "class_id x_center y_center width height",
# all normalized 0-1 relative to image size (not pixel coordinates)
def parse_yolo_label(label_path):
    detections = []
    try:
        with open(label_path) as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) != 5:
                    continue   # skip malformed/blank lines instead of crashing
                class_id = int(parts[0])
                x, y, w, h = map(float, parts[1:])
                detections.append({
                    "class_id": class_id,
                    "class_name": CLASS_NAMES[class_id] if class_id < len(CLASS_NAMES) else "unknown",
                    "x_center": x,
                    "y_center": y,
                    "width": w,
                    "height": h,
                })
    except FileNotFoundError:
        pass   # some images just don't have a label file, that's fine
    return detections


# downloads one image + its matching label file, uploads the image to
# RustFS, and returns a row describing it (with the real detections)
def upload_image_and_build_row(s3, index, image_repo_path):
    local_image_path = hf_hub_download(REPO_ID, image_repo_path, repo_type="dataset")
    img = Image.open(local_image_path).convert("RGB")

    # the matching label file lives in the same position under labels/
    # instead of images/, same filename but .txt instead of .jpg
    label_repo_path = image_repo_path.replace("/images/", "/labels/").replace(".jpg", ".txt")
    try:
        local_label_path = hf_hub_download(REPO_ID, label_repo_path, repo_type="dataset")
        detections = parse_yolo_label(local_label_path)
    except Exception:
        detections = []   # no matching label file found for this image

    key = f"{S3_PREFIX}/{index:04d}.jpg"
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    buf.seek(0)
    s3.put_object(Bucket=BUCKET, Key=key, Body=buf, ContentType="image/jpeg")

    # synthetic clip grouping -- see the note at the top of this file
    scene_id = f"synthetic-clip-{index // FRAMES_PER_CLIP:03d}"
    frame_number = index % FRAMES_PER_CLIP

    return {
        "image_uri": f"s3://{BUCKET}/{key}",
        "width": img.width,
        "height": img.height,
        "scene_id": scene_id,
        "frame_number": frame_number,
        "n_detections": len(detections),
        "detections_json": json.dumps(detections),
    }


def main():
    s3 = make_s3_client()
    con = attach_lakehouse()
    api = HfApi()

    print(f"Finding {N_IMAGES} images in {REPO_ID}/{SPLIT_DIR}...")
    image_files = list_image_files(api)
    print(f"Found {len(image_files)} images to pull")

    rows = []
    for i, image_path in enumerate(image_files):
        rows.append(upload_image_and_build_row(s3, i, image_path))

        if (i + 1) % 10 == 0:
            print(f"  ...{i + 1}/{len(image_files)} frames uploaded")

    print(f"Uploaded {len(rows)} frames to s3://{BUCKET}/{S3_PREFIX}/")

    df = pd.DataFrame(rows)
    con.register("visdrone_df", df)
    con.execute("CREATE OR REPLACE TABLE raw.visdrone_frames AS SELECT * FROM visdrone_df")

    count = con.sql("SELECT COUNT(*) FROM raw.visdrone_frames").fetchone()[0]
    total_detections = con.sql("SELECT SUM(n_detections) FROM raw.visdrone_frames").fetchone()[0]
    print(f"raw.visdrone_frames now has {count} rows, {total_detections} total real detections")

    # frames per synthetic scene -- matters later for the fragment index
    print("\nFrames per (synthetic) scene:")
    con.sql("SELECT scene_id, COUNT(*) AS n_frames, SUM(n_detections) AS n_detections FROM raw.visdrone_frames GROUP BY scene_id ORDER BY scene_id").show()

    print("\nMost recent snapshots:")
    con.sql("FROM ducklake_snapshots('lake') ORDER BY snapshot_id DESC LIMIT 5").show()


if __name__ == "__main__":
    main()
