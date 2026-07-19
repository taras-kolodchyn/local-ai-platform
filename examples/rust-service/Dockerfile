FROM rust:1.97.1-bookworm AS build
WORKDIR /src
COPY . .
RUN cargo build --locked --release

FROM debian:bookworm-slim
COPY --from=build /src/target/release/local-ai-rust-service /usr/local/bin/reports-api
EXPOSE 8080
# Deliberate exercise defect: the runtime defaults to root.
ENTRYPOINT ["/usr/local/bin/reports-api"]
