disable_mlock = true

vault {}

auto_auth {
  method "approle" {
    config = {
      role_id_file_path                   = "/run/openbao-auth/role-id"
      secret_id_file_path                 = "/run/openbao-auth/secret-id"
      remove_secret_id_file_after_reading = false
    }
  }
}

template_config {
  static_secret_render_interval = "10s"
}

env_template "UI_PASSWORD" {
  contents             = "{{ with secret \"secret/data/litellm/UI_PASSWORD\" }}{{ .Data.data.key }}{{ end }}"
  error_on_missing_key = true
}

exec {
  command                   = ["/bin/bash", "/opt/openbao/start-gateway.sh"]
  restart_on_secret_changes = "always"
  restart_stop_signal       = "SIGTERM"
}
