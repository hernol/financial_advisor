FROM python:3.11-slim

WORKDIR /app

# Dependencies first so the layer is cached across code edits.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY financial_analyzer.py .
COPY fa ./fa

# The SQLite database and the alert log live here; mount it as a volume.
ENV FA_DATA_DIR=/app/data
VOLUME ["/app/data"]

ENTRYPOINT ["python", "financial_analyzer.py"]
