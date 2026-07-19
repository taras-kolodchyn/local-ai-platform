# План статті для DOU

Робочий заголовок: **Підіймаємо локальну платформу для coding agents на Mac: Qwen, Codex, Hermes, LiteLLM і pgvector**

Статус: виконано. Повний текст: [docs/dou-article.md](dou-article.md).
План збережено як редакційний checklist; твердження про agent failure у статті
відповідають фактичному прогону, а не початково очікуваному happy path.

## Вступ від першої особи

- Яку практичну проблему я розв'язував: локальний coding agent, а не ще один чат.
- Для кого матеріал: backend/platform/DevSecOps інженери з Apple Silicon Mac і 32–48+ ГБ unified memory.
- Що читач отримає: відкритий репозиторій, одну команду запуску, чесні результати й відомі межі.

## Постановка задачі й критерії готовності

- Один OpenAI-compatible endpoint для Codex, Hermes та майбутніх агентів.
- Локальні generation/embedding models, RAG, MCP, cache, observability.
- Offline by default, localhost only, без Docker socket.
- Сумісність вважається доведеною лише реальними smoke/integration tests.

## Чим coding agent відрізняється від локального чату

- Shell, filesystem, Git, MCP і approvals суттєво розширюють attack surface.
- Qwen — inference backend; платформою її роблять gateway, policy, retrieval і telemetry.

## Архітектура

- Вставити component diagram з `docs/architecture.md`.
- Показати host/Compose trust boundary і чесну межу Metal на macOS.
- Пояснити model aliases та замінність runtime.

## Чому Docker Model Runner і Qwen

- Порівняння DMR, Ollama, MLX-LM і прямого llama.cpp з ADR.
- Точний artifact, quantization, context, unified-memory і disk trade-offs.
- Реальна проблема першого запуску: stale Docker Desktop backend; безпечне відновлення.

## LiteLLM як центр платформи

- `/v1`, virtual keys, rate limits, routing, usage, Responses bridge.
- Чому `use_chat_completions_api: true` тут принциповий.
- Що перевірив smoke-test: Chat Completions, Responses, streaming, tool round trip.

## Codex і Hermes через один gateway

- Показати згенеровані configs без ключів.
- Codex: sandbox, approvals, AGENTS.md, direct read-only MCP.
- Hermes: custom endpoint, `${LITELLM_API_KEY}`, MCP include lists; короткий one-shot result.
- Compatibility table: pass / degraded / fail / not tested.

## П'ять різних «кешів»

- LiteLLM response cache, semantic cache, runtime KV/prompt cache, embedding reuse, pgvector KB.
- Чому agent requests не кешуються автоматично.
- Вставити cache miss/hit evidence і metric name.

## Локальний RAG і MCP

- Scanner -> exclusions -> deterministic chunks -> Qwen embeddings -> HNSW cosine -> bounded results.
- Metadata, incremental update, stale deletion.
- Offline tools: filesystem/Git/PostgreSQL/custom retrieval; окремий read-only DB role.
- Prompt injection і retrieval poisoning boundaries.

## Observability без витоку коду

- Prometheus metrics, metadata-only JSON logs через Alloy/Loki, provisioned Grafana dashboard.
- Які поля навмисно не логуються.
- Вставити Grafana screenshot і sentinel leakage test.

## Що насправді робить `make up`

- Послідовність doctor -> DMR/model -> Compose -> migrations -> virtual key -> configs -> smoke.
- Повторний запуск, offline prerequisites, clean/reset semantics.

## Наскрізний Rust-кейс

Структура кожного підкейсу: **задача -> рішення агента -> використані технології -> перевірений результат**.

1. Знайти контрольовану SQL injection і відсутній JWT.
2. Знайти через retrieval правило про 30-секундний retry ceiling.
3. Додати regression/integration tests, спостерігати перший failure, виправити.
4. Узгодити README, non-root Dockerfile і Helm resources.
5. Показати фінальний diff Codex.
6. Повторити коротший bounded сценарій Hermes.

Evidence:

- Codex/Hermes result: [agent validation](results/agent-validation-2026-07-19.md)
- benchmark: [machine-readable record](results/benchmark-2026-07-19.json)
- tool/retrieval integration: `make smoke-test`
- Grafana: [Playwright screenshot](../output/playwright/grafana-local-ai-platform.png)
- release commit: the public repository history containing these artifacts

## Реальні помилки й обмеження

- Stale Docker Desktop daemon.
- Великий first pull і disk headroom.
- Model cold start, long-context memory, local tool-call reliability.
- Те, що свідомо відкладено: semantic cache, active fallback runtime, A2A, connected GitHub MCP writes.
- Де локальний Qwen поступається frontier-моделям; не маскувати failed/degraded cases.

## Benchmark

- Reference Mac, exact versions/digests, 64K context, one cold + three warm samples.
- Chat latency/tokens, retrieval latency/result count, memory/disk observations.
- Не робити універсальних висновків з одного Mac.

## Безпека

- Локальність не дорівнює безпеці.
- Loopback, scoped keys, read-only MCP, separate DB role, deny-by-default, no Docker socket.
- Residual risk людського підтвердження model-generated shell/write actions.

## Висновок

- Прямо відповісти: чи можна отримати відтворювану локальну agent platform на Apple Silicon і якою ціною.
- Дати одну команду clone/up, посилання на release і короткий список перевірених/неперевірених можливостей.

## План ілюстрацій

1. Component/trust-boundary Mermaid diagram.
2. `make up` із зеленим підсумком без секретів.
3. LiteLLM Responses tool-call artifact (redacted/synthetic only).
4. Grafana dashboard: gateway, retrieval, MCP, Postgres/Redis, logs.
5. Codex final diff і passing Rust tests.
6. Compatibility/benchmark table з датою й commit.
