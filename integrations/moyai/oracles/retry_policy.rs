//! Operator-owned oracle. This file is outside the editable repository.
#![allow(deprecated)]
use local_ai_rust_service::{bounded_backoff_ms, legacy_retry_delay_ms};

#[test]
fn shared_policy_starts_at_one_hundred_ms() {
    for (attempt, expected) in [(0, 100), (1, 200), (2, 400), (8, 25_600)] {
        assert_eq!(bounded_backoff_ms(attempt), expected);
    }
}

#[test]
fn shared_policy_caps_without_overflow() {
    for attempt in [9, 10, 16, 32, 63, 64, 65, 1000, u32::MAX] {
        assert_eq!(bounded_backoff_ms(attempt), 30_000);
    }
}

#[test]
fn legacy_entry_point_retains_shared_policy_compatibility() {
    for attempt in [0, 1, 8, 9, 10, 32, 64, u32::MAX] {
        assert_eq!(legacy_retry_delay_ms(attempt), bounded_backoff_ms(attempt));
    }
}

#[test]
fn shared_policy_is_monotonic_and_bounded() {
    let mut previous = 0;
    for attempt in 0..=128 {
        let delay = bounded_backoff_ms(attempt);
        assert!(delay >= previous && delay <= 30_000);
        previous = delay;
    }
}
