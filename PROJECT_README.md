# Project 1 — SMBC Multi-Agent Assistant on Databricks (LangGraph + MCP + MLflow)

A simple, interview-friendly multi-agent system:

| Piece | Technology | File |
|---|---|---|
| Orchestration (supervisor + 2 agents) | LangGraph `StateGraph` (sync, no asyncio) | `notebooks/agent.py` |
| **Agent 1** – SQL / Delta tables | Unity Catalog functions exposed via **Databricks managed MCP** | `01_setup_tables_and_mcp_tools.py` |
| **Agent 2** – RAG over PDFs | UC Volume → chunks → **Vector Search** Delta Sync index | `02_pdf_ingestion_vector_search.py` |
| Guardrails | input (injection, destructive SQL, PII) + output (PII mask, groundedness self-check) + AI Gateway | `agent.py` |
| Human-in-the-loop | `interrupt()` + checkpointer (notebook) / trace flag + Review App (serving) | `03_build_graph_step_by_step.py` |
| Observability | `mlflow.langchain.autolog()` traces every node, tool call, retrieval | all |
| Evaluation | `mlflow.genai.evaluate` — Groundedness, Correctness, fact accuracy, relevance, safety, routing | `04_evaluate_and_hypertune.py` |
| Hyper-tuning | grid search over temperature × top_k × LLM, nested MLflow runs | `04_evaluate_and_hypertune.py` |
| Deployment | models-from-code → UC registry → `agents.deploy()` | `05_log_register_deploy.py` |

## Architecture

```
            ┌──────────────────────── MLflow Tracing (every step) ────────────────────────┐
User ──► input_guardrail ──► supervisor ─┬─► sql_agent ──MCP──► UC functions ──► Delta tables
                │ blocked                │        (dq_summary, dq_exceptiondetails, clients,
                ▼                        │         credit_facilities, transactions)
               END                       └─► rag_agent ──► Vector Search ◄── PDF chunks (Volume)
                                                   │
                                     synthesizer ◄─┘
                                          │
                                 output_guardrail (PII mask, groundedness judge, credit-decision rule)
                                          │
                              review needed? ──yes──► human_review (interrupt / flag) ──► END
                                          │ no
                                          ▼
                                         END
```

### Interview talk track (60 seconds)
1. **Why multi-agent?** Structured questions need exact numbers (SQL); policy questions need documents (RAG). A supervisor LLM routes to one or both, and a synthesizer merges.
2. **Why MCP?** Tools are discovered at runtime from a standard protocol. We expose *governed, read-only, parameterised* UC functions — the LLM never writes raw SQL, and Unity Catalog permissions + lineage apply.
3. **Guardrails** on both sides: regex input checks (prompt injection, `DROP/DELETE`), PII masking, a runtime groundedness judge, and a business rule — the AI never makes credit decisions alone. AI Gateway adds platform-level safety/PII filters.
4. **HITL:** risky or low-groundedness answers pause with `interrupt()`; a human approves/edits/rejects and the graph resumes from its checkpoint. In serving, they're flagged in the trace and reviewed in the Review App.
5. **Quality loop:** golden dataset → `mlflow.genai.evaluate` (groundedness, correctness, safety…) → grid-search the config → deploy the winner → same scorers monitor production traces.

---

## Run order

| # | Notebook | What it does |
|---|---|---|
| 0 | `00_config` | names of catalog, schemas, index, model |
| 1 | `01_setup_tables_and_mcp_tools` | (optional sample data), creates 6 UC functions, tests them via MCP |
| 2 | `02_pdf_ingestion_vector_search` | PDFs → chunks Delta table → Vector Search index |
| 3 | `03_build_graph_step_by_step` | builds the graph cell by cell (state, nodes, edges, compile, stream, state history), guardrail + HITL demos |
| 4 | `04_evaluate_and_hypertune` | evaluation + grid search, writes best `agent_config.yaml` |
| 5 | `05_log_register_deploy` | logs, validates, registers in UC, deploys |

---

## Step-by-step in the Databricks UI

### A. Prerequisites
1. Workspace with **Unity Catalog**, **Serverless** compute, **Mosaic AI Model Serving**, **Vector Search** and **Foundation Model APIs** enabled (Admin → Previews: enable *Managed MCP Servers* if it is still listed as a preview).
2. Permissions: `USE CATALOG` on `smbc`, `CREATE SCHEMA/FUNCTION/TABLE/VOLUME` in it, `CREATE` on Vector Search endpoints, `CAN MANAGE` on Serving.

