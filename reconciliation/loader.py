"""Carga y normalización de extractos bancarios y libros contables.

Objetivo: que un CSV real de cliente (con `$`, comas de miles, negativos
entre paréntesis, encabezados en español) entre limpio al motor de
reconciliación, y que lo que NO se pueda interpretar quede registrado
explícitamente en vez de desaparecer en silencio.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# Cuenta usada cuando el archivo no trae columna `account`.
DEFAULT_ACCOUNT = "__default__"

# Columnas canónicas que produce este módulo.
CANONICAL_COLUMNS = [
    "_id", "account", "account_key", "date", "description", "amount", "invoice_ref",
]
REJECTED_COLUMNS = ["source", "row", "account", "date", "description", "amount", "reason"]

# Encabezados aceptados para cada columna canónica (sin acentos, en minúscula).
COLUMN_ALIASES = {
    "date": {"date", "fecha", "transaction_date", "post_date", "posted_date",
             "value_date", "fecha_operacion", "fecha_movimiento"},
    "description": {"description", "descripcion", "concepto", "detalle", "memo",
                    "narrative", "payee", "glosa"},
    "amount": {"amount", "monto", "importe", "valor", "cantidad"},
    "account": {"account", "cuenta", "account_id", "account_name", "nro_cuenta",
                "numero_cuenta", "bank_account"},
    "invoice_ref": {"invoice_ref", "invoice", "invoice_number", "factura",
                    "referencia", "reference", "ref", "nro_factura"},
}

REQUIRED_COLUMNS = ("date", "description", "amount")


class ReconciliationInputError(Exception):
    """Error de datos de entrada, con mensaje accionable para el usuario final."""


def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )


def _normalize_header(name: str) -> str:
    key = _strip_accents(str(name)).strip().lower()
    key = re.sub(r"[\s\-.]+", "_", key)
    return re.sub(r"[^a-z0-9_]", "", key)


def _where(path) -> str:
    return f" (archivo: {path})" if path else ""


def _resolve_columns(df: pd.DataFrame, source: str, path=None) -> dict:
    """Mapea columnas reales a canónicas. Falla con mensaje accionable."""
    resolved: dict = {}
    for original in df.columns:
        key = _normalize_header(original)
        for canonical, aliases in COLUMN_ALIASES.items():
            if key in aliases:
                if canonical in resolved:
                    raise ReconciliationInputError(
                        f"[{source}] Dos columnas se interpretan como {canonical!r}: "
                        f"{resolved[canonical]!r} y {original!r}. Renombra una de ellas."
                        + _where(path)
                    )
                resolved[canonical] = original
                break

    missing = [c for c in REQUIRED_COLUMNS if c not in resolved]
    if missing:
        found = ", ".join(str(c) for c in df.columns) or "(ninguna)"
        raise ReconciliationInputError(
            f"[{source}] Faltan columnas obligatorias: {', '.join(missing)}. "
            f"Columnas encontradas: {found}. "
            "Se aceptan alias como fecha / monto / descripcion." + _where(path)
        )
    return resolved


def parse_amount(value):
    """Convierte un monto en cualquier formato contable usual a float.

    Soporta '$1,450.00', '1.450,00', '(89.50)' como negativo, '89.50-',
    espacios duros y valores ya numéricos. Devuelve None si no se puede
    interpretar, para que la fila quede registrada como problemática.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if pd.isna(value) else round(float(value), 2)

    text = str(value).replace("\xa0", " ").strip()
    if not text or text.lower() in {"nan", "none", "null", "-", "--"}:
        return None

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1].strip()
    if text.endswith("-"):
        negative, text = True, text[:-1].strip()
    if text.startswith("-"):
        negative, text = True, text[1:].strip()
    elif text.startswith("+"):
        text = text[1:].strip()

    # Quita símbolos de moneda, códigos y espacios; deja dígitos y separadores.
    text = re.sub(r"[^\d,.]", "", text)
    if not any(ch.isdigit() for ch in text):
        return None

    has_comma, has_dot = "," in text, "." in text
    if has_comma and has_dot:
        # El separador decimal es el ultimo que aparece.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif has_comma:
        if re.fullmatch(r"\d{1,3}(,\d{3})+", text):
            text = text.replace(",", "")            # 1,450 -> separador de miles
        elif re.search(r",\d{1,2}$", text):
            text = text.replace(",", ".")           # 89,50 -> separador decimal
        else:
            text = text.replace(",", "")
    elif has_dot and re.fullmatch(r"\d{1,3}(\.\d{3}){2,}", text):
        # Solo con DOS o más grupos ('1.234.567') el punto es separador de
        # miles sin ambigüedad. Un solo grupo ('10.999') es ambiguo y se
        # trata como decimal: el formato europeo casi siempre trae coma
        # decimal ('1.450,00'), que ya se resolvió en la rama anterior.
        text = text.replace(".", "")

    try:
        amount = round(float(text), 2)
    except ValueError:
        return None
    return -amount if negative else amount


