"""Tests del motor de reconciliación.

Cubren en particular las dos fallas que tenía la versión anterior:
emparejar por fecha sin mirar el monto, y exigir fecha idéntica.
"""

from __future__ import annotations

import pytest

from conftest import bank_frame, bank_row, ledger_frame, ledger_row
from reconciliation.matching import (
    ONLY_BANK_COLUMNS,
    discrepancy_threshold,
    reconcile,
)


def run(bank_rows, ledger_rows, **kwargs):
    return reconcile(bank_frame(bank_rows), ledger_frame(ledger_rows), **kwargs)


def counts(results):
    return (
        len(results["matches"]),
        len(results["discrepancies"]),
        len(results["only_in_bank"]),
        len(results["only_in_ledger"]),
    )


# --------------------------------------------------------------------------
# Coincidencias exactas
# --------------------------------------------------------------------------

def test_coincidencia_exacta_mismo_dia():
    results = run(
        [bank_row("2026-08-01", "SHOPIFY DEP", "1450.00")],
        [ledger_row("2026-08-01", "Shopify sales", "1450.00", "INV-1")],
    )

    assert counts(results) == (1, 0, 0, 0)
    match = results["matches"].iloc[0]
    assert match["amount"] == 1450.00
    assert match["invoice_ref"] == "INV-1"
    assert match["day_gap"] == 0


def test_tolerancia_de_centavos_cuenta_como_coincidencia():
    results = run(
        [bank_row("2026-08-01", "PAGO", "100.00")],
        [ledger_row("2026-08-01", "Pago", "100.01", "INV-1")],
    )

    assert counts(results) == (1, 0, 0, 0)


# --------------------------------------------------------------------------
# Ventana de fechas (bug B de la versión anterior)
# --------------------------------------------------------------------------

def test_acredita_dias_despues_sigue_siendo_coincidencia():
    """El banco acredita un ACH 2 días después de que el libro lo registra."""
    results = run(
        [bank_row("2026-08-17", "CLIENT PAYMENT - GLOBEX", "1800.00")],
        [ledger_row("2026-08-15", "Payment Globex", "1800.00", "INV-3001")],
    )

    assert counts(results) == (1, 0, 0, 0)
    assert results["matches"].iloc[0]["day_gap"] == 2


def test_desfase_mayor_a_la_ventana_no_empareja():
    results = run(
        [bank_row("2026-08-25", "PAGO", "1800.00")],
        [ledger_row("2026-08-15", "Pago", "1800.00", "INV-1")],
        date_window=3,
    )

    assert counts(results) == (0, 0, 1, 1)


def test_ventana_cero_exige_fecha_identica():
    results = run(
        [bank_row("2026-08-02", "PAGO", "100.00")],
        [ledger_row("2026-08-01", "Pago", "100.00", "INV-1")],
        date_window=0,
    )

    assert counts(results) == (0, 0, 1, 1)


def test_ventana_negativa_es_error():
    with pytest.raises(ValueError, match="date_window"):
        run([], [], date_window=-1)


# --------------------------------------------------------------------------
# Discrepancias reales
# --------------------------------------------------------------------------

def test_discrepancia_dentro_del_umbral():
    results = run(
        [bank_row("2026-08-06", "CLIENT PAYMENT - GLOBEX", "1800.00")],
        [ledger_row("2026-08-06", "Payment Globex", "1750.00", "INV-1005")],
    )

    assert counts(results) == (0, 1, 0, 0)
    row = results["discrepancies"].iloc[0]
    assert row["bank_amount"] == 1800.00
    assert row["ledger_amount"] == 1750.00
    assert row["difference"] == 50.00
    assert row["invoice_ref"] == "INV-1005"


def test_diferencia_enorme_no_es_discrepancia_sino_dos_partidas_sueltas():
    """Sin umbral, un cobro y un gasto del mismo día se emparejarían."""
    results = run(
        [bank_row("2026-08-06", "PAGO GRANDE", "9000.00")],
        [ledger_row("2026-08-06", "Pago chico", "12.00", "INV-1")],
    )

    assert counts(results) == (0, 0, 1, 1)


def test_umbral_porcentual_aplica_en_montos_grandes():
    """300 sobre 10.300 es 2,9%: dentro del 5% aunque supere los $50."""
    results = run(
        [bank_row("2026-08-06", "PAGO", "10000.00")],
        [ledger_row("2026-08-06", "Pago", "10300.00", "INV-1")],
    )

    assert counts(results) == (0, 1, 0, 0)


