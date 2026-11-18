//! Integration test for the full retry schedule specification.
//!
//! This test verifies that both `bounded_backoff_ms` and `legacy_retry_delay_ms`
//! follow the exact same retry schedule as specified in the architecture rules.
//! The schedule starts at 100 ms and doubles each time, capping at 30,000 ms.
//!
//! Migration note: Callers should use `bounded_backoff_ms` directly.
//! The legacy entry point `legacy_retry_delay_ms` will be removed in a future release.
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
fn full_retry_schedule_matches_for_both_functions() {
    // Test the exact schedule for attempts 0 through 8
    assert_eq!(bounded_backoff_ms(0), 100);
    assert_eq!(bounded_backoff_ms(1), 200);
    assert_eq!(bounded_backoff_ms(2), 400);
    assert_eq!(bounded_backoff_ms(3), 800);
    assert_eq!(bounded_backoff_ms(4), 1600);
    assert_eq!(bounded_backoff_ms(5), 3200);
    assert_eq!(bounded_backoff_ms(6), 6400);
    assert_eq!(bounded_backoff_ms(7), 12800);
    assert_eq!(bounded_backoff_ms(8), 25600);

    // Test that both functions behave identically for the first 9 attempts
    assert_eq!(legacy_retry_delay_ms(0), 100);
    assert_eq!(legacy_retry_delay_ms(1), 200);
    assert_eq!(legacy_retry_delay_ms(2), 400);
    assert_eq!(legacy_retry_delay_ms(3), 800);
    assert_eq!(legacy_retry_delay_ms(4), 1600);
    assert_eq!(legacy_retry_delay_ms(5), 3200);
    assert_eq!(legacy_retry_delay_ms(6), 6400);
    assert_eq!(legacy_retry_delay_ms(7), 12800);
    assert_eq!(legacy_retry_delay_ms(8), 25600);

    // Test the ceiling behavior for attempts 9 through 32
    for attempt in 9..=32 {
        assert_eq!(bounded_backoff_ms(attempt), 30_000);
        assert_eq!(legacy_retry_delay_ms(attempt), 30_000);
    }

    // Test the ceiling behavior for large attempts
    assert_eq!(bounded_backoff_ms(63), 30_000);
    assert_eq!(bounded_backoff_ms(64), 30_000);
    assert_eq!(bounded_backoff_ms(u32::MAX), 30_000);

    assert_eq!(legacy_retry_delay_ms(63), 30_000);
    assert_eq!(legacy_retry_delay_ms(64), 30_000);
    assert_eq!(legacy_retry_delay_ms(u32::MAX), 30_000);
}