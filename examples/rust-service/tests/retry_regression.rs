use local_ai_rust_service::{bounded_backoff_ms, legacy_retry_delay_ms};

#[test]
fn bounded_backoff_starts_at_100ms() {
    assert_eq!(bounded_backoff_ms(0), 100);
}

#[test]
fn bounded_backoff_doubles_per_attempt() {
    assert_eq!(bounded_backoff_ms(1), 200);
    assert_eq!(bounded_backoff_ms(2), 400);
    assert_eq!(bounded_backoff_ms(3), 800);
}

#[test]
fn bounded_backoff_caps_at_30_seconds() {
    // This test verifies that the ceiling is 30,000ms (30 seconds)
    // Even for large attempt numbers, it should not exceed 30,000ms
    assert_eq!(bounded_backoff_ms(100), 30_000);
}

#[test]
fn legacy_retry_delay_delegates_to_bounded_backoff() {
    // Verify that legacy function delegates correctly to the new policy
    assert_eq!(legacy_retry_delay_ms(0), bounded_backoff_ms(0));
    assert_eq!(legacy_retry_delay_ms(1), bounded_backoff_ms(1));
    assert_eq!(legacy_retry_delay_ms(2), bounded_backoff_ms(2));
}

#[test]
fn legacy_large_attempt_obeys_documented_ceiling() {
    // This test ensures the legacy function now respects the 30-second ceiling
    // Previously it would return 60,000ms (60 seconds) for large attempts
    // Now it should cap at 30,000ms (30 seconds) as per the architecture rule
    assert_eq!(legacy_retry_delay_ms(100), 30_000);
}