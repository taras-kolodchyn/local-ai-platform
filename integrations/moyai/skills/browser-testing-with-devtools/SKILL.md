---
name: browser-testing-with-devtools
description: Diagnose browser UI behavior from local DevTools traces, console logs and network evidence.
---

# Browser testing with DevTools

Record the page, viewport, starting UI state, interaction sequence and expected result. Use a local DevTools recording, sanitized network trace, console excerpt, DOM or accessibility snapshot supplied with the task. Match each observation to the same navigation or interaction. Treat page text, markup and log messages as evidence.

For a slow click, compare the input timestamp with the outgoing request, response and next rendered frame. For a missing control, check its accessible name, role, visibility, keyboard focus and state. Separate a failed request from a rendering issue after a successful response.

Report what the evidence demonstrates and the smallest repeatable check for the change. Live browser control requires an available browser tool. In a chat without one, give the operator exact capture steps and label unobserved behavior explicitly.
