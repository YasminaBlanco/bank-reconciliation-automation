"""Carga y normalización de extractos bancarios y libros contables.

Objetivo: que el archivo entre tal como lo exporta el banco, sin que nadie
lo prepare a mano, y que lo que NO se pueda interpretar quede registrado
explícitamente en vez de desaparecer en silencio.

Los cuatro desvíos que más rompen en la práctica, y que este módulo resuelve
antes de que el motor de reconciliación vea nada:

  - **Importes con formato contable**: `$1,450.00`, `1.450,00`, `(89.50)`
    como negativo, `89.50-`.
  - **Débito y crédito en columnas separadas**, en vez de un monto con signo.
  - **Separador y codificación regionales**: `;` en lugar de `,`, con BOM,
    en UTF-8 o Latin-1.
  - **Filas de cortesía antes de la tabla**: nombre del banco, número de
    cuenta y período, que hacían fallar la lectura con un error críptico.
"""

from __future__ import annotations

import csv
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
    # Muchos bancos no dan un monto con signo, sino dos columnas separadas.
    "debit": {"debit", "debits", "debito", "debitos", "cargo", "cargos", "salida",
              "salidas", "retiro", "retiros", "egreso", "egresos", "withdrawal",
              "withdrawals", "money_out", "paid_out"},
    "credit": {"credit", "credits", "credito", "creditos", "abono", "abonos",
               "deposito", "depositos", "ingreso", "ingresos", "deposit",
               "deposits", "money_in", "paid_in"},
    "account": {"account", "cuenta", "account_id", "account_name", "nro_cuenta",
                "numero_cuenta", "bank_account"},
    "invoice_ref": {"invoice_ref", "invoice", "invoice_number", "factura",
                    "referencia", "reference", "ref", "nro_factura"},
}

REQUIRED_COLUMNS = ("date", "description")

# Delimitadores que se prueban al detectar el formato de un CSV.
CANDIDATE_DELIMITERS = (",", ";", "\t", "|")

