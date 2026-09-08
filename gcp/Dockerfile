# Imagen para Cloud Run Job (reconciliación programada).
# Construir desde la RAÍZ del proyecto:
#   gcloud builds submit --tag <IMAGEN> --file gcp/Dockerfile .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY gcp/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY reconcile.py ./
COPY reconciliation/ ./reconciliation/
COPY gcp/job.py ./gcp/job.py

# Corre como usuario sin privilegios.
RUN useradd --create-home --uid 1000 appuser
USER appuser

ENTRYPOINT ["python", "gcp/job.py"]
