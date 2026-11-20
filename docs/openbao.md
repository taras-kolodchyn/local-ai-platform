# LiteLLM dashboard password from OpenBao

The default `make up` reads the dashboard password from `.env` and works with
the ordinary LiteLLM image. The optional image adds OpenBao Agent 2.7.3 to
LiteLLM 1.110.2. The Agent supervises LiteLLM and injects `UI_PASSWORD` into its
process environment. This uses OpenBao's process supervisor (upstream labels
it public beta) and works with the standard LiteLLM image and license.
[OpenBao process supervisor](https://openbao.org/docs/agent-and-proxy/agent/process-supervisor/) ·
[LiteLLM UI credentials](https://docs.litellm.ai/docs/proxy/ui)

## Run OpenBao on this Mac

Run `make bootstrap`, then set `LITELLM_UI_PASSWORD_SOURCE=openbao-local` and
clear `UI_PASSWORD` in the private `.env`. Run `make up`. The first start
creates a single-node Raft store, a private TLS certificate and a random
dashboard password. The gateway reaches the server at `https://openbao:8200`.
Operator commands use HTTPS on `127.0.0.1:8200`.

| Command | Use |
| --- | --- |
| `make openbao-copy-password` | Copy the current password to the Mac clipboard. Paste it into the LiteLLM UI with the configured `UI_USERNAME`. |
| `make openbao-rotate` | Generate the next password with a KV version check. Allow the Agent to restart the gateway before signing in. |
| `make openbao-start` | Start or unlock the local server after Docker Desktop restarts. Ordinary `make up` also does this. |

The server uses a single Shamir share in the current user's macOS login
Keychain under `local-ai-platform.openbao.unseal`. The account ID is in
`.local/openbao-local/instance`. Unlock the login Keychain before starting
a sealed server. Bootstrap revokes the initial root token after provisioning.
Only the host operator has the AppRole that can rotate this one KV entry.

`make down` preserves the Raft data. For recovery on another Mac, stop the
server and back up `.local/openbao-local/` securely, including the TLS key,
AppRole files and Raft directory. Back up the matching Keychain share
separately. Both are needed to restore. The server certificate is issued
for 825 days. To replace the TLS certificates, run `make down`, move only
`.local/openbao-local/tls` to a protected backup, then run `make up`.
Startup issues a fresh certificate and installs its CA in the gateway.
[OpenBao seal and unseal](https://openbao.org/docs/concepts/seal/) ·
[Raft storage](https://openbao.org/docs/configuration/storage/raft/)

## Use an existing OpenBao server

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

### Connect the gateway

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
files. This mode connects to the specified external server.

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

Verified on the working Apple Silicon Mac on 2026-11-20 with OpenBao 2.7.3
and LiteLLM 1.110.2. KV version 2 opened a fresh `proxy_admin` session.
Both version 1 and the original pre-migration password returned HTTP 401
without issuing a session cookie. The gateway and database containers
stayed in place during rotation. After restarting OpenBao and unlocking it
from Keychain, the same Raft cluster served version 2 and login succeeded.
