# Contributing

Thank you for improving Local AI Platform.

1. Open an issue for architectural or security-sensitive changes.
2. Keep changes small and preserve `make up` on Apple Silicon.
3. Add or update tests for behavior changes.
4. Run `make lint`, `make test`, and the relevant smoke tests.
5. Update the capability matrix when changing a version or compatibility claim.

Never commit model files, `.env`, generated keys, private repositories, indexed content, or logs containing prompts/source.

Compatibility PRs should include host model, unified memory, macOS, Docker Desktop, Model Runner, LiteLLM, agent, model artifact/digest, context size, exact commands, and observed pass/fail results.
