FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

# Bake the embedding model into the image: no download at runtime, works offline.
ENV EMBEDDING_CACHE_DIR=/opt/models
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/opt/models')"     && chmod -R a+rX /opt/models
ENV HF_HUB_OFFLINE=1

COPY . .

RUN useradd --system --uid 10001 --no-create-home veyl
RUN mkdir -p /data/uploads && chown veyl /data/uploads
USER veyl

EXPOSE 8000
# Hosts such as Render choose the port through PORT. Forwarded headers are handled by the
# app (app/auth/client_ip.py), never by uvicorn.
CMD ["sh", "-c", "exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port \"${PORT:-8000}\" --no-proxy-headers"]
