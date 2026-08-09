-- Turns the JSON blobs sitting in raw into real, typed tables, one row
-- per detected object instead of one row per image. This is the "clean
-- up types, handle missing data" step of the medallion pattern.


-- COCO category ids have gaps in the numbering (not 1-80 in a row), so
-- we need a real lookup table to turn ids into readable names like "dog"
CREATE OR REPLACE TEMP TABLE coco_category_names AS
SELECT * FROM (VALUES
    (1,'person'),(2,'bicycle'),(3,'car'),(4,'motorcycle'),(5,'airplane'),(6,'bus'),
    (7,'train'),(8,'truck'),(9,'boat'),(10,'traffic light'),(11,'fire hydrant'),
    (13,'stop sign'),(14,'parking meter'),(15,'bench'),(16,'bird'),(17,'cat'),
    (18,'dog'),(19,'horse'),(20,'sheep'),(21,'cow'),(22,'elephant'),(23,'bear'),
    (24,'zebra'),(25,'giraffe'),(27,'backpack'),(28,'umbrella'),(31,'handbag'),
    (32,'tie'),(33,'suitcase'),(34,'frisbee'),(35,'skis'),(36,'snowboard'),
    (37,'sports ball'),(38,'kite'),(39,'baseball bat'),(40,'baseball glove'),
    (41,'skateboard'),(42,'surfboard'),(43,'tennis racket'),(44,'bottle'),
    (46,'wine glass'),(47,'cup'),(48,'fork'),(49,'knife'),(50,'spoon'),(51,'bowl'),
    (52,'banana'),(53,'apple'),(54,'sandwich'),(55,'orange'),(56,'broccoli'),
    (57,'carrot'),(58,'hot dog'),(59,'pizza'),(60,'donut'),(61,'cake'),(62,'chair'),
    (63,'couch'),(64,'potted plant'),(65,'bed'),(67,'dining table'),(70,'toilet'),
    (72,'tv'),(73,'laptop'),(74,'mouse'),(75,'remote'),(76,'keyboard'),
    (77,'cell phone'),(78,'microwave'),(79,'oven'),(80,'toaster'),(81,'sink'),
    (82,'refrigerator'),(84,'book'),(85,'clock'),(86,'vase'),(87,'scissors'),
    (88,'teddy bear'),(89,'hair drier'),(90,'toothbrush')
) AS t(category_id, category_name);


-- COCO: unnest the bbox/category JSON arrays into one row per object,
-- then join in real category names instead of leaving raw numeric ids
CREATE OR REPLACE TABLE silver.coco_annotations AS
WITH exploded AS (
    -- unnest zips the two lists together by position (1st bbox pairs
    -- with 1st category, 2nd with 2nd, etc), since they describe the
    -- same list of objects in the original image
    SELECT
        image_uri,
        width  AS image_width,
        height AS image_height,
        unnest(categories_json::JSON::INTEGER[]) AS category_id,
        unnest(bbox_json::JSON::DOUBLE[][])       AS bbox
    FROM raw.coco_annotations
    WHERE categories_json != '[]'   -- skip images with zero detected objects
)
SELECT
    e.image_uri,
    e.image_width,
    e.image_height,
    e.category_id,
    COALESCE(c.category_name, 'unknown') AS category_name,
    e.bbox[1] AS bbox_x,
    e.bbox[2] AS bbox_y,
    e.bbox[3] AS bbox_width,
    e.bbox[4] AS bbox_height
FROM exploded e
LEFT JOIN coco_category_names c USING (category_id)
-- dedupe defensively -- if this script gets run twice on the same raw
-- data, this keeps one row per (image, object) instead of doubling up
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY e.image_uri, e.category_id, e.bbox
    ORDER BY e.image_uri
) = 1;


-- VisDrone: same idea, unnest the detections JSON into one row per object.
--
-- NOTE: this schema changed from the original YOLO-based ingestion. Real
-- VisDrone-MOT data (via fiftyone) gives detections as { label, bounding_box,
-- track_id, occlusion, visibility } instead of the old YOLO-style
-- { class_id, class_name, x_center, y_center, width, height }. Two things
-- are genuinely different, not just renamed:
--   1. There's no numeric class_id anymore, only the string label.
--   2. bounding_box is [top_left_x, top_left_y, width, height], all
--      normalized 0-1 -- NOT center-based like the old x_center/y_center
--      were. Anything downstream that assumed center coordinates would
--      need to account for this; nothing currently does.
-- track_id is new: it's a real identity that follows the same object
-- across frames within a scene (VisDrone-MOT is tracking data), which
-- the old static YOLO detections never had.
CREATE OR REPLACE TABLE silver.visdrone_detections AS
SELECT DISTINCT
    r.image_uri,
    r.scene_id,
    r.frame_number,
    r.width  AS image_width,
    r.height AS image_height,
    d.label AS class_name,
    d.track_id,
    d.bounding_box[1] AS bbox_x,       -- top-left x, normalized 0-1
    d.bounding_box[2] AS bbox_y,       -- top-left y, normalized 0-1
    d.bounding_box[3] AS bbox_width,   -- normalized 0-1
    d.bounding_box[4] AS bbox_height,  -- normalized 0-1
    d.occlusion,
    d.visibility
FROM raw.visdrone_frames r,
UNNEST(
    detections_json::JSON::STRUCT(
        label VARCHAR, bounding_box DOUBLE[], track_id INTEGER,
        occlusion INTEGER, visibility INTEGER
    )[]
) AS t(d)
WHERE r.n_detections > 0;   -- skip frames with zero detections


-- schema evolution demo: adding a column here isn't needed for the data
-- to work, it's here so a schema CHANGE shows up as its own new snapshot
ALTER TABLE silver.coco_annotations ADD COLUMN box_area DOUBLE;
UPDATE silver.coco_annotations SET box_area = bbox_width * bbox_height;
