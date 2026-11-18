---
name: observability-and-instrumentation
description: Select logs, metrics, traces and alerts for production behavior.
---

# Observability and instrumentation

Start from the user-visible symptom and the operational decision a signal should support. Define the event being counted and where it begins and ends. Track request rate, failures and latency distribution together so that a percentile can be interpreted against traffic volume.

Use bounded labels such as route template, operation and outcome. Put a correlation identifier in structured logs or traces for individual requests. Keep identifiers with unbounded cardinality out of metric labels. Exclude credentials, request bodies and identifying payloads from evidence.

For an alert, give the threshold, observation window, minimum traffic and operator action. Specify how to distinguish an idle service from broken collection. Provide a sample dashboard query or aggregation, and a local way to check that an event is counted exactly once.
