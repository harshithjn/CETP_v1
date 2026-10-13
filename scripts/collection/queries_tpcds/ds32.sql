-- TPC-DS query 32, SF1 qualification parameters (dsqgen -QUALIFY Y).
-- Generated from tpcds-kit query_templates/query32.tpl via the postgres
-- dialect (a copy of netezza.tpl, whose `limit %d` form PostgreSQL accepts).
-- Bare `+ N days` rewritten to `+ interval 'N days'`.
select  sum(cs_ext_discount_amt)  as "excess discount amount" 
from 
   catalog_sales 
   ,item 
   ,date_dim
where
i_manufact_id = 722
and i_item_sk = cs_item_sk 
and d_date between '2001-03-09' and 
        (cast('2001-03-09' as date) + interval '90 days')
and d_date_sk = cs_sold_date_sk 
and cs_ext_discount_amt  
     > ( 
         select 
            1.3 * avg(cs_ext_discount_amt) 
         from 
            catalog_sales 
           ,date_dim
         where 
              cs_item_sk = i_item_sk 
          and d_date between '2001-03-09' and
                             (cast('2001-03-09' as date) + interval '90 days')
          and d_date_sk = cs_sold_date_sk 
      ) 
limit 100;
