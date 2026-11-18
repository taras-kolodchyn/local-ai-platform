use local_ai_rust_service::{bounded_backoff_ms, legacy_retry_delay_ms};

#[test]
fn bounded_backoff_obeys_documented_ceiling() {
    // Test that the new function caps at 30,000 ms
    assert_eq!(bounded_backoff_ms(0), 100);
    assert_eq!(bounded_backoff_ms(1), 200);
    assert_eq!(bounded_backoff_ms(2), 400);
    assert_eq!(bounded_backoff_ms(14), 30_000); // 100 * 2^14 = 1,638,400 -> capped
    assert_eq!(bounded_backoff_ms(15), 30_000); // 100 * 2^15 = 3,276,800 -> capped
    assert_eq!(bounded_backoff_ms(u32::MAX), 30_000); // Even with max attempt, capped
}

#[test]
fn legacy_retry_delay_obeys_documented_ceiling() {
    // Test that the legacy function also caps at 30,000 ms
    assert_eq!(legacy_retry_delay_ms(0), 100);
    assert_eq!(legacy_retry_delay_ms(1), 200);
    assert_eq!(legacy_retry_delay_ms(2), 400);
    assert_eq!(legacy_retry_delay_ms(14), 30_000); // 100 * 2^14 = 1,638,400 -> capped
    assert_eq!(legacy_retry_delay_ms(15), 30_000); // 100 * 2^15 = 3,276,800 -> capped
    assert_eq!(legacy_retry_delay_ms(u32::MAX), 30_000); // Even with max attempt, capped
}