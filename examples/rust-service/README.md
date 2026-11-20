# Reports API training fixture

This deliberately imperfect service is the repeatable Codex/Hermes exercise used in the article. Do not deploy it. The Rust training crate is not published to a registry.

The Rust training crate is published under the MIT license and is intended for educational purposes only.

## Documented contract

- `GET /health` is public.
- `GET /reports?owner=alice` requires a bearer JWT and uses a parameterized database query.
- Retry delays follow the shared architecture rule and never exceed 30 seconds.
- The runtime image uses a non-root user.

The current implementation intentionally disagrees with this contract. The agent must find the gaps, use the indexed rules, add regression tests, and leave a reviewable diff.

```sh
cargo test
cargo run
curl http://127.0.0.1:8080/health
```

## Running focused Rust retry tests

Contributors can run only the Rust retry regression integration target using:

```sh
cargo test --locked --manifest-path examples/rust-service/Cargo.toml --test retry_regression
```

Both `bounded_backoff_ms` and `legacy_retry_delay_ms` are checked against the 30,000 ms ceiling.

## Running the router construction test

Contributors can run the `health` integration target that checks that the application router can be constructed using:

```sh
cargo test --locked --manifest-path examples/rust-service/Cargo.toml --test health
```

The test function is `application_router_can_be_constructed`.