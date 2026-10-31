-- Demo input for gate/cetp_predict_cli.py --query-file
-- TPC-H Q6 (bandwidth-labelled in models/bottleneck_labels.json), SF1.
--
-- NOTE: this file replaces a demo_query.sql that previously sat in the repo root
-- and was deleted in error during the Tier 0 cleanup. If your original differed,
-- restore it over this one.
--
-- Example:
--   python gate/cetp_predict_cli.py --query-file gate/examples/demo_query.sql \
--     --query-id q6 --db tpch --config gate/cetp.yml \
--     --prod-bandwidth 14.9 --prod-compute 0.593
select
    sum(l_extendedprice * l_discount) as revenue
from
    lineitem
where
    l_shipdate >= date '1994-01-01'
    and l_shipdate < date '1994-01-01' + interval '1' year
    and l_discount between 0.06 - 0.01 and 0.06 + 0.01
    and l_quantity < 24;
