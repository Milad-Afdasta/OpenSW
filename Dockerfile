# Public, source-built images. See docs/SECURITY-AND-BUILDS.md.
FROM node:24-alpine@sha256:a34ab6d7f6f4813f4d0c5469d74685604b962fbdbf5793a7bc7b4998ecb867c8 AS frontend
ARG OPENSW_APP
WORKDIR /build
COPY web/package.json web/package-lock.json ./web/
RUN cd web && npm ci
COPY shared ./shared
COPY web ./web
WORKDIR /build/web
RUN VITE_OPENSW_APP="$OPENSW_APP" npm run build

FROM restic/restic:0.19.1@sha256:136600b6ff6843d61d355f7f71f460a166429f35de6fd11b568fece3c9a4d510 AS restic

FROM python:3.13-slim@sha256:09a17cad02668ae61ece136b4fd36ac3849e14f670ebedb637ea3e15580525bc
ARG OPENSW_APP
ARG SOURCE_COMMIT=source-checkout
ARG SOURCE_SHA256=not-recorded
LABEL org.opencontainers.image.source="https://github.com/Milad-Afdasta/OpenSW"
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.revision="$SOURCE_COMMIT"
LABEL io.opensw.app="$OPENSW_APP"
LABEL io.opensw.source-sha256="$SOURCE_SHA256"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/backend
ENV OPENSW_APP="$OPENSW_APP" OPENSW_DATA_DIR=/data
WORKDIR /app
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt && groupadd --gid 10001 opensw && useradd --uid 10001 --gid 10001 --no-create-home opensw
COPY backend/opensw ./backend/opensw
COPY shared ./shared
COPY apps/${OPENSW_APP}/migrations ./apps/${OPENSW_APP}/migrations
COPY apps/${OPENSW_APP}/schema.sql ./apps/${OPENSW_APP}/schema.sql
COPY tools/verify_api.py ./tools/verify_api.py
COPY operations ./operations
COPY --from=restic /usr/bin/restic /usr/local/bin/restic
COPY --from=frontend /build/web/dist ./web/dist
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=4s --start-period=15s --retries=6 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" || exit 1
CMD ["uvicorn", "opensw.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log", "--no-proxy-headers"]
