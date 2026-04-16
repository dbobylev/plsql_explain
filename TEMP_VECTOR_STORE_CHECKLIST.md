# Временная инструкция: проверка Embedding + Qdrant для PoC

Этот файл нужен для быстрого самостоятельного исследования в закрытой среде.
Цель: понять, можно ли уже сейчас загружать данные в Qdrant и строить первый PoC-поиск по PL/SQL.

## Короткий ответ

Да, загружать данные в Qdrant уже можно, но только после минимальной проверки:

1. embedding endpoint отвечает
2. Qdrant доступен
3. в Qdrant можно создать collection
4. в Qdrant можно записать тестовый vector point
5. этот point находится обратным поиском
6. работают metadata filters

Если эти 6 пунктов проходят, можно начинать грузить `rag_document.content_text`.

## Что не нужно делать на первом шаге

- Не нужно сразу грузить весь исходный код
- Не нужно сразу грузить 100K строк
- Не нужно строить production-схему
- Не нужно класть полный `code_text` в Qdrant

Для первого PoC лучше грузить:

- vector: только embedding от `content_text`
- payload: `chunk_id`, `chunk_type`, `schema_name`, `object_name`, `subprogram`, `title`, `summary_text`

Полный код лучше пока оставлять в SQLite и доставать по `chunk_id`.

## Что подготовить

Нужно знать 4 вещи:

1. URL OpenAI-compatible endpoint для embeddings
2. имя embedding модели
3. URL Qdrant endpoint
4. нужен ли API key для Qdrant

Если Qdrant API key неизвестен, сначала пробуйте без него.

## Рекомендуемые переменные окружения

В PowerShell:

```powershell
$env:EMBEDDING_BASE_URL = "https://your-openai-compatible-endpoint/v1"
$env:EMBEDDING_API_KEY = "..."
$env:EMBEDDING_MODEL = "..."
$env:QDRANT_URL = "http://host:6333"
$env:QDRANT_API_KEY = ""
```

Если у Qdrant нет ключа, оставьте `QDRANT_API_KEY` пустым.

## Шаг 1. Проверить embedding endpoint

Создайте файл `embedding_probe.py` рядом с проектом или запустите код inline:

```python
import os
from openai import OpenAI

client = OpenAI(
    base_url=os.environ["EMBEDDING_BASE_URL"],
    api_key=os.environ["EMBEDDING_API_KEY"],
)

response = client.embeddings.create(
    model=os.environ["EMBEDDING_MODEL"],
    input="PL/SQL procedure that calculates order total and writes audit log.",
)

vector = response.data[0].embedding
print("Embedding ok")
print("Vector size:", len(vector))
print("First 5 values:", vector[:5])
```

Успешный результат:

- запрос проходит без ошибки
- приходит непустой массив чисел
- размерность вектора фиксированная

Что зафиксировать:

- точное имя модели
- размерность вектора
- среднее время ответа

## Шаг 2. Проверить Qdrant на доступность

Проверка через PowerShell:

```powershell
Invoke-RestMethod -Method Get -Uri "$env:QDRANT_URL/collections"
```

Если нужен API key:

```powershell
$headers = @{ "api-key" = $env:QDRANT_API_KEY }
Invoke-RestMethod -Method Get -Uri "$env:QDRANT_URL/collections" -Headers $headers
```

Успешный результат:

- сервер отвечает
- вы видите JSON со списком collection или пустой результат

Если здесь ошибка, дальше идти рано.

## Шаг 3. Создать тестовую collection

Размерность должна совпадать с embedding model.
Подставьте реальное число вместо `VECTOR_SIZE`.

Без API key:

```powershell
$body = @{
  vectors = @{
    size = VECTOR_SIZE
    distance = "Cosine"
  }
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Method Put `
  -Uri "$env:QDRANT_URL/collections/plsql_rag_poc" `
  -ContentType "application/json" `
  -Body $body
```

С API key:

```powershell
$headers = @{ "api-key" = $env:QDRANT_API_KEY }

Invoke-RestMethod -Method Put `
  -Uri "$env:QDRANT_URL/collections/plsql_rag_poc" `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body
```

Успешный результат:

- collection создаётся без ошибки

## Шаг 4. Загрузить 1 тестовый point

Сначала получите embedding для 1 тестового текста.
Потом отправьте его в Qdrant.

Пример payload point:

```json
{
  "id": 1,
  "vector": [0.123, 0.456, 0.789],
  "payload": {
    "chunk_id": "test:1",
    "chunk_type": "method_summary",
    "schema_name": "S",
    "object_name": "PKG_ORDERS",
    "subprogram": "CALCULATE_TOTAL",
    "title": "PKG_ORDERS.CALCULATE_TOTAL",
    "summary_text": "Calculates order total."
  }
}
```

Проверка через Python удобнее:

```python
import os
import requests
from openai import OpenAI

client = OpenAI(
    base_url=os.environ["EMBEDDING_BASE_URL"],
    api_key=os.environ["EMBEDDING_API_KEY"],
)

