# Demonstrates the two required queries: COCO's crowded-scenes metadata
# query, and the VisDrone "give me the busy fragments" query -- the real
# point of this second one is proving we fetch ONLY the selected
# fragments' images from RustFS, not the whole dataset.

import duckdb
import boto3

BUCKET = "lakehouse"
TOP_N_FRAGMENTS = 3   # how many busiest fragments to actually fetch


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


# COCO metadata query -- pure SQL over silver, no image bytes touched at all.
# Threshold is low on purpose since our sample is only 60 images, real COCO
# would use a higher bar (the assignment's own example uses >= 5)
def run_coco_crowded_scenes_query(con, min_count=2):
    print(f"COCO images with >= {min_count} people detected:")
    result = con.sql(f"""
        SELECT image_uri, COUNT(*) AS n_people
        FROM silver.coco_annotations
        WHERE category_name = 'person'
        GROUP BY image_uri
        HAVING COUNT(*) >= {min_count}
        ORDER BY n_people DESC
    """)
    result.show()
    if result.fetchone() is None:
        print(f"(no images hit the >= {min_count} threshold in this small sample)")


# picks the busiest VisDrone fragments by object count, straight from the
# gold fragment index -- this is pure SQL too, still no bytes touched yet
def pick_busiest_fragments(con):
    print(f"\nTop {TOP_N_FRAGMENTS} busiest VisDrone fragments:")
    fragments = con.sql(f"""
        SELECT fragment_id, clip_id, n_frames, n_objects, image_uris
        FROM gold.visdrone_fragment_index
        ORDER BY n_objects DESC
        LIMIT {TOP_N_FRAGMENTS}
    """).fetchall()

    for fragment_id, clip_id, n_frames, n_objects, image_uris in fragments:
        print(f"  {fragment_id}: {n_objects} objects across {n_frames} frames")

    return fragments


# this is the part that actually proves selectivity: fetch ONLY the
# frames belonging to the chosen fragments from RustFS, and compare that
# count against the total number of images that exist in the bucket
def fetch_selected_fragments(s3, con, fragments):
    # s3://lakehouse/assets/visdrone/images/0000.jpg -> assets/visdrone/images/0000.jpg
    def uri_to_key(uri):
        return uri.replace(f"s3://{BUCKET}/", "")

    fetched_bytes_total = 0
    fetched_count = 0
    for fragment_id, clip_id, n_frames, n_objects, image_uris in fragments:
        for uri in image_uris:
            obj = s3.get_object(Bucket=BUCKET, Key=uri_to_key(uri))
            fetched_bytes_total += obj["ContentLength"]
            fetched_count += 1

    total_images_in_bucket = con.sql(
        "SELECT COUNT(*) FROM raw.visdrone_frames"
    ).fetchone()[0]

    print(f"\nActually fetched {fetched_count} images ({fetched_bytes_total} bytes)")
    print(f"Total VisDrone images in the bucket: {total_images_in_bucket}")
    print(f"-> only touched {fetched_count}/{total_images_in_bucket} images "
          f"({100 * fetched_count / total_images_in_bucket:.0f}%), not the whole dataset")


def main():
    con = attach_lakehouse()
    s3 = make_s3_client()

    run_coco_crowded_scenes_query(con)

    fragments = pick_busiest_fragments(con)
    fetch_selected_fragments(s3, con, fragments)


if __name__ == "__main__":
    main()
