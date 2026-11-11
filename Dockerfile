# Base images are pinned by digest. Update them deliberately (e.g. via Dependabot).

# Stage 1: build the Tailwind stylesheet
FROM node:26-alpine@sha256:c6ad9e9c1d80f6eb607c26b4b0a132ff7a2344fad1c78ea3baa2a7d4c8de6d94 AS css
WORKDIR /build
COPY package.json package-lock.json tailwind.config.js ./
RUN npm ci --no-audit --no-fund
COPY chronicle/ ./chronicle/
RUN npm run build:css

# Stage 2: runtime
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 1000 chronicle \
    && mkdir /output /db \
    && chown chronicle:chronicle /output /db

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY chronicle/ ./chronicle/
COPY --from=css /build/chronicle/static/app.css ./chronicle/static/app.css

USER chronicle

# Default: the API. Reports are built in the app by admins (ADR 0006), not by a command in the image.
ENTRYPOINT ["python", "-m", "chronicle.serve"]
CMD ["--db", "/db/chronicle.db", "--data", "/data", "--host", "0.0.0.0", "--port", "8000"]
