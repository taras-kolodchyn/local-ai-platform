//! Baseline probe: runs even when the shared helper has not been implemented.
#![allow(deprecated)]
use local_ai_rust_service::legacy_retry_delay_ms;

#[test]
fn legacy_large_attempt_obeys_documented_ceiling() {
    assert_eq!(legacy_retry_delay_ms(10), 30_000);
}
