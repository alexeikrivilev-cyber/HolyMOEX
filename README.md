# HolyMOEX

**HolyMOEX** — модульный AI/quant-агент для анализа и автономного runtime-а на рынке акций MOEX. Проект соединяет классический quant-пайплайн, строгую PostgreSQL-модель данных, событийную обработку новостей, LLM-извлечение смысла из текста, риск-контроль и слой исполнения через broker/gateway-интерфейсы.

Главная идея проекта: **LLM не торгует сам**. Модель помогает там, где действительно нужен язык и контекст: новости, раскрытия, отчётность, дивиденды, корпоративные события, макро-комментарии. Торговое решение принимает `Decision Engine Module` на базе нормализованных признаков, весов, состояния портфеля, рыночного режима и правил риска. Исполнение проходит только после `Risk Control Module`.

---

## Почему проект интересный

HolyMOEX построен не как один большой скрипт, а как **контрактная система из 22 модулей**. Каждый модуль имеет собственную документацию, входные/выходные контракты, доступы к хранилищам, формулы метрик, failure policy и acceptance criteria.

Самые важные архитектурные особенности:

- **Единая точка внешних запросов.** Все вызовы к MOEX ISS, ArenaGo, Polza AI, новостным и макро-источникам идут через `External Request Gateway Module`. Аналитические модули не знают токены, URL-ы и детали провайдеров.
- **Оркестрация через `module_job`.** Рабочие модули запускаются только через `Orchestration Module`, который учитывает dependency graph, календарь торгов, контуры обновления, idempotency и run mode.
- **Feature Store вместо “магических сигналов”.** Метрики записываются как `feature_record`, затем собираются в `feature_vector` с горизонтом, TTL, confidence score, quality flags и ссылками на источники.
- **Risk-first execution.** `Decision Engine Module` формирует намерение, но `Risk Control Module` может заблокировать, урезать или пометить сделку до передачи в `Execution Engine Module`.
- **LLM встроен ограниченно и проверяемо.** Текстовые модули используют LLM для семантики и извлечения структурированных событий, а не для прямого выставления заявок.
- **PostgreSQL как источник правды.** Схемы разделены по зонам: `registry`, `raw_market`, `raw_text`, `raw_macro`, `events`, `features`, `weights`, `risk`, `portfolio`, `decisions`, `orders`, `request_logs`, `audit`, `analytics`.
- **In-memory runtime для тестов.** Почти каждый модуль имеет `InMemory...Repository` и `Postgres...Repository`, поэтому бизнес-логика тестируется отдельно от базы.
- **Автономный scheduler.** `agent_app.scheduler` читает расписания из `audit.schedule_config`, проверяет рыночную сессию, берёт scheduler lock и запускает due-модули через оркестрацию.
- **Startup preflight.** Перед live/sandbox runtime-ом проект проверяет БД, провайдеров, ArenaGo bot identity, портфель и readiness views.
- **Наблюдаемость и аудит.** Внешние запросы, решения, заявки, ошибки, pipeline runs и monitoring snapshots пишутся в audit/request/order stores.

---

## Что делает HolyMOEX простыми словами

Система собирает данные по выбранной вселенной ликвидных российских акций, превращает их в проверенные признаки, оценивает рыночный контекст и события, собирает decision set, пропускает его через риск-контроль и только потом готовит исполнение.

Пайплайн можно представить так:

```text
Данные рынка + новости + макро + портфель
        ↓
Проверка качества и дедупликация
        ↓
Метрики цены, ликвидности, волатильности, событий, фундаментала
        ↓
Нормализация и сбор feature_vector
        ↓
Decision Engine
        ↓
Risk Control
        ↓
Execution Engine / Paper Trading
        ↓
Portfolio State + Monitoring + Analytics
```

Отдельно важно: если источник данных плохой, устаревший или неполный, это не “замалчивается”. Такие проблемы превращаются в `quality_flags`, штрафуют `data_quality_score` и влияют на downstream-решения.

---

## Текущий состав проекта

```text
HolyMOEX/
├── agent_app/                         # runtime-код агента
│   ├── contracts/unified_objects/      # ModuleJob, ModuleJobResult, ExternalRequest, ExternalResponse
│   ├── modules/                        # реализации 22 модулей
│   ├── storage/postgres/migrations/    # SQL-миграции PostgreSQL
│   ├── main.py                         # one-shot orchestration entrypoint
│   ├── scheduler.py                    # автономный scheduler
│   ├── server_startup.py               # startup preflight
│   ├── runtime_calendar.py             # торговые сессии и runtime phase
│   └── staging_runner.py               # controlled staging/smoke pipeline
├── modules/                            # подробные markdown-контракты модулей
├── integration/                         # контракты ArenaGo и Polza AI
├── docker/                              # compose-конфигурации
├── scripts/                             # deployment/startup/smoke scripts
├── tests/                               # unit и contract tests
├── MODULE_DATABASE_ACCESS_MATRIX.md     # матрица доступов модулей к stores
├── MODULE_FORMULA_INDEX.md              # индекс формул и правил расчёта
└── README.md
```

