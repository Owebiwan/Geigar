FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    BDO_CONFIG=/app/config/battery.yaml BDO_DATA_DIR=/data
WORKDIR /app/backend
COPY backend/pyproject.toml ./
COPY backend/bdo ./bdo
RUN pip install .
COPY config /app/config
RUN useradd --create-home bdo && mkdir -p /data && chown bdo /data
USER bdo
VOLUME /data
CMD ["bdo", "serve"]
