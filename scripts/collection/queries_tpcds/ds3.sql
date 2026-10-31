-- TPC-DS query 3, SF1 qualification parameters (dsqgen -QUALIFY Y).
-- Generated from tpcds-kit query_templates/query3.tpl via the postgres
-- dialect (a copy of netezza.tpl, whose `limit %d` form PostgreSQL accepts).
-- Bare `+ N days` rewritten to `+ interval 'N days'`.
select  dt.d_year 
       ,item.i_brand_id brand_id 
       ,item.i_brand brand
       ,sum(ss_sales_price) sum_agg
 from  date_dim dt 
      ,store_sales
      ,item
 where dt.d_date_sk = store_sales.ss_sold_date_sk
   and store_sales.ss_item_sk = item.i_item_sk
   and item.i_manufact_id = 816
   and dt.d_moy=11
 group by dt.d_year
      ,item.i_brand
      ,item.i_brand_id
 order by dt.d_year
         ,sum_agg desc
         ,brand_id
 limit 100;
