# Hermes and Mem0 dependency migration

Goal: replace Hermes 0.21.3 with the published v0.23.1 image, install the
catalog-pinned standalone Mem0 provider at image build time, and remove the
certifi/Pillow/PyJWT advisory findings without relaxing upstream requirements.

Preserve local model aliases, OSS pgvector storage and existing facts, profile
identities, read-only source/configuration mounts, disabled telemetry and lazy
installs, and `make up` as the supported offline-default entry point.

- [x] Inspect the new loader and plugin API; pin source and verify upstream
      dependency requirements against an isolated resolved environment.
- [x] Update runtime/agent images and dependency lock, adapt the memory loader
      and reconcile only migration-required profile settings. Verify actual
      agent memory tool discovery, not just direct provider CRUD.
- [x] Check the final installed runtime with strict upstream-lock-aware validation and `pip-audit`, including
      all three originally reported packages; run focused persistence/isolation
      checks and independent read-only candidate review.
- [x] Run `make lint`, `make test`, `make up`, `make smoke-test`, and client checks;
      document exact versions, preserved-state checks, and any skipped checks.
