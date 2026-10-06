# Baseline bots. One image; the bot is selected by the BOT environment
# variable (naive-max | even-split | proportional).
#
# Copyright 2026 The CoGNETs Consortium
# SPDX-License-Identifier: Apache-2.0

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY agent-template/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Reuse the participants' HTTP client so there is one retry implementation in
# the repository rather than two that drift apart.
COPY agent-template/client.py /app/_lib/client.py
COPY agent-template/runner.py /app/_lib/runner.py
COPY baselines/bot.py /app/bot.py

RUN useradd --create-home --uid 10001 bot && chown -R bot:bot /app
USER bot

ENV ARENA_URL=http://arena:8080 \
    BOT=naive-max

CMD ["python", "-u", "bot.py"]
