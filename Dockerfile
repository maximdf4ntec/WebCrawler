FROM python:3.12-slim

WORKDIR /app

# lxml needs a C toolchain + libxml2/libxslt headers to build from source
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libxml2-dev \
        libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

# DB and output are expected to live on mounted volumes (see docker-compose.yml)
VOLUME ["/app/data", "/app/output"]

ENTRYPOINT ["python", "-m", "crawler"]
CMD ["crawl", "--config", "/app/config.yaml", "--db", "/app/data/crawl.db", "--output", "/app/output"]
