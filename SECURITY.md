# Security policy

## Reporting a vulnerability

Please do not open a public issue for a vulnerability that could expose credentials, source code, or host access. Use GitHub's private vulnerability reporting for this repository.

Include the affected version/commit, reproduction steps, impact, and any suggested mitigation. Do not include real secrets or private source in the report.

## Supported versions

Until the first tagged release, only the current `main` branch receives security fixes.

## Operational warning

This project gives AI agents access to local tools. Keep it bound to localhost, review generated commands, use the default offline profile, and do not add Docker socket or broad filesystem access without a separate threat review.
