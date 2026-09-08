"""Resumen, salida por consola y exportación a Excel.

Separación deliberada:
  - `summarize()` calcula y devuelve datos (testeable sin capturar stdout).
  - `format_report()` arma el texto; `print_report()` solo lo imprime.
  - `export_report()` escribe el Excel que recibe el cliente.

Los totales de control se suman con `Decimal` para que cuadren al centavo:
un contador revisa primero que la diferencia banco-libro se explique por
completo, y un error de coma flotante ahí destruye la confianza.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path

import pandas as pd

from .i18n import Translator
from .loader import DEFAULT_ACCOUNT, REJECTED_COLUMNS

logger = logging.getLogger(__name__)

SUMMARY_COLUMNS = [
    "account", "bank_total", "ledger_total", "difference",
    "matched", "discrepancies", "only_in_bank", "only_in_ledger", "rejected", "flagged",
    "by_discrepancies", "by_only_bank", "by_only_ledger", "by_match_rounding", "unexplained",
]

# Columnas que se muestran con formato de dinero en el Excel.
MONEY_COLUMNS = {
    "amount", "bank_amount", "ledger_amount", "difference", "bank_total", "ledger_total",
    "by_discrepancies", "by_only_bank", "by_only_ledger", "by_match_rounding", "unexplained",
}

SHEET_ORDER = ["summary", "matches", "discrepancies", "only_in_bank", "only_in_ledger", "rejected"]

# Cuántas filas de cada sección se muestran en consola antes de truncar.
CONSOLE_ROW_LIMIT = 25


def _dsum(values) -> Decimal:
    """Suma exacta al centavo."""
    total = Decimal("0")
    for value in values:
        if pd.isna(value):
            continue
        total += Decimal(str(round(float(value), 2)))
    return total


def _f(value: Decimal) -> float:
    return float(round(value, 2))


def _accounts_in(results: dict, bank: pd.DataFrame, ledger: pd.DataFrame) -> list:
    accounts = set()
    for frame in (bank, ledger):
        if frame is not None and not frame.empty:
            accounts.update(frame["account"].unique())
    for key in ("matches", "discrepancies", "only_in_bank", "only_in_ledger"):
        frame = results.get(key)
        if frame is not None and not frame.empty:
            accounts.update(frame["account"].unique())
    return sorted(accounts)


def _rows_for(frame: pd.DataFrame, account) -> pd.DataFrame:
    if frame is None or frame.empty:
        return frame if frame is not None else pd.DataFrame()
    if account is None:
        return frame
    return frame[frame["account"] == account]


def summarize(results: dict, bank: pd.DataFrame, ledger: pd.DataFrame,
              rejected: pd.DataFrame = None) -> pd.DataFrame:
    """Una fila por cuenta con totales, conteos y la composición de la diferencia.

    `unexplained` debe ser 0.00 siempre: es un control aritmético de que el
    reporte cierra consigo mismo.
    """
    if rejected is None:
        rejected = pd.DataFrame(columns=REJECTED_COLUMNS)

    accounts = _accounts_in(results, bank, ledger)
    rows = []
    for account in accounts:
        bank_rows = _rows_for(bank, account)
        ledger_rows = _rows_for(ledger, account)
        matches = _rows_for(results["matches"], account)
        discrepancies = _rows_for(results["discrepancies"], account)
        only_bank = _rows_for(results["only_in_bank"], account)
        only_ledger = _rows_for(results["only_in_ledger"], account)

        bank_total = _dsum(bank_rows["amount"]) if len(bank_rows) else Decimal("0")
        ledger_total = _dsum(ledger_rows["amount"]) if len(ledger_rows) else Decimal("0")
        difference = bank_total - ledger_total

        by_discrepancies = _dsum(discrepancies["difference"]) if len(discrepancies) else Decimal("0")
        by_only_bank = _dsum(only_bank["amount"]) if len(only_bank) else Decimal("0")
        by_only_ledger = _dsum(only_ledger["amount"]) if len(only_ledger) else Decimal("0")
        by_match_rounding = _dsum(matches["difference"]) if len(matches) else Decimal("0")

        explained = by_discrepancies + by_only_bank - by_only_ledger + by_match_rounding
        n_rejected = 0
        if rejected is not None and not rejected.empty and "account" in rejected:
            account_label = "" if account == DEFAULT_ACCOUNT else account
            n_rejected = int((rejected["account"] == account_label).sum())

        rows.append({
            "account": account,
            "bank_total": _f(bank_total),
            "ledger_total": _f(ledger_total),
            "difference": _f(difference),
            "matched": len(matches),
            "discrepancies": len(discrepancies),
            "only_in_bank": len(only_bank),
            "only_in_ledger": len(only_ledger),
            "rejected": n_rejected,
            "flagged": len(discrepancies) + len(only_bank) + len(only_ledger) + n_rejected,
            "by_discrepancies": _f(by_discrepancies),
            "by_only_bank": _f(by_only_bank),
            "by_only_ledger": _f(-by_only_ledger),
            "by_match_rounding": _f(by_match_rounding),
            "unexplained": _f(difference - explained),
        })

    summary = pd.DataFrame(rows, columns=SUMMARY_COLUMNS)

    # Fila TOTAL solo si hay más de una cuenta.
    if len(summary) > 1:
        total = {"account": "__total__"}
        for column in SUMMARY_COLUMNS[1:]:
            total[column] = (
                _f(_dsum(summary[column])) if column in MONEY_COLUMNS
                else int(summary[column].sum())
            )
        summary = pd.concat([summary, pd.DataFrame([total])], ignore_index=True)

    unexplained = summary["unexplained"].abs().max() if len(summary) else 0
    if unexplained and unexplained > 0.005:
        logger.error(
            "Control de totales: quedan %.2f sin explicar. El reporte no cierra; "
            "revísalo antes de enviarlo al cliente.", unexplained,
        )
    return summary


def total_flagged(summary: pd.DataFrame) -> int:
    """Ítems que requieren revisión manual (excluye la fila TOTAL)."""
    if summary.empty:
        return 0
    rows = summary[summary["account"] != "__total__"]
    return int(rows["flagged"].sum())


# --------------------------------------------------------------------------
# Presentación
# --------------------------------------------------------------------------

def _account_label(account: str, tr: Translator) -> str:
    if account == DEFAULT_ACCOUNT:
        return tr.t("value.account.default")
    if account == "__total__":
        return tr.t("value.total")
    return account


def localize(frame: pd.DataFrame, tr: Translator, drop_account: bool = False) -> pd.DataFrame:
    """Traduce encabezados y valores especiales para mostrar o exportar."""
    if frame is None:
        return pd.DataFrame()
    out = frame.copy()
    if "account" in out.columns:
        if drop_account:
            out = out.drop(columns=["account"])
        elif not out.empty:
            out["account"] = out["account"].map(lambda a: _account_label(a, tr))
    if "source" in out.columns and not out.empty:
        out["source"] = out["source"].map(
            lambda s: tr.t(f"value.source.{s}") if s in {"bank", "ledger"} else s
        )
    if "reason" in out.columns and not out.empty:
        out["reason"] = out["reason"].map(tr.t)
    return out.rename(columns=tr.columns(out.columns))


def _section(title: str, frame: pd.DataFrame, tr: Translator, single_account: bool) -> list:
    lines = ["", f"--- {title} ---"]
    if frame is None or frame.empty:
        lines.append(tr.t("report.none"))
        return lines
    shown = frame.head(CONSOLE_ROW_LIMIT)
    lines.append(localize(shown, tr, drop_account=single_account).to_string(index=False))
    if len(frame) > CONSOLE_ROW_LIMIT:
        lines.append(tr.t("report.truncated", n=len(frame) - CONSOLE_ROW_LIMIT))
    return lines


def format_report(results: dict, summary: pd.DataFrame, rejected: pd.DataFrame = None,
                  lang: str = "es") -> str:
    """Arma el reporte de consola. Devuelve texto, no imprime."""
    tr = Translator(lang)
    accounts = [a for a in summary["account"] if a != "__total__"] if len(summary) else []
    single = len(accounts) <= 1 and (not accounts or accounts[0] == DEFAULT_ACCOUNT)

    width = 74
    lines = ["=" * width, tr.t("report.title").center(width), "=" * width]

    for _, row in summary.iterrows():
        label = _account_label(row["account"], tr)
        if not single or row["account"] == "__total__":
            lines.append("")
            lines.append(f"[ {tr.t('report.account')}: {label} ]")
        lines += [
            "",
            f"  {tr.t('report.bank_total'):<42} {row['bank_total']:>14,.2f}",
            f"  {tr.t('report.ledger_total'):<42} {row['ledger_total']:>14,.2f}",
            f"  {tr.t('report.difference'):<42} {row['difference']:>14,.2f}",
            "",
            f"  {tr.t('report.breakdown')}:",
            f"    {tr.t('col.by_discrepancies'):<40} {row['by_discrepancies']:>14,.2f}",
            f"    {tr.t('col.by_only_bank'):<40} {row['by_only_bank']:>14,.2f}",
            f"    {tr.t('col.by_only_ledger'):<40} {row['by_only_ledger']:>14,.2f}",
            f"    {tr.t('col.by_match_rounding'):<40} {row['by_match_rounding']:>14,.2f}",
            f"    {tr.t('col.unexplained'):<40} {row['unexplained']:>14,.2f}",
            "",
            f"  {tr.t('report.matches'):<42} {row['matched']:>14,}",
            f"  {tr.t('report.discrepancies'):<42} {row['discrepancies']:>14,}",
            f"  {tr.t('report.only_in_bank'):<42} {row['only_in_bank']:>14,}",
            f"  {tr.t('report.only_in_ledger'):<42} {row['only_in_ledger']:>14,}",
            f"  {tr.t('report.rejected'):<42} {row['rejected']:>14,}",
        ]

    lines += _section(tr.t("report.section.discrepancies"), results["discrepancies"], tr, single)
    lines += _section(tr.t("report.section.only_in_bank"), results["only_in_bank"], tr, single)
    lines += _section(tr.t("report.section.only_in_ledger"), results["only_in_ledger"], tr, single)
    if rejected is not None and not rejected.empty:
        lines += _section(tr.t("report.section.rejected"), rejected, tr, False)

    lines += [
        "",
        f"{tr.t('report.flagged')}: {total_flagged(summary)}",
        "=" * width,
    ]
    return "\n".join(lines)


def print_report(results: dict, summary: pd.DataFrame, rejected: pd.DataFrame = None,
                 lang: str = "es") -> None:
    print(format_report(results, summary, rejected, lang))


# --------------------------------------------------------------------------
# Excel
# --------------------------------------------------------------------------

def _autosize_and_format(worksheet, frame: pd.DataFrame, money_headers: set) -> None:
    """Ancho de columna, negrita en encabezados, formato de dinero y panel fijo."""
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    header_font = Font(bold=True)
    for cell in worksheet[1]:
        cell.font = header_font
        cell.alignment = Alignment(vertical="center")

    for index, header in enumerate(frame.columns, start=1):
        letter = get_column_letter(index)
        values = frame[header].astype(str) if not frame.empty else pd.Series(dtype=str)
        longest = max([len(str(header))] + [len(v) for v in values]) if len(values) else len(str(header))
        worksheet.column_dimensions[letter].width = min(max(longest + 3, 11), 46)
        if header in money_headers:
            for cell in worksheet[letter][1:]:
                cell.number_format = "#,##0.00"
    worksheet.freeze_panes = "A2"


def export_report(results: dict, summary: pd.DataFrame, out_path, rejected: pd.DataFrame = None,
                  lang: str = "es") -> Path:
    """Escribe el Excel multi-pestaña que recibe el cliente.

    Las pestañas se escriben siempre, incluso vacías: una hoja con
    encabezados y sin filas comunica "revisado, nada que reportar"; una
    hoja en blanco parece un error.
    """
    tr = Translator(lang)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    frames = {
        "summary": summary,
        "matches": results["matches"],
        "discrepancies": results["discrepancies"],
        "only_in_bank": results["only_in_bank"],
        "only_in_ledger": results["only_in_ledger"],
        "rejected": rejected if rejected is not None else pd.DataFrame(columns=REJECTED_COLUMNS),
    }
    money_headers = {tr.t(f"col.{c}") for c in MONEY_COLUMNS}

    try:
        with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
            for key in SHEET_ORDER:
                localized = localize(frames[key], tr)
                sheet_name = tr.sheet(key)
                localized.to_excel(writer, sheet_name=sheet_name, index=False)
                _autosize_and_format(writer.sheets[sheet_name], localized, money_headers)
    except PermissionError as exc:
        raise PermissionError(
            f"No se pudo escribir {out_path}: el archivo está abierto en Excel "
            "u otro programa. Ciérralo y vuelve a ejecutar."
        ) from exc
    except ImportError as exc:
        raise ImportError(
            f"Falta la dependencia para escribir Excel ({exc}). "
            "Instálala con: pip install -r requirements.txt"
        ) from exc

    logger.info("Reporte exportado a %s", out_path)
    return out_path
