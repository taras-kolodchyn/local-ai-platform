---
name: test-driven-development
description: Develop behavior through a failing test, a minimal implementation and refactoring.
---

# Test-driven development

Choose one observable behavior through a real public entry point. Locate the project's existing test harness and the smallest fixture that expresses the case. Write a focused failing assertion. Verify that the failure is the expected behavior gap rather than a broken fixture or missing dependency.

Implement the smallest correction that makes the test pass. Refactor under that test and run the closest related checks. For a regression, compare the assertion against the original behavior where possible. Prefer behavior-oriented assertions over mocks of implementation details.

Report the initial failure, correction and passing result separately. In analysis mode, present the proposed regression, expected initial failure and expected result after the change. Mark checks as unexecuted until the runtime provides an actual result.
