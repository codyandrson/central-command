> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Evaluate Zep for Your Use Case

This guide shows you how to use Zep's evaluation harness to systematically test your context implementation.

## Why use the evaluation harness?

With this evaluation harness, you can:

* **Evaluate Zep's performance for your use case**: Test how well Zep retrieves relevant information from conversations, structured data, and documents, and answers questions specific to your domain.
* **Systematically experiment with Zep ontologies, search strategies, and other capabilities**: Compare different configurations to optimize retrieval accuracy and response quality.
* **Develop a suite of tests that can be run in CI**: Continuously evaluate your application for regressions, ensuring that changes to your data model or Zep configuration don't degrade context retrieval performance over time.

The harness provides objective metrics for context completeness and answer accuracy, enabling data-driven decisions about context configuration and search strategies.

## How the harness works

The harness is a local evaluation workflow made up of five scripts you run in sequence (some optional):

1. **`zep_ingest_users.py`** — load conversations and telemetry from `data/` into Zep user graphs
2. **`zep_chunk_documents.py`** *(optional)* — chunk and contextualize files in `data/documents/`
3. **`zep_ingest_documents.py`** *(optional)* — ingest a chunk set into a shared document graph
4. **`zep_evaluate.py`** — run test questions against your ingestion runs and score retrieval
5. **`zep_graph_inspect.py`** *(optional)* — inspect what was extracted into a graph when debugging failures

User graphs and document graphs are ingested **independently**, each producing a numbered run under `runs/` with a config snapshot. At evaluation time, combine any user run with any document run using `--user-run N --doc-run M` without re-ingesting the same data.

**Single-shot retrieval only.** Each test case runs one retrieval against the user graph and optional document graph — using the strategy in `config/evaluation_config/retrieval_strategy.py` — then generates a response in a single turn. There is no query reformulation or multi-turn retrieval. This measures whether your ingestion and retrieval configuration surfaces the right facts for a well-formed query — not how a tool-using agent might search over multiple turns. Treat strong harness results as a prerequisite, then evaluate your full agent separately if it exposes Zep through tools.

## Steps

### Clone the Zep repository

