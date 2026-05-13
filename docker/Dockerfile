FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# ffmpeg + ffprobe are required by the JDR worker:
#   - ffprobe reads audio duration at upload time (best-effort).
#   - ffmpeg chunks the audio file into ~30s pieces before sending each
#     piece to the Whisper backend. Chunking is the only client-side defence
#     against the "Whisper locks onto one phrase" repetition loop, because
#     speaches' OpenAI-compatible API silently drops faster-whisper-specific
#     anti-hallucination kwargs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .

COPY app ./app

RUN useradd --create-home --uid 1000 app && chown -R app:app /app
USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
