-- Input view period_hours is explicitly restricted to the requested historical
-- interval. Every record represents one node and one fully observed hour.
WITH totals AS (
  SELECT node_id, name, function_type, weekday, hour,
         COUNT(*) AS observed_days,
         SUM(borrows) AS completed_borrows,
         SUM(returns) AS completed_returns,
         AVG(borrows) AS mean_borrows,
         AVG(returns) AS mean_returns,
         AVG(borrows - returns) AS mean_net_outflow
  FROM period_hours
  GROUP BY node_id, name, function_type, weekday, hour
)
SELECT *,
       ROW_NUMBER() OVER (
         PARTITION BY weekday, hour
         ORDER BY mean_borrows DESC, node_id
       ) AS borrow_rank,
       ROW_NUMBER() OVER (
         PARTITION BY weekday, hour
         ORDER BY mean_net_outflow DESC, node_id
       ) AS outflow_rank,
       ROW_NUMBER() OVER (
         PARTITION BY weekday, hour
         ORDER BY mean_net_outflow ASC, node_id
       ) AS inflow_rank
FROM totals
