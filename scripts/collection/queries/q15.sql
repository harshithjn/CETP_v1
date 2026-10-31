-- TPC-H Q15. Dropped from the original collection because the spec form needs
-- CREATE VIEW / DROP VIEW around the query, which the single-statement
-- EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) harness could not express.
--
-- Rewritten as a CTE, which is a single statement and semantically identical.
-- NOT MATERIALIZED is deliberate: a view is inlined at each reference, and this
-- CTE is referenced twice, so without it PostgreSQL would materialise once and
-- share the result -- a different plan from the spec's view form. See
-- results/q15_resolution.md for the measured difference between the two.
with revenue0 (supplier_no, total_revenue) as not materialized (
  select l_suppkey, sum(l_extendedprice * (1 - l_discount))
  from lineitem
  where l_shipdate >= date '1996-01-01'
    and l_shipdate < date '1996-01-01' + interval '3' month
  group by l_suppkey
)
select s_suppkey, s_name, s_address, s_phone, total_revenue
from supplier, revenue0
where s_suppkey = supplier_no
  and total_revenue = (select max(total_revenue) from revenue0)
order by s_suppkey;
