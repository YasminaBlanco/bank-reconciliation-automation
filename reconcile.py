#!/usr/bin/env python3
"""Reconciliación bancaria automatizada - punto de entrada CLI.

Compara un extracto bancario contra el libro contable interno y genera un
Excel con el resumen por cuenta, las coincidencias, las discrepancias de
monto y las partidas que aparecen en un solo lado.

Uso básico (usa los archivos de ejemplo en data/):
    python reconcile.py

Uso real:
    python reconcile.py --bank extracto.csv --ledger libro.csv \
        --out reports/agosto.xlsx --lang en

Ver todas las opciones:
    python reconcile.py --help

La función `run()` concentra el pipeline completo y no depende de argparse,
para poder invocarla igual desde una Cloud Function o un Cloud Run Job.

Códigos de salida:
    0  la reconciliación corrió bien
    1  error de datos de entrada o de escritura (mensaje en stderr)
    2  corrió bien pero hay ítems que requieren revisión (solo con
       --fail-on-exceptions; útil para alertas automáticas)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from reconciliation import matching
from reconciliation.i18n import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES
from reconciliation.loader import ReconciliationInputError, load_transactions
from reconciliation.matching import reconcile
from reconciliation.report import export_report, print_report, summarize, total_flagged

BASE_DIR = Path(__file__).parent
DEFAULT_BANK = BASE_DIR / "data" / "bank_statement.csv"
DEFAULT_LEDGER = BASE_DIR / "data" / "internal_ledger.csv"
DEFAULT_OUT = BASE_DIR / "reports" / "reconciliation_report.xlsx"

EXIT_OK = 0
EXIT_INPUT_ERROR = 1
EXIT_EXCEPTIONS_FOUND = 2

logger = logging.getLogger("reconcile")


class _CloudLoggingFormatter(logging.Formatter):
    """JSON de una línea con el campo `severity` que espera Cloud Logging."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "component": record.name,
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _force_utf8_output() -> None:
    """Evita UnicodeEncodeError con acentos en consolas Windows (cp1252)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover - stream ya cerrado o redirigido
                pass


def configure_logging(level: str = "INFO", json_logs: bool = False) -> None:
    """Logs operativos a stderr; el reporte del cliente va a stdout.

    Mantenerlos separados permite `python reconcile.py > reporte.txt` sin
    que se mezcle el ruido técnico con lo que lee el contador.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        _CloudLoggingFormatter() if json_logs
        else logging.Formatter("%(levelname)-8s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def run(
    bank_path=DEFAULT_BANK,
    ledger_path=DEFAULT_LEDGER,
    out_path=DEFAULT_OUT,
    lang: str = DEFAULT_LANGUAGE,
    amount_tolerance: float = matching.AMOUNT_TOLERANCE,
    date_window: int = matching.DATE_WINDOW_DAYS,
    discrepancy_abs: float = matching.DISCREPANCY_ABS,
    discrepancy_pct: float = matching.DISCREPANCY_PCT,
    debit_negative: bool = True,
    quiet: bool = False,
) -> dict:
    """Pipeline completo: cargar, conciliar, resumir, exportar.

    Devuelve un dict con `results`, `summary`, `rejected`, `flagged` y
    `out_path`, para que quien lo invoque (CLI, Cloud Function) decida qué
    hacer con el resultado.
    """
    bank, ledger, rejected = load_transactions(
        bank_path, ledger_path, debit_negative=debit_negative
    )

    results = reconcile(
        bank, ledger,
        amount_tolerance=amount_tolerance,
        date_window=date_window,
        discrepancy_abs=discrepancy_abs,
        discrepancy_pct=discrepancy_pct,
    )
    summary = summarize(results, bank, ledger, rejected)

    if not quiet:
        print_report(results, summary, rejected, lang=lang)

    written = export_report(results, summary, out_path, rejected=rejected, lang=lang)
    if not quiet:
        from reconciliation.i18n import Translator
        print(f"\n{Translator(lang).t('report.exported')}: {written}")

    return {
        "results": results,
        "summary": summary,
        "rejected": rejected,
        "flagged": total_flagged(summary),
        "out_path": written,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reconcile.py",
        description="Concilia un extracto bancario contra el libro contable interno.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--bank", default=str(DEFAULT_BANK),
                        help="Extracto bancario (CSV o Excel).")
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER),
                        help="Libro contable interno (CSV o Excel).")
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help="Ruta del Excel de salida.")
    parser.add_argument("--lang", default=DEFAULT_LANGUAGE, choices=list(SUPPORTED_LANGUAGES),
                        help="Idioma del reporte que recibe el cliente.")

    tuning = parser.add_argument_group("ajuste de la conciliación")
    tuning.add_argument("--amount-tolerance", type=float, default=matching.AMOUNT_TOLERANCE,
                        help="Diferencia máxima para considerar dos montos iguales.")
    tuning.add_argument("--date-window", type=int, default=matching.DATE_WINDOW_DAYS,
                        help="Días de desfase permitidos entre banco y libro.")
    tuning.add_argument("--discrepancy-abs", type=float, default=matching.DISCREPANCY_ABS,
                        help="Diferencia absoluta máxima para reportar una discrepancia.")
    tuning.add_argument("--discrepancy-pct", type=float, default=matching.DISCREPANCY_PCT,
                        help="Diferencia porcentual máxima para reportar una discrepancia.")
    tuning.add_argument("--debit-positive", action="store_true",
                        help="Invierte el signo cuando el archivo trae columnas de débito "
                             "y crédito separadas: trata el débito como entrada de dinero "
                             "(convención contable en vez de la del extracto bancario).")

    output = parser.add_argument_group("salida")
    output.add_argument("--quiet", action="store_true",
                        help="No imprimir el reporte en consola (solo genera el Excel).")
    output.add_argument("--fail-on-exceptions", action="store_true",
                        help="Salir con código 2 si hay ítems que requieren revisión.")
    output.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="Nivel de detalle de los logs (a stderr).")
    output.add_argument("--json-logs", action="store_true",
                        help="Logs en JSON para Cloud Logging (GCP).")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    _force_utf8_output()
    configure_logging(args.log_level, args.json_logs)

    if args.date_window < 0:
        logger.error("--date-window no puede ser negativo.")
        return EXIT_INPUT_ERROR

    try:
        outcome = run(
            bank_path=args.bank,
            ledger_path=args.ledger,
            out_path=args.out,
            lang=args.lang,
            amount_tolerance=args.amount_tolerance,
            date_window=args.date_window,
            discrepancy_abs=args.discrepancy_abs,
            discrepancy_pct=args.discrepancy_pct,
            debit_negative=not args.debit_positive,
            quiet=args.quiet,
        )
    except ReconciliationInputError as exc:
        logger.error("%s", exc)
        return EXIT_INPUT_ERROR
    except (PermissionError, ImportError) as exc:
        logger.error("%s", exc)
        return EXIT_INPUT_ERROR
    except Exception:
        logger.exception("Error inesperado durante la reconciliación.")
        return EXIT_INPUT_ERROR

    if args.fail_on_exceptions and outcome["flagged"] > 0:
        logger.warning("%d ítem(s) requieren revisión manual.", outcome["flagged"])
        return EXIT_EXCEPTIONS_FOUND
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
