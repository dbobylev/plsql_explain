from dotenv import load_dotenv

load_dotenv()

from fetcher import oracle_client, sqlite_store


def run(schema: str, object_name: str | None = None) -> None:
    sqlite_store.init_db()

    counts = {"inserted": 0, "updated": 0, "unchanged": 0}

    with sqlite_store._connect() as conn:
        for schema_name, name, obj_type, source_text in oracle_client.fetch_objects(schema, object_name):
            result = sqlite_store.upsert_object(conn, schema_name, name, obj_type, source_text)
            counts[result] += 1
            if result != "unchanged":
                print(f"  [{result}] {schema_name}.{name} ({obj_type})")

    total = sum(counts.values())
    print(
        f"\nГотово: всего {total} объектов — "
        f"{counts['inserted']} новых, {counts['updated']} обновлено, {counts['unchanged']} без изменений."
    )
