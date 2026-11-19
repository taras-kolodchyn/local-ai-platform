//! Integration tests for retry delay boundaries.
//!
//! This module verifies the exact retry delays for both public functions
//! bounded_backoff_ms and legacy_retry_delay_ms when the low 16 bits match
//! but the full 32-bit attempt values differ.
//!
//! The larger input can reach the ceiling even when its low 16 bits match
//! the smaller input because the ceiling is applied after the exponential
//! calculation, not based on the low 16 bits alone.

use local_ai_rust_service::{bounded_backoff_ms, legacy_retry_delay_ms};

#[test]
fn retry_boundaries_match_16_bit_inputs() {
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