//! Integration test for retry attempts at the u64 multiplication boundary.
//!
//! This test verifies that both `bounded_backoff_ms` and `legacy_retry_delay_ms`
//! return exactly 30,000 milliseconds for attempt 57 and for attempt 58.
//! These attempts cross the u64 multiplication boundary where the uncapped
//! calculation would exceed u64::MAX, but the capped result should be 30,000 ms.
//!
//! The retry policy is:
//! - Attempt 0: 100 ms
//! - Attempt 1: 200 ms
//! - Attempt 2: 400 ms
//! - ...
//! - Attempt 57: 30,000 ms (capped)
//! - Attempt 58: 30,000 ms (capped)
//! - ...
//! - Attempt u32::MAX: 30,000 ms (capped)

use local_ai_rust_service::{bounded_backoff_ms, legacy_retry_delay_ms};

#[test]
fn bounded_backoff_u64_boundary() {
    // Test that the new function returns 30,000 ms for attempts 57 and 58
    // These attempts cross the u64 multiplication boundary
    assert_eq!(bounded_backoff_ms(57), 30_000);
    assert_eq!(bounded_backoff_ms(58), 30_000);
}

#[test]
fn legacy_retry_delay_u64_boundary() {
    // Test that the legacy function also returns 30,000 ms for attempts 57 and 58
    // These attempts cross the u64 multiplication boundary
    assert_eq!(legacy_retry_delay_ms(57), 30_000);
    assert_eq!(legacy_retry_delay_ms(58), 30_000);
}