FROM python:3.11-slim

# Git is needed for the commit/push step
RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY enrich.py .

CMD ["python", "enrich.py"]
