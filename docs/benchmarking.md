# Benchmark method

Choose a time when other clients can pause model requests. The benchmark
unloads the configured coding and embedding models before timing.

```sh
make up
make benchmark
```

The first phase indexes `docs/offline.md` and `SECURITY.md` in the dedicated
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

## Startup timings

Run `make up TIMINGS=1` to include timings in the terminal during a full
startup. The final line reports wall time for `make up`. Qwen and retrieval/MCP
each report their own elapsed time before monitoring begins. The two durations
overlap. The smoke total also includes the initial checks, monitoring and log scan.

Each Compose `up --wait` prints elapsed time from the start of its progress
stream, the container name and the reported transition. A completion such as
`Healthy` or `Exited` includes the time since the previous `Waiting` or
`Starting` event for that container. A dependency can be checked more than
once. Every three seconds, a `pending` line lists outstanding operations and
their ages. If Compose has yet to report an active operation,
the line says `awaiting Compose progress`.

Verified on macOS with Docker Compose v5.7.1 on 2026-11-22.
[Compose progress output](https://docs.docker.com/reference/cli/docker/compose/)
and [readiness options](https://docs.docker.com/reference/cli/docker/compose/up/).

## Paired repository context benchmark

`make benchmark` also runs `make benchmark-context`. Use the latter on its own
to test warm requests against the current checkout with the running stack.
It calls the same scoped search function used by the Moyai vector endpoint
and streams answers through the `local-qwen` alias.

The benchmark incrementally indexes nine public platform scripts under a unique
temporary repository scope and removes its chunks on exit. Both arms use the
same query, indexed source, model and result limit. The baseline uses the
general automatic ranking. The other arm enables file-aware selection. After
warm-up, four pairs per question alternate which arm goes first. Each request
has a fresh prompt prefix and disables the response cache.

Two questions need one file. Two need evidence from different files, including
a sourced library that the question does not name. The checks require the
expected answers and a supporting line visible to the model for each fact.
The command saves private metadata to `.local/benchmarks/*-context-*.json`,
with per-pair timings, token counts, answer and citation checks, and medians by
question and arm. First-token and total times include retrieval. A failed
focused quality check returns a nonzero exit status after writing the result.
Compare times within each question. These are small, warm source lookups.