Clone the [Zep repository](https://github.com/getzep/zep/tree/main) that includes the evaluation harness:

```bash
git clone https://github.com/getzep/zep.git
cd zep/zep-eval-harness
```

### Set up your environment

Install UV package manager for macOS/Linux:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

For other platforms, visit the [UV installation guide](https://docs.astral.sh/uv/).

Install all required dependencies using UV:

```bash
uv sync
```

Set up your API keys by copying the example file and adding your keys:

```bash
cp .env.example .env
```

Edit `.env` and add your keys:

```bash
ZEP_API_KEY=your-zep-api-key
GOOGLE_API_KEY=your-google-api-key
```

Get your Zep API key at [app.getzep.com](https://app.getzep.com) and Google API key at [aistudio.google.com/app/api-keys](https://aistudio.google.com/app/api-keys). The Google key is used for document chunking and LLM-based evaluation.

### Prepare evaluation data

**Most important step**: Your evaluation is only as good as your test data. Define what kinds of questions your agent should answer, prepare source material that contains those answers, then generate test cases grounded in that material.

The harness evaluates retrieval against test questions in `data/test_cases/`. Those questions are answered from source material in one or more of these locations:

| Data type        | Location              | Ingested into         |
| ---------------- | --------------------- | --------------------- |
| Conversations    | `data/conversations/` | User graph            |
| Telemetry (JSON) | `data/telemetry/`     | User graph            |
| Documents        | `data/documents/`     | Shared document graph |

Whether your source material is real, anonymized, or synthetic:

1. **Define evaluation intent** — what kinds of questions should your agent answer?
2. **Prepare source material** — use existing data, or generate synthetic data designed to support those question types
3. **Generate test cases following best practices** — questions and golden answers grounded in your source material
4. **Review** — validate test cases against the best practices and your evaluation intent

If you already have source material, skip generating ingestion files in step 2 and go to step 3.

#### 1. Define evaluation intent

Start by describing the **types of questions** your agent should be able to answer when it has access to the data — not necessarily the full test suite yet. Use natural language categories, seed examples, or both:

```
Question types we care about:
- Basic facts the user stated in conversation (preferences, names, dates)
- Information buried in reference documents (policies, product specs)
- Questions that require combining a conversation fact with a document
- Questions scoped to a specific timeframe or instance when similar topics appear multiple times

Seed examples:
- "What is my dog's name?" → "Max"
- "When is my vet appointment?" → "Friday at Dr. Peterson's clinic"
```

This intent spec drives everything downstream — what scenarios to include in synthetic source material and what question types to cover in test cases.

#### 2. Prepare source material

**If you already have data** (conversation logs, telemetry exports, documents — real or anonymized), place it in the appropriate `data/` directories and curate if needed. You do not need to generate ingestion files. Skip to step 3.

**If you are bootstrapping with synthetic data**, generate source material after defining intent in step 1, so the conversations and documents contain the scenarios you plan to evaluate. Place conversation JSON in `data/conversations/`, optional JSON in `data/telemetry/`, and documents in `data/documents/`. Spread facts across multiple conversations rather than one thread.

Each conversation, telemetry, and test case file is keyed to a `user_id` from `data/users.json` (for example, `zep_eval_test_user_001_conv_001.json` and `zep_eval_test_user_001_tests.json`). When starting from the sample harness, keep that `user_id` consistent across all files. To use a different user, update `data/users.json` and rename all related files to match.

#### 3. Generate test cases following best practices

Once source material exists, have a coding agent (or write manually) produce `data/test_cases/zep_eval_test_user_001_tests.json` by reading the actual files. Each test case needs an `id`, `category`, `query`, and `golden_answer`. The `category` should map back to your intent from step 1 (for example, `basic_facts`, `cross_document`, or `temporal_reasoning`) so you can see which scenario types perform well or poorly.

Add a `needles` array to record where each answer lives — source file, line, and excerpt.

**Do not generate test cases before source material exists.** Questions written in a vacuum tend to assume facts that aren't in the data, which produces unfair tests.

When writing test cases, follow these practices:

* **Ensure answer availability**: The answer to every test question must appear somewhere in the ingested data — conversations, telemetry, or documents. Tests are unfair when they expect the agent to answer questions about information that was never provided.
* **Write clear golden answers**: Keep each golden answer specific and concise, focused on the key information a correct response must contain. For "What is my dog's name?", prefer "Your dog's name is Max." over a verbose restatement of the whole adoption story.
* **Write unambiguous test questions**: Specific questions produce consistent, reliable results. "What did I request?" is ambiguous when several requests were discussed; "When is my vet appointment?" is not.
* **Consider context and scope**: When the history contains multiple similar topics, specify the context a question needs — a timeframe, location, or instance — so that only one answer is correct.

#### 4. Review before ingestion

Before running ingestion, review your test cases against the best practices from step 3:

* **Answer availability**: Every `golden_answer` is supported by text in your conversations, telemetry, or documents. Use `needles` to verify.
* **Clear golden answers**: Each golden answer is specific and concise — focused on the key information required, not a verbose restatement.
* **Unambiguous questions**: Each question has one clear answer given the source material.
* **Context and scope**: Questions that could match multiple facts in the data specify enough context (timeframe, location, instance) to disambiguate.
* **Intent coverage**: Your test suite exercises the question types you defined in step 1 — not just the easiest facts in the data.

If source material or test cases were generated by an AI, manually review several random test cases before ingestion to catch hallucinated answers, ambiguous phrasing, or missing context.

### Ingest user data

Load conversations and telemetry into Zep user graphs:

```bash
uv run zep_ingest_users.py
```

User ingestion creates a numbered run directory under `runs/users/` (for example, `runs/users/1_20260331T222436/`) containing a manifest with created users, thread IDs, and a snapshot of the ingestion config.

For ingestion with custom ontology, instructions, or user summary instructions:

```bash
uv run zep_ingest_users.py --custom-ontology --custom-instructions --user-summary-instructions
```

Use `--no-poll` to skip waiting for graph processing, or `--graphs zep_eval_test_user_001` to ingest a specific user only.

### Ingest documents (optional)

If your evaluation includes documents in `data/documents/`, ingest them into a shared document graph. Document ingestion is a two-step pipeline:

1. **Chunk documents** — split files and generate summaries and contextualizations:

```bash
uv run zep_chunk_documents.py
```

This writes a chunk set to `runs/chunk_sets/{N}_{timestamp}/`.

2. **Ingest chunks into Zep**:

```bash
uv run zep_ingest_documents.py --chunk-set 1
```

Document ingestion creates a run under `runs/documents/`. You can reuse a chunk set across multiple ingestion runs with different ontology or instruction configurations. Skip this section entirely if your test cases only require conversation or telemetry data.

For ingestion with a custom ontology or instructions:

```bash
uv run zep_ingest_documents.py --chunk-set 1 --custom-ontology --custom-instructions
```

### Wait for graph processing to complete

After ingestion completes, Zep needs time to process episodes and extract facts, entities, and relationships from your data.

**Processing time**: Roughly 5–20 seconds per message or document chunk. You can monitor processing status in the Zep dashboard. By default, the ingestion scripts poll until processing completes; use `--no-poll` to skip waiting.

### Run the evaluation script

Execute the evaluation pipeline against your ingestion runs:

```bash
# Evaluate the latest user ingestion run
uv run zep_evaluate.py

# Evaluate a specific user run
uv run zep_evaluate.py --user-run 1

# Include a document graph in search
uv run zep_evaluate.py --user-run 1 --doc-run 1
```

The script processes each test question through four automated steps:

1. **Search**: Build a context block from the user graph and optional document graph using `build_context_block()` in `config/evaluation_config/retrieval_strategy.py`. The default uses [auto search](/searching-the-graph#auto-search) (`scope="auto"`) with a character budget, returning a ready-to-use context string across facts, entities, episodes, and observations
2. **Evaluate context**: Assess whether the retrieved information is sufficient to answer the test question (produces the primary metric: COMPLETE, PARTIAL, or INSUFFICIENT)
3. **Generate response**: Use an LLM with the retrieved context to generate an answer
4. **Grade answer**: Evaluate the generated response against the golden answer using an LLM judge (produces the secondary metric: CORRECT or WRONG)

Retrieval is fully configurable — edit `build_context_block()` to match how your application assembles context. [Auto search](/searching-the-graph#auto-search) is the recommended default; you can also compose scoped searches (for example, facts-only) or any other combination your use case needs. See [Advanced Context Block construction](/advanced-context-block-construction) for manual assembly patterns.

The context completeness evaluation (step 2) is the primary metric as it measures Zep's core capability: retrieving relevant information. The answer grading (step 4) is secondary since it also depends on the LLM's ability to use that context.

### Interpret your results

Results live at `runs/evaluations/{N}_{timestamp}/results.json`. Each file references its parent user and document ingestion runs in `parent_runs`, snapshots the active retrieval strategy in `search_configuration`, and records the evaluation config used. Read `aggregate_scores`, `category_scores`, and `user_scores` for the headline numbers, then drill into `detailed_results` for per-test context, grades, and judge reasoning.

The harness produces two distinct measures:

* **Context completeness (primary)** — Did Zep retrieve sufficient information to answer the question? (COMPLETE, PARTIAL, or INSUFFICIENT). This is Zep's job. Focus here when comparing runs or tuning configuration.
* **Answer accuracy (secondary)** — Did the LLM produce a correct answer from the retrieved context? (CORRECT or WRONG). This depends on the response model and prompt in `config/evaluation_config/response_prompt.py`, not on Zep's retrieval.

Use the breakdowns to localize problems:

* **Per-category breakdown** — Completeness and accuracy grouped by each test case's `category` field.
* **Per-user breakdown** — Metrics for each user in a multi-user evaluation.
* **Correlation analysis** — How completeness and accuracy relate across the test set.

#### Diagnose with completeness and accuracy

Use the two measures together:

| Pattern                                 | Likely cause           | What to fix                                                                         |
| --------------------------------------- | ---------------------- | ----------------------------------------------------------------------------------- |
| Completeness **high**, accuracy **low** | Response generation    | `response_prompt.py`, LLM model, or how your agent uses context — not Zep retrieval |
| Completeness **low**                    | Ingestion or retrieval | Follow the localization steps below                                                 |

When completeness is low, work through where the information stops:

1. **Not in the source data** — The fact was never in your conversations, telemetry, or documents. Fix your test data or what your application sends to Zep.
2. **In the source data but not in the graph** — Zep received the data but extraction missed it. Tune ingestion: custom instructions, ontology, or how you pre-process ambiguous data before ingest. Use `zep_graph_inspect.py` to see what was extracted.
3. **In the graph but not in the retrieved context** — Extraction worked; search did not surface the fact. Tune your retrieval strategy in `config/evaluation_config/retrieval_strategy.py` — for example, adjust the auto-search character budget, or switch to a different search composition. See [Searching the Graph](/searching-the-graph#auto-search).

For each failed test, read `detailed_results` — the retrieved `context`, completeness reasoning (including `missing_elements` and `present_elements`), generated answer, and judge reasoning — before changing configuration.

#### Compare runs

To compare ingestion or retrieval configurations, evaluate different `--user-run` / `--doc-run` pairings without re-ingesting, or change `retrieval_strategy.py` and re-run evaluation. Each run directory includes config snapshots — ingestion config in the parent runs and the active retrieval strategy in `search_configuration` inside `results.json` — so you can tie results back to the exact ontology, instructions, and retrieval approach in use.

Present comparisons as a table — rows are runs or configs, columns are completeness and accuracy (aggregate and per-category). **Focus on completeness differences** when deciding which configuration is better. Accuracy is still worth tracking, but it can move independently of Zep when you change the response model or prompt.

Treat large score gaps as stronger signal than small ones. A small test set gives weaker confidence in the numbers — expand test cases before drawing hard conclusions.

Both metrics describe **single-shot retrieval** in the harness (see the callout above). State that limitation when reporting results. If production exposes Zep through tools rather than deterministic retrieval, run a separate end-to-end evaluation of the agent path.

### Review results and iterate

For each missed question:

1. Confirm the source data contains the answer and the `golden_answer` is clear and specific
2. Read the per-test entry in `detailed_results` to see what context was retrieved
3. Apply the diagnostic steps above — source data, graph extraction, or search
4. Adjust source material, test questions, ingestion config, or retrieval strategy as needed

Use `zep_graph_inspect.py` to inspect what Zep extracted into a graph:

```bash
# Inspect a user graph (use the full zep_user_id from runs/users/*/manifest.json)
uv run zep_graph_inspect.py --user zep_eval_test_user_001_a7390b47

# Inspect the document graph (use graph_id from runs/documents/*/manifest.json)
uv run zep_graph_inspect.py --graph zep_eval_shared_documents_f1a2b3c4
```

Iterate by modifying your data files or config, then re-run the relevant ingestion and evaluation scripts.

## Next steps

Once you have the basic evaluation working, consider these next steps:

* **Add more test cases and source material**: Expand your test set with additional questions and source files to cover more edge cases and retrieval scenarios.

* **Evaluate Zep's performance with your existing agent**: After validating retrieval with the evaluation harness, integrate Zep into your agent and evaluate end-to-end performance. Create test cases based on conversations from your application to reflect your usage patterns.

* **Define a custom ontology for your domain**: Create entity and edge types tailored to your specific use case for better Context Graph structure and retrieval. User and document graphs have separate ontology files under `config/user_ingestion_config/` and `config/document_ingestion_config/`. See [customizing graph structure](/customizing-graph-structure), then run ingestion with the appropriate flags:

```bash
uv run zep_ingest_users.py --custom-ontology
uv run zep_ingest_documents.py --chunk-set 1 --custom-ontology
```

* **Add background data**: Ingest a larger volume of conversations, telemetry, or documents before your test material to evaluate retrieval when relevant information is buried in a larger graph.

* **Compare ingestion configurations**: User and document graphs are ingested independently, so you can test different ontology or instruction combinations without re-ingesting everything. Evaluate any pairing at runtime with `--user-run N --doc-run M`. Each run directory includes a config snapshot for reproducibility.

* **Tune retrieval strategy**: Retrieval behavior lives in `config/evaluation_config/retrieval_strategy.py`. The default `build_context_block()` uses [auto search](/searching-the-graph#auto-search) with a character budget and prepends the user-node summary. Edit this function to match how your application retrieves context — for example, a facts-only scoped search, manual multi-scope assembly, or a different auto-search budget. Each evaluation run records the active strategy in `search_configuration` inside `results.json`. See [Advanced Context Block construction](/advanced-context-block-construction) for manual assembly patterns.

* **Add custom instructions**: Beyond ontologies, user and document graphs support separate custom instructions that guide fact extraction. Enable them during ingestion:

```bash
uv run zep_ingest_users.py --custom-instructions --user-summary-instructions
uv run zep_ingest_documents.py --chunk-set 1 --custom-instructions
```

Edit `config/user_ingestion_config/custom_instructions.py`, `config/user_ingestion_config/user_summary_instructions.py`, and `config/document_ingestion_config/custom_instructions.py`.

* **Customize the evaluation response prompt**: The system prompt used when generating answers during evaluation lives in `config/evaluation_config/response_prompt.py`. Edit `get_response_system_prompt()` to match your agent's persona or response style.