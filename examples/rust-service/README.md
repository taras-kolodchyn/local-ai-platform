# Reports API training fixture

This service is intentionally imperfect. Use it for local coding-agent exercises only. The original [Codex and Hermes evaluation](../../docs/results/agent-validation-2026-07-19.md) records the early experiments. Moyai uses the retry policy in its [GitHub issue workflow](../../docs/moyai-github-issues.md).

The [Cargo manifest](Cargo.toml) defines `local-ai-rust-service` 0.1.0, uses Rust edition 2024 and disables registry publication with `publish = false`. The repository includes the [Apache License 2.0](../../LICENSE).

## Documented contract

The [architecture rules](docs/architecture-rules.md) define the exercise. Compare the implementation with an indexed rule, add a regression test for the gap and submit a reviewable diff. The following observations describe revision `8fad124`, reviewed on 2026-11-20.

| Area | Required behavior | Current implementation |
| --- | --- | --- |
| Health | `GET /health` is public. | The handler returns `ok`. |
| Reports | `GET /reports` requires a valid, correctly scoped bearer JWT. SQL values use typed parameters. | The router has no JWT check. The handler returns a SQL string containing the supplied owner. |
| Retries | All callers share one helper with a 30,000 ms ceiling. | `bounded_backoff_ms` enforces the ceiling. The deprecated `legacy_retry_delay_ms` calls it. |
| Container | The runtime uses a numeric non-root user. | The [Dockerfile](Dockerfile) leaves the runtime image running as root. |

From the repository root, run the complete Rust test suite with the checked-in lockfile. The [CI workflow](../../.github/workflows/ci.yml) runs the same command.

```sh
cargo test --locked --manifest-path examples/rust-service/Cargo.toml
```

For a manual health check in an isolated development environment, run `cargo run --locked` from this crate’s directory. From a second shell in the same environment, `curl http://127.0.0.1:8080/health` prints `ok`.

## Request flow

The router and handlers are defined in [src/lib.rs](src/lib.rs).

1. When the router is constructed, `app()` registers `GET /reports` with the `reports` handler.
2. For a request such as `GET /reports?owner=alice`, the `Query<ReportFilter>` extractor reads `owner` from the query string.
3. The handler passes the owner to `build_report_query` and places the resulting string in `ReportResponse.query`.
4. The handler returns `200 OK` with that field as JSON. It never opens a database connection or executes the SQL.

## Server startup

The server starts listening on `0.0.0.0:8080` as defined in [src/main.rs](src/main.rs). The `app()` function supplies the router, and the `axum::serve()` call starts serving requests on the listener.

## Running focused Rust retry tests

The [retry regression target](tests/retry_regression.rs) covers the initial delays, the 30,000 ms ceiling and the extreme input `u32::MAX` for both `bounded_backoff_ms` and `legacy_retry_delay_ms`. Run it from the repository root.

```sh
cargo test --locked --manifest-path examples/rust-service/Cargo.toml --test retry_regression
```

## Running the router construction test

The [health target](tests/health.rs) constructs the router by calling `app()`. Its coverage ends before any HTTP request reaches a handler. Run it from the repository root.

```sh
cargo test --locked --manifest-path examples/rust-service/Cargo.toml --test health
```

The test function is `application_router_can_be_constructed`.