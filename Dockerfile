FROM python:3.13-slim

WORKDIR /app

# System deps for PDF scraping (optional)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App
COPY . .

EXPOSE 8080
CMD ["python", "app.py"]
