FROM ghcr.io/openbao/openbao:2.7.3@sha256:ba4b133eb9586630024e71a617e66f37f29cc3534cdd46a6233e098ceded8dc9 AS openbao
FROM ghcr.io/berriai/litellm:v1.110.2@sha256:ef798594ca3db8d81bad2e1439f536f49b71fe095ee9f20416f5e149b4e22035
COPY --from=openbao /usr/bin/bao /usr/local/bin/bao
COPY agent.hcl start-gateway.sh /opt/openbao/
COPY master-templates.hcl /opt/openbao/
RUN cat /opt/openbao/agent.hcl /opt/openbao/master-templates.hcl > /opt/openbao/agent-master.hcl
