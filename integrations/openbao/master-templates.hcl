env_template "LITELLM_MASTER_KEY" {
  contents = "{{ with secret \"secret/data/litellm/LITELLM_MASTER_KEY\" }}{{ .Data.data.key }}{{ end }}"
  error_on_missing_key = true
}

env_template "LITELLM_SALT_KEY" {
  contents = "{{ with secret \"secret/data/litellm/LITELLM_SALT_KEY\" }}{{ .Data.data.key }}{{ end }}"
  error_on_missing_key = true
}
