# syntax=docker/dockerfile:1.7

############
# Stage 1: deps (install once; cached until requirements.txt changes)
############
FROM python:3.11-slim-bookworm AS deps
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# Install required OS libraries including libGL for OpenCV
RUN --mount=type=cache,target=/var/cache/apt \
    apt-get update && apt-get install -y --no-install-recommends \
      libmagic1 curl netcat-openbsd \
      libgl1 libglib2.0-0 \
      build-essential \
    && rm -rf /var/lib/apt/lists/*


# Copy and install Python dependencies
COPY requirements.txt . 
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt

############
# Stage 2: app (reuse Python + site-packages from deps)
############
FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# Copy installed packages from deps stage
COPY --from=deps /usr/local /usr/local

# Copy application code
COPY . .

EXPOSE 5075

# Start the FastAPI app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5075"]
