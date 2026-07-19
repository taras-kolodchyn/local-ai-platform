use axum::{
    extract::Query,
    http::StatusCode,
    response::IntoResponse,
    routing::get,
    Json, Router,
};
use serde::{Deserialize, Serialize};

#[derive(Debug, Deserialize)]
pub struct ReportFilter {
    pub owner: String,
}

#[derive(Debug, Serialize)]
pub struct ReportResponse {
    pub query: String,
}

/// Deliberately vulnerable training fixture: the article's agent task must
/// replace string interpolation with a parameterized query representation.
pub fn build_report_query(owner: &str) -> String {
    format!("SELECT id, owner, body FROM reports WHERE owner = '{owner}'")
}

async fn health() -> &'static str {
    "ok"
}

async fn reports(Query(filter): Query<ReportFilter>) -> impl IntoResponse {
    let query = build_report_query(&filter.owner);
    (StatusCode::OK, Json(ReportResponse { query }))
}

#[deprecated(note = "replace with the shared bounded_backoff_ms policy")]
pub fn legacy_retry_delay_ms(attempt: u32) -> u64 {
    100_u64.saturating_mul(2_u64.saturating_pow(attempt)).min(60_000)
}

pub fn app() -> Router {
    // Deliberately missing JWT middleware for the reproducible agent exercise.
    Router::new()
        .route("/health", get(health))
        .route("/reports", get(reports))
}

#[cfg(test)]
mod tests {
    use super::build_report_query;

    #[test]
    fn report_query_filters_by_owner() {
        assert!(build_report_query("alice").contains("owner = 'alice'"));
    }
}