text = "PL/SQL method calculates order total and applies discount."
vector = client.embeddings.create(
    model=os.environ["EMBEDDING_MODEL"],
    input=text,
).data[0].embedding

payload = {
    "points": [
        {
            "id": 1,
            "vector": vector,
            "payload": {
                "chunk_id": "test:1",
                "chunk_type": "method_summary",
                "schema_name": "S",
                "object_name": "PKG_ORDERS",
                "subprogram": "CALCULATE_TOTAL",
                "title": "PKG_ORDERS.CALCULATE_TOTAL",
                "summary_text": "Calculates order total."
            }
        }
    ]
}

headers = {}
if os.environ.get("QDRANT_API_KEY"):
    headers["api-key"] = os.environ["QDRANT_API_KEY"]

r = requests.put(
    f"{os.environ['QDRANT_URL']}/collections/plsql_rag_poc/points",
    json=payload,
    headers=headers,
    timeout=60,
)

print(r.status_code)
print(r.text)
```

Успешный результат:

- запись проходит
- point сохраняется

## Шаг 5. Выполнить обратный поиск

Нужно убедиться, что тот же текст находит сохранённый point.

```python
import os
import requests
from openai import OpenAI

client = OpenAI(
    base_url=os.environ["EMBEDDING_BASE_URL"],
    api_key=os.environ["EMBEDDING_API_KEY"],
)

query = "Where is the PL/SQL method that calculates order total?"
vector = client.embeddings.create(
    model=os.environ["EMBEDDING_MODEL"],
    input=query,
).data[0].embedding

headers = {}
if os.environ.get("QDRANT_API_KEY"):
    headers["api-key"] = os.environ["QDRANT_API_KEY"]

r = requests.post(
    f"{os.environ['QDRANT_URL']}/collections/plsql_rag_poc/points/search",
    json={
        "vector": vector,
        "limit": 3,
        "with_payload": True
    },
    headers=headers,
    timeout=60,
)

print(r.status_code)
print(r.text)
```

Успешный результат:

- `test:1` попадает в top results
- payload возвращается целиком

## Шаг 6. Проверить metadata filter

Это критично для будущего поиска по PL/SQL.

Проверить хотя бы фильтр по:

- `schema_name`
- `object_name`
- `subprogram`
- `chunk_type`

Пример:

```python
r = requests.post(
    f"{os.environ['QDRANT_URL']}/collections/plsql_rag_poc/points/search",
    json={
        "vector": vector,
        "limit": 3,
        "with_payload": True,
        "filter": {
            "must": [
                {"key": "schema_name", "match": {"value": "S"}},
                {"key": "chunk_type", "match": {"value": "method_summary"}}
            ]
        }
    },
    headers=headers,
    timeout=60,
)
```

Успешный результат:

- поиск работает
- нерелевантные chunk types отфильтровываются

## Шаг 7. Только после этого грузить реальные данные

Когда шаги 1-6 проходят, можно начинать индексировать реальные данные из `rag_document`.

Для первого захода:

1. Сначала загрузить только `method_summary`
2. Проверить качество поиска
3. Потом добавить `method_step`
4. Таблицы `table_doc` можно добавить отдельно

## Что грузить в Qdrant на первом PoC

Рекомендованный минимальный payload:

```json
{
  "chunk_id": "...",
  "chunk_type": "method_summary",
  "schema_name": "...",
  "object_name": "...",
  "subprogram": "...",
  "title": "...",
  "summary_text": "..."
}
```

Вектор считать от:

- `rag_document.content_text`

Пока не грузить:

- `code_text` целиком
- большие markdown-рендеры
- всё подряд без фильтров

## Когда можно сказать “да, уже можно загружать данные”

Можно начинать загрузку, если одновременно верно следующее:

1. embedding endpoint стабильно отвечает
2. размерность вектора известна
3. collection создаётся вручную
4. один тестовый point успешно записывается
5. он находится через vector search
6. metadata filters работают

Если это выполнено, технический PoC-контур готов.

## Практический план для вас

Сейчас разумный порядок такой:

1. Прогнать один synthetic test point
2. Загрузить 5-10 реальных `method_summary`
3. Проверить 10-20 реальных запросов по коду
4. Потом добавить `method_step`
5. Потом масштабироваться до 20 пакетов

## Что записывать по ходу проверки

В отдельный блокнот или md:

- embedding model name
- vector size
- qdrant url
- collection name
- есть ли API key
- среднее время embedding запроса
- среднее время search запроса
- работает ли filter
- сколько документов уже загружено

Это потом пригодится и для презентации, и для “взрослой” реализации.

## Следующий шаг в этом репозитории

После ручной проверки среды можно делать следующий кодовый шаг:

1. взять записи из `rag_document`
2. считать embeddings для `content_text`
3. загрузить points в Qdrant
4. сделать маленькую команду `index-rag`

Если ручные проверки пройдут, можно переходить к этой реализации.
