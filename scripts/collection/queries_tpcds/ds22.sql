-- TPC-DS query 22, SF1 qualification parameters (dsqgen -QUALIFY Y).
-- Generated from tpcds-kit query_templates/query22.tpl via the postgres
-- dialect (a copy of netezza.tpl, whose `limit %d` form PostgreSQL accepts).
-- Bare `+ N days` rewritten to `+ interval 'N days'`.
select  i_product_name
             ,i_brand
             ,i_class
             ,i_category
             ,avg(inv_quantity_on_hand) qoh
       from inventory
           ,date_dim
           ,item
       where inv_date_sk=d_date_sk
              and inv_item_sk=i_item_sk
              and d_month_seq between 1200 and 1200 + 11
       group by rollup(i_product_name
                       ,i_brand
                       ,i_class
                       ,i_category)
order by qoh, i_product_name, i_brand, i_class, i_category
limit 100;
