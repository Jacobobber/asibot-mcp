FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir .

# Create non-root user and group
RUN groupadd -r asibot && useradd -r -g asibot -m -s /bin/bash asibot

# Create data directory and set ownership
RUN mkdir -p /data && chown -R asibot:asibot /data

EXPOSE 8080 8081 9090

VOLUME /data

ENV ASIBOT_DATA_DIR=/data \
    ASIBOT_TRANSPORT=streamable-http

USER asibot

CMD ["asibot"]
