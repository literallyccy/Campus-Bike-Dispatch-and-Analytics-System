-- 修改这一个查询后，重新运行 src/lesson04_preprocessing.py。
-- SQL 查询 clean_rides（清洗后的订单表）。
SELECT start_zone, COUNT(*) AS departures
FROM clean_rides
GROUP BY start_zone
ORDER BY departures DESC, start_zone ASC;
