disable_mlock = true
api_addr = "https://openbao:8200"
cluster_addr = "https://openbao:8201"

storage "raft" {
  path = "/var/lib/openbao"
  node_id = "local-mac"
}

listener "tcp" {
  address = "0.0.0.0:8200"
  tls_cert_file = "/etc/openbao/tls/server.pem"
  tls_key_file = "/etc/openbao/tls/server.key"
  tls_min_version = "tls12"
}
