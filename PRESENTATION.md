# plsql_explain — Архитектурные схемы

## 1. Общий поток работы

```mermaid
flowchart TD
    User([Пользователь]) -->|python main.py fetch| Fetch
    User -->|python main.py parse| Parse
    User -->|python main.py summarize| Traversal

    subgraph Stage1 [" Этап 1 — Загрузка исходников "]
        Fetch[fetcher/sync.py] -->|DBA_SOURCE| Oracle[(Oracle DB)]
        Oracle -->|source_text| Fetch
        Fetch -->|upsert + SHA256| DB1[(SQLite<br>object_source)]
    end

    subgraph Stage2 [" Этап 2 — Парсинг и индексация "]
        Parse[indexer/sync.py] -->|читает object_source| DB1
        Parse -->|JSON via stdin| CSharp["C# ANTLR4 Parser<br>(PlsqlParser)"]
        CSharp -->|JSON via stdout<br>call_edges, table_accesses,<br>substatements| Parse
        Parse -->|bulk replace| DB2[(SQLite<br>call_edge, table_access<br>substatement)]
    end

    subgraph Stage3 [" Этап 3 — Обход графа "]
        Traversal["traversal/graph.py<br>build_tree()"] -->|читает call_edge| DB2
        Traversal -->|DependencyNode tree| Engine
    end

    subgraph Stage4 [" Этап 4 — LLM суммаризация "]
        Engine["summarizer/engine.py<br>summarize_node()"] -->|запросы к кэшу| DB3[(SQLite<br>summary<br>analysis_cache)]
        Engine -->|промпт| LLM["OpenAI-совместимый<br>LLM API"]
        LLM -->|текст| Engine
        Engine -->|сохранение| DB3
    end

    DB1 --> Parse
    DB2 --> Traversal
    Engine -->|итоговое описание| User
```

---

## 2. Два измерения анализа — `summarizer/engine.py`

Движок анализирует код сразу в двух измерениях: **вглубь** по иерархии зависимостей и **вширь** по блокам внутри одного объекта.

```mermaid
flowchart TD
    B["summarize_node(B)"] -->|brief| A
    C["summarize_node(C)"] -->|brief| A

    A(["summarize_node(A)"]) --> Cache{"Кэш<br>актуален?"}
    Cache -->|да| Done(["готово"])
    Cache -->|нет| SizeCheck{"≥ 4000<br>символов?"}

    SizeCheck -->|нет| LLMC["LLM<br>— полный исходник →<br>summary"]

    SizeCheck -->|да| Plan["Планировщик<br>AnalysisUnit tree"]
    Plan --> Leaf["Leaf unit"]
    Leaf --> LeafLLM["LLM → leaf analysis"]
    LeafLLM --> Branch["Агрегация ветки"]
    Branch --> Block["Агрегация блока"]
    Block --> Agg["LLM → summary метода"]

    Agg --> Persist["SQLite кэш"]
    LLMC --> Persist
    Persist --> Done
```

> **Иерархия** — суммари дочерних объектов (B, C) передаются в промпт родителя (A) как контекст.
> **Substatements** — код объекта раскладывается в дерево `AnalysisUnit`, leaf-фрагменты анализируются отдельно, затем ветки и блоки агрегируются снизу вверх.
