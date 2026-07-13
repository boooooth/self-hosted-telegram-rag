# Single image shared by both the `bot` and `worker` services in
# docker-compose.yml — they differ only in the `command:` each one runs.
# Both need the same ML models, so there's no benefit to splitting this
# into two images.
FROM python:3.11-slim

WORKDIR /srv

# Avoid any accidental network calls to Hugging Face Hub at container start —
# the models are baked in below, at build time.
ENV HF_HUB_OFFLINE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .

# CPU-only torch wheel first (much smaller than the default CUDA build);
# the pinned version in requirements.txt is then already satisfied.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.5.1 \
    && pip install --no-cache-dir -r requirements.txt

# Pre-download both local models into the image so cold starts never hit
# the network. Runs with HF_HUB_OFFLINE unset just for this one step.
RUN HF_HUB_OFFLINE=0 python -c "\
from sentence_transformers import SentenceTransformer, CrossEncoder; \
SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2'); \
CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"

COPY app/ ./app/
COPY schema.sql .

# No CMD here on purpose — docker-compose.yml sets `command:` per service
# (`python -m app.bot` vs `python -m app.worker`).
