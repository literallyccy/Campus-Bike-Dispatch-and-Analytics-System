-- Facility summaries describe completed-order patterns, not stockout risk.
SELECT function_type, weekday, hour,
       COUNT(DISTINCT node_id) AS node_count,
       SUM(completed_borrows) AS completed_borrows,
       SUM(completed_returns) AS completed_returns,
       SUM(mean_borrows) AS mean_borrows_all_nodes,
       SUM(mean_returns) AS mean_returns_all_nodes
FROM node_patterns
GROUP BY function_type, weekday, hour
