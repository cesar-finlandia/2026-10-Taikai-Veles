# Your agent.
#
# This is the file you hand in. It should build from a clean clone of your
# repository with a plain `docker build .` and need no arguments.
#
# Copyright 2026 The CoGNETs Consortium
# SPDX-License-Identifier: Apache-2.0

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app/

RUN useradd --create-home --uid 10001 agent && chown -R agent:agent /app
USER agent

# Both are supplied by docker-compose (or by the grading harness). Do not
# hardcode them anywhere in your code - it is worth points.
ENV ARENA_URL=http://arena:8080 \
    TEAM_NAME=unnamed-team

CMD ["python", "-u", "agent.py"]
