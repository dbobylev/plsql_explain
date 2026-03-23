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