---

## Архитектура в деталях

### 1. Orchestration-first

`Orchestration Module` — корневой системный сервис. Он создаёт `module_job`, строит pipeline run, уважает dependency graph и сохраняет результаты выполнения.

Рабочие модули не должны запускаться “как попало”. Их штатный вход — `module_job` с `job_id`, `module_name`, `contour`, `trigger_type`, `universe_id`, `instrument_ids`, `horizons`, `time_range`, `run_mode`, `idempotency_key` и `priority`.

Это даёт проекту несколько сильных свойств:

- повторяемость запусков;
- защиту от дублей через idempotency;
- явный audit trail;
- управляемые retries;
- возможность replay/backtest без обхода контрактов.

### 2. Gateway-only внешние интеграции

`External Request Gateway Module` — единственный модуль, которому разрешено ходить наружу. Он нормализует запросы и ответы, применяет cache policy, rate limits, retry policy, скрывает секреты и пишет request logs.

Поддерживаемые направления в коде и конфигурации проекта:

- `moex_iss` / `moex_fast` — рыночные данные, свечи, сделки, инструменты, top-of-book proxy;
- `arena_go` — sandbox/live broker-style операции: bots, positions, trades, submit order;
- `polza_ai` — LLM completion, модели, JSON-ответы, task-specific model routing;
- `news_api`, `issuer_disclosure`, `macro_api`, `broker_api` — generic REST-провайдеры;
- `internal_cache` — чтение из request cache без внешнего HTTP.

### 3. Контуры обновления

Проект разделяет задачи по контурам, чтобы не смешивать быстрые рыночные сигналы, дневной фундаментал, события и исследования.

| Контур | Что обновляется |
|---|---|
| `realtime_contour` | свечи, сделки, стакан/quote proxy, спред, intraday price/liquidity metrics |
| `intraday_contour` | новости, routing, intraday market/event context |
| `global_contour` | макро, индексы, sector/risk regime |
| `daily_contour` | фундаментал, дневные признаки, refresh после рынка |
| `event_contour` | отчётность, дивиденды, корпоративные события, сильные новости |
| `decision_contour` | сбор `feature_vector`, scoring, decisions, risk checks |
| `execution_contour` | заявки, fills, портфель, позиции |
| `monitoring_contour` | аудит, health, request logs, order/portfolio monitoring |
| `research_contour` | backtest, feature validation, calibration, research reports |
| `service_contour` | orchestration, gateway, storage maintenance, startup checks |

### 4. Горизонты решений

Признаки и решения разделяются по горизонтам:

- `intraday` — быстрые решения внутри дня: стакан, спред, объём, реакция на новости;
- `swing` — несколько дней/недель: momentum, volatility, event reaction, market context;
- `position` — более длинный горизонт: fundamentals, dividends, valuation, macro.

Это защищает систему от типичной ошибки: например, не смешивать `order_flow_imbalance` с долгосрочным valuation-решением без отдельного правила веса.

---

## Модули

| № | Модуль | Роль |
|---:|---|---|
| 01 | `Orchestration Module` | строит и запускает pipeline, управляет `module_job`, dependency graph и audit |
| 02 | `External Request Gateway Module` | единый шлюз внешних API, кэш, retry, rate limit, logging |
| 03 | `Selected Instruments Registry Module` | выбранная вселенная инструментов, alias/mapping, ограничения активных бумаг |
| 04 | `Data Intake & Routing Module` | приём, дедупликация и маршрутизация текстовых источников |
| 05 | `Data Quality Module` | coverage, stale/expired/future/duplicate/outlier/conflict checks |
| 06 | `Market Data Metrics Module` | price returns, momentum, volume/turnover, market-data features |
| 07 | `Liquidity & Microstructure Module` | spread, slippage, Amihud-style liquidity, execution constraints |
| 08 | `Volatility & Risk Metrics Module` | realized volatility, ATR, gaps, drawdown, beta, correlations |
| 09 | `Market Context Module` | macro/sector/index regime, risk-on/risk-off, market state |
| 10 | `Fundamental & Valuation Module` | financial statements, valuation, peer context, fundamental snapshots |
| 11 | `Event & News Intelligence Module` | новости, materiality, sentiment, event reaction, fast-first LLM path |
| 12 | `Earnings & Dividend Intelligence Module` | отчётность, surprise, dividends, payout, gap risk |
| 13 | `Corporate Actions Adjustment Module` | splits, dividends, buybacks, supply pressure, historical adjustments |
| 14 | `Derivatives & Positioning Module` | futures basis, OI, options metrics, derivatives pressure |
| 15 | `Normalization & Feature Vector Module` | нормализация признаков, TTL, quality penalties, сбор `feature_vector` |
| 16 | `Feature Validation & Research Module` | research reports, validation, draft weights |
| 17 | `Decision Engine Module` | weighted scoring, expected edge, target position, explanations |
| 18 | `Risk Control Module` | limits, drawdown, exposure, liquidity/slippage checks, order intents |
| 19 | `Execution Engine Module` | submit/cancel/track orders, fills, execution results |
| 20 | `Portfolio State Module` | snapshots, positions, cash/equity, PnL, broker sync |
| 21 | `Backtesting & Paper Trading Module` | historical simulation, paper execution, performance metrics |
| 22 | `Monitoring & Audit Module` | observability, monitoring records, audit summaries, runtime diagnostics |

