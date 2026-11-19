//! Integration tests for retry delay boundaries at the 30-second ceiling.
//!
//! This module verifies that both `bounded_backoff_ms` and `legacy_retry_delay_ms`
//! return exactly 30,000 milliseconds for retry attempts at the upper bit of a u32.
//!
//! The top bit of a u32 (bit 31) is significant because it represents the boundary
//! where signed 32-bit arithmetic would overflow. However, since these functions
//! operate on u32 inputs and use saturating arithmetic, setting the top bit does
//! not reset the delay. The policy is designed to cap at 30 seconds regardless of
//! the attempt number, ensuring consistent behavior for high retry counts.

use local_ai_rust_service::{bounded_backoff_ms, legacy_retry_delay_ms};

#[test]
fn retry_boundaries_at_ceiling() {
    // Test the specific values mentioned in the issue: 2^31 - 1, 2^31, and 2^31 + 1
    let attempt_1 = 2_147_483_647_u32; // 2^31 - 1
    let attempt_2 = 2_147_483_648_u32; // 2^31
    let attempt_3 = 2_147_483_649_u32; // 2^31 + 1

    // All should return exactly 30,000 ms
    assert_eq!(bounded_backoff_ms(attempt_1), 30_000);
    assert_eq!(bounded_backoff_ms(attempt_2), 30_000);
    assert_eq!(bounded_backoff_ms(attempt_3), 30_000);

    assert_eq!(legacy_retry_delay_ms(attempt_1), 30_000);
    assert_eq!(legacy_retry_delay_ms(attempt_2), 30_000);
    assert_eq!(legacy_retry_delay_ms(attempt_3), 30_000);
}