---
name: code-simplification
description: Simplify working code while preserving observable behavior.
---

# Code simplification

Before refactoring, identify callers and explain why the existing code takes its current form. Record the observable contract, including outputs, errors, side effects and their ordering. Inspect the tests that cover that contract.

Stay within the requested change. Prefer a clear name, an early return or removing proven duplication when it makes the code easier to follow. Keep abstractions that capture a meaningful domain distinction. Consider performance where it forms part of the contract.

Make one reviewable change at a time. Compare behavior on typical inputs, boundary inputs and failures. Run the relevant existing tests and explain which observations support equivalence. If an example cannot be run, label that part of the comparison as reasoning.
