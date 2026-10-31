-- TPC-H SF1 schema, dumped from the reference cluster with pg_dump --schema-only.
-- pg_hint_plan statements are intentionally absent: that extension belongs to the
-- author's local cluster, is not part of the TPC-H schema, and is unavailable in the
-- stock postgres image -- where it aborted the whole load under ON_ERROR_STOP.
-- Plan forcing (scripts/collection/plan_to_hints.py) needs it; loading data does not.
--
-- PostgreSQL database dump
--

\restrict JOvS9jWOQzEYWlWEw6EeoBNZmHX1pnAbaucDCfhza7CNP60Nz9aBPZN79irIKDq

-- Dumped from database version 15.17 (Homebrew)
-- Dumped by pg_dump version 15.17 (Homebrew)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: hint_plan; Type: SCHEMA; Schema: -; Owner: -
--



--
-- Name: pg_hint_plan; Type: EXTENSION; Schema: -; Owner: -
--



SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: customer; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer (
    c_custkey bigint NOT NULL,
    c_name character varying NOT NULL,
    c_address character varying NOT NULL,
    c_nationkey integer NOT NULL,
    c_phone character varying NOT NULL,
    c_acctbal numeric(15,2) NOT NULL,
    c_mktsegment character varying NOT NULL,
    c_comment character varying NOT NULL
);


--
-- Name: lineitem; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.lineitem (
    l_orderkey bigint NOT NULL,
    l_partkey bigint NOT NULL,
    l_suppkey bigint NOT NULL,
    l_linenumber bigint NOT NULL,
    l_quantity numeric(15,2) NOT NULL,
    l_extendedprice numeric(15,2) NOT NULL,
    l_discount numeric(15,2) NOT NULL,
    l_tax numeric(15,2) NOT NULL,
    l_returnflag character varying NOT NULL,
    l_linestatus character varying NOT NULL,
    l_shipdate date NOT NULL,
    l_commitdate date NOT NULL,
    l_receiptdate date NOT NULL,
    l_shipinstruct character varying NOT NULL,
    l_shipmode character varying NOT NULL,
    l_comment character varying NOT NULL
);


--
-- Name: nation; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.nation (
    n_nationkey integer NOT NULL,
    n_name character varying NOT NULL,
    n_regionkey integer NOT NULL,
    n_comment character varying
);


--
-- Name: orders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.orders (
    o_orderkey bigint NOT NULL,
    o_custkey bigint NOT NULL,
    o_orderstatus character varying NOT NULL,
    o_totalprice numeric(15,2) NOT NULL,
    o_orderdate date NOT NULL,
    o_orderpriority character varying NOT NULL,
    o_clerk character varying NOT NULL,
    o_shippriority integer NOT NULL,
    o_comment character varying NOT NULL
);


--
-- Name: part; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.part (
    p_partkey bigint NOT NULL,
    p_name character varying NOT NULL,
    p_mfgr character varying NOT NULL,
    p_brand character varying NOT NULL,
    p_type character varying NOT NULL,
    p_size integer NOT NULL,
    p_container character varying NOT NULL,
    p_retailprice numeric(15,2) NOT NULL,
    p_comment character varying NOT NULL
);


--
-- Name: partsupp; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.partsupp (
    ps_partkey bigint NOT NULL,
    ps_suppkey bigint NOT NULL,
    ps_availqty bigint NOT NULL,
    ps_supplycost numeric(15,2) NOT NULL,
    ps_comment character varying NOT NULL
);


--
-- Name: region; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.region (
    r_regionkey integer NOT NULL,
    r_name character varying NOT NULL,
    r_comment character varying
);


--
-- Name: supplier; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.supplier (
    s_suppkey bigint NOT NULL,
    s_name character varying NOT NULL,
    s_address character varying NOT NULL,
    s_nationkey integer NOT NULL,
    s_phone character varying NOT NULL,
    s_acctbal numeric(15,2) NOT NULL,
    s_comment character varying NOT NULL
);


--
-- Name: customer customer_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer
    ADD CONSTRAINT customer_pkey PRIMARY KEY (c_custkey);


--
-- Name: lineitem lineitem_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lineitem
    ADD CONSTRAINT lineitem_pkey PRIMARY KEY (l_orderkey, l_linenumber);


--
-- Name: nation nation_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.nation
    ADD CONSTRAINT nation_pkey PRIMARY KEY (n_nationkey);


--
-- Name: orders orders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_pkey PRIMARY KEY (o_orderkey);


--
-- Name: part part_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part
    ADD CONSTRAINT part_pkey PRIMARY KEY (p_partkey);


--
-- Name: partsupp partsupp_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.partsupp
    ADD CONSTRAINT partsupp_pkey PRIMARY KEY (ps_partkey, ps_suppkey);


--
-- Name: region region_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.region
    ADD CONSTRAINT region_pkey PRIMARY KEY (r_regionkey);


--
-- Name: supplier supplier_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supplier
    ADD CONSTRAINT supplier_pkey PRIMARY KEY (s_suppkey);


--
-- Name: idx_customer_nationkey; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_customer_nationkey ON public.customer USING btree (c_nationkey);


--
-- Name: idx_lineitem_orderkey; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_lineitem_orderkey ON public.lineitem USING btree (l_orderkey);


--
-- Name: idx_lineitem_partkey; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_lineitem_partkey ON public.lineitem USING btree (l_partkey);


--
-- Name: idx_lineitem_suppkey; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_lineitem_suppkey ON public.lineitem USING btree (l_suppkey);


--
-- Name: idx_nation_regionkey; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_nation_regionkey ON public.nation USING btree (n_regionkey);


--
-- Name: idx_orders_custkey; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_orders_custkey ON public.orders USING btree (o_custkey);


--
-- Name: idx_partsupp_suppkey; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_partsupp_suppkey ON public.partsupp USING btree (ps_suppkey);


--
-- Name: idx_supplier_nationkey; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_supplier_nationkey ON public.supplier USING btree (s_nationkey);


--
-- PostgreSQL database dump complete
--

\unrestrict JOvS9jWOQzEYWlWEw6EeoBNZmHX1pnAbaucDCfhza7CNP60Nz9aBPZN79irIKDq

