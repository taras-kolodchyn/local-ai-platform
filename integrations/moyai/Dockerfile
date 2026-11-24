FROM ghcr.io/berriai/moyai@sha256:05e9b95fdd96507b39784b9d8531758efdf2db30f0bceb03c161e2bd46b43419
USER root
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*
COPY *.py LICENSE.upstream /opt/moyai-local/
COPY tests /opt/moyai-local/tests
ENV PYTHONPATH=/opt/moyai-local:/app PYDANTIC_AI_NO_BANNER=1
RUN python /opt/moyai-local/offline_assets.py && python /opt/moyai-local/verifier_review.py \
    && python /opt/moyai-local/scheduled_turns.py
RUN --mount=type=bind,source=skills,target=/opt/test-skill-seeds python -m unittest discover -s /opt/moyai-local/tests
ENTRYPOINT ["python", "/opt/moyai-local/server.py"]
