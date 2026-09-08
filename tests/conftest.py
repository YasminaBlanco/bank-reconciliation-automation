"""Utilidades compartidas por los tests.

Los helpers construyen frames ya normalizados (como los entrega `loader`)
para que los tests de matching describan escenarios contables legibles y
no el formato interno.
"""

from __future__ import annotations

import pandas as pd
import pytest

from reconciliation.loader import normalize_transactions

BANK_COLUMNS = ["account", "date", "description", "amount"]
LEDGER_COLUMNS = ["account", "date", "description", "amount", "invoice_ref"]


def raw_frame(rows, columns=None) -> pd.DataFrame:
    """DataFrame crudo a partir de una lista de dicts."""
    if columns is None:
        columns = list({key: None for row in rows for key in row})
        if not columns:
            columns = ["date", "description", "amount"]
    return pd.DataFrame(rows, columns=columns)


def bank_frame(rows, columns=None) -> pd.DataFrame:
    frame, _ = normalize_transactions(raw_frame(rows, columns or BANK_COLUMNS), "bank")
    return frame


def ledger_frame(rows, columns=None) -> pd.DataFrame:
    frame, _ = normalize_transactions(raw_frame(rows, columns or LEDGER_COLUMNS), "ledger")
    return frame


def bank_row(date, description, amount, account="ACME-BANK"):
    return {"account": account, "date": date, "description": description, "amount": amount}


def ledger_row(date, description, amount, invoice_ref="INV-000", account="ACME-BANK"):
    return {
        "account": account, "date": date, "description": description,
        "amount": amount, "invoice_ref": invoice_ref,
    }


@pytest.fixture
def demo_paths():
    """Rutas a los CSV de ejemplo del repositorio."""
    from pathlib import Path
    base = Path(__file__).resolve().parent.parent / "data"
    return base / "bank_statement.csv", base / "internal_ledger.csv"
