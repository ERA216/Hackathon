# Смена.AI

Веб-сервис для малых производств. Мастер пишет отчёт о смене обычным текстом. Сервис превращает его в записи об инцидентах, сам находит повторяющиеся поломки и считает, какая мера быстрее всего снизит затраты.

> ИИ переводит свободный текст в структуру и объясняет выводы. Все деньги, интервалы и аномалии считает код.

## Запуск

```sh
python -m pip install -r requirements.txt
cp .env.example .env          # вставить API-ключ; без ключа работает резервный режим
python -m streamlit run app.py
```

База `smena.db` и демо-данные создаются при первом запуске автоматически.

> Пока `app.py` в работе: бэкенд готов, интерфейс подключается следующим шагом. Визуальный прототип без бэкенда запускается командой `python -m streamlit run ui_preview.py`.

Проверка бэкенда без сети и без ключа:

```sh
python tests/test_backend.py
```

## Бэкенд (`backend/`)

| Модуль | Что делает |
| --- | --- |
| `config.py` | Настройки из `.env`, выбор провайдера (gemini / openai / anthropic / mock) |
| `llm.py` | Вызов модели через REST, JSON-вывод, проверка схемой Pydantic, до 2 повторов с текстом ошибки, понятные исключения (нет сети, лимит, неверный формат), журнал `llm_calls` |
| `schemas.py` | Строгие схемы ответов модели, закрытый список типов неисправностей |
| `extract.py` | Шаг 1: текст смены → черновики инцидентов. Проверка кодом: станок из справочника, простой подтверждается текстом, выдуманная причина удаляется, низкая уверенность → на проверку. Резервный разбор по правилам |
| `analytics.py` | Шаг 2 (только код): потери, интервалы между поломками, аномалии, сравнение «до/после» |
| `advisor.py` | Шаг 3: план экономии. Меры — только из `data/measures.json`, экономика — по формулам, ИИ выбирает меры и объясняет; чужие `measure_id` и числа, которых нет в расчёте, отбрасываются |
| `db.py` | SQLite: `shift_logs`, `incidents`, `recommendations`, `applied_measures`, `llm_calls` |
| `seed.py` | Демо-данные с пометкой ДЕМО |
| `service.py` | **Фасад для интерфейса** — UI вызывает только его |

### Функции для интерфейса (`backend/service.py`)

```python
from backend import service

service.bootstrap()                      # один раз при старте: база + демо-данные
service.status()                         # {"ai_enabled", "mode_label", "calls", "ok", ...}

# Экран 1 · Новая запись
r = service.analyze_text(raw)            # ValueError при пустом/длинном вводе
# r = {"drafts": [...], "questions": [...], "mode": "ai" | "rules", "warning": str | None}
# draft: equipment_id, equipment_name, failure_type, failure_label, downtime_min, downtime_quote,
#        parts, parts_cost_kzt, cause, action, confidence, issues, needs_review
service.save_record(raw, drafts, r["mode"])   # → {"log_id", "incident_ids", "signals"}

# Экран 2 · Аномалии
service.dashboard()                      # total_loss_kzt, by_equipment, anomalies (с incident_ids)
service.incident_evidence(ids)           # исходные записи для «На чём основан сигнал»

# Экран 3 · План экономии
p = service.savings_plan("M3", "belt_break")
# p = {"stats", "measures": [{title, savings_kzt, payback_label, formula, payback_formula, why}], "summary", "mode", "ai_generated", "warning"}
service.mark_applied("M3", "MS02"); service.unmark_applied("M3", "MS02"); service.applied_set()

# Экран 4 · История и эффект
service.history(query)                   # строки для st.dataframe
service.effects()                        # «до/после» по внедрённым мерам
service.reset_demo()                     # пересоздать базу для репетиции
```

`issues` в черновике: `equipment` (станок не найден), `downtime` (простой не подтверждён текстом), `cause_removed` (причины нет в тексте), `low_confidence`. Поля с `issues` подсвечиваются, мастер подтверждает их вручную.

## Почему эта модель

Задаче нужна LLM с надёжным JSON-выводом, которая понимает смешанный русско-казахский текст с опечатками. Задача извлечения простая, поэтому по умолчанию взята быстрая и недорогая модель (`gemini-2.5-flash`, меняется в `.env`). Расчёты модели не доверяются. Классическое ML для прогноза не используется: на десятках записей прозрачные правила надёжнее и объяснимее.

## Данные

Реальных персональных данных нет: автор записи хранится только как роль. Все демо-записи помечены `is_demo = 1` и «ДЕМО». Коэффициенты эффекта мер — демо-оценки.

## Интерфейс

Визуальный прототип: `ui_preview.py` (см. `UI_README.md`). Его блоки переносятся в `app.py` и подключаются к `backend.service`.
