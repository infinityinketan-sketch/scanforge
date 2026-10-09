# Production image (Render: runtime docker). Runs the API under Litestream; see deploy/start.sh.
FROM python:3.11-slim

ARG LITESTREAM_VERSION=0.5.17
ADD https://github.com/benbjohnson/litestream/releases/download/v${LITESTREAM_VERSION}/litestream-${LITESTREAM_VERSION}-linux-x86_64.tar.gz /tmp/litestream.tar.gz
RUN tar -xzf /tmp/litestream.tar.gz -C /usr/local/bin litestream && rm /tmp/litestream.tar.gz

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV DATA_DIR=/var/data PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["/app/deploy/start.sh"]
