# Benchmark method

Choose a time when other clients can pause model requests. The benchmark
unloads the configured coding and embedding models before timing.

```sh
make up
make benchmark
```

The benchmark first indexes `docs/offline.md` and `SECURITY.md` in the dedicated
`platform-checks/retrieval` scope. It leaves the artifacts on disk when it
unloads the models. Each of four rounds sends a fixed chat request with the
response cache bypassed, then searches the two documents and checks the citation.
Round one includes loading the models, and rounds two through four measure
requests with previously loaded models.

## Reading the result

The private JSON file at `.local/benchmarks/<UTC timestamp>.json` records the
Git HEAD, macOS version, architecture, unified memory, Docker **server** version,
DMR client version, configured coding model and requested context size. Chat
samples contain the HTTP status, end-to-end time, usage and completion tokens
per elapsed second. Retrieval samples contain the HTTP status, time and result
count. Request and response bodies are omitted from the JSON.
[Benchmark script](../scripts/benchmark.sh) · [Citation check](../scripts/retrieval_probe.py)

Report the cold sample separately from the three warm samples. Completion tokens
per elapsed second includes startup and request overhead. When comparing model
or platform revisions, also record active workloads and the effective image
pins, embedding model and checkout changes alongside the JSON. The script
records the last commit even when the tree has local edits.
