-- PostgreSQL compatibility: inlined 2 `lochierarchy` reference(s).
-- TPC-DS query 36, SF1 qualification parameters (dsqgen -QUALIFY Y).
-- Generated from tpcds-kit query_templates/query36.tpl via the postgres
-- dialect (a copy of netezza.tpl, whose `limit %d` form PostgreSQL accepts).
-- Bare `+ N days` rewritten to `+ interval 'N days'`.
select  
    sum(ss_net_profit)/sum(ss_ext_sales_price) as gross_margin
   ,i_category
   ,i_class
   ,grouping(i_category)+grouping(i_class) as lochierarchy
   ,rank() over (
 	partition by grouping(i_category)+grouping(i_class),
 	case when grouping(i_class) = 0 then i_category end 
 	order by sum(ss_net_profit)/sum(ss_ext_sales_price) asc) as rank_within_parent
 from
    store_sales
   ,date_dim       d1
   ,item
   ,store
 where
    d1.d_year = 2000 
 and d1.d_date_sk = ss_sold_date_sk
 and i_item_sk  = ss_item_sk 
 and s_store_sk  = ss_store_sk
 and s_state in ('TN','TN','TN','TN',
                 'TN','TN','TN','TN')
 group by rollup(i_category,i_class)
 order by
   (grouping(i_category)+grouping(i_class)) desc
  ,case when (grouping(i_category)+grouping(i_class)) = 0 then i_category end
  ,rank_within_parent
  limit 100;
