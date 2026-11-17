# Local Moyai UI verification

Use `make up`, register the bundled fixture as described in
[repository tasks](repository-workflow.md), and submit an ordinary retry-repair
task in one conversation. The controller supplies the verification profile's
integration-test contract; the user does not need to name its test directory.

## Rust feedback and retries

New detecting regressions belong in Cargo integration targets under `tests/*.rs`.
Inline unit tests still run as project tests. The protected oracle and compiled
60,000-ms counterexample remain independent requirements.

Check replies include `next_action`. A missing integration regression asks the
model to create that file with `write_file`; a passed check points to
`finish_repository_task`. Rechecking an unchanged current tree without an
intervening admitted write returns the latest settled repair receipt with
`reused: true` and its original operation ID. This runs no Cargo and consumes no
additional validation attempt. Failed evidence is reused only when the signed
receipt explicitly identifies a regression-requirement failure, commands and the
protected oracle passed, and there were no launch errors.
Failed processes remain eligible for a bounded retry on unchanged source.
Source changes require another validation. Every admitted write invalidates a prior pass, even
if it leaves identical bytes; that pass cannot be reused until a fresh validation.
Pending, uncertain, expired and terminal work remains
fenced. Reuse never changes signed receipt bytes or reopens a failed task.

## Bound browser download observation

The native **Download original** link remains the authenticated Files endpoint.
For a CUA demonstration, arm its download event before clicking, limit individual
operations, and impose an overall deadline. Individual API timeouts alone may
exclude browser permission handling. Keep actions in the CUA runtime and use a
locator grounded in the current page:

```js
const started = Date.now();
let timer;
const observed = (async () => {
  const event = tab.playwright.waitForEvent("download", { timeout: 8000 });
  // A failed click can leave the event waiter pending until its timeout.
  event.catch(() => {});
  await tab.playwright.getByRole("link", { name: "Download original" })
    .click({ timeout: 4000 });
  const download = await event;
  const path = await download.path({ timeout: 8000 });
  if (!path) throw new Error("The browser did not return a saved file path.");
  return { path, elapsed_ms: Date.now() - started };
})();
try {
  const result = await Promise.race([
    observed,
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error("Download observation exceeded 10 seconds.")), 10000);
    }),
  ]);
  nodeRepl.write(result);
} finally {
  clearTimeout(timer);
}
```

After a timeout, inspect the browser's visible state before taking another action.
Do not report a successful download until saved bytes match the native Files and
controller SHA-256. The deadline returns control; it does not cancel an OS dialog
or establish that the transfer failed.

## Diagnosis — 2026-11-17

At platform commit `82705209a5d220a4ae58d66a1e25bee78a367870`, before these
feedback changes, three server downloads of the existing 1,476-byte patch took
18–20 ms. The native link had the correct content length and attachment filename.
The unchanged UI button then completed in 285 ms, including the saved-path
observation, with matching SHA-256. The earlier 19-minute wait was not reproduced;
no historical network trace identifies its exact cause. The bounded observation
above prevents another silent multi-minute automation wait. These measurements
do not justify changing the functioning native download endpoint.

Private evidence: `.local/verification/download-diagnosis-20261117.json` and
the browser observations recorded with the repair acceptance results. Generated
source, downloaded artifacts and request bodies remain outside Git.