def test_umbral_configurable_reduce_lo_que_se_considera_discrepancia():
    results = run(
        [bank_row("2026-08-06", "PAGO", "1800.00")],
        [ledger_row("2026-08-06", "Pago", "1750.00", "INV-1")],
        discrepancy_abs=10.0, discrepancy_pct=0.0,
    )

    assert counts(results) == (0, 0, 1, 1)


def test_discrepancy_threshold_toma_el_mayor_de_los_dos_limites():
    assert discrepancy_threshold(100.0, 100.0, 50.0, 5.0) == 50.0     # gana el absoluto
    assert discrepancy_threshold(10000.0, 10000.0, 50.0, 5.0) == 500.0  # gana el porcentual


# --------------------------------------------------------------------------
# Regresión: el bug principal de la versión anterior
# --------------------------------------------------------------------------

def test_no_empareja_una_comision_con_un_cobro_del_mismo_dia():
    """REGRESIÓN.

    La versión anterior tomaba el primer asiento del día sin mirar el monto:
    cruzaba la comisión de -15 con el cobro de 3.200 y reportaba dos
    discrepancias falsas de ~3.215, perdiendo la discrepancia real de 0,50.
    """
    results = run(
        [
            bank_row("2026-08-14", "BANK FEE", "-15.00"),
            bank_row("2026-08-14", "CLIENT PAYMENT - ACME", "3200.00"),
        ],
        [
            ledger_row("2026-08-14", "Payment Acme Corp", "3200.00", "INV-2001"),
            ledger_row("2026-08-14", "Comision bancaria", "-15.50", "INV-2002"),
        ],
    )

    assert counts(results) == (1, 1, 0, 0)
    assert results["matches"].iloc[0]["amount"] == 3200.00

    discrepancy = results["discrepancies"].iloc[0]
    assert discrepancy["bank_description"] == "BANK FEE"
    assert discrepancy["ledger_description"] == "Comision bancaria"
    assert discrepancy["difference"] == 0.50


def test_signos_opuestos_nunca_se_emparejan():
    """Un reembolso de +500 y un pago de -500 son movimientos distintos."""
    results = run(
        [bank_row("2026-08-20", "REEMBOLSO", "500.00")],
        [ledger_row("2026-08-20", "Pago proveedor", "-500.00", "INV-9")],
    )

    assert counts(results) == (0, 0, 1, 1)


def test_elige_el_candidato_mas_cercano_no_el_primero():
    """Con dos asientos posibles gana el de monto más parecido."""
    results = run(
        [bank_row("2026-08-01", "PAGO", "100.00")],
        [
            ledger_row("2026-08-01", "Lejano", "100.40", "INV-LEJOS"),
            ledger_row("2026-08-01", "Cercano", "100.05", "INV-CERCA"),
        ],
    )

    assert counts(results) == (0, 1, 0, 1)
    assert results["discrepancies"].iloc[0]["invoice_ref"] == "INV-CERCA"
    assert results["only_in_ledger"].iloc[0]["invoice_ref"] == "INV-LEJOS"


def test_la_coincidencia_exacta_gana_sobre_la_discrepancia():
    results = run(
        [bank_row("2026-08-01", "PAGO", "100.00")],
        [
            ledger_row("2026-08-01", "Aproximado", "100.40", "INV-APROX"),
            ledger_row("2026-08-01", "Exacto", "100.00", "INV-EXACTO"),
        ],
    )

    assert results["matches"].iloc[0]["invoice_ref"] == "INV-EXACTO"


def test_prefiere_el_desfase_menor_ante_montos_iguales():
    results = run(
        [bank_row("2026-08-03", "PAGO", "100.00")],
        [
            ledger_row("2026-08-01", "Lejano", "100.00", "INV-LEJOS"),
            ledger_row("2026-08-03", "Mismo dia", "100.00", "INV-HOY"),
        ],
    )

    assert results["matches"].iloc[0]["invoice_ref"] == "INV-HOY"


# --------------------------------------------------------------------------
# Uno a uno
# --------------------------------------------------------------------------

