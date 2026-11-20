# Contributing

Open an issue for an architectural change. For vulnerabilities, follow the [private reporting instructions](SECURITY.md).

Keep `make up` as the supported entry point on Apple Silicon and the offline profile as the default. Add or update tests for behavior changes and document the operator-facing commands in the relevant guide.

## Verify a change

Run `make lint` and `make test`. With the platform running on an Apple Silicon Mac, run `make smoke-test`. Include the checks you ran and any checks you skipped in the pull request. GitHub CI exercises Linux tests and validates Compose configuration. The macOS smoke test also exercises local inference and the running services. [Verification guide](README.md#verification)

For compatibility changes, update the [capability matrix](docs/capability-matrix.md) and link the official upstream documentation. Record the verification date, exact component versions and commands. For inference changes, also record the Mac's memory, model identity and context size.

Keep generated credentials, private source, indexed data, model files and request bodies out of commits and pull requests.
