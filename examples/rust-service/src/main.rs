use local_ai_rust_service::app;
use tokio::net::TcpListener;

#[tokio::main]
async fn main() {
    let listener = TcpListener::bind("0.0.0.0:8080")
        .await
        .expect("bind API listener");
    axum::serve(listener, app()).await.expect("serve API");
}