def test_un_asiento_no_puede_cubrir_dos_movimientos():
    results = run(
        [
            bank_row("2026-08-01", "PAGO A", "500.00"),
            bank_row("2026-08-01", "PAGO B", "500.00"),
        ],
        [ledger_row("2026-08-01", "Pago", "500.00", "INV-1")],
    )

    assert counts(results) == (1, 0, 1, 0)


def test_movimientos_duplicados_se_emparejan_uno_a_uno():
    results = run(
        [
            bank_row("2026-08-01", "COMISION", "-50.00"),
            bank_row("2026-08-01", "COMISION", "-50.00"),
        ],
        [
            ledger_row("2026-08-01", "Comision", "-50.00", "INV-1"),
            ledger_row("2026-08-01", "Comision", "-50.00", "INV-2"),
        ],
    )

    assert counts(results) == (2, 0, 0, 0)
    assert set(results["matches"]["invoice_ref"]) == {"INV-1", "INV-2"}


# --------------------------------------------------------------------------
# Multi-cuenta
# --------------------------------------------------------------------------

def test_no_cruza_movimientos_entre_cuentas():
    """Mismo monto y misma fecha en dos cuentas: cada uno con el suyo."""
    results = run(
        [
            bank_row("2026-08-01", "DEP A", "1000.00", account="CHECKING"),
            bank_row("2026-08-01", "DEP B", "1000.00", account="SAVINGS"),
        ],
        [
            ledger_row("2026-08-01", "Deposito B", "1000.00", "S-1", account="SAVINGS"),
            ledger_row("2026-08-01", "Deposito A", "1000.00", "C-1", account="CHECKING"),
        ],
    )

    assert counts(results) == (2, 0, 0, 0)
    pares = dict(zip(results["matches"]["bank_description"], results["matches"]["invoice_ref"]))
    assert pares == {"DEP A": "C-1", "DEP B": "S-1"}


def test_cuenta_presente_en_un_solo_lado_queda_como_excepcion():
    results = run(
        [bank_row("2026-08-01", "DEP", "100.00", account="NUEVA")],
        [ledger_row("2026-08-01", "Deposito", "100.00", "INV-1", account="VIEJA")],
    )

    assert counts(results) == (0, 0, 1, 1)


def test_el_resultado_conserva_la_cuenta_de_cada_movimiento():
    results = run(
        [bank_row("2026-08-01", "DEP", "100.00", account="CHECKING")],
        [ledger_row("2026-08-01", "Deposito", "100.00", "INV-1", account="CHECKING")],
    )

    assert results["matches"].iloc[0]["account"] == "CHECKING"


# --------------------------------------------------------------------------
# Casos límite
# --------------------------------------------------------------------------

def test_banco_vacio_deja_todo_el_libro_como_excepcion():
    results = run([], [ledger_row("2026-08-01", "Pago", "100.00", "INV-1")])

    assert counts(results) == (0, 0, 0, 1)


def test_libro_vacio_deja_todo_el_banco_como_excepcion():
    results = run([bank_row("2026-08-01", "PAGO", "100.00")], [])

    assert counts(results) == (0, 0, 1, 0)


def test_ambos_vacios_devuelve_el_esquema_completo():
    """Aunque no haya datos, las columnas deben existir: el Excel del
    cliente necesita encabezados en todas las pestañas."""
    results = run([], [])

    assert counts(results) == (0, 0, 0, 0)
    assert list(results["only_in_bank"].columns) == ONLY_BANK_COLUMNS


def test_monto_cero_no_rompe():
    results = run(
        [bank_row("2026-08-01", "AJUSTE", "0.00")],
        [ledger_row("2026-08-01", "Ajuste", "0.00", "INV-1")],
    )

    assert counts(results) == (1, 0, 0, 0)


def test_el_resultado_es_determinista():
    escenario = (
        [
            bank_row("2026-08-01", "A", "100.00"),
            bank_row("2026-08-01", "B", "100.00"),
            bank_row("2026-08-02", "C", "250.00"),
        ],
        [
            ledger_row("2026-08-01", "a", "100.00", "INV-1"),
            ledger_row("2026-08-02", "c", "249.50", "INV-2"),
        ],
    )
    primero = run(*escenario)
    segundo = run(*escenario)

    assert primero["matches"].equals(segundo["matches"])
    assert primero["discrepancies"].equals(segundo["discrepancies"])
