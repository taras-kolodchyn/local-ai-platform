use local_ai_rust_service::{bounded_backoff_ms, legacy_retry_delay_ms};

#[test]
fn bounded_backoff_respects_30_second_ceiling() {
    // Test that the new function respects the 30-second ceiling
    assert_eq!(bounded_backoff_ms(0), 100);
    assert_eq!(bounded_backoff_ms(1), 200);
    assert_eq!(bounded_backoff_ms(2), 400);
    assert_eq!(bounded_backoff_ms(3), 800);
    assert_eq!(bounded_backoff_ms(4), 1600);
    assert_eq!(bounded_backoff_ms(5), 3200);
    assert_eq!(bounded_backoff_ms(6), 6400);
    assert_eq!(bounded_backoff_ms(7), 12800);
    assert_eq!(bounded_backoff_ms(8), 25600);
    assert_eq!(bounded_backoff_ms(9), 30000); // Ceiling at 30 seconds
    assert_eq!(bounded_backoff_ms(10), 30000); // Still at ceiling
    assert_eq!(bounded_backoff_ms(100), 30000); // Even extreme inputs are capped
}

#[test]
fn legacy_retry_delay_delegates_to_bounded_backoff() {
    // Test that the legacy function properly delegates to the new policy
    assert_eq!(legacy_retry_delay_ms(0), 100);
    assert_eq!(legacy_retry_delay_ms(1), 200);
    assert_eq!(legacy_retry_delay_ms(2), 400);
    assert_eq!(legacy_retry_delay_ms(3), 800);
    assert_eq!(legacy_retry_delay_ms(4), 1600);
    assert_eq!(legacy_retry_delay_ms(5), 3200);
    assert_eq!(legacy_retry_delay_ms(6), 6400);
    assert_eq!(legacy_retry_delay_ms(7), 12800);
    assert_eq!(legacy_retry_delay_ms(8), 25600);
    assert_eq!(legacy_retry_delay_ms(9), 30000); // Ceiling at 30 seconds
    assert_eq!(legacy_retry_delay_ms(10), 30000); // Still at ceiling
    assert_eq!(legacy_retry_delay_ms(100), 30000); // Even extreme inputs are capped
}

#[test]
fn large_attempt_obeys_documented_ceiling() {
    // This test specifically verifies the issue requirement that the ceiling is 30 seconds
    // Previously this would return 60000 (60 seconds) but now should return 30000 (30 seconds)
    assert_eq!(bounded_backoff_ms(9), 30000); // 30 seconds ceiling
    assert_eq!(legacy_retry_delay_ms(9), 30000); // Should also be 30 seconds
}