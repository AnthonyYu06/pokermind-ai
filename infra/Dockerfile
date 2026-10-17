# Supply reviewed official base-image digests at build time; no moving tags.
ARG NODE_IMAGE
ARG PYTHON_IMAGE
FROM ${NODE_IMAGE} AS web
WORKDIR /build
COPY apps/web/package*.json ./
RUN npm ci
COPY apps/web/ ./
RUN npm run build
FROM ${PYTHON_IMAGE} AS app
WORKDIR /app
COPY requirements.lock ./
RUN python -m pip install --no-cache-dir -r requirements.lock && useradd --uid 10001 --create-home pokermind
COPY packages/ packages/
COPY services/ services/
COPY configs/ configs/
COPY --from=web /build/dist apps/web/dist
RUN mkdir -p /app/.data && chown 10001:10001 /app/.data
USER 10001:10001
ENV POKERMIND_DATA=/app/.data PYTHONDONTWRITEBYTECODE=1
HEALTHCHECK --interval=30s --timeout=3s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"
CMD ["python", "-m", "services.api.cli", "serve"]
