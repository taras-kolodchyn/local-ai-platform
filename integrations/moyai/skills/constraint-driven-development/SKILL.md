---
name: constraint-driven-development
description: Record project quality thresholds and check changes against them.
---

# Development against explicit constraints

Extract existing budgets and invariants from local policy, test configuration, release criteria and the user's requirements. For each, record the measured quantity, unit, scope, threshold and evidence source. Mark a proposed threshold as provisional when the project has no measured baseline.

Build a short acceptance table. For example, a latency budget needs the request mix and percentile as well as milliseconds. A static-check report needs its scan coverage as well as its finding count. Distinguish a completed clean check from a missing, partial or unreadable report.

Compare the proposed change and its test configuration against the same contract. Call out a threshold being relaxed or coverage being removed. Present exceptions with their owner, rationale and revisit condition. In analysis mode, return the table and proposed checks in chat.
