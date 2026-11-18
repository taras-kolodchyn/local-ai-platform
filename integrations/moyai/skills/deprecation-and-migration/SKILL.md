---
name: deprecation-and-migration
description: Stage compatibility changes, migrate users and retire old interfaces.
---

# Deprecation and migration

Inventory old and new representations, their readers and writers, compatibility promises and evidence of active use. Define how an old client behaves while new versions are rolling out and what a downgrade would read.

Sequence the transition. Introduce the new representation compatibly, backfill existing data in bounded resumable batches, compare old and new results, and switch reads after validation. Remove the old write path or schema only after the agreed observation period. Define how a retry handles already migrated rows.

Give each phase a precondition, verification query or local check, and rollback boundary. Mark the first irreversible step and the evidence needed to reach it. Work through expected counts or examples locally; state explicitly when a production migration is only proposed.
