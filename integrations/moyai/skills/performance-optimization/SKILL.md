---
name: performance-optimization
description: Profile bottlenecks and verify improvements against a baseline.
---

# Performance optimization

Define the workload, metric and target before proposing an optimization. Record the environment, input sizes, warm-up policy, cache state and concurrency. Compare like-for-like runs using several samples. Keep throughput, median and tail latency distinct.

Report the latency difference in its original units. Divide the reduction by the baseline to get a percentage, and divide baseline latency by new latency to get the speedup. Use a profile, query plan or timings across stages to identify the dominant cost and distinguish observations from possible explanations.

Change one factor and repeat the workload. Check resource use and an adjacent workload for regressions. Give the measured outcome, variability, trade-off and remaining bottleneck. If only sample measurements are supplied, explain what they establish and how to reproduce them.