def _read_file(path, source: str) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise ReconciliationInputError(
            f"[{source}] No se encontró el archivo: {path}. "
            "Verifica la ruta o indícala con --bank / --ledger."
        )
    try:
        if path.suffix.lower() in {".xlsx", ".xls"}:
            return pd.read_excel(path, dtype=str)
        return pd.read_csv(path, dtype=str, keep_default_na=False, skipinitialspace=True)
    except ImportError as exc:
        raise ReconciliationInputError(
            f"[{source}] Falta una dependencia para leer {path.name}: {exc}. "
            "Instálala con: pip install -r requirements.txt"
        ) from exc
    except pd.errors.EmptyDataError as exc:
        raise ReconciliationInputError(
            f"[{source}] El archivo {path.name} está vacío o no tiene encabezados."
        ) from exc
    except UnicodeDecodeError:
        try:
            return pd.read_csv(path, dtype=str, keep_default_na=False,
                               skipinitialspace=True, encoding="latin-1")
        except Exception as exc:  # pragma: no cover - camino defensivo
            raise ReconciliationInputError(
                f"[{source}] No se pudo leer {path.name}: codificación no reconocida ({exc})."
            ) from exc
    except Exception as exc:
        raise ReconciliationInputError(
            f"[{source}] No se pudo leer {path.name}: {exc}"
        ) from exc


def empty_clean_frame() -> pd.DataFrame:
    """Frame vacío con el esquema canónico y los dtypes correctos."""
    frame = pd.DataFrame(columns=CANONICAL_COLUMNS)
    frame["date"] = pd.to_datetime(frame["date"])
    frame["amount"] = frame["amount"].astype(float)
    frame["has_account_column"] = pd.Series(dtype=bool)
    return frame


