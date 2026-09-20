FROM nousresearch/hermes-agent:v2026.9.14
USER root
COPY requirements.lock /opt/workspace/requirements.lock
RUN python -m ensurepip && python -m pip install --no-cache-dir --no-deps -r /opt/workspace/requirements.lock && python -m pip check
COPY src /opt/workspace/src
ENV MEM0_TELEMETRY=false \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/workspace/src:/opt/hermes \
    HOME=/opt/data \
    HERMES_HOME=/opt/data
ENTRYPOINT ["python", "-m", "uvicorn", "workspace_runtime.service:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8100", "--no-access-log"]
