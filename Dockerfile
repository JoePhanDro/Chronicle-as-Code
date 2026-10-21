# Base images are pinned by digest. Update them deliberately (e.g. via Dependabot).

# Stage 1: build the Tailwind stylesheet
FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293 AS css
WORKDIR /build
COPY package.json package-lock.json tailwind.config.js ./
RUN npm ci --no-audit --no-fund
COPY chronicle/ ./chronicle/
RUN npm run build:css

# Stage 2: runtime
FROM python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2

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
CMD ["--db", "/db/chronicle.db", "--host", "0.0.0.0", "--port", "8000"]
