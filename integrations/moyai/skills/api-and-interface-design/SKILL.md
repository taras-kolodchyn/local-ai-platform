---
name: api-and-interface-design
description: Design public interfaces, type contracts and API boundaries.
---

# API and interface design

Start with an actual caller and the operation it needs. Sketch one successful exchange and one failure. Specify required fields, optional fields, units, defaults and error semantics. For a retryable operation, define how the caller knows whether the first request took effect.

Draw the boundary around the owning component. Keep storage details private. For collection reads, define ordering, limits and a stable way to continue. For a change to a public interface, trace the existing callers and decide which inputs and outputs must remain compatible.

Return a small contract with examples and checks a consumer can run. Identify a change that would break an old client and show how the proposed interface handles it.
