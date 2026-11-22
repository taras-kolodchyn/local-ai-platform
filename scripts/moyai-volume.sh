#!/usr/bin/env bash

temporal_volume_current() {
  local container expected mounted owner
  container=$(docker compose "$@" ps -q moyai-temporal) || return 1
  [[ -n $container ]] || return 1

  # An existing Temporal container may still see the old volume after a rename.
  # Resolve both services' /data mounts against the configuration being started.
  expected=$(docker compose "$@" config --format json | jq -er '
    def data_volume($service):
      .services[$service].volumes[] | select(.target == "/data" and .type == "volume"
        and (.volume.subpath // "") == "" and (.read_only != true)) | .source;
    data_volume("moyai-temporal") as $source
    | select(data_volume("moyai-volume-init") == $source)
    | .volumes[$source].name // empty') || return 1
  mounted=$(docker inspect --format '{{range .Mounts}}{{if and (eq .Destination "/data") (eq .Type "volume") .RW}}{{.Name}}{{end}}{{end}}' "$container") || return 1
  [[ -n $expected && $mounted == "$expected" ]] || return 1
  owner=$(docker exec "$container" stat -c '%u:%g' /data) || return 1
  [[ $owner == 1000:1000 ]]
}

start_moyai_services() {
  if temporal_volume_current "$@" 2>/dev/null; then
    info "Temporal data volume is owned by 1000:1000; skipping moyai-volume-init"
    set -- "$@" --file "$ROOT_DIR/compose.moyai-volume-current.yaml"
  else
    info "Initializing Temporal data volume ownership"
  fi
  compose_wait Moyai "$@" up --detach --no-build --wait --wait-timeout 300
}
