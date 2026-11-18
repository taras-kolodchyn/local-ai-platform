//! Integration tests for interleaved retry calls across both entry points.
//!
//! This test verifies that both `bounded_backoff_ms` and `legacy_retry_delay_ms`
//! behave independently of each other, with each call computing its own delay
//! based on the attempt number passed in.
//!
//! The tests below verify the exact behavior for the interleaved call patterns
//! specified in the issue requirements:
//!
//! 1. Current then legacy: current(9) → 30,000 ms, current(u32::MAX) → 30,000 ms,
//!    legacy(0) → 100 ms, legacy(2) → 400 ms
//! 2. Legacy then current: legacy(9) → 30,000 ms, legacy(u32::MAX) → 30,000 ms,
//!    current(0) → 100 ms, current(2) → 400 ms
//!
//! Migration note: Callers should use `bounded_backoff_ms` directly.
//! The deprecated `legacy_retry_delay_ms` delegates to the same capped implementation.
//! During migration, both functions behave identically.
//!
//! The retry policy is:
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
fn current_then_legacy_interleaved_calls() {
    // Test the first interleaved pattern: current then legacy
    assert_eq!(bounded_backoff_ms(9), 30_000);     // 100 * 2^9 = 51,200 -> capped
    assert_eq!(bounded_backoff_ms(u32::MAX), 30_000); // Even with max attempt, capped
    assert_eq!(legacy_retry_delay_ms(0), 100);    // 100 * 2^0 = 100 ms
    assert_eq!(legacy_retry_delay_ms(2), 400);    // 100 * 2^2 = 400 ms
}

#[test]
fn legacy_then_current_interleaved_calls() {
    // Test the second interleaved pattern: legacy then current
    assert_eq!(legacy_retry_delay_ms(9), 30_000);     // 100 * 2^9 = 51,200 -> capped
    assert_eq!(legacy_retry_delay_ms(u32::MAX), 30_000); // Even with max attempt, capped
    assert_eq!(bounded_backoff_ms(0), 100);    // 100 * 2^0 = 100 ms
    assert_eq!(bounded_backoff_ms(2), 400);    // 100 * 2^2 = 400 ms
}