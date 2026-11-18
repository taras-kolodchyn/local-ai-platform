//! Integration test for interleaved retry calls after maximum Rust attempt.
//!
//! This test verifies the behavior when interleaving the current and deprecated
//! retry-delay entry points on independent requests. It ensures that an extreme
//! call on either API does not change subsequent small delays on the other.
//!
//! The test covers both call orders:
//! 1. Call `bounded_backoff_ms` with `u32::MAX`, then call `legacy_retry_delay_ms` with small attempts
//! 2. Call `legacy_retry_delay_ms` with `u32::MAX`, then call `bounded_backoff_ms` with small attempts
//!
//! Migration note: Both functions delegate to the same capped implementation.
//! During migration, both functions behave identically.
//!
//! The schedule is:
//! - Attempt 0: 100 ms
//! - Attempt 1: 200 ms
//! - Attempt 2: 400 ms
//! - ...
//! - Attempt 8: 25,600 ms
//! - Attempt 9: 30,000 ms (capped)
//! - Attempt 10: 30,000 ms (capped)
//! - ...
//! - Attempt 32: 30,000 ms (capped)
//! - Attempt 63: 30,000 ms (capped)
//! - Attempt 64: 30,000 ms (capped)
//! - Attempt u32::MAX: 30,000 ms (capped)

use local_ai_rust_service::{bounded_backoff_ms, legacy_retry_delay_ms};

#[test]
fn interleaved_calls_bounded_then_legacy() {
    // Call bounded_backoff_ms with u32::MAX (should cap at 30,000 ms)
    assert_eq!(bounded_backoff_ms(u32::MAX), 30_000);

    // Then call legacy_retry_delay_ms with small attempts (should be unaffected)
    assert_eq!(legacy_retry_delay_ms(0), 100);
    assert_eq!(legacy_retry_delay_ms(1), 200);
    assert_eq!(legacy_retry_delay_ms(2), 400);
}

#[test]
fn interleaved_calls_legacy_then_bounded() {
    // Call legacy_retry_delay_ms with u32::MAX (should cap at 30,000 ms)
    assert_eq!(legacy_retry_delay_ms(u32::MAX), 30_000);

    // Then call bounded_backoff_ms with small attempts (should be unaffected)
    assert_eq!(bounded_backoff_ms(0), 100);
    assert_eq!(bounded_backoff_ms(1), 200);
    assert_eq!(bounded_backoff_ms(2), 400);
}