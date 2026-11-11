# The daily job's image (spec FR-005; specs/002-azure-daily-run/research.md R8, R12).
#
# Built and published by .github/workflows/ci.yml on pushes to main. Contains the application,
# its locked runtime dependencies and the committed universe file (package data), and no secret,
# environment file or credential. The job runs `detect --latest --migrate`.
#
# Local smoke test on the host (never inside the agent container):
#   podman build --build-arg GIT_SHA="$(git rev-parse HEAD)" -t market-anomaly-agent:local .
#   podman run --rm market-anomaly-agent:local --help

# --- build: resolve and install the locked dependencies into /app/.venv -----------------------
# Same base image as the runtime stage, so the venv's interpreter path (/usr/local/bin/python3.12)
# is identical in both; uv itself is copied in from its pinned distroless image (uv's Docker guide).
FROM python:3.12-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON_PREFERENCE=only-system \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# --- runtime ---------------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# CA guard (research R8): the job's DATABASE_URL names this bundle with
# sslrootcert=/etc/ssl/certs/ca-certificates.crt, because sslrootcert=system does not find a store
# with the OpenSSL bundled in psycopg[binary]. Fail the build unless the bundle holds both roots
# Microsoft documents for Azure Database for PostgreSQL.
RUN python -c "import ssl; s={dict(x[0] for x in c['subject']).get('commonName') for c in ssl.create_default_context(cafile='/etc/ssl/certs/ca-certificates.crt').get_ca_certs()}; missing={'DigiCert Global Root G2','Microsoft RSA Root Certificate Authority 2017'}-s; assert not missing, missing"

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app

COPY --from=build /app/.venv /app/.venv

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

ARG GIT_SHA=unknown
ENV APP_COMMIT=$GIT_SHA
LABEL org.opencontainers.image.revision=$GIT_SHA \
      org.opencontainers.image.source=https://github.com/XaverPilgrimm/market-anomaly-agent

USER app
ENTRYPOINT ["detect"]
