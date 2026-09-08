"""Etiquetas del reporte en español e inglés.

El pipeline trabaja siempre con nombres de columna canónicos en inglés
(`bank_amount`, `only_in_bank`, ...). La traducción ocurre solo al momento
de renderizar: consola y Excel. Así la lógica y los tests no dependen del
idioma elegido por el cliente.
"""

from __future__ import annotations

SUPPORTED_LANGUAGES = ("es", "en")
DEFAULT_LANGUAGE = "es"

_ES = {
    # --- Nombres de pestañas del Excel ---
    "sheet.summary": "Resumen",
    "sheet.matches": "Coincidencias",
    "sheet.discrepancies": "Discrepancias",
    "sheet.only_in_bank": "Solo en banco",
    "sheet.only_in_ledger": "Solo en libro",
    "sheet.rejected": "Filas con problemas",
    # --- Encabezados de columna ---
    "col.account": "Cuenta",
    "col.date": "Fecha",
    "col.bank_date": "Fecha banco",
    "col.ledger_date": "Fecha libro",
    "col.day_gap": "Desfase (días)",
    "col.description": "Descripción",
    "col.bank_description": "Descripción banco",
    "col.ledger_description": "Descripción libro",
    "col.amount": "Monto",
    "col.bank_amount": "Monto banco",
    "col.ledger_amount": "Monto libro",
    "col.difference": "Diferencia",
    "col.invoice_ref": "Factura",
    "col.source": "Origen",
    "col.row": "Fila",
    "col.reason": "Motivo",
    # --- Columnas de la pestaña Resumen ---
    "col.bank_total": "Total banco",
    "col.ledger_total": "Total libro",
    "col.matched": "Coincidencias",
    "col.discrepancies": "Discrepancias",
    "col.only_in_bank": "Solo en banco",
    "col.only_in_ledger": "Solo en libro",
    "col.rejected": "Filas con problemas",
    "col.flagged": "Requieren revisión",
    "col.by_discrepancies": "Dif. por discrepancias",
    "col.by_only_bank": "Dif. por solo-en-banco",
    "col.by_only_ledger": "Dif. por solo-en-libro",
    "col.by_match_rounding": "Dif. por redondeo",
    "col.unexplained": "Sin explicar",
    # --- Valores ---
    "value.source.bank": "Banco",
    "value.source.ledger": "Libro",
    "value.account.default": "Cuenta única",
    "value.total": "TOTAL",
    "reason.invalid_date": "Fecha inválida o vacía",
    "reason.invalid_amount": "Monto inválido o vacío",
    "reason.invalid_date_and_amount": "Fecha y monto inválidos",
    # --- Reporte de consola ---
    "report.title": "REPORTE DE RECONCILIACIÓN BANCARIA",
    "report.account": "Cuenta",
    "report.accounts_total": "cuentas conciliadas",
    "report.matches": "Coincidencias",
    "report.discrepancies": "Discrepancias de monto",
    "report.only_in_bank": "Solo en banco (falta registrar)",
    "report.only_in_ledger": "Solo en libro (no se refleja en banco)",
    "report.rejected": "Filas con problemas (no procesadas)",
    "report.bank_total": "Total banco",
    "report.ledger_total": "Total libro",
    "report.difference": "Diferencia",
    "report.breakdown": "Composición de la diferencia",
    "report.flagged": "Total de ítems que requieren revisión manual",
    "report.section.discrepancies": "DISCREPANCIAS DE MONTO",
    "report.section.only_in_bank": "SOLO EN BANCO",
    "report.section.only_in_ledger": "SOLO EN LIBRO INTERNO",
    "report.section.rejected": "FILAS CON PROBLEMAS",
    "report.none": "(ninguno)",
    "report.exported": "Reporte exportado a",
    "report.truncated": "... y {n} fila(s) más (ver el Excel)",
}