Подробные контракты лежат в [`modules/`](modules/), а права доступа модулей к stores — в [`MODULE_DATABASE_ACCESS_MATRIX.md`](MODULE_DATABASE_ACCESS_MATRIX.md).

---

## Данные и PostgreSQL

Проект использует PostgreSQL не как “просто БД”, а как слой архитектурного контракта. Миграции создают отдельные схемы для разных зон ответственности:

| Схема | Назначение |
|---|---|
| `registry` | вселенная инструментов, профили, alias, provider mappings |
| `raw_market` | свечи, сделки, orderbook/quote proxy, индексы, execution constraints |
| `raw_text` | новости, раскрытия, text source configs, routing messages |
| `raw_macro` | макро-ряды и публичные macro points |
| `events` | structured events, clusters, reactions, dividends, corporate actions |
| `features` | feature records, feature vectors, data quality records, market state |
| `weights` | weights profiles и metric weight rules |
| `risk` | policies, limits, checks, risk context |
| `portfolio` | snapshots и positions |
| `decisions` | decision sets, records, explanations |
| `orders` | order intents, statuses, fills, execution results |
| `request_logs` | external requests/responses, provider config, cache |
| `audit` | module jobs, runs, results, readiness, monitoring, research |
| `analytics` | read-only views для анализа стратегии, исполнения и качества LLM |

В `002_seed_selected_instruments.sql` зашита стартовая вселенная `moex_top20_manual` из 20 активных тикеров: `LKOH`, `SBER`, `ROSN`, `GAZP`, `VTBR`, `YDEX`, `PLZL`, `T`, `NVTK`, `X5`, `GMKN`, `MGNT`, `ALRS`, `AFLT`, `CHMF`, `NLMK`, `MOEX`, `SNGSP`, `MTSS`, `PIKK`.

---

## Безопасность исполнения

В проекте явно разделены analysis/paper/live сценарии:

- `RUN_MODE=analysis_only` — расчёты и диагностика без торгового исполнения;
- `RUN_MODE=paper_trading` — симуляция решений и исполнения;
- `RUN_MODE=live_trading` — live/sandbox runtime через gateway и risk gates;
- `SAFE_LIVE_SUBMIT=false` по умолчанию — важный предохранитель, чтобы live-submit не включался случайно;
- `LIVE_READINESS_PASSED` выставляется только после startup/readiness-проверок;
- ArenaGo session probe может требоваться для подтверждения открытой market session;
- scheduler умеет пропускать торгово-тяжёлые задачи вне рынка;
- governance stores (`weights`, `risk`, `registry`, `provider_config`, `schedule_config`) не должны меняться runtime-модулями без явного процесса.

---

## Быстрый старт без PostgreSQL

Подходит для проверки, что проект импортируется и orchestration создаёт jobs в in-memory режиме.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

python -m agent_app.main --no-postgres --system-mode analysis_only
```

Запуск тестов:

```bash
pytest
```

---

## Запуск с PostgreSQL локально

1. Подготовить env-файл:

```bash
cp config/api_keys.example.env config/api_keys.local.env
```

2. Заполнить реальные значения только локально. Не коммитить `config/api_keys.local.env`.

3. Поднять PostgreSQL и применить миграции через compose:

```bash
docker compose -f docker/docker-compose.example.yml up --build migration_runner
```

4. Запустить автономный scheduler:

```bash
docker compose -f docker/docker-compose.example.yml up --build scheduler_worker
```

Опциональные профили:

```bash
# ручной one-shot запуск agent_app.main
docker compose -f docker/docker-compose.example.yml --profile manual up --build agent_app

