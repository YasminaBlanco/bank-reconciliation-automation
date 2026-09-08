"""Tests de carga, normalización y validación de entrada."""

from __future__ import annotations

import pandas as pd
import pytest

from conftest import raw_frame
from reconciliation.loader import (
    DEFAULT_ACCOUNT,
    ReconciliationInputError,
    load_transactions,
    normalize_transactions,
    parse_amount,
    validate_accounts,
)


# --------------------------------------------------------------------------
# parse_amount: formatos que llegan de verdad en un export bancario
# --------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("1450.00", 1450.00),
    ("$1,450.00", 1450.00),
    ("1,234,567.89", 1234567.89),
    ("USD 1234.56", 1234.56),
    ("1.450,00", 1450.00),          # formato europeo
    ("89,50", 89.50),               # coma decimal
    ("1,450", 1450.00),             # coma de miles sin decimales
    ("(89.50)", -89.50),            # paréntesis contables = negativo
    ("(5,000.00)", -5000.00),
    ("89.50-", -89.50),             # signo al final
    ("-15.00", -15.00),
    ("+42.00", 42.00),
    ("0.00", 0.00),
    ("\xa0134.25\xa0", 134.25),     # espacios duros de un copy/paste
    (42, 42.00),
    (42.005, 42.01),                # redondeo a centavos
])
def test_parse_amount_formatos_validos(value, expected):
    assert parse_amount(value) == pytest.approx(expected)


@pytest.mark.parametrize("value", ["", "   ", "abc", "N/D", "-", "nan", None, True])
def test_parse_amount_devuelve_none_si_no_se_puede_interpretar(value):
    assert parse_amount(value) is None


def test_parse_amount_redondea_a_centavos():
    """Los montos se fijan a 2 decimales al cargar, para que los totales cuadren."""
    assert parse_amount("10.999") == 11.00
    assert parse_amount("10.994") == 10.99


@pytest.mark.parametrize("value,expected", [
    ("1.234.567", 1234567.00),   # dos grupos: separador de miles inequívoco
    ("1.234.567,89", 1234567.89),
    ("10.999", 11.00),           # un solo grupo: ambiguo, se lee como decimal
])
def test_punto_como_separador_de_miles_solo_si_no_es_ambiguo(value, expected):
    """'1.450' podría ser mil cuatrocientos cincuenta o 1,45. El formato
    europeo casi siempre trae coma decimal, así que ante un solo grupo se
    prefiere leer el punto como decimal."""
    assert parse_amount(value) == pytest.approx(expected)


# --------------------------------------------------------------------------
# Resolución de encabezados
# --------------------------------------------------------------------------

def test_acepta_encabezados_en_espanol_con_acentos():
    raw = pd.DataFrame([{
        "Cuenta": "CTA-1", "Fecha": "2026-08-01",
        "Descripción": "Depósito", "Importe": "1.450,00", "Nro Factura": "F-1",
    }])
    clean, rejected = normalize_transactions(raw, "ledger")

    assert rejected.empty
    assert clean.loc[0, "account"] == "CTA-1"
    assert clean.loc[0, "amount"] == 1450.00
    assert clean.loc[0, "invoice_ref"] == "F-1"


def test_falta_columna_obligatoria_da_error_accionable():
    raw = pd.DataFrame([{"fecha": "2026-08-01", "concepto": "Pago"}])

    with pytest.raises(ReconciliationInputError) as exc:
        normalize_transactions(raw, "bank")

    mensaje = str(exc.value)
    assert "amount" in mensaje                 # dice qué falta
    assert "fecha" in mensaje and "concepto" in mensaje   # y qué sí encontró


def test_dos_columnas_que_mapean_a_lo_mismo_da_error():
    raw = pd.DataFrame([{"date": "2026-08-01", "description": "x", "amount": "1", "monto": "2"}])

    with pytest.raises(ReconciliationInputError, match="amount"):
        normalize_transactions(raw, "bank")


# --------------------------------------------------------------------------
# Filas problemáticas: se registran, no desaparecen
# --------------------------------------------------------------------------

def test_fecha_invalida_va_a_rechazadas_con_numero_de_fila():
    raw = raw_frame([
        {"date": "2026-08-01", "description": "OK", "amount": "100.00"},
        {"date": "no es fecha", "description": "MALA", "amount": "250.00"},
    ])
    clean, rejected = normalize_transactions(raw, "bank")

    assert len(clean) == 1
    assert len(rejected) == 1
    assert rejected.loc[0, "reason"] == "reason.invalid_date"
    # Fila 3 del archivo: encabezado + primera fila buena + esta.
    assert rejected.loc[0, "row"] == 3
    assert rejected.loc[0, "description"] == "MALA"


def test_monto_invalido_va_a_rechazadas():
    raw = raw_frame([{"date": "2026-08-01", "description": "Ilegible", "amount": "N/D"}])
    clean, rejected = normalize_transactions(raw, "ledger")

    assert clean.empty
    assert rejected.loc[0, "reason"] == "reason.invalid_amount"
    assert rejected.loc[0, "row"] == 2