_EN = {
    "sheet.summary": "Summary",
    "sheet.matches": "Matched",
    "sheet.discrepancies": "Discrepancies",
    "sheet.only_in_bank": "Bank only",
    "sheet.only_in_ledger": "Ledger only",
    "sheet.rejected": "Problem rows",
    "col.account": "Account",
    "col.date": "Date",
    "col.bank_date": "Bank date",
    "col.ledger_date": "Ledger date",
    "col.day_gap": "Day gap",
    "col.description": "Description",
    "col.bank_description": "Bank description",
    "col.ledger_description": "Ledger description",
    "col.amount": "Amount",
    "col.bank_amount": "Bank amount",
    "col.ledger_amount": "Ledger amount",
    "col.difference": "Difference",
    "col.invoice_ref": "Invoice",
    "col.source": "Source",
    "col.row": "Row",
    "col.reason": "Reason",
    "col.bank_total": "Bank total",
    "col.ledger_total": "Ledger total",
    "col.matched": "Matched",
    "col.discrepancies": "Discrepancies",
    "col.only_in_bank": "Bank only",
    "col.only_in_ledger": "Ledger only",
    "col.rejected": "Problem rows",
    "col.flagged": "Needs review",
    "col.by_discrepancies": "Diff. from discrepancies",
    "col.by_only_bank": "Diff. from bank-only",
    "col.by_only_ledger": "Diff. from ledger-only",
    "col.by_match_rounding": "Diff. from rounding",
    "col.unexplained": "Unexplained",
    "value.source.bank": "Bank",
    "value.source.ledger": "Ledger",
    "value.account.default": "Single account",
    "value.total": "TOTAL",
    "reason.invalid_date": "Invalid or empty date",
    "reason.invalid_amount": "Invalid or empty amount",
    "reason.invalid_date_and_amount": "Invalid date and amount",
    "report.title": "BANK RECONCILIATION REPORT",
    "report.account": "Account",
    "report.accounts_total": "accounts reconciled",
    "report.matches": "Matched transactions",
    "report.discrepancies": "Amount discrepancies",
    "report.only_in_bank": "Bank only (not recorded)",
    "report.only_in_ledger": "Ledger only (not cleared)",
    "report.rejected": "Problem rows (not processed)",
    "report.bank_total": "Bank total",
    "report.ledger_total": "Ledger total",
    "report.difference": "Difference",
    "report.breakdown": "Difference breakdown",
    "report.flagged": "Total items needing manual review",
    "report.section.discrepancies": "AMOUNT DISCREPANCIES",
    "report.section.only_in_bank": "BANK ONLY",
    "report.section.only_in_ledger": "LEDGER ONLY",
    "report.section.rejected": "PROBLEM ROWS",
    "report.none": "(none)",
    "report.exported": "Report exported to",
    "report.truncated": "... and {n} more row(s) (see the Excel file)",
}

_CATALOG = {"es": _ES, "en": _EN}


class Translator:
    """Traduce claves canónicas al idioma del reporte."""

    def __init__(self, lang: str = DEFAULT_LANGUAGE):
        lang = (lang or DEFAULT_LANGUAGE).lower()
        if lang not in _CATALOG:
            raise ValueError(
                f"Idioma no soportado: {lang!r}. "
                f"Opciones válidas: {', '.join(SUPPORTED_LANGUAGES)}"
            )
        self.lang = lang
        self._labels = _CATALOG[lang]

    def t(self, key: str, **kwargs) -> str:
        """Devuelve la etiqueta. Si falta la clave, devuelve la clave misma
        (falla visible pero no rompe el reporte del cliente)."""
        text = self._labels.get(key, key)
        return text.format(**kwargs) if kwargs else text

    def columns(self, names) -> dict:
        """Mapa {nombre_canonico: etiqueta} para renombrar un DataFrame."""
        return {name: self.t(f"col.{name}") for name in names}

    def sheet(self, name: str) -> str:
        return self.t(f"sheet.{name}")
