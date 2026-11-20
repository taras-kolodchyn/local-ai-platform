# LiteLLM dashboard password from OpenBao

Use this mode when an OpenBao service is reachable from the gateway container.
The default `make up` reads the dashboard password from `.env` and works with
the ordinary LiteLLM image. The optional image adds OpenBao Agent 2.7.3 to
LiteLLM 1.110.2. The Agent supervises LiteLLM and injects `UI_PASSWORD` into its
process environment. This uses OpenBao's process supervisor (upstream labels
it public beta) and works with the standard LiteLLM image and license.
[OpenBao process supervisor](https://openbao.org/docs/agent-and-proxy/agent/process-supervisor/) ·
[LiteLLM UI credentials](https://docs.litellm.ai/docs/proxy/ui)

## Prepare OpenBao

Use a KV v2 engine mounted at `secret/` and AppRole authentication mounted at
`auth/approle/`. From an authorized OpenBao CLI session, create a dedicated
read policy and AppRole. The operator who rotates the password needs separate
write access.

```sh
bao policy write litellm-ui - <<'HCL'
path "secret/data/litellm/UI_PASSWORD" {
  capabilities = ["read"]
}
HCL
bao write auth/approle/role/litellm-ui token_policies=litellm-ui
```

Write the dashboard password as a nonempty string in the `key` field. In the
following command, supply a private file from your password manager containing
the exact password bytes. A trailing newline becomes part of the password.

```sh
bao kv put -mount=secret litellm/UI_PASSWORD key=@/absolute/private/path/to/password
```

From the platform checkout, create the AppRole login files. Keep the auth
directory dedicated to this gateway. The Agent needs a usable SecretID on
restart or reauthentication. Arrange reprovisioning according to the role's
SecretID TTL and use limit. [AppRole auto-auth](https://openbao.org/docs/agent-and-proxy/autoauth/methods/approle/)

```sh
make bootstrap
(umask 077
 mkdir -p .local/openbao/auth
 bao read -field=role_id auth/approle/role/litellm-ui/role-id > .local/openbao/auth/role-id &&
 bao write -f -field=secret_id auth/approle/role/litellm-ui/secret-id > .local/openbao/auth/secret-id
)
```

## Enable and sign in

Set these entries in the checkout's private `.env`. Supply the HTTPS address
as seen by the container. For a private CA, copy its public PEM to
`.local/openbao/auth/ca.pem` and set `OPENBAO_CACERT=/run/openbao-auth/ca.pem`.
An empty `OPENBAO_CACERT` uses the image's trusted CAs.

```dotenv
LITELLM_UI_PASSWORD_SOURCE=openbao
OPENBAO_ADDR=https://bao.example.internal:8200
UI_PASSWORD=
```

Run `make up`. Open the [local LiteLLM dashboard](http://127.0.0.1:4000/ui)
and sign in with `UI_USERNAME` (default `admin`) and the password in OpenBao.
The master key, API keys, PostgreSQL and Grafana credentials use their existing
configuration. The Agent in the gateway container can read the mounted AppRole
files. No OpenBao listener or server is deployed by this mode.

## Rotate and verify

Write the new value to the same KV path and field. The Agent polls static
secrets every 10 seconds and restarts the LiteLLM process when the value
changes. Allow time for rendering and gateway startup. In-flight gateway
requests can be interrupted. Other services keep running.

Use a fresh private browser session to verify the new password, then use a
second fresh session to confirm that the previous dashboard password is
rejected. Previously issued dashboard sessions have their own lifetime.
`make smoke-test` also opens a fresh login. It reads the active password over
a pipe from a mode-0600 file in the gateway's private tmpfs. The password does
not go into the Compose environment, the host filesystem or command arguments
during this check.

To return to local credentials, set `LITELLM_UI_PASSWORD_SOURCE=env` and the
desired local `UI_PASSWORD` in `.env`, then run `make up`. A blank local
password selects the platform's existing master-key fallback.
[Agent configuration](../integrations/openbao/agent.hcl) · [Compose overlay](../compose.openbao.yaml)

On 2026-11-20 a disposable Apple Silicon deployment with OpenBao 2.7.3,
LiteLLM 1.110.2 and PostgreSQL 17 issued a fresh `proxy_admin` session after
rotation. A fresh login with the retired password returned HTTP 401. The
gateway container and database stayed in place during the password change.
