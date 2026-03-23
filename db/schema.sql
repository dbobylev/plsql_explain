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
           callee_object, callee_subprogram)
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
    UNIQUE(schema_name, object_name, object_type, subprogram, table_name, operation)
);
