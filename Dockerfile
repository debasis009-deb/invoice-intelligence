FROM python:3.10-slim
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY invoice_ai invoice_ai
ENV HOST=0.0.0.0 INVOICE_DB=/data/reviews.db
VOLUME /data
EXPOSE 8000
CMD ["python", "-m", "invoice_ai"]
