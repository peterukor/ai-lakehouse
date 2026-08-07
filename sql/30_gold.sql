-- Builds the curated "gold" tables: one ML-ready table per dataset, plus
-- the VisDrone fragment index that lets us query "which fragments are
-- busy" without touching every frame.


-- COCO gold: one row per image (not per object like silver), with a
-- comma-joined label list and a synthetic train/val split. Real COCO has
-- real splits, but we only pulled from "validation", so we assign our
-- own 80/20 split here just so the table has something to demo on.
CREATE OR REPLACE TABLE gold.coco_training AS
SELECT
    image_uri,
    image_width,
    image_height,
    STRING_AGG(DISTINCT category_name, ', ') AS labels,
    COUNT(*) AS n_objects,
    CASE WHEN ROW_NUMBER() OVER (ORDER BY image_uri) % 5 = 0
         THEN 'val' ELSE 'train' END AS split
FROM silver.coco_annotations
GROUP BY image_uri, image_width, image_height;


-- VisDrone gold: same idea, one row per image, ML-ready
CREATE OR REPLACE TABLE gold.visdrone_training AS
SELECT
    image_uri,
    scene_id,
    frame_number,
    image_width,
    image_height,
    STRING_AGG(DISTINCT class_name, ', ') AS labels,
    COUNT(*) AS n_objects,
    CASE WHEN ROW_NUMBER() OVER (ORDER BY image_uri) % 5 = 0
         THEN 'val' ELSE 'train' END AS split
FROM silver.visdrone_detections
GROUP BY image_uri, scene_id, frame_number, image_width, image_height;


-- The fragment index. Each synthetic 10-frame "clip" gets split into two
-- 5-frame fragments, so there's real variance to query against (a clip
-- can have one busy fragment and one quiet one). image_uris is the list
-- of frames that actually belong to that fragment -- this is what lets a
-- downstream query fetch ONLY those specific objects from RustFS instead
-- of scanning the whole clip.
CREATE OR REPLACE TABLE gold.visdrone_fragment_index AS
WITH per_frame AS (
    SELECT
        image_uri,
        scene_id,
        frame_number,
        SUM(n_objects) AS n_objects,          -- reuse counts already computed in gold.visdrone_training
        STRING_AGG(DISTINCT labels, ', ') AS classes
    FROM gold.visdrone_training
    GROUP BY image_uri, scene_id, frame_number
),
fragmented AS (
    SELECT
        *,
        -- frame_number is 0-9 within each synthetic clip; splitting on
        -- whether it's in the first or second half gives 2 fragments/clip
        scene_id || '-frag-' || (frame_number // 5) AS fragment_id
    FROM per_frame
)
SELECT
    fragment_id,
    scene_id AS clip_id,
    MIN(frame_number) AS start_frame,
    MAX(frame_number) AS end_frame,
    COUNT(*) AS n_frames,
    SUM(n_objects) AS n_objects,
    STRING_AGG(DISTINCT classes, ', ') AS classes,
    LIST(image_uri ORDER BY frame_number) AS image_uris   -- the actual frames to fetch for this fragment
FROM fragmented
GROUP BY fragment_id, scene_id
ORDER BY fragment_id;
