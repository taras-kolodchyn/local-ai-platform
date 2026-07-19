FROM python:3.14.6-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --gid 10001 localai \
    && useradd --uid 10001 --gid localai --create-home --shell /usr/sbin/nologin localai

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY migrations ./migrations

RUN pip install --no-cache-dir .

USER 10001:10001

EXPOSE 8000

ENTRYPOINT ["local-ai-retrieval"]
CMD ["serve"]
