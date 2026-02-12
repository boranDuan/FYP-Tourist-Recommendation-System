-- Step0 人口适配缓存：POI 表增加两列（仅当列不存在时添加，可重复执行）
-- must-visit 解析缓存表

SET @db = DATABASE();

SET @add_children = (SELECT COUNT(*) = 0 FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'poi' AND COLUMN_NAME = 'suitable_for_children');
SET @sql_children = IF(@add_children,
  'ALTER TABLE poi ADD COLUMN suitable_for_children TINYINT(1) NULL DEFAULT NULL COMMENT ''Step0 缓存：1=适合儿童, 0=不适合, NULL=未计算''',
  'SELECT 1');
PREPARE stmt FROM @sql_children;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @add_seniors = (SELECT COUNT(*) = 0 FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'poi' AND COLUMN_NAME = 'suitable_for_seniors');
SET @sql_seniors = IF(@add_seniors,
  'ALTER TABLE poi ADD COLUMN suitable_for_seniors TINYINT(1) NULL DEFAULT NULL COMMENT ''Step0 缓存：1=适合老人, 0=不适合, NULL=未计算''',
  'SELECT 1');
PREPARE stmt FROM @sql_seniors;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

CREATE TABLE IF NOT EXISTS must_visit_cache (
  id INT AUTO_INCREMENT PRIMARY KEY,
  poi_id INT NULL,
  user_input VARCHAR(255) NOT NULL,
  resolved_name VARCHAR(255) NULL,
  google_place_id VARCHAR(128) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX ix_user_input (user_input),
  CONSTRAINT fk_must_visit_cache_poi FOREIGN KEY (poi_id) REFERENCES poi(poi_id) ON DELETE CASCADE
);
