# Databricks notebook source
# MAGIC %md
# MAGIC # 01 · Tables + MCP tools for Agent 1 (SQL / Delta agent)
# MAGIC
# MAGIC **Idea to explain in the interview:** the LLM never writes free-form SQL.
# MAGIC We publish a small set of *read-only, parameterised* **Unity Catalog functions**. Databricks
# MAGIC exposes every function in a schema as an **MCP tool** at
# MAGIC `https://<workspace>/api/2.0/mcp/functions/<catalog>/<schema>`.
# MAGIC The agent discovers these tools over MCP, and Unity Catalog permissions govern who can run them.
# MAGIC
# MAGIC Steps: (1) optional sample data → (2) create UC functions → (3) test them through MCP.

# COMMAND ----------

# MAGIC %pip install -U databricks-mcp databricks-sdk mlflow

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %run ./00_config

# COMMAND ----------

dbutils.widgets.dropdown("create_sample_data", "no", ["yes", "no"], "Create sample data?")
CREATE_SAMPLE = dbutils.widgets.get("create_sample_data") == "yes"

for schema in [DQ_SCHEMA, CB_SCHEMA, TOOLS_SCHEMA, RAG_SCHEMA]:
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{schema}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. (Optional) Sample data — only for demo workspaces
# MAGIC Skip this if the real `smbc` tables already exist. Column names below are the ones the
# MAGIC UC functions expect; if your real columns differ, edit the functions in step 2.

# COMMAND ----------

