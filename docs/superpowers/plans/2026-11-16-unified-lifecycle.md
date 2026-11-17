# Unified local platform lifecycle

**User requirement:** `make up` starts the full local platform including Moyai;
`make down` stops everything it started and releases memory used by its models.
After live verification, leave the platform stopped and its models unloaded.

**Design:** Keep the existing Compose files/projects as isolated implementation
units, managed by one public lifecycle. This preserves Moyai's existing volumes,
credentials and conversations without a data migration. Moving the services into
one Compose project would change ownership and naming without improving the
requested start/stop behavior. The default remains offline and loopback-only.

**Shutdown order:** Moyai first, then every enabled core Compose profile, then
`docker model unload` for the configured Qwen and embedding models. Preserve
volumes, model artifacts, unrelated Docker projects and unrelated loaded models.
Attempt every cleanup phase after errors, return failure if any phase fails, and
show the remaining resident-model inventory. Do not stop Docker Desktop itself.

## Implementation and evidence

- [x] Add failing public-command tests for full startup, compatibility aliases,
  startup failure, ordered shutdown, unrelated-model preservation, repeated
  shutdown and reported cleanup failures. Test with controlled Docker boundaries.
- [x] Wire Moyai into `scripts/up.sh`; make `moyai-up` an alias without a second
  startup. Add `scripts/down.sh` and route `make down` through it. Make restart
  use the same full lifecycle and status show Moyai plus resident models.
- [x] Update README and Moyai documentation with the shared commands, shutdown
  semantics, official unload references, exact verified versions and limitations.
- [x] Run focused tests, `make lint`, `make test`, and `make smoke-test`; exercise
  real down/up, retained login/artifacts and model reloading; finish with real
  `make down` twice. Check both projects stopped, resident models absent and
  downloaded model IDs/volumes preserved. Record metadata-only receipts.
- [x] Obtain a fresh review of this lifecycle patch and fix significant findings
  with regression tests. Commit/push and the exact CI result are delivery steps
  recorded in the final handoff.

## Review focus

- Stop all platform clients before unloading inference, including profile-scoped
  services and Moyai's bridge attached to the core gateway network.
- Do not hide partial cleanup or model-unload errors behind a success message.
- Keep shutdown repeatable without deleting data or requiring a bootstrap.
- Keep named model unloading scoped to this platform rather than `--all`.
- Preserve existing Moyai credentials, storage and offline runtime boundaries.

## Review and verification notes

The fresh read-only review found two important environment-precedence problems.
Both received failing regression tests before fixing: managed Compose scope is
now pinned, including Moyai's explicit project/file, and startup loads `.env`
after bootstrap so provisioning, gateway inference and shutdown agree on models.
An inherited connected profile does not change the default offline startup.
The success message reports completed unload requests alongside the current
resident inventory, rather than promising against a later request from an
independent Model Runner client. Destructive `reset` remains outside this change.

Both real lifecycle cycles passed, including the final cycle after review: admin
login, history, file hashes, volume names and model IDs/tags were retained. Both
models reloaded for live inference, and final shutdown unloaded all three active
model instances/modes. A repeated shutdown also passed. The requested final host
state is stopped with no resident models. Focused lifecycle tests: eight passed. Full local
tests after review: 22 retrieval, five MCP, 17 execution including the required
database test, and 51 script tests passed; Rust was skipped locally (no Cargo).
