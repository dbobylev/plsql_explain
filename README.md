# plsql_explain

Инструмент для глубокого анализа PL/SQL кода Oracle. По имени метода строит иерархическое текстовое описание его логики с учётом всех транзитивных зависимостей.

## Пример

**Вход:**
```
python main.py summarize --schema MYSCHEMA --object pkg_orders --subprogram calculate_total
```

**Выход:**
```
1. Метод pkg_orders.calculate_total
   Назначение: вычисляет итоговую сумму заказа с учётом скидок и налогов.

   1.1. Вызов pkg_discount.get_rate
        Получает процент скидки для клиента по его категории.

   1.2. Вызов pkg_tax.compute
        Рассчитывает сумму налога.

        1.2.1. Вызов pkg_reference.get_code
               Получает код налоговой ставки из справочника.
```

## Установка

**Требования:** Python 3.10+, .NET 9 SDK (для сборки C#-парсера)

```bash
pip install -r requirements.txt
cp .env.example .env  # заполнить параметры подключения
```

### Переменные окружения (`.env`)

| Переменная | Описание |
|---|---|
| `ORACLE_DSN` | Строка подключения к Oracle, например `host:port/service_name` |
| `ORACLE_USER` | Пользователь Oracle |
| `ORACLE_PASSWORD` | Пароль Oracle |
| `SQLITE_PATH` | Путь к SQLite-базе, например `./data/plsql.db` |
| `PLSQL_PARSER_PATH` | Путь к скомпилированному C#-бинарнику парсера |
| `PARSER_TIMEOUT_SECONDS` | Таймаут запуска C#-парсера в секундах, по умолчанию `600` |
| `LLM_BASE_URL` | URL OpenAI-совместимого API, например `http://corporate-llm/v1` |
| `LLM_API_KEY` | API-ключ LLM |
| `LLM_MODEL` | Имя модели, например `gpt-4o` |

### Сборка C#-парсера

```bash
dotnet build plsql_parser/PlsqlParser.csproj -c Release
```

Бинарник будет в `plsql_parser/bin/Release/net8.0/PlsqlParser`.

### Пересборка ANTLR4-парсера и лексера из грамматики

Файлы `PlSqlLexer.cs` и `PlSqlParser.cs` (и сопутствующие) — сгенерированы из ANTLR4-грамматики.
Пересобирать нужно только при обновлении грамматики.

**Требования:** Java, ANTLR4 tool jar (antlr-4.x-complete.jar)

```bash
cd plsql_parser/Grammar

# Скачать исходники грамматики (если ещё не скачаны)
# PlSqlLexer.g4 и PlSqlParser.g4:
# https://github.com/antlr/grammars-v4/tree/master/sql/plsql

# Сгенерировать C#-источники
java -jar antlr-4.13.1-complete.jar \
  -Dlanguage=CSharp \
  -package PlsqlParser.Grammar \
  -o . \
  PlSqlLexer.g4 PlSqlParser.g4

# Пересобрать бинарник
cd ../..
dotnet build plsql_parser/PlsqlParser.csproj -c Release
```

После генерации в `plsql_parser/Grammar/` появятся: `PlSqlLexer.cs`, `PlSqlParser.cs`, `PlSqlParserVisitor.cs`, `PlSqlParserBaseVisitor.cs`, `*.interp`, `*.tokens`.

## Использование

### Шаг 1 — Загрузить исходники из Oracle в SQLite

```bash
# Загрузить всю схему
python main.py fetch --schema MYSCHEMA

# Загрузить конкретный объект
python main.py fetch --schema MYSCHEMA --object PKG_ORDERS

# Загрузить и сразу распарсить
python main.py fetch --schema MYSCHEMA --parse

# Загрузить, распарсить и подтянуть описания таблиц/колонок
python main.py fetch --schema MYSCHEMA --parse --with-table-meta

# Загрузить, распарсить и подтянуть значения словарных констант из ais.dicti
python main.py fetch --schema MYSCHEMA --parse --with-dict-const
```

### Шаг 2 — Распарсить объекты (построить граф вызовов)

```bash
# Распарсить всё, что изменилось
python main.py parse --schema MYSCHEMA

# Распарсить конкретный объект
python main.py parse --schema MYSCHEMA --object PKG_ORDERS

# Принудительно перепарсить (игнорировать кэш по хэшу)
python main.py parse --schema MYSCHEMA --force

# После парсинга синхронизировать описания таблиц и колонок
python main.py parse --schema MYSCHEMA --with-table-meta

# После парсинга синхронизировать значения словарных констант из ais.dicti
python main.py parse --schema MYSCHEMA --with-dict-const
```

### Шаг 2.5 — Подтянуть метаданные таблиц

Если `table_access` уже собран, описания таблиц и колонок можно обновить отдельной командой:

```bash
python main.py sync-table-meta --schema MYSCHEMA
python main.py sync-table-meta --schema MYSCHEMA --object PKG_ORDERS
```

### Шаг 2.6 — Подтянуть значения словарных констант

Если в исходниках встречаются вызовы `c.get('CONST_NAME')`, их значения можно синхронизировать из `ais.dicti` отдельной командой:

```bash
python main.py sync-dict-const --schema MYSCHEMA
python main.py sync-dict-const --schema MYSCHEMA --object PKG_ORDERS
```

По каждой найденной константе выполняется запрос:

```sql
select shortname, fullname
from ais.dicti
where constname = upper(:ConstName);
```

Результаты сохраняются в локальную SQLite-таблицу `dict_constant`.

### Debug — запустить парсер на произвольном PL/SQL

Команда `debug` прогоняет C#-парсер на произвольном исходнике (без Oracle и SQLite) и выводит результат: граф вызовов, обращения к таблицам, список подпрограмм и дерево операторов.

```bash
# Передать исходник inline
python main.py debug --source "BEGIN pkg_orders.calculate_total; END;"

# Передать исходник из файла
python main.py debug --source-file my_package.sql

# Указать схему/объект/тип (влияют на подпись в выводе, по умолчанию DEBUG.ANONYMOUS / PACKAGE BODY)
python main.py debug --source-file my_pkg.sql --schema MYSCHEMA --object MY_PKG --type "PACKAGE BODY"

# Получить результат как JSON (удобно для скриптов)
python main.py debug --source-file my_pkg.sql --json

# Сохранить результат в файл — надёжный способ без проблем с кодировкой на Windows
python main.py debug --source-file my_pkg.sql --output my_pkg_report.txt
python main.py debug --source-file my_pkg.sql --json --output my_pkg_parse.json
python main.py debug --source-file .\tests\handstest\my_pkg.sql --output .\tests\handstest\my_pkg_report.txt
python3 main.py debug --source-file ./tests/handstest/my_pkg.sql --output ./tests/handstest/my_pkg_report.txt
```

### Шаг 3 — Посмотреть дерево зависимостей (без LLM)

```bash
python main.py explain --schema MYSCHEMA --object PKG_ORDERS
python main.py explain --schema MYSCHEMA --object PKG_ORDERS --subprogram CALCULATE_TOTAL

# Ограничить глубину обхода зависимостей
python main.py explain --schema MYSCHEMA --object PKG_ORDERS --depth 1
```

### Шаг 4 — Получить LLM-суммаризацию

```bash
# Построить дерево описаний для конкретной подпрограммы
python main.py summarize --schema MYSCHEMA --object PKG_ORDERS --subprogram CALCULATE_TOTAL

# Ограничить глубину раскрытия вызовов дочерних методов
python main.py summarize --schema MYSCHEMA --object PKG_ORDERS --depth 2

# Игнорировать кэш описаний узлов
python main.py summarize --schema MYSCHEMA --object PKG_ORDERS --force
```

`summarize` сам не загружает значения из `ais.dicti` и не заполняет `dict_constant`.
Он только читает уже синхронизированные значения из SQLite и добавляет их в prompt для LLM.
Если `dict_constant` пустая, сначала выполните `parse --with-dict-const` или `sync-dict-const`.

Результат автоматически сохраняется в Markdown-файл в папку `rusult_summary/`.
Файл содержит обзор (`Overview`) со статистикой по дереву и `Numbered Outline`
с ручными переносами длинных описаний.
Дополнительно рядом сохраняется HTML-отчёт с карточками overview и
presentation-friendly иерархической таблицей для просмотра в браузере.
Шаблон имени: `summary_<schema>_<object>_<subprogram|root>_<timestamp>.md`
Помимо Markdown-вывода, полное дерево узлов с описаниями сохраняется в SQLite в таблицу `node_description`.

#### Параметры `summarize`

| Флаг | Описание |
|---|---|
| `--depth N` | Глубина обхода зависимостей: `0` = только корень, `1` = прямые зависимости, без флага = без ограничения |
| `--force` | Игнорировать кэш описаний узлов и заново обратиться к LLM для всего дерева |
| `--subprogram NAME` | Анализировать конкретную процедуру/функцию внутри пакета |

### Шаг 5 — Подготовить документы для RAG-индексации

После того как `summarize` сохранил дерево `node_description`, можно собрать
нормализованные документы для последующей индексации в локальную таблицу
`rag_document`.

```bash
# Экспортировать все latest completed summarize-runs по схеме
python main.py build-rag --schema MYSCHEMA

# Экспортировать только один объект
python main.py build-rag --schema MYSCHEMA --object PKG_ORDERS

# Экспортировать один конкретный метод
python main.py build-rag --schema MYSCHEMA --object PKG_ORDERS --subprogram CALCULATE_TOTAL
```

В `rag_document` сохраняются три типа документов:

1. `method_summary` — корневое описание метода
2. `method_step` — отдельные шаги/узлы дерева (`SQL`, `CALL`, `IF`, `LOOP` и т.д.)
3. `table_doc` — описание таблиц и колонок, найденных через `table_access`

Для каждого документа сохраняются:

1. `content_text` — текст для embedding/vector index
2. `summary_text` — краткое смысловое описание
3. `code_text` — исходный PL/SQL-фрагмент для точной подстановки в prompt
4. `metadata_json` — связи: таблицы, вызовы, дочерние chunk'и, словарные константы

#### Анализ по substatement'ам

Суммаризатор работает по дереву операторов `substatement` и строит описание снизу вверх:

1. Загружает дерево операторов метода (IF, LOOP, SQL, EXCEPTION и т.д.) из результатов парсинга
2. Для leaf-узлов делает короткие LLM-запросы по конкретному фрагменту кода
3. Для внутренних узлов делает отдельные LLM-запросы-агрегации на основе уже описанных детей
4. Для call-site встраивает дерево дочернего метода и продолжает агрегацию вверх до корня
5. Переиспользует кэш по `source_hash`, если конкретный узел дерева не менялся

Пользователь получает структуру дерева метода, где каждому узлу и листу соответствует отдельное описание.

#### Описания таблиц и колонок

После `parse --with-table-meta` или `sync-table-meta` в SQLite сохраняются:

1. Описание таблицы или view
2. Список колонок с типами и nullable
3. Описания колонок

В prompt для LLM описание таблицы передаётся всегда, если метаданные найдены.
Колонки передаются выборочно: только если их имена найдены в текущем фрагменте исходника. Это снижает шум и не перегружает контекст.

#### Словарные константы `c.get(...)`

После `parse --with-dict-const` или `sync-dict-const` в SQLite сохраняются:

1. Имя константы `const_name`
2. Значение `shortname`
3. Значение `fullname`
4. Поле `resolved_text` для анализа: `fullname`, а если оно пустое, то `shortname`

При суммаризации проект ищет в текущем фрагменте вызовы вида `c.get('CONST_NAME')`,
поднимает для них значения из `dict_constant` и добавляет в prompt отдельный блок
`Константы словаря`.

## License

Проект распространяется под лицензией Apache License 2.0. Подробности в файлах `LICENSE` и `NOTICE`.

## Архитектура

Система — многоступенчатый пайплайн:

```
Oracle DBA_SOURCE
       ↓
  [1] fetch        — загрузка исходников в SQLite
       ↓
  [2] parse        — C#-парсер (ANTLR4) → граф вызовов + доступ к таблицам
       ↓
  [2.5] sync-table-meta — описания таблиц/колонок из Oracle → SQLite
       ↓
  [2.6] sync-dict-const — значения констант из ais.dicti → SQLite
       ↓
  [3] explain      — обход графа в глубину, дерево зависимостей
       ↓
  [4] summarize    — иерархическая LLM-суммаризация снизу вверх
```

### Хранилище данных (SQLite)

| Таблица | Содержимое |
|---|---|
| `object_source` | Исходный код объектов + SHA256-хэш для инкрементального обновления |
| `parse_result` | Статус парсинга + хэш последнего разбора |
| `call_edge` | Граф вызовов между объектами и подпрограммами |
| `table_access` | Обращения к таблицам (SELECT/INSERT/UPDATE/DELETE/MERGE) |
| `table_metadata` | Описание таблиц/view, найденных в `table_access` |
| `column_metadata` | Колонки, типы и комментарии для таблиц/view |
| `dict_constant` | Кэш значений констант из `ais.dicti` для вызовов `c.get(...)` |
| `subprogram` | Процедуры/функции внутри пакетов (имя, тип, исходный код) |
| `substatement` | Дерево операторов внутри подпрограмм (IF, LOOP, SQL, EXCEPTION и т.д.) |
| `node_description` | Дерево описаний метода: узлы, иерархия, хэши, тексты описаний LLM |
| `rag_document` | Нормализованные документы для RAG-индексации: method summary, method steps, table docs |

### Иерархическая суммаризация

Ключевой механизм для работы с большими деревьями без переполнения контекста LLM:

1. Сначала описываются leaf-узлы дерева `substatement`
2. Затем описываются ветви, блоки и call-site с уже готовыми описаниями детей
3. После этого строится описание корня метода
4. Для дочерних методов используется тот же процесс, а их дерево встраивается в дерево вызывающего метода

Циклические зависимости обнаруживаются и обрываются — вместо повторного анализа подставляется ссылка на уже описанный метод. Глубина обхода зависимостей настраивается через `--depth`.

## Компоненты

| Модуль | Описание |
|---|---|
| `fetcher/` | Подключение к Oracle, выгрузка через `DBA_SOURCE`, сохранение в SQLite |
| `plsql_parser/` | C# (ANTLR4) — парсинг PL/SQL, построение графа вызовов |
| `parser/` | Python-обёртка над C#-бинарником (subprocess + JSON) |
| `indexer/` | Инкрементальное обновление графа в SQLite по хэшу |
| `tablemeta/` | Загрузка описаний таблиц и колонок из Oracle в SQLite |
| `dictconst/` | Загрузка значений констант из `ais.dicti` в SQLite и подмешивание их в LLM-анализ |
| `traversal/` | Обход графа в глубину, построение дерева зависимостей |
| `summarizer/` | LLM-описание дерева substatement: короткие запросы для leaf-узлов, агрегация снизу вверх, сохранение дерева в SQLite |
| `rag/` | Подготовка нормализованных документов для RAG-индексации из `node_description`, `call_edge` и `table_metadata` |

## Ограничения

- Зашифрованные (WRAPPED) пакеты пропускаются без анализа
- Источник исходников — только Oracle `DBA_SOURCE`
- Production-развёртывание на Windows без Docker, в закрытой корпоративной сети