def test_fecha_y_monto_invalidos_reportan_ambos():
    raw = raw_frame([{"date": "", "description": "X", "amount": ""}])
    _, rejected = normalize_transactions(raw, "bank")

    assert rejected.loc[0, "reason"] == "reason.invalid_date_and_amount"


def test_una_fila_mala_no_impide_procesar_el_resto():
    raw = raw_frame([
        {"date": "2026-08-01", "description": "A", "amount": "10.00"},
        {"date": "", "description": "B", "amount": "20.00"},
        {"date": "2026-08-03", "description": "C", "amount": "30.00"},
    ])
    clean, rejected = normalize_transactions(raw, "bank")

    assert list(clean["description"]) == ["A", "C"]
    assert len(rejected) == 1


# --------------------------------------------------------------------------
# Columnas opcionales
# --------------------------------------------------------------------------

def test_ledger_sin_invoice_ref_no_rompe():
    """Regresión: antes esto lanzaba KeyError a mitad de la reconciliación."""
    raw = raw_frame([{"date": "2026-08-01", "description": "Pago", "amount": "100.00"}])
    clean, _ = normalize_transactions(raw, "ledger")

    assert clean.loc[0, "invoice_ref"] == ""


def test_sin_columna_account_usa_cuenta_por_defecto():
    raw = raw_frame([{"date": "2026-08-01", "description": "Pago", "amount": "100.00"}])
    clean, _ = normalize_transactions(raw, "bank")

    assert clean.loc[0, "account"] == DEFAULT_ACCOUNT
    assert clean.attrs["has_account_column"] is False


def test_account_vacia_cae_a_cuenta_por_defecto():
    raw = raw_frame([{"account": "", "date": "2026-08-01", "description": "P", "amount": "1.00"}])
    clean, _ = normalize_transactions(raw, "bank")

    assert clean.loc[0, "account"] == DEFAULT_ACCOUNT


def test_account_se_compara_sin_distinguir_mayusculas():
    bank_raw = raw_frame([{"account": "Checking", "date": "2026-08-01", "description": "P", "amount": "1.00"}])
    ledger_raw = raw_frame([{"account": "CHECKING", "date": "2026-08-01", "description": "p", "amount": "1.00"}])
    bank, _ = normalize_transactions(bank_raw, "bank")
    ledger, _ = normalize_transactions(ledger_raw, "ledger")

    assert bank.loc[0, "account_key"] == ledger.loc[0, "account_key"]


# --------------------------------------------------------------------------
# Validación cruzada
# --------------------------------------------------------------------------

def test_account_en_un_solo_lado_falla_temprano():
    """Sin esta validación no coincidiría nada y el cliente vería
    'todo es una excepción' sin saber por qué."""
    bank_raw = raw_frame([{"account": "CTA-1", "date": "2026-08-01", "description": "P", "amount": "1.00"}])
    ledger_raw = raw_frame([{"date": "2026-08-01", "description": "p", "amount": "1.00"}])
    bank, _ = normalize_transactions(bank_raw, "bank")
    ledger, _ = normalize_transactions(ledger_raw, "ledger")

    with pytest.raises(ReconciliationInputError, match="account"):
        validate_accounts(bank, ledger)


def test_mismas_columnas_en_ambos_lados_no_falla():
    bank_raw = raw_frame([{"date": "2026-08-01", "description": "P", "amount": "1.00"}])
    ledger_raw = raw_frame([{"date": "2026-08-01", "description": "p", "amount": "1.00"}])
    bank, _ = normalize_transactions(bank_raw, "bank")
    ledger, _ = normalize_transactions(ledger_raw, "ledger")

    validate_accounts(bank, ledger)  # no debe lanzar


# --------------------------------------------------------------------------
# Lectura de archivos
# --------------------------------------------------------------------------

def test_archivo_inexistente_da_mensaje_claro(tmp_path):
    with pytest.raises(ReconciliationInputError) as exc:
        load_transactions(tmp_path / "no_existe.csv", tmp_path / "tampoco.csv")

    assert "No se encontró el archivo" in str(exc.value)


def test_carga_los_csv_de_ejemplo_del_repositorio(demo_paths):
    bank_path, ledger_path = demo_paths
    bank, ledger, rejected = load_transactions(bank_path, ledger_path)

    assert len(bank) == 13          # 14 filas, 1 sin fecha
    assert len(ledger) == 12        # 13 filas, 1 con monto ilegible
    assert len(rejected) == 2
    assert set(bank["account"]) == {"CHECKING-4821", "SAVINGS-9107"}
    # Los formatos contables quedaron convertidos a número.
    assert bank["amount"].dtype.kind == "f"
    assert bank.loc[bank["description"] == "TRANSFER TO SAVINGS", "amount"].iloc[0] == -5000.00