if CREATE_SAMPLE:
    spark.sql(f"""
    CREATE OR REPLACE TABLE {CATALOG}.{CB_SCHEMA}.clients AS SELECT * FROM VALUES
      ('C001','Tokyo Precision Mfg','Manufacturing','JP','A-','Sato K.','2019-04-01','Verified'),
      ('C002','Pacific Shipping Ltd','Logistics','SG','BBB','Lee M.','2020-07-15','Verified'),
      ('C003','Hudson Retail Group','Retail','US','BB+','Smith J.','2021-02-10','Pending Review'),
      ('C004','Nordic Energy AS','Energy','NO','A','Hansen P.','2018-11-20','Verified')
      AS t(client_id, client_name, industry, country, risk_rating, relationship_manager, onboarding_date, kyc_status)
    """)
    spark.sql(f"""
    CREATE OR REPLACE TABLE {CATALOG}.{CB_SCHEMA}.credit_facilities AS SELECT * FROM VALUES
      ('F100','C001','Revolving Credit', 50000000.0, 32000000.0,'USD','2023-01-01','2027-12-31','Active'),
      ('F101','C001','Term Loan',        20000000.0, 18500000.0,'USD','2022-06-01','2029-06-01','Active'),
      ('F200','C002','Trade Finance',    15000000.0, 14700000.0,'USD','2024-03-01','2026-12-31','Active'),
      ('F300','C003','Revolving Credit', 10000000.0,  9900000.0,'USD','2023-09-01','2026-09-01','Watchlist'),
      ('F400','C004','Project Finance', 120000000.0, 60000000.0,'EUR','2021-01-01','2033-01-01','Active')
      AS t(facility_id, client_id, facility_type, limit_amount, outstanding_amount, currency, start_date, maturity_date, status)
    """)
    spark.sql(f"""
    CREATE OR REPLACE TABLE {CATALOG}.{CB_SCHEMA}.transactions AS SELECT * FROM VALUES
      ('T1','C001','F100', current_date() - 3,  'Drawdown',  5000000.0,'USD','Self','Settled'),
      ('T2','C001','F100', current_date() - 10, 'Repayment', 2000000.0,'USD','Self','Settled'),
      ('T3','C002','F200', current_date() - 1,  'LC Issuance',3000000.0,'USD','Maersk','Pending'),
      ('T4','C003','F300', current_date() - 5,  'Drawdown',  1500000.0,'USD','Self','Settled'),
      ('T5','C004','F400', current_date() - 20, 'Drawdown', 10000000.0,'EUR','Self','Settled')
      AS t(txn_id, client_id, facility_id, txn_date, txn_type, amount, currency, counterparty, status)
    """)
    spark.sql(f"""
    CREATE OR REPLACE TABLE {CATALOG}.{DQ_SCHEMA}.dq_summary AS SELECT * FROM VALUES
      ('R1', current_date(), 'clients',           'kyc_status_not_null', 'Completeness', 4, 0, 100.0, 'PASS'),
      ('R1', current_date(), 'credit_facilities', 'outstanding_le_limit','Validity',     5, 0, 100.0, 'PASS'),
      ('R1', current_date(), 'transactions',      'counterparty_valid',  'Validity',     5, 1,  80.0, 'FAIL'),
      ('R1', current_date(), 'transactions',      'currency_iso_code',   'Conformity',   5, 0, 100.0, 'PASS')
      AS t(run_id, run_date, table_name, rule_name, dq_dimension, records_checked, records_failed, pass_rate, status)
    """)
    spark.sql(f"""
    CREATE OR REPLACE TABLE {CATALOG}.{DQ_SCHEMA}.dq_exceptiondetails AS SELECT * FROM VALUES
      ('E1','R1','transactions','counterparty_valid','counterparty','T1','Self',
       'Counterparty must be an external legal entity for drawdowns','HIGH', current_timestamp())
      AS t(exception_id, run_id, table_name, rule_name, column_name, record_key, failed_value, error_message, severity, created_at)
    """)
    print("Sample data created.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Unity Catalog functions = the agent's tools
# MAGIC * Read-only `SELECT`s only, with parameters (no SQL injection, no free SQL).
# MAGIC * `COMMENT`s matter: they become the **tool descriptions** the LLM reads via MCP.
# MAGIC * `LIMIT` keeps responses small (cost + context window guardrail).

# COMMAND ----------

T = f"{CATALOG}.{TOOLS_SCHEMA}"
DQ = f"{CATALOG}.{DQ_SCHEMA}"
CB = f"{CATALOG}.{CB_SCHEMA}"

spark.sql(f"""
CREATE OR REPLACE FUNCTION {T}.get_dq_summary(p_table_name STRING COMMENT 'Table name, e.g. transactions, or ALL')
RETURNS TABLE (run_id STRING, run_date DATE, table_name STRING, rule_name STRING, dq_dimension STRING,
               records_checked BIGINT, records_failed BIGINT, pass_rate DOUBLE, status STRING)
COMMENT 'Latest data-quality rule results (pass rate, failures, PASS/FAIL) per table from dq_summary.'
RETURN SELECT run_id, CAST(run_date AS DATE), table_name, rule_name, dq_dimension,
              CAST(records_checked AS BIGINT), CAST(records_failed AS BIGINT), CAST(pass_rate AS DOUBLE), status
       FROM {DQ}.dq_summary
       WHERE upper(p_table_name) = 'ALL' OR lower(table_name) = lower(p_table_name)
       ORDER BY run_date DESC LIMIT 100
""")

spark.sql(f"""
CREATE OR REPLACE FUNCTION {T}.get_dq_exceptions(
    p_table_name STRING COMMENT 'Table name or ALL',
    p_severity   STRING COMMENT 'HIGH, MEDIUM, LOW or ALL')
RETURNS TABLE (exception_id STRING, run_id STRING, table_name STRING, rule_name STRING, column_name STRING,
               record_key STRING, failed_value STRING, error_message STRING, severity STRING)
COMMENT 'Row-level data-quality exceptions (which record failed which rule and why) from dq_exceptiondetails.'
RETURN SELECT exception_id, run_id, table_name, rule_name, column_name, record_key,
              CAST(failed_value AS STRING), error_message, severity
       FROM {DQ}.dq_exceptiondetails
       WHERE (upper(p_table_name) = 'ALL' OR lower(table_name) = lower(p_table_name))
         AND (upper(p_severity) = 'ALL' OR upper(severity) = upper(p_severity))
       LIMIT 100
""")

spark.sql(f"""
CREATE OR REPLACE FUNCTION {T}.get_client_profile(p_client STRING COMMENT 'Client ID (e.g. C001) or part of the client name')
RETURNS TABLE (client_id STRING, client_name STRING, industry STRING, country STRING, risk_rating STRING,
               relationship_manager STRING, kyc_status STRING)
COMMENT 'Look up a corporate client by ID or name: industry, country, internal risk rating, RM and KYC status.'
RETURN SELECT client_id, client_name, industry, country, risk_rating, relationship_manager, kyc_status
       FROM {CB}.clients
       WHERE client_id = p_client OR lower(client_name) LIKE concat('%', lower(p_client), '%')
       LIMIT 20
""")

spark.sql(f"""
CREATE OR REPLACE FUNCTION {T}.get_client_facilities(p_client_id STRING COMMENT 'Client ID, e.g. C001')
RETURNS TABLE (facility_id STRING, facility_type STRING, limit_amount DOUBLE, outstanding_amount DOUBLE,
               utilization_pct DOUBLE, currency STRING, maturity_date DATE, status STRING)
COMMENT 'Credit facilities for a client with limit, outstanding, utilization % and status.'
RETURN SELECT facility_id, facility_type, CAST(limit_amount AS DOUBLE), CAST(outstanding_amount AS DOUBLE),
              round(outstanding_amount / limit_amount * 100, 2), currency, CAST(maturity_date AS DATE), status
       FROM {CB}.credit_facilities
       WHERE client_id = p_client_id
""")

spark.sql(f"""
CREATE OR REPLACE FUNCTION {T}.get_client_transactions(
    p_client_id STRING COMMENT 'Client ID, e.g. C001',
    p_days      INT    COMMENT 'Look-back window in days, e.g. 30')
RETURNS TABLE (txn_id STRING, facility_id STRING, txn_date DATE, txn_type STRING, amount DOUBLE,
               currency STRING, counterparty STRING, status STRING)
COMMENT 'Recent transactions for a client in the last N days.'
RETURN SELECT txn_id, facility_id, CAST(txn_date AS DATE), txn_type, CAST(amount AS DOUBLE), currency, counterparty, status
       FROM {CB}.transactions
       WHERE client_id = p_client_id AND txn_date >= date_sub(current_date(), p_days)
       ORDER BY txn_date DESC LIMIT 200
""")

spark.sql(f"""
CREATE OR REPLACE FUNCTION {T}.get_portfolio_exposure()
RETURNS TABLE (industry STRING, currency STRING, total_limit DOUBLE, total_outstanding DOUBLE,
               utilization_pct DOUBLE, facilities BIGINT)
COMMENT 'Portfolio-level exposure by industry and currency (limits, outstanding, utilization).'
RETURN SELECT c.industry, f.currency, sum(f.limit_amount), sum(f.outstanding_amount),
              round(sum(f.outstanding_amount) / sum(f.limit_amount) * 100, 2), count(*)
       FROM {CB}.credit_facilities f JOIN {CB}.clients c ON f.client_id = c.client_id
       GROUP BY c.industry, f.currency
""")

spark.sql(f"USE CATALOG {CATALOG}")
display(spark.sql(f"SHOW USER FUNCTIONS IN {T}"))

# COMMAND ----------

# MAGIC %md
# MAGIC ### Quick SQL test (same functions the agent will call)

# COMMAND ----------

display(spark.sql(f"SELECT * FROM {T}.get_client_facilities('C001')"))
display(spark.sql(f"SELECT * FROM {T}.get_dq_summary('ALL')"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Test the tools **through MCP** (exactly what the agent does)

# COMMAND ----------

import nest_asyncio
nest_asyncio.apply()

from databricks.sdk import WorkspaceClient
from databricks_mcp import DatabricksMCPClient

ws = WorkspaceClient()
mcp_url = f"{ws.config.host}/api/2.0/mcp/functions/{CATALOG}/{TOOLS_SCHEMA}"
mcp_client = DatabricksMCPClient(server_url=mcp_url, workspace_client=ws)

tools = mcp_client.list_tools()
for t in tools:
    print(f"🔧 {t.name}\n   {t.description}\n   args: {list(t.input_schema.get('properties', {}).keys())}")

# COMMAND ----------

profile_tool = next(t.name for t in tools if t.name.endswith("get_client_profile"))
result = mcp_client.call_tool(profile_tool, {"p_client": "C001"})
print("".join(c.text for c in result.content if hasattr(c, "text")))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Governance (who may use the tools)
# MAGIC Grant the serving principal / user group EXECUTE on the functions and SELECT on the tables.

# COMMAND ----------

# Example — replace with your group / service principal
# spark.sql(f"GRANT USE CATALOG ON CATALOG {CATALOG} TO `smbc-ai-agents`")
# spark.sql(f"GRANT USE SCHEMA, EXECUTE ON SCHEMA {T} TO `smbc-ai-agents`")
# spark.sql(f"GRANT SELECT ON SCHEMA {CB} TO `smbc-ai-agents`")
# spark.sql(f"GRANT SELECT ON SCHEMA {DQ} TO `smbc-ai-agents`")