FROM python:3.12-slim

# LibreOffice is required by the PPTX preview feature.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libreoffice \
        libreoffice-impress \
        fonts-dejavu \
        fonts-liberation \
    && libreoffice --headless --version \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render provides PORT; 10000 is a local/default fallback.
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-10000} app:app"]