### B. Import the code
1. Left sidebar → **Workspace** → your user folder → **⋮ → Import** → upload the zip, *or* **Create → Git folder** and paste your repo URL.
2. Keep `agent.py`, `agent_config.yaml` and the notebooks **in the same folder** (`agent.py` is imported as a workspace file) and `requirements.txt` one level up.

### C. Compute
* Attach notebooks to **Serverless** (environment version 3+), or a classic cluster with **DBR 15.4 LTS ML+**. Each notebook installs its own libraries with `%pip`.

### D. Data + tools
1. Open `00_config` and set your catalog/schema names.
2. Run `01_setup_tables_and_mcp_tools`. Set widget *Create sample data?* = `yes` only for a demo workspace.
3. Verify in **Catalog → smbc → agent_tools → Functions**: six functions with comments.
4. (Optional UI check) **Catalog → agent_tools → Agents/MCP tab** shows the MCP server URL for the schema. You can also try the tools in **Playground → Tools → Add tool → UC function / MCP server**.

### E. PDFs → Vector Search
1. **Catalog → smbc → rag → Volumes → pdf_docs → Upload to this volume** — add your policy PDFs.
2. Run `02_pdf_ingestion_vector_search`.
3. Check **Compute → Vector Search → smbc_vs_endpoint** → index status *Online*; Catalog Explorer shows the index with a *Sync now* button.

### F. Try, evaluate, tune
1. Run `03_build_graph_step_by_step`: under each cell expand the **MLflow Trace** — you see every node, tool call and retrieval with latency and tokens.
2. Run `04_…`: open **Experiments → /Shared/smbc_multi_agent**.
   * **Evaluations / Traces tab** — per-question judge results with rationales (groundedness, correctness…).
   * **Runs** — parent run `hypertune_grid` with one nested run per config; sort by `objective`; use **Compare** to chart params vs metrics.

### G. Deploy (UI view of what notebook 05 does)
1. Run `05_log_register_deploy`.
2. **Catalog → smbc → agent_tools → Models → smbc_multi_agent** — the new version with alias `@champion`, lineage to tables/functions/index.
3. **Serving** (left sidebar) → endpoint `agents_smbc-agent_tools-smbc_multi_agent` → wait for **Ready** (~10-20 min).
   * **Use → Query** tab: send `{"input":[{"role":"user","content":"Show C001 facilities"}]}`.
   * **Edit AI Gateway** → enable **Inference tables**, **Usage tracking**, **Rate limits**, and **AI Guardrails** (safety filter + PII *Block/Mask* for input and output) — platform-level guardrails on top of the code-level ones.
4. **Review App** (link printed by `agents.deploy`): SMEs chat with the agent and give thumbs-up/down + corrections; their labels land as **assessments on traces** and can be added to the evaluation dataset.
5. **Monitoring:** Experiment → **Traces** filtered by `tags.needs_human_review = 'true'` shows answers awaiting a human; production scorers (notebook 04 §6) show groundedness/safety trends.

### H. Manual deployment from the UI (alternative to `agents.deploy`)
**Serving → Create serving endpoint** → *Served entity*: `smbc.agent_tools.smbc_multi_agent`, version = champion → *Compute*: CPU, Small, scale-to-zero ✓ → **Create**. (`agents.deploy` is preferred because it also wires tracing, Review App and feedback.)

### I. Update / rollback
Re-run 04 → 05 to register a new version; set alias `champion` on the version you want and re-run `agents.deploy` (old version keeps serving until the new one is ready). Roll back by deploying the previous version.

---

## Production hardening (good follow-up answers)
* Durable HITL: replace `MemorySaver` with a Lakebase/Postgres LangGraph checkpointer; route flagged answers to a Delta table + Databricks App/Slack approval.
* Row-level security & column masks on `clients` so the agent only sees what the caller may see (On-behalf-of-user auth).
* Add Genie space as a third MCP server for ad-hoc analytics; keep UC functions for audited queries.
* CI/CD with **Databricks Asset Bundles**: `databricks bundle deploy -t prod` running notebooks 04→05 as a job, with an evaluation gate (fail if groundedness < 0.8).
