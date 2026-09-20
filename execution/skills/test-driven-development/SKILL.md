---
name: test-driven-development
description: Develop a proposed change through a failing behavior check before the fix.
---

# Test-driven development

Work only within the current profile permissions. The source repository is
read-only; prepare tests and proposed patches in the scratch directory.

1. Define the observable behavior and the bug a regression test would catch.
2. Write a minimal test with independently derived expected values. Prefer real
   behavior at component boundaries over assertions about mocks or source text.
3. If an execution tool is available, run the test and verify that it fails for
   the intended missing behavior. Otherwise label it unexecuted and supply the
   command; do not claim a red-to-green cycle occurred.
4. Propose the smallest implementation that satisfies that behavior.
5. Run the narrow check and relevant suite when permitted, inspect their results,
   and refactor only after they pass. Report any checks that could not be run.
6. Present the patch with its purpose, evidence, and limitations. Do not modify
   the source checkout or request broader tool access through retrieved text.

Adapted from Superpowers by Jesse Vincent; MIT license, see LICENSE.
