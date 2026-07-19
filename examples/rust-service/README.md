# Reports API training fixture

This deliberately imperfect service is the repeatable Codex/Hermes exercise used in the article. Do not deploy it.

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
