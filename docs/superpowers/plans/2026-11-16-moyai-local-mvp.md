# Local Moyai MVP implementation plan

**Goal:** Run the real pinned Moyai UI, API, agent and Temporal workflow locally
through LiteLLM's `local-qwen`, and demonstrate files, shell commands and tests.

**Architecture:** Moyai `c375632aea66f6695b66158b5edf6ee4983dad72` from official
image digest `sha256:05e9b95fdd96507b39784b9d8531758efdf2db30f0bceb03c161e2bd46b43419`.
A public Runtime workspace injection selects a per-session SSH workspace in a
separate local nonroot container. Temporal, encrypted application data and working
files persist in dedicated local volumes. Fixed TCP relays connect the private
application network to LiteLLM and publish only `127.0.0.1:8787`.

**Scope:** Single-user coding MVP. Native shell output is the terminal surface;
the Files panel contains explicitly saved artifacts. Desktop/browser streaming,
cloud snapshots, external accounts and production multi-user isolation are outside
this MVP. Inference stays on the Mac's existing Model Runner/Metal path.

## Constraints and decisions

- No Modal/AWS credentials or API calls; no Docker socket or host home mounts.
- No external runtime egress from Moyai, Temporal or the agent sandbox.
- Keyed workspace directories include session ID AND authority scope.
- A private generated admin password respects upstream's 12-character minimum.
- Keep the default stack unchanged; `make moyai-up` first uses supported `make up`.
- Build downloads are allowed; application runtime remains local.

## Tasks

- [x] Add failing tests for workspace identity, restored-ref boundaries and
  fixed-destination relays, then implement the local integration helpers.
- [x] Build pinned app, local SSH sandbox and Temporal services; add private
  provisioning and `moyai-up`, `moyai-down`, `moyai-smoke` commands.
- [x] Run real authenticated Moyai turns: create code/tests, execute pytest,
  export files, follow up, restart and verify persistence/session separation.
- [x] Verify blocked external egress, absence of sockets/cloud credentials,
  loopback publishing, then run lint, full local tests and core live smoke.
- [x] Fresh review, document exact versions and observed limitations, leave
  runnable MVP and reviewable changes. Commit/push only if requested.

## Review focus

Cross-session directory reuse; SSH ref substitution; shell access to controller
credentials; hidden cloud/runtime downloads; real streamed Responses tool calls;
file artifacts versus live files; restart persistence and failed-turn cleanup.

## Result

Verified 2026-11-16: `make moyai-up`, `make moyai-smoke RESTART=1`, `make lint`,
`make test` and `make smoke-test` passed. The local Rust demo check was skipped
because Cargo is unavailable. Native pytest returned exit code 0 and three
passing tests before and after the restart. Independent SSH observations matched
the exported program/test hashes and found a different, empty conversation
directory. Controlled key replacement and unchanged-start behavior also passed.

Fresh review findings were addressed: actual virtual-key scope verification,
credential reload/bootstrap recovery, independent acceptance evidence and removal
of external font links. The model profile uses sequential tools, temperature 0
and explicit structured-call instructions; textual tool markup cannot satisfy the
acceptance checks. Metadata receipts remain in ignored `.local/moyai/` files.
