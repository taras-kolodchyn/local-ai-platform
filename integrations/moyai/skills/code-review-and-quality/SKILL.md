---
name: code-review-and-quality
description: Review a change for correctness, design, maintainability and test coverage.
---

# Code review and quality

Read the change against its base and identify the behavior promised to callers. Follow a changed value through its producers, consumers and error handling. Check compatibility with existing data, partial failure, retries and concurrent work where relevant.

Prioritize findings that affect correctness, reliability, authorization or a measurable performance requirement. For each, name the changed location, triggering condition, observed or inferred consequence and a focused fix. Verify that tests exercise the public entry point and would detect the regression. Keep style observations separate from behavior defects.

Return the most consequential findings first. If no actionable defect is established, say which paths were reviewed and where evidence is missing.
