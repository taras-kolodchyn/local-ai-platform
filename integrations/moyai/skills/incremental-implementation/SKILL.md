---
name: incremental-implementation
description: Deliver a change in small increments with a check for each one.
---

# Incremental implementation

Pick the smallest end-to-end slice that changes observable behavior and can be verified independently. Name its entry point, inputs, outputs, prerequisites and acceptance check. Choose an early slice that addresses a significant uncertainty.

Follow existing code paths. Keep each change small enough to diagnose on its own. Preserve compatibility while old and new versions coexist. Run the narrowest relevant check allowed by the environment before building the next slice. When a check fails, repair the current slice before adding more behavior.

Report each completed slice with evidence, the remaining slices in dependency order and the next useful checkpoint. In a planning-only request, present these as proposed increments.
