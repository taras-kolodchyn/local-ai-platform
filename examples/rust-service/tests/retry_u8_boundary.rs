//! Integration test for retry attempts at the u8 boundary.
//!
//! This test verifies that both `bounded_backoff_ms` and `legacy_retry_delay_ms`
//! return exactly 30,000 milliseconds for attempts 255, 256, and 257.
//! These attempts cross the u8 boundary where the attempt number is narrowed
//! to a byte before calculating the delay, which would make 256 and 257 look
//! like the first attempts again if not handled correctly.
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
//!
//! Note: The u8 boundary test cases (255, 256, 257) are included to ensure
//! that the retry delay calculation correctly handles the narrowing of the
//! attempt number to a byte, which would otherwise cause incorrect behavior
//! when the attempt number exceeds 255.

use local_ai_rust_service::{bounded_backoff_ms, legacy_retry_delay_ms};

#[test]
fn bounded_backoff_u8_boundary() {
    // Test that the new function returns 30,000 ms for attempts 255, 256, and 257
    // These attempts cross the u8 boundary where narrowing to byte would make
    // 256 and 257 look like attempts 0 and 1 respectively
    assert_eq!(bounded_backoff_ms(255), 30_000);
    assert_eq!(bounded_backoff_ms(256), 30_000);
    assert_eq!(bounded_backoff_ms(257), 30_000);
}

#[test]
fn legacy_retry_delay_u8_boundary() {
    // Test that the legacy function also returns 30,000 ms for attempts 255, 256, and 257
    // These attempts cross the u8 boundary where narrowing to byte would make
    // 256 and 257 look like attempts 0 and 1 respectively
    assert_eq!(legacy_retry_delay_ms(255), 30_000);
    assert_eq!(legacy_retry_delay_ms(256), 30_000);
    assert_eq!(legacy_retry_delay_ms(257), 30_000);
}