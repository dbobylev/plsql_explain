# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**plsql_explain** is a tool for deep analysis of Oracle PL/SQL code. Given a method name, it fetches source from Oracle, parses it with an ANTLR4-based C# binary, builds a dependency graph, and generates hierarchical LLM summaries of all transitive dependencies.

Designed for corporate Windows deployment in closed networks. LLM access is via OpenAI-compatible API. WRAPPED (encrypted) Oracle packages are detected and skipped.

## Setup & Run

```bash
pip install -r requirements.txt
cp .env.example .env  # fill in connection details
```

Required environment variables (`.env`):
```
ORACLE_DSN=host:port/service_name
ORACLE_USER=user
ORACLE_PASSWORD=password
SQLITE_PATH=./data/plsql.db
PLSQL_PARSER_PATH=./plsql_parser/bin/Release/net8.0/PlsqlParser
LLM_BASE_URL=http://corporate-llm/v1
LLM_API_KEY=your_key_here
LLM_MODEL=gpt-4o
```

```bash
# Fetch schema from Oracle into SQLite
python main.py fetch --schema MYSCHEMA [--object pkg_name] [--parse]

# Parse fetched objects with C# ANTLR4 parser (builds call graph)
python main.py parse --schema MYSCHEMA [--object pkg_name] [--force]

# Display dependency tree without LLM
python main.py explain --schema MYSCHEMA --object pkg_name [--subprogram proc_name]

# Generate hierarchical LLM summaries
python main.py summarize --schema MYSCHEMA --object pkg_name [--subprogram proc_name] [--force]
```

## Build the C# Parser

```bash
cd plsql_parser
dotnet build -c Release
```

The binary must exist at `PLSQL_PARSER_PATH` before `parse` or `summarize` commands will work.

## Testing

```bash
pytest                                                        # run all tests
pytest tests/test_traversal_graph.py                          # run single module
pytest tests/test_traversal_graph.py::test_cycle              # run single test
pytest --cov=. --cov-report=term-missing                      # with coverage
```

All tests use in-memory SQLite via the `mem_conn` fixture in `tests/conftest.py`. No Oracle connection or C# binary required for tests.

## Architecture

### 4-Stage Pipeline

All four stages are fully implemented:

**Stage 1 — Source Fetching** (`fetcher/`)
- `oracle_client.py`: Queries Oracle `DBA_SOURCE`, yields `(schema, name, type, full_source)` tuples
- `sqlite_store.py`: Upserts into `object_source` with SHA256 hash; returns `"inserted"/"updated"/"unchanged"`
- `sync.py`: Orchestrates fetch, prints stats

**Stage 2 — Parsing & Indexing** (`parser/`, `indexer/`)
- `parser/runner.py`: Subprocess wrapper — sends JSON to C# binary's stdin, reads JSON from stdout
- `indexer/sync.py`: Hash-based incremental parsing (skips unchanged objects unless `--force`)
- `indexer/sqlite_store.py`: Bulk-replaces `call_edge` and `table_access` rows per object; stores parse status

**C# Parser** (`plsql_parser/`)
- Console app (.NET 8.0): reads `ParseInput` JSON from stdin, writes `ParseOutput` JSON to stdout
- `WrappedDetector.cs`: regex checks first 200 chars for Oracle WRAPPED keyword
- `PlsqlVisitor.cs`: ANTLR4 tree visitor — extracts call graph and table accesses (SELECT/INSERT/UPDATE/DELETE/MERGE), filters known built-in packages (DBMS_OUTPUT, UTL_FILE, etc.), tracks subprogram nesting for package-level attribution

**Stage 3 — Graph Traversal** (`traversal/`)
- `graph.py`: Recursive DFS from root node; cycle detection via `_in_stack` set; returns `DependencyNode` tree with `status` in `{ok, cycle, missing, wrapped, error, unindexed}`
- `sqlite_store.py`: NULL-safe subprogram filtering (NULL = package-level or standalone; non-NULL = named subprogram inside package)

**Stage 4 — LLM Summarization** (`summarizer/`)
- `engine.py`: Post-order DFS (children summarized first); SQLite cache checked by source hash; in-memory `_cache` dict deduplicates diamond dependencies within a single call
- `extractor.py`: State machine (`FIND_HEADER → FIND_IS → COUNT_BEGIN_END`) extracts individual subprogram bodies from package source; falls back to full source
- `prompts.py`: Russian-language prompt builder — includes object name/type, source fragment, table accesses, and child summaries
- `llm_client.py`: Thin wrapper over `openai` library with lazy import (enables test mocking)

### SQLite Schema (`db/schema.sql`)

Five tables: `object_source`, `parse_result`, `call_edge`, `table_access`, `summary`. The `summary` table caches LLM output keyed by `(schema, object, type, subprogram)` with source hash for invalidation.

### Key Design Patterns

- **Hash-based incremental processing**: SHA256 of source text gates fetch, parse, and summarize stages independently
- **Subprocess JSON protocol**: C# binary communicates purely via stdin/stdout JSON — decoupled from Python
- **NULL-aware subprogram identity**: `NULL` subprogram means package-level or standalone object; queries must use `IS NULL` not `= NULL`
- **Post-order DFS with diamond deduplication**: Bottom-up summarization ensures leaves are summarized before their callers; in-memory cache handles shared dependencies
