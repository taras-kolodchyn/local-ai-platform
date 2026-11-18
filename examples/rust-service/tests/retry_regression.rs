use local_ai_rust_service::{bounded_backoff_ms, legacy_retry_delay_ms};

#[test]
fn bounded_backoff_handles_large_attempts() {
    // Test that bounded_backoff_ms caps at 30,000 ms even with large attempts
    assert_eq!(bounded_backoff_ms(100), 30_000);
}

#[test]
fn legacy_retry_delay_obeyed_ceiling() {
    // Test that legacy_retry_delay_ms also caps at 30,000 ms
    assert_eq!(legacy_retry_delay_ms(100), 30_000);
}

#[test]
fn bounded_backoff_starts_at_100ms() {
    assert_eq!(bounded_backoff_ms(0), 100);
}

#[test]
fn bounded_backoff_doubles_each_attempt() {
    assert_eq!(bounded_backoff_ms(1), 200);
    assert_eq!(bounded_backoff_ms(2), 400);
    assert_eq!(bounded_backoff_ms(3), 800);
}

#[test]
fn legacy_retry_delay_matches_bounded_backoff() {
    // Ensure legacy function produces same results as new one
    for i in 0..10 {
        assert_eq!(legacy_retry_delay_ms(i), bounded_backoff_ms(i));
    }
}