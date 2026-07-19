# Benchmark method

Benchmarks are evidence for one machine and one released commit, not a universal performance promise.

After `make up` and `make index REPO=examples/rust-service`, run:

```sh
make benchmark
```

The script writes a timestamped JSON record under `.local/benchmarks/`. It records:

- macOS version, architecture, unified memory, and platform commit;
- Docker Desktop, Model Runner, LiteLLM image, and model inventory;
- one cold and three warm chat requests plus matching retrieval requests;
- HTTP latency, token usage when reported, result counts, and status codes.

It deliberately does not record keys, prompt bodies, retrieved source bodies, or model responses. The fixed synthetic prompt and retrieval query are versioned inside the script so a result is interpretable without persisting content.

Before sampling, the script unloads both models from memory without removing their artifacts, so run 1 includes model load while runs 2-4 reuse resident models. For article-quality numbers, state the machine's workload and report the cold value separately from the warm median/range, context limit, quantization, power mode, and whether another model was resident. Do not compare results collected from different commits without saying so.
