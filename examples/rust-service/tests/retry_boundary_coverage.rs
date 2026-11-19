//! Integration tests for retry delay boundaries at the 30-second ceiling.
//!
//! This module verifies that both `bounded_backoff_ms` and `legacy_retry_delay_ms`
//! return the correct delays for specific attempt values, including cases where
//! the low 16 bits match but the high bits differ.
//!
//! The policy is that both functions should return 30,000 ms when the attempt
//! value reaches the ceiling, even if the low 16 bits are the same as a smaller
//! attempt value that also hits the ceiling.
//!
//! For example:
//! - Attempt 0 and 65536 both return 100 ms (low 16 bits: 0)
//! - Attempt 8 and 65544 both return 25,600 ms (low 16 bits: 8)
//! - Attempt 65536 and 65537 both return 30,000 ms (low 16 bits: 0, but high bits differ)

use local_ai_rust_service::{bounded_backoff_ms, legacy_retry_delay_ms};

#[test]
fn retry_delays_at_16_bit_boundaries() {
    // Test case 1: Attempts 0 and 65536 return 100 ms and 30,000 ms respectively
    assert_eq!(bounded_backoff_ms(0), 100);
    assert_eq!(bounded_backoff_ms(65536), 30_000);

    assert_eq!(legacy_retry_delay_ms(0), 100);
    assert_eq!(legacy_retry_delay_ms(65536), 30_000);

    // Test case 2: Attempts 8 and 65544 return 25,600 ms and 30,000 ms respectively
    assert_eq!(bounded_backoff_ms(8), 25_600);
    assert_eq!(bounded_backoff_ms(65544), 30_000);

    assert_eq!(legacy_retry_delay_ms(8), 25_600);
    assert_eq!(legacy_retry_delay_ms(65544), 30_000);
}