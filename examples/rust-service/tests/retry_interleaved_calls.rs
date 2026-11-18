//! Integration test for interleaved retry calls across both Rust entry points.
//!
//! This test verifies that both `bounded_backoff_ms` and `legacy_retry_delay_ms`
//! behave independently when called in interleaved sequences. The functions
//! share the same underlying policy but are separate entry points.
//!
//! Migration note: Callers should use `bounded_backoff_ms` directly.
//! The deprecated `legacy_retry_delay_ms` delegates to the same capped implementation.
//! During migration, both functions behave identically.
//!
//! The test interleaves calls to both functions to ensure that:
//! 1. A capped attempt on one function doesn't affect the other
//! 2. A subsequent return to small attempts works correctly on both
//! 3. The exact millisecond delays are preserved
//!
//! Example interleaved sequence:
//! - Call bounded_backoff_ms(14) -> 30,000 ms (capped)
//! - Call legacy_retry_delay_ms(15) -> 30,000 ms (capped)
//! - Call bounded_backoff_ms(0) -> 100 ms (small)
//! - Call legacy_retry_delay_ms(2) -> 400 ms (small)

use local_ai_rust_service::{bounded_backoff_ms, legacy_retry_delay_ms};

#[test]
fn interleaved_calls_preserve_exact_delays() {
    // Test the exact millisecond delays for interleaved calls
    // First, exercise a capped attempt on the new function
    assert_eq!(bounded_backoff_ms(14), 30_000); // 100 * 2^14 = 1,638,400 -> capped
    
    // Then, exercise a capped attempt on the legacy function
    assert_eq!(legacy_retry_delay_ms(15), 30_000); // 100 * 2^15 = 3,276,800 -> capped
    
    // Then, return to small attempts on both functions
    assert_eq!(bounded_backoff_ms(0), 100); // 100 * 2^0 = 100 ms
    assert_eq!(legacy_retry_delay_ms(2), 400); // 100 * 2^2 = 400 ms
    
    // Test that the legacy function still behaves correctly after the new function was called
    assert_eq!(legacy_retry_delay_ms(14), 30_000); // Should still cap at 30,000 ms
    assert_eq!(bounded_backoff_ms(15), 30_000); // Should still cap at 30,000 ms
}

#[test]
fn interleaved_calls_in_reverse_order() {
    // Test the same sequence in reverse order
    // First, exercise a capped attempt on the legacy function
    assert_eq!(legacy_retry_delay_ms(15), 30_000); // 100 * 2^15 = 3,276,800 -> capped
    
    // Then, exercise a capped attempt on the new function
    assert_eq!(bounded_backoff_ms(14), 30_000); // 100 * 2^14 = 1,638,400 -> capped
    
    // Then, return to small attempts on both functions
    assert_eq!(legacy_retry_delay_ms(0), 100); // 100 * 2^0 = 100 ms
    assert_eq!(bounded_backoff_ms(2), 400); // 100 * 2^2 = 400 ms
    
    // Test that the new function still behaves correctly after the legacy function was called
    assert_eq!(bounded_backoff_ms(15), 30_000); // Should still cap at 30,000 ms
    assert_eq!(legacy_retry_delay_ms(14), 30_000); // Should still cap at 30,000 ms
}