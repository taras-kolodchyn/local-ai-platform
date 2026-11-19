//! Integration tests for retry delay boundaries.
//!
//! This module verifies that `bounded_backoff_ms` and `legacy_retry_delay_ms`
//! produce the correct delays for matching low-byte pairs.
//!
//! The shared policy uses a base delay of 100 ms and doubles it for each
//! attempt. However, when the attempt number has the same low byte but
//! different high bytes, the delay can differ due to the ceiling of 30,000 ms.
//!
//! For example, attempt 512 (0x200) and 0 have the same low byte (0) but
//! different high bytes (2 and 0). The delay for attempt 0 is 100 ms, but
//! for attempt 512 it is 30,000 ms because 100 * 2^512 exceeds the ceiling.
//!
//! This test ensures that the policy correctly handles such cases.

use local_ai_rust_service::{bounded_backoff_ms, legacy_retry_delay_ms};

#[test]
fn test_retry_delay_boundaries() {
    // Test pairs with same low byte but different delays
    let test_cases = [
        // (attempt_a, attempt_b, expected_delay_a, expected_delay_b)
        (0, 512, 100, 30_000),
        (1, 513, 200, 30_000),
        (2, 514, 400, 30_000),
    ];

    for (attempt_a, attempt_b, expected_a, expected_b) in test_cases {
        let delay_a = bounded_backoff_ms(attempt_a);
        let delay_b = bounded_backoff_ms(attempt_b);
        assert_eq!(delay_a, expected_a, "bounded_backoff_ms({attempt_a}) should be {expected_a} ms");
        assert_eq!(delay_b, expected_b, "bounded_backoff_ms({attempt_b}) should be {expected_b} ms");

        // Also test the legacy function which should behave identically
        let legacy_a = legacy_retry_delay_ms(attempt_a);
        let legacy_b = legacy_retry_delay_ms(attempt_b);
        assert_eq!(legacy_a, expected_a, "legacy_retry_delay_ms({attempt_a}) should be {expected_a} ms");
        assert_eq!(legacy_b, expected_b, "legacy_retry_delay_ms({attempt_b}) should be {expected_b} ms");
    }
}