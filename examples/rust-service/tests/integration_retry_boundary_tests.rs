//! Integration tests for retry delay boundaries.
//!
//! This module verifies that both public retry APIs respect the 30-second ceiling
//! for attempt numbers whose low 24 bits are identical to the boundary cases
//! described in the issue.

use local_ai_rust_service::{bounded_backoff_ms, legacy_retry_delay_ms};

#[test]
fn retry_delay_at_30s_ceiling_for_low_24_bits() {
    // Test case 1: attempt 3 (low 24 bits = 3) should return 800 ms
    assert_eq!(bounded_backoff_ms(3), 800);
    assert_eq!(legacy_retry_delay_ms(3), 800);

    // Test case 2: attempt 16_777_219 (low 24 bits = 3) should return 30_000 ms
    assert_eq!(bounded_backoff_ms(16_777_219), 30_000);
    assert_eq!(legacy_retry_delay_ms(16_777_219), 30_000);

    // Test case 3: attempt 8 (low 24 bits = 8) should return 25_600 ms
    assert_eq!(bounded_backoff_ms(8), 25_600);
    assert_eq!(legacy_retry_delay_ms(8), 25_600);

    // Test case 4: attempt 16_777_224 (low 24 bits = 8) should return 30_000 ms
    assert_eq!(bounded_backoff_ms(16_777_224), 30_000);
    assert_eq!(legacy_retry_delay_ms(16_777_224), 30_000);
}