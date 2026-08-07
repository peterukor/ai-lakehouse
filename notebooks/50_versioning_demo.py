# Demonstrates DuckLake's versioning: time travel by version number and
# by timestamp, comparing two snapshots, and a rollback after a
# deliberately bad transform. Uses silver.coco_annotations, since it's
# already been through a real schema change (the box_area column added
# in 20_silver.sql), so there's a genuine "before/after" to time-travel to.

import duckdb


def attach_lakehouse():
    con = duckdb.connect()
    con.execute(open("sql/00_attach.sql").read())
    return con


# finds the earliest and latest snapshot that touched this table, so we
# don't have to hardcode version numbers that'll differ every time this
# script gets run
def find_table_snapshot_range(con, table_name):
    rows = con.sql(f"""
        SELECT snapshot_id, snapshot_time
        FROM ducklake_snapshots('lake')
        WHERE changes::VARCHAR LIKE '%{table_name}%'
        ORDER BY snapshot_id
    """).fetchall()
    if not rows:
        raise RuntimeError(f"No snapshots found touching {table_name}")
    return rows[0], rows[-1]   # (earliest, latest)


# time travel two ways: by an exact version number, and by a timestamp.
# both should show the SAME older state, just addressed differently
def demo_time_travel(con, earliest_snapshot):
    earliest_id, earliest_time = earliest_snapshot

    print(f"\nTime travel by VERSION (snapshot {earliest_id}):")
    con.sql(f"""
        SELECT * FROM silver.coco_annotations
        AT (VERSION => {earliest_id}) LIMIT 3
    """).show()

    print(f"Time travel by TIMESTAMP ({earliest_time}):")
    con.sql(f"""
        SELECT * FROM silver.coco_annotations
        AT (TIMESTAMP => TIMESTAMP '{earliest_time}') LIMIT 3
    """).show()


# a real, visible difference between two snapshots: the box_area column
# didn't exist yet right when the table was first created, and got added
# a few snapshots later by the ALTER TABLE step in 20_silver.sql. ALTER
# snapshots log a numeric table id, not the table's name, so we can't
# string-search for them directly -- instead we just check a handful of
# snapshots after creation and find the first one where the column count
# actually changes
def compare_snapshots(con, earliest_snapshot):
    create_id, _ = earliest_snapshot

    old_cols = con.sql(f"""
        SELECT COUNT(*) FROM (DESCRIBE SELECT * FROM silver.coco_annotations AT (VERSION => {create_id}))
    """).fetchone()[0]

    print(f"\nColumn count right at creation (snapshot {create_id}): {old_cols}")

    for offset in range(1, 6):
        candidate_id = create_id + offset
        try:
            cols = con.sql(f"""
                SELECT COUNT(*) FROM (DESCRIBE SELECT * FROM silver.coco_annotations AT (VERSION => {candidate_id}))
            """).fetchone()[0]
        except Exception:
            continue   # that version might belong to a different table entirely, skip it

        if cols != old_cols:
            print(f"Column count after schema evolution (snapshot {candidate_id}): {cols}")
            print("(the difference is box_area, added by the ALTER TABLE step in 20_silver.sql)")
            return

    print("(schema evolution snapshot not found in the next 5 -- check ducklake_snapshots manually)")


# deliberately wrecks gold.coco_training, then restores it using time
# travel -- this is the actual "rollback a bad transform" requirement
def demo_rollback(con):
    before_count = con.sql("SELECT COUNT(*) FROM gold.coco_training").fetchone()[0]
    print(f"\ngold.coco_training before the mistake: {before_count} rows")

    # find the snapshot id right before we break things, so we know
    # exactly what version to roll back to afterward
    good_snapshot = con.sql("SELECT max(snapshot_id) FROM ducklake_snapshots('lake')").fetchone()[0]

    print("Deliberately wiping the table (pretend this was an accidental bad transform)...")
    con.execute("DELETE FROM gold.coco_training")

    after_delete = con.sql("SELECT COUNT(*) FROM gold.coco_training").fetchone()[0]
    print(f"gold.coco_training after the mistake: {after_delete} rows  <- uh oh")

    # rollback = read the old version back out via time travel, then
    # replace the current table with it. DuckLake doesn't have a
    # dedicated "rollback" function -- this IS the documented pattern
    print(f"Rolling back to snapshot {good_snapshot}...")
    con.execute(f"""
        CREATE OR REPLACE TABLE gold.coco_training AS
        SELECT * FROM gold.coco_training AT (VERSION => {good_snapshot})
    """)

    after_rollback = con.sql("SELECT COUNT(*) FROM gold.coco_training").fetchone()[0]
    print(f"gold.coco_training after rollback: {after_rollback} rows  <- restored")


def main():
    con = attach_lakehouse()

    earliest, latest = find_table_snapshot_range(con, "silver.coco_annotations")
    print(f"silver.coco_annotations: earliest snapshot {earliest[0]}, latest {latest[0]}")

    demo_time_travel(con, earliest)
    compare_snapshots(con, earliest)
    demo_rollback(con)

    print("\nFull snapshot history after this demo:")
    con.sql("FROM ducklake_snapshots('lake') ORDER BY snapshot_id DESC LIMIT 10").show()


if __name__ == "__main__":
    main()
