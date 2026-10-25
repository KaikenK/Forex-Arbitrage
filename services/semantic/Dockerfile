# Optional container for the semantic microservice (core runtime only: keyword
# sentiment + linear reaction model). For FinBERT, install requirements-ml.txt instead.
FROM python:3.14-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY frontend ./frontend
COPY data ./data
COPY artifacts/usdinr_targeted_expanded ./artifacts/usdinr_targeted_expanded
COPY serve.py config.toml.example ./

ENV SEMANTIC_HOST=0.0.0.0 \
    SEMANTIC_PORT=9000
EXPOSE 9000
# config.toml (API keys) is never baked in: mount it and point SEMANTIC_CONFIG at it.
CMD ["python", "serve.py"]
