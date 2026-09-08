"""Cloud Function: reconciliación disparada por subida a Cloud Storage.

Flujo del servicio:

  1. El cliente sube al bucket, dentro de una carpeta por período:
         incoming/2026-08/bank_statement.csv
         incoming/2026-08/internal_ledger.csv
  2. Cada subida dispara esta función. Si el par del período todavía no
     está completo, la función termina sin hacer nada y espera al otro
     archivo (los dos archivos llegan casi siempre por separado).
  3. Con ambos presentes, corre la reconciliación y sube el Excel a
     reports/2026-08/reconciliation_report.xlsx
  4. Publica un mensaje en Pub/Sub si hay ítems que revisar, para que otra
     pieza (email, Slack) avise al cliente.

Punto de entrada: `on_file_uploaded` (trigger de CloudEvent).

Desplegar: ver DEPLOY.md
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path

import functions_framework
from google.cloud import storage

# La lógica de negocio vive en el paquete del proyecto, no aquí: esta
# función solo traduce entre Cloud Storage y `run()`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from reconcile import run  # noqa: E402
from reconciliation.loader import ReconciliationInputError  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reconcile.gcp")

INCOMING_PREFIX = os.environ.get("INCOMING_PREFIX", "incoming/")
REPORTS_PREFIX = os.environ.get("REPORTS_PREFIX", "reports/")
BANK_FILENAME = os.environ.get("BANK_FILENAME", "bank_statement.csv")
LEDGER_FILENAME = os.environ.get("LEDGER_FILENAME", "internal_ledger.csv")
REPORT_FILENAME = os.environ.get("REPORT_FILENAME", "reconciliation_report.xlsx")
REPORT_LANG = os.environ.get("REPORT_LANG", "es")
ALERT_TOPIC = os.environ.get("ALERT_TOPIC")          # opcional
DATE_WINDOW = int(os.environ.get("DATE_WINDOW", "3"))


@functions_framework.cloud_event
def on_file_uploaded(cloud_event):
    """Trigger: google.cloud.storage.object.v1.finalized."""
    data = cloud_event.data
    bucket_name, object_name = data["bucket"], data["name"]

    if not object_name.startswith(INCOMING_PREFIX):
        logger.debug("Se ignora %s: fuera de %s", object_name, INCOMING_PREFIX)
        return
    if not object_name.lower().endswith((".csv", ".xlsx", ".xls")):
        logger.debug("Se ignora %s: no es un archivo de datos", object_name)
        return

    period = Path(object_name).parent.name or "sin-periodo"
    logger.info("Archivo recibido: gs://%s/%s (período %s)", bucket_name, object_name, period)

    client = storage.Client()
    bucket = client.bucket(bucket_name)

    bank_blob = bucket.blob(f"{INCOMING_PREFIX}{period}/{BANK_FILENAME}")
    ledger_blob = bucket.blob(f"{INCOMING_PREFIX}{period}/{LEDGER_FILENAME}")

    if not (bank_blob.exists() and ledger_blob.exists()):
        # Situación normal: llegó uno de los dos. Se espera al otro.
        logger.info(
            "Período %s incompleto (extracto=%s, libro=%s). Se espera el archivo faltante.",
            period, bank_blob.exists(), ledger_blob.exists(),
        )
        return

    # En Cloud Functions solo /tmp es escribible.
    with tempfile.TemporaryDirectory() as workdir:
        work = Path(workdir)
        bank_path, ledger_path = work / BANK_FILENAME, work / LEDGER_FILENAME
        bank_blob.download_to_filename(bank_path)
        ledger_blob.download_to_filename(ledger_path)

        report_path = work / REPORT_FILENAME
        try:
            outcome = run(
                bank_path=bank_path,
                ledger_path=ledger_path,
                out_path=report_path,
                lang=REPORT_LANG,
                date_window=DATE_WINDOW,
                quiet=True,
            )
        except ReconciliationInputError as exc:
            # Error del cliente (columnas mal, archivo corrupto): se deja el
            # motivo junto al reporte para que pueda corregirlo y reintentar.
            logger.error("Datos de entrada inválidos en %s: %s", period, exc)
            bucket.blob(f"{REPORTS_PREFIX}{period}/ERROR.txt").upload_from_string(
                str(exc), content_type="text/plain; charset=utf-8"
            )
            return

        destination = f"{REPORTS_PREFIX}{period}/{REPORT_FILENAME}"
        bucket.blob(destination).upload_from_filename(
            report_path,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        logger.info("Reporte disponible en gs://%s/%s", bucket_name, destination)

    summary = {
        "period": period,
        "bucket": bucket_name,
        "report": destination,
        "flagged": outcome["flagged"],
        "accounts": int(len(outcome["summary"])),
    }
    logger.info("Reconciliación completa: %s", json.dumps(summary, ensure_ascii=False))

    if ALERT_TOPIC and outcome["flagged"] > 0:
        _publish_alert(summary)

    return summary


def _publish_alert(summary: dict) -> None:
    """Avisa por Pub/Sub que hay ítems para revisar."""
    from google.cloud import pubsub_v1

    publisher = pubsub_v1.PublisherClient()
    topic = publisher.topic_path(os.environ["GOOGLE_CLOUD_PROJECT"], ALERT_TOPIC)
    publisher.publish(
        topic,
        json.dumps(summary, ensure_ascii=False).encode("utf-8"),
        period=summary["period"],
        flagged=str(summary["flagged"]),
    ).result(timeout=30)
    logger.info("Alerta publicada en %s (%d ítems)", ALERT_TOPIC, summary["flagged"])
