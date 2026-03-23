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
```

### Шаг 2 — Распарсить объекты (построить граф вызовов)

```bash
# Распарсить всё, что изменилось
python main.py parse --schema MYSCHEMA

# Распарсить конкретный объект
python main.py parse --schema MYSCHEMA --object PKG_ORDERS

# Принудительно перепарсить (игнорировать кэш по хэшу)
python main.py parse --schema MYSCHEMA --force
```

### Шаг 3 — Посмотреть дерево зависимостей (без LLM)

```bash
python main.py explain --schema MYSCHEMA --object PKG_ORDERS
python main.py explain --schema MYSCHEMA --object PKG_ORDERS --subprogram CALCULATE_TOTAL
```

### Шаг 4 — Получить LLM-суммаризацию

```bash
python main.py summarize --schema MYSCHEMA --object PKG_ORDERS
python main.py summarize --schema MYSCHEMA --object PKG_ORDERS --subprogram CALCULATE_TOTAL

# Игнорировать кэш суммаризаций
python main.py summarize --schema MYSCHEMA --object PKG_ORDERS --force
```

## Архитектура

Система — многоступенчатый пайплайн:

```
Oracle DBA_SOURCE
       ↓
  [1] fetch        — загрузка исходников в SQLite
       ↓
  [2] parse        — C#-парсер (ANTLR4) → граф вызовов + доступ к таблицам
       ↓
  [3] explain      — обход графа в глубину, дерево зависимостей
       ↓
  [4] summarize    — иерархическая LLM-суммаризация снизу вверх
```

### Хранилище данных (SQLite)

| Таблица | Содержимое |
|---|---|
| `object_source` | Исходный код объектов + SHA256-хэш для инкрементального обновления |
| `call_graph` | Граф вызовов между объектами и подпрограммами |
| `table_accesses` | Обращения к таблицам (SELECT/INSERT/UPDATE/DELETE) |
| `parse_results` | Статус парсинга + хэш последнего разбора |
| `object_summary` | Кэш LLM-суммаризаций (не пересчитываются при неизменённом исходнике) |

### Иерархическая суммаризация

Ключевой механизм для работы с большими деревьями зависимостей без переполнения контекста LLM:

1. Суммаризируются листовые узлы (методы без внешних зависимостей)
2. При анализе родительского метода вызовы заменяются кратким описанием уже обработанных методов
3. Процесс поднимается вверх до целевого метода

Циклические зависимости обнаруживаются и обрываются — вместо повторного анализа подставляется ссылка на уже описанный метод.

## Компоненты

| Модуль | Описание |
|---|---|
| `fetcher/` | Подключение к Oracle, выгрузка через `DBA_SOURCE`, сохранение в SQLite |
| `plsql_parser/` | C# (ANTLR4) — парсинг PL/SQL, построение графа вызовов |
| `parser/` | Python-обёртка над C#-бинарником (subprocess + JSON) |
| `indexer/` | Инкрементальное обновление графа в SQLite по хэшу |
| `traversal/` | Обход графа в глубину, построение дерева зависимостей |
| `summarizer/` | LLM-суммаризация через OpenAI-совместимый API |

## Ограничения

- Зашифрованные (WRAPPED) пакеты пропускаются без анализа
- Источник исходников — только Oracle `DBA_SOURCE`
- Production-развёртывание на Windows без Docker, в закрытой корпоративной сети
