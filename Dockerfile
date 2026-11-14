# Plate Stream: offline number-plate recognition, with the live web UI.
#
#   docker compose up --build          (one command: build, then run on http://127.0.0.1:8765)
#
# or without Compose:
#   docker build -t plate-stream .
#   docker run --rm -p 127.0.0.1:8765:8765 --memory 512m -v "$PWD:/videos:ro" plate-stream
#
# The models are downloaded and SHA-256-checked once, while the image is built. At run time the
# container needs no internet access.

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1

# Shared libraries that OpenCV (headless) and ONNX Runtime load at import time.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so code changes do not reinstall them.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Models: the only step that uses the internet.
COPY scripts/setup_models.py scripts/setup_models.py
RUN python scripts/setup_models.py

COPY main.py .
COPY anpr anpr
COPY ui ui

# Run as an unprivileged user. Results go to /app/output; sample clips are mounted at /videos.
RUN useradd --create-home --uid 10001 plate \
    && mkdir -p /app/output /videos \
    && chown -R plate:plate /app/output /videos
USER plate

EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/info', timeout=4)"]

# Inside the container the server must listen on all interfaces; Compose publishes it on the
# host's 127.0.0.1 only. For the command-line tool instead, see the README.
CMD ["python", "-m", "ui", "--host", "0.0.0.0", "--port", "8765", "--no-browser", "--samples", "/videos"]
