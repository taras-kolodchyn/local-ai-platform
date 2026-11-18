---
name: ci-cd-and-automation
description: Design CI pipelines, deployment checks and rollback procedures.
---

# CI and delivery automation

Trace a change from its triggering event through jobs, dependencies, artifacts and release conditions in the existing pipeline. Check whether validation runs on the revision that will ship, including changes to build inputs and lockfiles. Identify which steps require credentials or network access.

Put fast deterministic checks early. Isolate stateful tests and key caches by the inputs that determine their contents. Ensure a failed or skipped required job cannot silently promote an artifact. Distinguish build artifacts from caches and carry a specific artifact identity across the release boundary.

Give the smallest pipeline change, its trigger matrix, expected success and failure behavior, and recovery if a job is retried. Inspect local workflow definitions or saved logs offline. Describe remote execution or publication separately from checks actually run.
