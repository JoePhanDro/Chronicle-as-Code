FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 1000 chronicle \
    && mkdir /output \
    && chown chronicle:chronicle /output

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY chronicle/ ./chronicle/

USER chronicle

ENTRYPOINT ["python", "-m", "chronicle"]
CMD ["--data", "/data", "--out", "/output"]
