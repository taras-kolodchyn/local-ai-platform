---
name: shipping-and-launch
description: Prepare staged rollouts, monitoring and rollback plans.
---

# Shipping and launch

Identify the release candidate, intended audience, dependencies and prerequisites. Confirm which checks apply to that artifact. Define a staged rollout with an owner, an observable success condition and a stop condition for each stage.

Choose a user-facing signal and its baseline. Specify an observation window and the action to take when the signal deteriorates. Check whether old clients and stored data remain compatible during a rollback. A feature flag may limit exposure even when it cannot undo stored changes.

Return a go/no-go decision with evidence, outstanding checks and a rollback sequence tied to the release identity. In an offline chat, work from local receipts and produce the launch plan. Record deployment as observed only with an actual deployment receipt.
