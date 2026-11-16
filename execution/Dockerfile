FROM python:3.14.10-slim-bookworm AS mem0-plugin
# Official Hermes v0.23.1 catalog pin; verify bytes before extracting any code.
ADD --checksum=sha256:da2285987bf72e3d89bca3275659c11239a57c0265962f3393027a8c1026b588 https://codeload.github.com/mem0ai/mem0/tar.gz/43849c6e9d4d773b83a8144b6373aedc67241a5d /tmp/mem0.tar.gz
RUN mkdir /plugin && tar -xzf /tmp/mem0.tar.gz -C /plugin --strip-components=3 \
    mem0-43849c6e9d4d773b83a8144b6373aedc67241a5d/integrations/hermes-plugin-mem0

FROM nousresearch/hermes-agent:v0.23.1@sha256:276d44685c65c0cb2a25f190b63c65c8906f9dc76486fb1c05ae71b5826bda08
USER root
ENV MEM0_TELEMETRY=false \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/workspace/src:/opt/hermes \
    HOME=/opt/data \
    HERMES_HOME=/opt/data
COPY requirements.lock check-dependencies.py /opt/workspace/
RUN python -m ensurepip && python -m pip install --no-cache-dir --no-deps -r /opt/workspace/requirements.lock \
    && python /opt/workspace/check-dependencies.py
# Bake the external provider into Hermes' immutable provider discovery directory.
# No source checkout, plugin download, or lazy installation occurs at request time.
COPY --from=mem0-plugin /plugin /opt/hermes/plugins/memory/mem0
COPY src /opt/workspace/src
ENTRYPOINT ["python", "-m", "uvicorn", "workspace_runtime.service:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8100", "--no-access-log"]
