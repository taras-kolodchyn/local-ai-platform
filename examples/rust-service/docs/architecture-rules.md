# Internal architecture rules

These rules are indexed into the local pgvector knowledge base and should be treated as project policy, not inferred from existing code.

1. Every endpoint except `/health` must reject missing, invalid, expired, or wrongly scoped JWT bearer tokens.
2. SQL values must use typed parameters. Building SQL by interpolating request data is forbidden.
3. Retry delays must use one shared helper and have a hard ceiling of 30,000 milliseconds.
4. Runtime containers must use a numeric non-root user and a read-only root filesystem where the platform permits it.
5. Kubernetes workloads must define both requests and limits; CPU limits above `500m` and memory limits below `128Mi` require measured justification.
6. Security regressions require a test that fails against the vulnerable implementation.