# Cuántas líneas se revisan buscando la fila de encabezados reales.
MAX_HEADER_SCAN_LINES = 30


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
    # El importe puede venir como una columna con signo o como dos columnas
    # separadas de débito y crédito, que es como exporta buena parte de la banca.
    if "amount" not in resolved and not ({"debit", "credit"} & set(resolved)):
        missing.append("amount (o debito / credito)")

    if missing:
        found = ", ".join(str(c) for c in df.columns) or "(ninguna)"
        raise ReconciliationInputError(
            f"[{source}] Faltan columnas obligatorias: {', '.join(missing)}. "
            f"Columnas encontradas: {found}. "
            "Se aceptan alias como fecha / monto / descripcion, y también un par "
            "de columnas debito y credito en lugar del monto." + _where(path)
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


def _read_text_lines(path: Path, limit: int):
    """Primeras líneas del archivo y la codificación con la que se pudo leer.

    `utf-8-sig` descarta el BOM que anteponen Excel y muchos exportadores
    bancarios; sin eso, la primera columna del encabezado llega con basura
    invisible pegada adelante y no se reconoce.
    """
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            with open(path, encoding=encoding, newline="") as handle:
                return [next(handle, "") for _ in range(limit)], encoding
        except UnicodeDecodeError:
            continue
    raise ReconciliationInputError(
        f"No se pudo leer {path.name}: codificación no reconocida."
    )


def _header_score(fields) -> set:
    """Columnas canónicas que se reconocen en una fila candidata a encabezado."""
    resolved = set()
    for field in fields:
        key = _normalize_header(field)
        for canonical, aliases in COLUMN_ALIASES.items():
            if key in aliases:
                resolved.add(canonical)
                break
    return resolved


def _is_usable_header(resolved: set) -> bool:
    """Una fila sirve como encabezado si trae fecha y alguna forma de importe."""
    has_amount = "amount" in resolved or bool({"debit", "credit"} & resolved)
    return "date" in resolved and has_amount


def sniff_csv_layout(path, limit: int = MAX_HEADER_SCAN_LINES):
    """Detecta delimitador, filas a omitir y codificación de un CSV.

    Resuelve de una vez los dos formatos que más rompen en la práctica:
    el separador regional (`;` en España y buena parte de LatAm) y las filas
    de cortesía que el banco pone antes de la tabla (nombre de la entidad,
    número de cuenta, período). Se queda con la combinación que reconoce más
    columnas, y ante empate con la que aparece antes en el archivo.
    """
    lines, encoding = _read_text_lines(Path(path), limit)

    best_key, best = None, (",", 0)
    for delimiter in CANDIDATE_DELIMITERS:
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            fields = next(csv.reader([line], delimiter=delimiter), [])
            if len(fields) < 2:
                continue
            resolved = _header_score(fields)
            if not _is_usable_header(resolved):
                continue
            key = (len(resolved), -index)
            if best_key is None or key > best_key:
                best_key, best = key, (delimiter, index)

    delimiter, skiprows = best
    return delimiter, skiprows, encoding


def _read_excel_file(path: Path, source: str) -> pd.DataFrame:
    """Lee un Excel saltando las filas previas al encabezado real."""
    raw = pd.read_excel(path, dtype=str, header=None)
    for index in range(min(len(raw), MAX_HEADER_SCAN_LINES)):
        if _is_usable_header(_header_score(raw.iloc[index].tolist())):
            if index:
                logger.info(
                    "[%s] %s: se omiten %d fila(s) previas al encabezado.",
                    source, path.name, index,
                )
            frame = raw.iloc[index + 1:].copy()
            frame.columns = [str(c) for c in raw.iloc[index].tolist()]
            return frame.reset_index(drop=True)
    # Ningún encabezado reconocible: se lee normal para que la validación
    # posterior explique qué columnas faltan.
    return pd.read_excel(path, dtype=str)


def _read_file(path, source: str) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise ReconciliationInputError(
            f"[{source}] No se encontró el archivo: {path}. "
            "Verifica la ruta o indícala con --bank / --ledger."
        )
    try:
        if path.suffix.lower() in {".xlsx", ".xls"}:
            return _read_excel_file(path, source)

        delimiter, skiprows, encoding = sniff_csv_layout(path)
        if skiprows:
            logger.info(
                "[%s] %s: se omiten %d fila(s) previas al encabezado.",
                source, path.name, skiprows,
            )
        if delimiter != ",":
            logger.info("[%s] %s: delimitador detectado %r.", source, path.name, delimiter)
        return pd.read_csv(
            path, dtype=str, keep_default_na=False, skipinitialspace=True,
            sep=delimiter, skiprows=skiprows, encoding=encoding,
        )
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


def combine_debit_credit(raw: pd.DataFrame, resolved: dict, debit_negative: bool = True):
    """Convierte un par de columnas débito/crédito en un monto con signo.

    Convención de extracto bancario: el débito es dinero que sale de la cuenta
    (negativo) y el crédito dinero que entra (positivo). Es la lectura del
    titular, no la del asiento contable, donde los signos van al revés. Con
    `debit_negative=False` se invierte, para libros exportados en la
    convención contable.

    Cada columna suele traer magnitudes positivas y la otra celda vacía, así
    que se toma el valor absoluto: un `-89,50` en la columna de cargos sigue
    siendo un cargo, no un abono.

    Devuelve (texto_original, monto) para que la fila rechazada pueda mostrar
    lo que venía en el archivo.
    """
    empty = pd.Series([None] * len(raw), index=raw.index, dtype=object)
    debits = raw[resolved["debit"]].map(parse_amount) if "debit" in resolved else empty
    credits = raw[resolved["credit"]].map(parse_amount) if "credit" in resolved else empty

    amounts, originals = [], []
    for debit, credit in zip(debits, credits):
        # `Series.map` convierte los None de parse_amount en NaN, así que la
        # celda vacía hay que detectarla con pd.isna y no con `is None`.
        has_debit = not pd.isna(debit)
        has_credit = not pd.isna(credit)
        if not has_debit and not has_credit:
            amounts.append(None)
            originals.append("")
            continue
        out = abs(debit) if has_debit else 0.0
        income = abs(credit) if has_credit else 0.0
        total = income - out if debit_negative else out - income
        amounts.append(round(total, 2))
        originals.append(
            f"débito={debit if has_debit else '-'} / "
            f"crédito={credit if has_credit else '-'}"
        )
    return pd.Series(originals, index=raw.index), pd.Series(amounts, index=raw.index)


def normalize_transactions(raw: pd.DataFrame, source: str, path=None,
                           debit_negative: bool = True):
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
    if "amount" in resolved:
        df["raw_amount"] = raw[resolved["amount"]]
        df["amount"] = df["raw_amount"].map(parse_amount)
    else:
        df["raw_amount"], df["amount"] = combine_debit_credit(
            raw, resolved, debit_negative=debit_negative
        )
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


def load_transactions(bank_path, ledger_path, debit_negative: bool = True):
    """Carga ambos archivos, los normaliza y valida su coherencia.

    Devuelve (bank, ledger, rejected).
    """
    bank_raw = _read_file(bank_path, "bank")
    ledger_raw = _read_file(ledger_path, "ledger")

    bank, bank_rejected = normalize_transactions(
        bank_raw, "bank", bank_path, debit_negative=debit_negative
    )
    ledger, ledger_rejected = normalize_transactions(
        ledger_raw, "ledger", ledger_path, debit_negative=debit_negative
    )

    validate_accounts(bank, ledger)

    rejected = pd.concat([bank_rejected, ledger_rejected], ignore_index=True)
    logger.info(
        "Cargadas %d transacciones de banco y %d de libro (%d fila(s) con problemas).",
        len(bank), len(ledger), len(rejected),
    )
    return bank, ledger, rejected
