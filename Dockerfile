# One image for everything: the same one runs on LiveKit Cloud, in `docker compose up`, and in CI.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH \
    HF_HUB_DISABLE_TELEMETRY=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates libgomp1 \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir uv==0.12.3

WORKDIR /app

# dependencies first, so a code change doesn't reinstall them; exact versions come from uv.lock
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project

COPY ala ala
COPY web web
COPY scenarios scenarios
COPY tests tests

# the models are part of the image, pinned by revision and checksum, so a call never waits for a download
RUN python -c "from livekit.plugins import silero; silero.VAD.load()" \
 && python -c "from ala.perception.turn import SMART_TURN; assert SMART_TURN.load()"

CMD ["python", "-m", "ala.voice.agent", "start"]
