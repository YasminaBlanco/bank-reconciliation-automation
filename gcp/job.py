#!/usr/bin/env python3
"""Cloud Run Job: reconciliación programada de un período.

Alternativa al trigger por archivo, para clientes que prefieren un corte
fijo ("todos los lunes", "el día 1 de cada mes"). Cloud Scheduler lanza el
job y este toma del bucket los archivos del período indicado.

Variables de entorno:
    BUCKET           (obligatoria) bucket de Cloud Storage
    PERIOD           período a procesar; por defecto, el mes anterior
    INCOMING_PREFIX  carpeta de entrada        (default: incoming/)
    REPORTS_PREFIX   carpeta de salida         (default: reports/)
    REPORT_LANG      idioma del reporte        (default: es)
    DATE_WINDOW      días de desfase tolerados (default: 3)

Código de salida 0 si todo fue bien, 1 ante un error de datos, y 2 si la
reconciliación corrió pero hay ítems para revisar (Cloud Scheduler puede
usarlo para disparar una alerta).
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

from google.cloud import storage

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from reconcile import run  # noqa: E402
from reconciliation.loader import ReconciliationInputError  # noqa: E402

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("reconcile.job")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_FLAGGED = 2


def previous_month(today: date = None) -> str:
    """Período por defecto: el mes cerrado, en formato YYYY-MM."""
    today = today or date.today()
    year, month = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    return f"{year:04d}-{month:02d}"


def main() -> int:
    bucket_name = os.environ.get("BUCKET")
    if not bucket_name:
        logger.error("Falta la variable de entorno BUCKET.")
        return EXIT_ERROR

    period = os.environ.get("PERIOD") or previous_month()
    incoming = os.environ.get("INCOMING_PREFIX", "incoming/")
    reports = os.environ.get("REPORTS_PREFIX", "reports/")
    bank_name = os.environ.get("BANK_FILENAME", "bank_statement.csv")
    ledger_name = os.environ.get("LEDGER_FILENAME", "internal_ledger.csv")
    report_name = os.environ.get("REPORT_FILENAME", "reconciliation_report.xlsx")

    bucket = storage.Client().bucket(bucket_name)
    bank_blob = bucket.blob(f"{incoming}{period}/{bank_name}")
    ledger_blob = bucket.blob(f"{incoming}{period}/{ledger_name}")

    missing = [b.name for b in (bank_blob, ledger_blob) if not b.exists()]
    if missing:
        logger.error("Faltan archivos del período %s: %s", period, ", ".join(missing))
        return EXIT_ERROR

    logger.info("Procesando período %s desde gs://%s", period, bucket_name)
    with tempfile.TemporaryDirectory() as workdir:
        work = Path(workdir)
        bank_blob.download_to_filename(work / bank_name)
        ledger_blob.download_to_filename(work / ledger_name)

        try:
            outcome = run(
                bank_path=work / bank_name,
                ledger_path=work / ledger_name,
                out_path=work / report_name,
                lang=os.environ.get("REPORT_LANG", "es"),
                date_window=int(os.environ.get("DATE_WINDOW", "3")),
                quiet=False,
            )
        except ReconciliationInputError as exc:
            logger.error("Datos de entrada inválidos: %s", exc)
            return EXIT_ERROR

        destination = f"{reports}{period}/{report_name}"
        bucket.blob(destination).upload_from_filename(
            work / report_name,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    logger.info("Reporte en gs://%s/%s (%d ítem(s) para revisar)",
                bucket_name, destination, outcome["flagged"])
    return EXIT_FLAGGED if outcome["flagged"] else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
