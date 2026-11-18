use local_ai_rust_service::{bounded_backoff_ms, legacy_retry_delay_ms};

#[test]
fn bounded_backoff_ceiling_applies_at_transition_points() {
    // Test the exact delay values at transition points where the ceiling first applies
    assert_eq!(bounded_backoff_ms(8), 25_600); // 100 * 2^8 = 25,600 ms
    assert_eq!(bounded_backoff_ms(9), 30_000);  // 100 * 2^9 = 51,200 ms -> capped
    assert_eq!(bounded_backoff_ms(10), 30_000); // 100 * 2^10 = 102,400 ms -> capped
}

#[test]
fn legacy_retry_delay_ceiling_applies_at_transition_points() {
    // Test the exact delay values at transition points where the ceiling first applies
    assert_eq!(legacy_retry_delay_ms(8), 25_600); // 100 * 2^8 = 25,600 ms
    assert_eq!(legacy_retry_delay_ms(9), 30_000);  // 100 * 2^9 = 51,200 ms -> capped
    assert_eq!(legacy_retry_delay_ms(10), 30_000); // 100 * 2^10 = 102,400 ms -> capped
}