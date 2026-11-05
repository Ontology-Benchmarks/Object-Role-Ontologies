-- ORO counterexample sets for the original Q1-Q4 (Q4 = clos(Offer, Returned)), run from the repo root; mirroring the SPARQL pattern templates in
-- src/ocel_inference/models.py (CREATION/CLOSURE_PATTERN_COUNTEREXAMPLE_QUERY) and the
-- exactly_one scenario query in src/ocel_inference/benchmark.py.

ATTACH 'input/data/logistics/ContainerLogistics.duckdb' AS cl (READ_ONLY);
ATTACH 'input/data/bpi17/bpi17_ocel.duckdb' AS bpi (READ_ONLY);

-- Q1 init(Forklift, Weigh): Weigh events of a forklift later than its earliest event.
COPY (
  WITH ev AS (SELECT x.object_id, e.eid, e.activity, e.timestamp
              FROM cl.log_e2o x JOIN cl.log_events e USING (eid)
              WHERE lower(x.object_type) = 'forklift')
  SELECT DISTINCT w.object_id AS obj, w.eid AS event
  FROM ev w
  WHERE lower(w.activity) = 'weigh'
    AND w.timestamp > (SELECT min(timestamp) FROM ev f WHERE f.object_id = w.object_id)
  ORDER BY 1, 2
) TO 'experiments/results/baselines/oro_counterexamples/Q1.csv' (HEADER);

-- Q2: offers without exactly one of Accepted/Cancelled/Refused.
COPY (
  SELECT o.object_id AS obj, count(DISTINCT e.activity) AS n
  FROM bpi.objects o
  LEFT JOIN bpi.log_e2o x ON x.object_id = o.object_id
  LEFT JOIN bpi.log_events e ON e.eid = x.eid
       AND e.activity IN ('Accepted_complete', 'Cancelled_complete', 'Refused_complete')
  WHERE lower(o.object_type) = 'offer'
  GROUP BY o.object_id
  HAVING count(DISTINCT e.activity) != 1
  ORDER BY 1
) TO 'experiments/results/baselines/oro_counterexamples/Q2.csv' (HEADER);

-- Q3/Q4 clos(Offer, a): a-events of an offer earlier than its latest event.
COPY (
  WITH ev AS (SELECT x.object_id, e.eid, e.activity, e.timestamp
              FROM bpi.log_e2o x JOIN bpi.log_events e USING (eid)
              WHERE lower(x.object_type) = 'offer')
  SELECT DISTINCT s.object_id AS obj, s.eid AS event
  FROM ev s
  WHERE s.activity = 'Sent (mail and online)_complete'
    AND s.timestamp < (SELECT max(timestamp) FROM ev l WHERE l.object_id = s.object_id)
  ORDER BY 1, 2
) TO 'experiments/results/baselines/oro_counterexamples/Q3.csv' (HEADER);

COPY (
  WITH ev AS (SELECT x.object_id, e.eid, e.activity, e.timestamp
              FROM bpi.log_e2o x JOIN bpi.log_events e USING (eid)
              WHERE lower(x.object_type) = 'offer')
  SELECT DISTINCT s.object_id AS obj, s.eid AS event
  FROM ev s
  WHERE s.activity = 'Returned_complete'
    AND s.timestamp < (SELECT max(timestamp) FROM ev l WHERE l.object_id = s.object_id)
  ORDER BY 1, 2
) TO 'experiments/results/baselines/oro_counterexamples/Q4.csv' (HEADER);
