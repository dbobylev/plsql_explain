CREATE TABLE IF NOT EXISTS object_source (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_name TEXT NOT NULL,
    object_name TEXT NOT NULL,
    object_type TEXT NOT NULL,
    source_text TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    UNIQUE(schema_name, object_name, object_type)
);

CREATE TABLE IF NOT EXISTS parse_result (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_name    TEXT NOT NULL,
    object_name    TEXT NOT NULL,
    object_type    TEXT NOT NULL,
    parsed_at      TEXT NOT NULL,
    source_hash    TEXT NOT NULL,
    status         TEXT NOT NULL,
    error_message  TEXT,
    UNIQUE(schema_name, object_name, object_type)
);

CREATE TABLE IF NOT EXISTS call_edge (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    caller_schema     TEXT NOT NULL,
    caller_object     TEXT NOT NULL,
    caller_type       TEXT NOT NULL,
    caller_subprogram TEXT,
    callee_schema     TEXT,
    callee_object     TEXT NOT NULL,
    callee_subprogram TEXT,
    UNIQUE(caller_schema, caller_object, caller_type, caller_subprogram,
           callee_schema, callee_object, callee_subprogram)
);

CREATE TABLE IF NOT EXISTS table_access (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_name    TEXT NOT NULL,
    object_name    TEXT NOT NULL,
    object_type    TEXT NOT NULL,
    subprogram     TEXT,
    table_schema   TEXT,
    table_name     TEXT NOT NULL,
    operation      TEXT NOT NULL,
    UNIQUE(schema_name, object_name, object_type, subprogram,
           table_schema, table_name, operation)
);

CREATE TABLE IF NOT EXISTS table_metadata (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_name   TEXT NOT NULL,
    table_name    TEXT NOT NULL,
    object_type   TEXT,
    table_comment TEXT,
    refreshed_at  TEXT NOT NULL,
    UNIQUE(schema_name, table_name)
);

CREATE TABLE IF NOT EXISTS column_metadata (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_name    TEXT NOT NULL,
    table_name     TEXT NOT NULL,
    column_name    TEXT NOT NULL,
    column_id      INTEGER,
    data_type      TEXT,
    nullable       INTEGER,
    column_comment TEXT,
    refreshed_at   TEXT NOT NULL,
    UNIQUE(schema_name, table_name, column_name)
);

CREATE TABLE IF NOT EXISTS subprogram (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_name     TEXT NOT NULL,
    object_name     TEXT NOT NULL,
    object_type     TEXT NOT NULL,
    subprogram_name TEXT NOT NULL,
    subprogram_type TEXT NOT NULL,
    start_line      INTEGER NOT NULL,
    end_line        INTEGER NOT NULL,
    source_text     TEXT NOT NULL,
    source_hash     TEXT NOT NULL,
    UNIQUE(schema_name, object_name, object_type, subprogram_name)
);

CREATE TABLE IF NOT EXISTS substatement (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_name    TEXT NOT NULL,
    object_name    TEXT NOT NULL,
    object_type    TEXT NOT NULL,
    subprogram     TEXT NOT NULL DEFAULT '',
    seq            INTEGER NOT NULL,
    parent_seq     INTEGER,
    position       INTEGER NOT NULL,
    statement_type TEXT NOT NULL,
    start_line     INTEGER NOT NULL,
    end_line       INTEGER NOT NULL,
    source_text    TEXT NOT NULL,
    source_hash    TEXT NOT NULL,
    UNIQUE(schema_name, object_name, object_type, subprogram, seq)
);

DROP TABLE IF EXISTS summary;

DROP TABLE IF EXISTS chunk_analysis;
DROP TABLE IF EXISTS analysis_cache;

CREATE TABLE IF NOT EXISTS node_description (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_name     TEXT NOT NULL,
    object_name     TEXT NOT NULL,
    object_type     TEXT NOT NULL,
    subprogram      TEXT NOT NULL DEFAULT '',
    node_id         TEXT NOT NULL,
    node_kind       TEXT NOT NULL,
    statement_type  TEXT NOT NULL DEFAULT '',
    title           TEXT NOT NULL DEFAULT '',
    start_line      INTEGER NOT NULL DEFAULT 0,
    end_line        INTEGER NOT NULL DEFAULT 0,
    parent_node_id  TEXT,
    position        INTEGER NOT NULL DEFAULT 0,
    source_hash     TEXT NOT NULL,
    description     TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    described_at    TEXT NOT NULL,
    UNIQUE(schema_name, object_name, object_type, subprogram, node_id, prompt_version)
);
