# Codex repair pass after a failed local-model attempt

The previous turn did not meet the task: `cargo test` failed, JWT validation was only simulated, the SQL API was misleading, an invalid Axum test helper was used, a `.bak` file was left behind, MCP was not called, and the final success claim contradicted the command evidence. Repair the current dirty working tree; do not discard the baseline commit.

Acceptance criteria:

1. Before editing, call the MCP tool `local_retrieval-search_code` with repository `rust-service` and query `JWT SQL retry Docker Helm security rules`. Cite the returned policy file in your final evidence. If this call fails, report the failure instead of claiming MCP use.
2. Inspect `git status` and the full diff. Remove the accidental `.bak` file. Use the patch tool for edits; do not use `sed -i`, heredocs, or shell redirection to rewrite source files.
3. Remove `sqlx`. Implement a small typed parameterized-query representation whose SQL contains `$1` and whose separate parameter value contains the owner. Never interpolate the owner into SQL.
4. Pin `jsonwebtoken = "=10.4.0"`. Implement real HS256 validation for `sub`, `exp`, and a `scope` containing `reports:read`. Missing, malformed, invalid-signature, expired, or wrong-scope bearer tokens must return 401. Keep `/health` public. Make the router testable with an injected secret; `main` must require `JWT_SECRET` from the environment rather than contain a production fallback.
5. Replace the legacy retry helper with one shared exponential backoff capped at 30,000 ms, including large-attempt tests.
6. Use `tower = { version = "=0.5.3", features = ["util"] }` as a dev dependency and `tower::ServiceExt::oneshot` for HTTP tests. Test public health, missing token, invalid token, expired token, wrong scope, a valid token, SQL parameter separation, and the retry ceiling.
7. Keep the runtime image on a numeric non-root UID. In Helm, set `runAsNonRoot`, `readOnlyRootFilesystem`, `allowPrivilegeEscalation: false`, drop all capabilities, use `RuntimeDefault` seccomp, and keep limits within the indexed policy (`cpu <= 500m`, `memory >= 128Mi`).
8. Update README with the real JWT environment contract and explicitly say that the query object demonstrates binding but this fixture does not connect to a database.
9. Run `cargo fmt --check`, `cargo test --offline`, and `git diff --check`. Inspect the final diff. Do not claim success unless all three commands pass. State any residual limitations.

The required crates were prefetched into the host Cargo cache. Do not access unrelated paths, print credentials, publish, push, commit, or use destructive Git commands.
