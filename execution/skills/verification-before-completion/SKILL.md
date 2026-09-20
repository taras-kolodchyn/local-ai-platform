---
name: verification-before-completion
description: Check evidence before claiming that work is complete, fixed, or passing.
---

# Verification before completion

For each claim, identify the command or observable result that would prove it.
Use available permitted tools to obtain fresh evidence and inspect the full result,
including exit status and failures. A build result does not prove tests passed;
a source inspection does not prove the program executed successfully.

If the profile cannot execute a check, give the exact suggested check and state
that it was not run. Never describe a proposed patch as an applied source change.
A successful tool call proves only what that tool actually verified.

Compare the result with each requirement. Report failures and unverified areas
explicitly. Cite source evidence and distinguish persistent saved facts from
information remembered only in the current conversation. Confirm memory writes
through the memory tool before claiming they are saved.

Adapted from Superpowers by Jesse Vincent; MIT license, see LICENSE.
