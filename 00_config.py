# Databricks notebook source
# MAGIC %md
# MAGIC # 00 · Config
# MAGIC Shared names used by every notebook. Run it from others with `%run ./00_config`.
# MAGIC
# MAGIC **Change these to match your workspace.** Table/column names in notebook 01 assume the
# MAGIC layout below; adjust the SQL there if your real `smbc` catalog differs.

# COMMAND ----------

CATALOG = "smbc"

# Source tables (Delta Lake, Unity Catalog)
DQ_SCHEMA = "data_quality"                 # smbc.data_quality.dq_summary / dq_exceptiondetails
CB_SCHEMA = "corporate_banking"            # smbc.corporate_banking.clients / credit_facilities / transactions

# Where the agent's governed tools (UC functions) live -> exposed through managed MCP
TOOLS_SCHEMA = "agent_tools"

# RAG
RAG_SCHEMA = "rag"
PDF_VOLUME = f"/Volumes/{CATALOG}/{RAG_SCHEMA}/pdf_docs"        # upload PDFs here
CHUNKS_TABLE = f"{CATALOG}.{RAG_SCHEMA}.pdf_chunks"
VS_ENDPOINT = "smbc_vs_endpoint"
VS_INDEX = f"{CATALOG}.{RAG_SCHEMA}.pdf_chunks_index"
EMBEDDING_ENDPOINT = "databricks-gte-large-en"

# Model / MLflow
LLM_ENDPOINT = "databricks-claude-sonnet-4-5"
UC_MODEL_NAME = f"{CATALOG}.{TOOLS_SCHEMA}.smbc_multi_agent"
EXPERIMENT_PATH = "/Shared/smbc_multi_agent"

print(f"Catalog={CATALOG} | tools={CATALOG}.{TOOLS_SCHEMA} | index={VS_INDEX} | model={UC_MODEL_NAME}")