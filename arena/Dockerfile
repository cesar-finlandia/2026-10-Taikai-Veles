# CoGNETs Swarm Arena
# Copyright 2026 The CoGNETs Consortium
# SPDX-License-Identifier: Apache-2.0

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so a code edit does not re-resolve the whole tree.
COPY arena/requirements.txt /app/arena/requirements.txt
RUN pip install --no-cache-dir -r /app/arena/requirements.txt

COPY arena/ /app/arena/

# Run as a non-root user. Nothing here needs privileges.
RUN useradd --create-home --uid 10001 arena && chown -R arena:arena /app
USER arena

EXPOSE 8080

HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=12 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status==200 else 1)"

CMD ["python", "-m", "uvicorn", "arena.main:app", \
     "--host", "0.0.0.0", "--port", "8080", "--log-level", "info"]
