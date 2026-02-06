-- 为 trip_preferences 表添加 interests_other 列
-- 用法：mysql -u root -p tourist_recommend < scripts/add_interests_other.sql

USE tourist_recommend;

ALTER TABLE trip_preferences ADD COLUMN interests_other TEXT NULL AFTER interests;
