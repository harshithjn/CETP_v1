-- TPC-DS query 55, SF1 qualification parameters (dsqgen -QUALIFY Y).
-- Generated from tpcds-kit query_templates/query55.tpl via the postgres
-- dialect (a copy of netezza.tpl, whose `limit %d` form PostgreSQL accepts).
-- Bare `+ N days` rewritten to `+ interval 'N days'`.
select  i_brand_id brand_id, i_brand brand,
 	sum(ss_ext_sales_price) ext_price
 from date_dim, store_sales, item
 where d_date_sk = ss_sold_date_sk
 	and ss_item_sk = i_item_sk
 	and i_manager_id=52
 	and d_moy=11
 	and d_year=2000
 group by i_brand, i_brand_id
 order by ext_price desc, i_brand_id
limit 100 ;
