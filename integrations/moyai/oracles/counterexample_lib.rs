//! Operator-owned original 60,000 ms policy, compatible with const functions.
//! Only these bodies replace candidate retry bodies; other edits are preserved.
pub const fn bounded_backoff_ms(attempt: u32) -> u64 {
    if attempt >= 10 { 60_000 } else { 100_u64 << attempt }
}

pub const fn legacy_retry_delay_ms(attempt: u32) -> u64 {
    if attempt >= 10 { 60_000 } else { 100_u64 << attempt }
}
