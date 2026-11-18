---
name: security-and-hardening
description: Review trust boundaries and strengthen input validation, authentication and sensitive data handling.
---

# Defensive code and configuration review

Identify the protected asset, who can supply each input, and where the application makes a trust decision. Trace data across validation, authorization, storage and logging. Include inputs originating in files, tool results and model-generated text.

Inspect the existing controls against the intended policy. Check ownership on the server, default permissions, parameterized database access, encoding at output boundaries and redaction of sensitive values. Review only the supplied code, configuration and local dependency information. State when a claim requires evidence unavailable offline.

For each material gap, give the affected location, consequence, narrow remediation and a regression check using benign fixtures. Test permitted and denied cases through the normal application interface, keeping real credentials and user records out of test data.
