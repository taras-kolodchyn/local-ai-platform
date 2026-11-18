//! Integration test for the interleaved Rust retry caller contract.
//!
//! This test verifies the specific interleaved behavior requested in issue #34.
//! It covers two independent test cases, one per row below. Each row is a single
//! ordered sequence of four calls within the same test.
//!
//! | Test | First call | Second call | Third call | Fourth call |
//! | --- | --- | --- | --- | --- |
//! | Current then legacy | current(9) → 30,000 ms | current(u32::MAX) → 30,000 ms | legacy(0) → 100 ms | legacy(2) → 400 ms |
//! | Legacy then current | legacy(9) → 30,000 ms | legacy(u32::MAX) → 30,000 ms | current(0) → 100 ms | current(2) → 400 ms |
//!
//! The interleaved calls at attempts 0 and 2 must be independent of earlier calls.
//! This test ensures that the shared policy is correctly applied regardless of
//! which API is called first, and that the small delays (0 and 2) are computed
//! correctly on the opposite API.

use local_ai_rust_service::{bounded_backoff_ms, legacy_retry_delay_ms};

#[test]
fn current_then_legacy_interleaved_contract() {
    // Test case: Current then legacy
    // current(9) → 30,000 ms
    assert_eq!(bounded_backoff_ms(9), 30_000);
    // current(u32::MAX) → 30,000 ms
    assert_eq!(bounded_backoff_ms(u32::MAX), 30_000);
    // legacy(0) → 100 ms
    assert_eq!(legacy_retry_delay_ms(0), 100);
    // legacy(2) → 400 ms
    assert_eq!(legacy_retry_delay_ms(2), 400);
}

#[test]
fn legacy_then_current_interleaved_contract() {
    // Test case: Legacy then current
    // legacy(9) → 30,000 ms
    assert_eq!(legacy_retry_delay_ms(9), 30_000);
    // legacy(u32::MAX) → 30,000 ms
    assert_eq!(legacy_retry_delay_ms(u32::MAX), 30_000);
    // current(0) → 100 ms
    assert_eq!(bounded_backoff_ms(0), 100);
    // current(2) → 400 ms
    assert_eq!(bounded_backoff_ms(2), 400);
}