def normalize_transactions(raw: pd.DataFrame, source: str, path=None):
    """Normaliza un DataFrame crudo.

    Devuelve (clean, rejected). `clean` usa CANONICAL_COLUMNS; `rejected`
    usa REJECTED_COLUMNS e incluye el número de fila del archivo original
    para que el cliente pueda corregirla.
    """
    if raw is None:
        raise ReconciliationInputError(f"[{source}] No hay datos que procesar.")

    resolved = _resolve_columns(raw, source, path)
    has_account = "account" in resolved

    raw = raw.reset_index(drop=True)
    df = pd.DataFrame(index=raw.index)
    df["raw_date"] = raw[resolved["date"]]
    df["description"] = raw[resolved["description"]].astype(str).str.strip()
    df["raw_amount"] = raw[resolved["amount"]]
    df["invoice_ref"] = (
        raw[resolved["invoice_ref"]].astype(str).str.strip()
        if "invoice_ref" in resolved else ""
    )
    if has_account:
        account = raw[resolved["account"]].astype(str).str.strip()
        df["account"] = account.where(
            account.ne("") & account.str.lower().ne("nan"), DEFAULT_ACCOUNT
        )
    else:
        df["account"] = DEFAULT_ACCOUNT

    df["date"] = pd.to_datetime(df["raw_date"], errors="coerce").dt.normalize()
    df["amount"] = df["raw_amount"].map(parse_amount)

    bad_date = df["date"].isna()
    bad_amount = df["amount"].isna()
    bad = bad_date | bad_amount

    rejected = pd.DataFrame(columns=REJECTED_COLUMNS)
    if bad.any():
        reasons = pd.Series("reason.invalid_amount", index=df.index)
        reasons[bad_date] = "reason.invalid_date"
        reasons[bad_date & bad_amount] = "reason.invalid_date_and_amount"
        rejected = pd.DataFrame(
            {
                "source": source,
                # +2: la fila 1 es el encabezado del archivo.
                "row": [int(i) + 2 for i in df.index[bad]],
                "account": df.loc[bad, "account"].replace(DEFAULT_ACCOUNT, ""),
                "date": df.loc[bad, "raw_date"].astype(str),
                "description": df.loc[bad, "description"],
                "amount": df.loc[bad, "raw_amount"].astype(str),
                "reason": reasons[bad],
            },
            columns=REJECTED_COLUMNS,
        ).reset_index(drop=True)
        logger.warning(
            "[%s] %d fila(s) no se pudieron procesar; quedan listadas en el reporte.",
            source, len(rejected),
        )

    clean = df.loc[~bad].copy()
    if clean.empty:
        clean = empty_clean_frame()
        clean["has_account_column"] = pd.Series([has_account] * 0, dtype=bool)
        clean.attrs["has_account_column"] = has_account
        return clean, rejected

    clean["_id"] = [f"{source}:{i}" for i in clean.index]
    clean["account_key"] = clean["account"].str.casefold()
    clean = clean[CANONICAL_COLUMNS].reset_index(drop=True)
    clean["amount"] = clean["amount"].astype(float).round(2)
    clean["has_account_column"] = has_account
    clean.attrs["has_account_column"] = has_account
    return clean, rejected


def validate_accounts(bank: pd.DataFrame, ledger: pd.DataFrame) -> None:
    """Evita el fallo silencioso de tener `account` en un solo lado."""
    bank_has = bool(bank.attrs.get("has_account_column", False))
    ledger_has = bool(ledger.attrs.get("has_account_column", False))

    if bank_has != ledger_has and len(bank) and len(ledger):
        present, missing = (
            ("extracto bancario", "libro contable") if bank_has
            else ("libro contable", "extracto bancario")
        )
        raise ReconciliationInputError(
            f"El {present} tiene columna 'account' pero el {missing} no. "
            "Con una sola cuenta, quítala de ambos archivos; con varias, agrégala "
            "a ambos. De lo contrario ninguna transacción podría coincidir."
        )

    if not (bank_has and ledger_has):
        return

    only_bank = sorted(set(bank["account_key"]) - set(ledger["account_key"]))
    only_ledger = sorted(set(ledger["account_key"]) - set(bank["account_key"]))
    if only_bank:
        logger.warning(
            "Cuentas solo en el extracto bancario (sus movimientos quedarán como "
            "excepciones): %s", ", ".join(only_bank),
        )
    if only_ledger:
        logger.warning(
            "Cuentas solo en el libro contable (sus movimientos quedarán como "
            "excepciones): %s", ", ".join(only_ledger),
        )


def load_transactions(bank_path, ledger_path):
    """Carga ambos archivos, los normaliza y valida su coherencia.

    Devuelve (bank, ledger, rejected).
    """
    bank_raw = _read_file(bank_path, "bank")
    ledger_raw = _read_file(ledger_path, "ledger")

    bank, bank_rejected = normalize_transactions(bank_raw, "bank", bank_path)
    ledger, ledger_rejected = normalize_transactions(ledger_raw, "ledger", ledger_path)

    validate_accounts(bank, ledger)

    rejected = pd.concat([bank_rejected, ledger_rejected], ignore_index=True)
    logger.info(
        "Cargadas %d transacciones de banco y %d de libro (%d fila(s) con problemas).",
        len(bank), len(ledger), len(rejected),
    )
    return bank, ledger, rejected
