# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**plsql_explain** is a tool for deep analysis of Oracle PL/SQL code. Given a method name, it generates a hierarchical text description of the method's logic including all transitive dependencies, table access patterns, and control flow.

The system is a multi-stage pipeline — only Stage 1 (source fetching) is currently implemented. Stages 2–4 (ANTLR4 parsing, dependency graph traversal, LLM summarization) are planned.

## Setup & Run

```bash
pip install -r requirements.txt
cp .env.example .env  # fill in Oracle connection details
```

Required environment variables (`.env`):
```
ORACLE_DSN=host:port/service_name
ORACLE_USER=user
ORACLE_PASSWORD=password
SQLITE_PATH=./data/plsql.db
```

```bash
# Fetch entire schema from Oracle into SQLite
python main.py fetch --schema MYSCHEMA

# Fetch a specific object
python main.py fetch --schema MYSCHEMA --object pkg_name
```

## Architecture

### Implemented: Source Fetching (`fetcher/`)

- `oracle_client.py` — connects to Oracle via `oracledb`, queries `DBA_SOURCE`
- `sqlite_store.py` — stores source in SQLite with SHA256 hash-based change tracking
- `sync.py` — orchestrates the fetch pipeline

Database schema in `db/schema.sql`: single table `object_source` with `schema_name`, `object_name`, `object_type`, `source_text`, `source_hash`, `fetched_at`.

### Planned Stages

1. **ANTLR4 Parsing** (C# console app) — parse PL/SQL into call graph, table access map, package dependencies
2. **Indexing** — incremental graph updates based on source hash changes
3. **Dependency Tree Traversal** — depth-first with circular dependency detection
4. **Hierarchical LLM Summarization** — bottom-up: summarize leaf nodes first, replace call sites with cached summaries, aggregate upward

Key design constraints: corporate LLM via OpenAI-compatible API, Windows deployment (no Docker), closed network, handles WRAPPED/encrypted packages by skipping them.