# research worker
docker compose -f docker/docker-compose.example.yml --profile research up --build research_worker
```

---

## Production/autonomous container mode

`Dockerfile` собирает образ на базе `postgres:16` с установленным Python runtime. По умолчанию entrypoint — `scripts/start_autonomous.sh`.

Этот сценарий делает всё в одном runtime-контейнере:

1. создаёт/запускает локальный PostgreSQL в `$DATA_DIR/postgres`;
2. применяет SQL-миграции;
3. запускает `server_startup` preflight;
4. синхронизирует portfolio/bot identity для ArenaGo;
5. запускает автономный scheduler.

Для smoke-проверки деплоя есть:

```bash
scripts/deploy_check.sh
```

Для контролируемого staging-прогона:

```bash
scripts/controlled_full_pipeline.sh
```

---

## Runtime entrypoints

| Команда | Назначение |
|---|---|
| `python -m agent_app.main` | one-shot orchestration pipeline |
| `python -m agent_app.scheduler` | автономный schedule-aware worker |
| `python -m agent_app.server_startup` | preflight, readiness, ArenaGo bot/portfolio sync |
| `python -m agent_app.storage.postgres.apply_migrations` | применение SQL-миграций |
| `python -m agent_app.staging_runner` | controlled staging pipeline с mock/staging данными |
| `python -m agent_app.research` | research worker entrypoint |

---

## Интеграции

### MOEX ISS

Используется для публичных рыночных данных: instruments, candles, trades, marketdata/top-of-book proxy. Нормализация запросов находится в `agent_app/modules/external_request_gateway/providers.py`.

### ArenaGo

Используется как broker/sandbox-интеграция: `get_bots`, `get_positions`, `get_trades`, `submit_order`. Проект отдельно проверяет market session, bot identity и portfolio sync.

### Polza AI

Используется для LLM-задач в текстовых модулях. В gateway включены ограничения: JSON response format, task-specific model routing, запрет нежелательных моделей, throttle по минуте/часу/дню.

Контракты интеграций лежат в [`integration/`](integration/).

---

## Тестирование и контрактная дисциплина

Проект содержит несколько уровней тестов:

- contract tests для всех 22 module markdown specs;
- проверки PostgreSQL-миграций, схем, таблиц и seed-вселенной;
- unit tests для отдельных метрик и сервисов;
- runtime tests для live/sandbox turnover и feature quality patches;
- тесты gateway parsing и edge cases публичных источников;
- тесты нормализации, data quality penalties, quote proxy handling и volatility lookback.

Главная ценность тестов — они защищают не только код, но и архитектурный контракт: модули, stores, forbidden actions, acceptance criteria и readiness views.

---

## Где искать подробности

| Файл/папка | Что внутри |
|---|---|
| [`modules/`](modules/) | полные спецификации 22 модулей |
| [`MODULE_DATABASE_ACCESS_MATRIX.md`](MODULE_DATABASE_ACCESS_MATRIX.md) | кто какие stores читает и пишет |
| [`MODULE_FORMULA_INDEX.md`](MODULE_FORMULA_INDEX.md) | индекс формул и расчётных правил |
| [`agent_app/modules/`](agent_app/modules/) | реализации сервисов, репозиториев и метрик |
| [`agent_app/storage/postgres/migrations/`](agent_app/storage/postgres/migrations/) | PostgreSQL schema, seeds, readiness, analytics views |
| [`integration/arena_go_contract.md`](integration/arena_go_contract.md) | контракт ArenaGo |
| [`integration/polza_ai_contract.md`](integration/polza_ai_contract.md) | контракт Polza AI |
| [`docker/`](docker/) | compose-файлы для local/prod-like запуска |
| [`scripts/`](scripts/) | startup, deploy check, controlled staging |
| [`tests/`](tests/) | тесты архитектурных контрактов и runtime-поведения |

---

## Разработка новых модулей и изменений

При добавлении логики важно сохранять стиль проекта:

1. Сначала описать контракт модуля или изменение существующего контракта.
2. Явно указать входы, выходы, stores, TTL/freshness, failure policy и forbidden actions.
3. Не обращаться к внешним API напрямую — только через `External Request Gateway Module`.
4. Не писать в governance stores без отдельного разрешённого процесса.
5. Любой новый признак должен иметь `metric_name`, `metric_group`, `metric_type`, `horizon`, `contour`, `timestamp`, `ttl_seconds`, `confidence_score`, `source_refs` и версию расчёта/модели.
6. Для runtime-кода держать две реализации доступа к данным, где это возможно: `InMemory...Repository` для тестов и `Postgres...Repository` для реального запуска.
7. Обновить тесты и миграции, если меняются stores или контракты.

---

## Коротко

HolyMOEX — это не “бот, который спросил LLM покупать или продавать”. Это попытка построить управляемую, проверяемую и расширяемую торгово-аналитическую систему:

```text
contracts → orchestration → gateway → raw stores → features → decisions → risk → execution → audit
```

Сильная сторона проекта — не отдельная формула, а архитектура: изолированные модули, единый gateway, строгие хранилища, quality-aware features, risk gates, readiness checks и наблюдаемость всего цикла.
