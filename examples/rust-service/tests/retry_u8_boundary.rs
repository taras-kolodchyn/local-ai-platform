//! Both `bounded_backoff_ms` and `legacy_retry_delay_ms` return 30,000 milliseconds
//! for attempts 255, 256, and 257, across the boundary of an unsigned byte.

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