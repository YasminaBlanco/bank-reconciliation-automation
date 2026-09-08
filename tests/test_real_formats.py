"""Tests de los formatos con los que exporta la banca de verdad.

Los CSV de ejemplo del repositorio son prolijos: coma como separador, una
sola columna de monto con signo, y el encabezado en la primera fila. Los
archivos que manda un cliente casi nunca son así. Estos tests cubren los
tres desvíos más frecuentes, cada uno de los cuales rompía el cargador.
"""

from __future__ import annotations

import pytest

from reconciliation.loader import (
    ReconciliationInputError,
    _read_file,
    combine_debit_credit,
    load_transactions,
    normalize_transactions,
    sniff_csv_layout,
)


def write(tmp_path, name, content, encoding="utf-8"):
    destination = tmp_path / name
    destination.write_bytes(content.encode(encoding))
    return destination


def load(path):
    raw = _read_file(path, "bank")
    return normalize_transactions(raw, "bank")


# --------------------------------------------------------------------------
# Columnas de débito y crédito separadas
# --------------------------------------------------------------------------

DEBIT_CREDIT_CSV = (
    "Fecha,Descripcion,Debito,Credito\n"
    "2026-08-01,DEPOSITO,,1450.00\n"
    "2026-08-02,AWS,89.50,\n"
)


def test_columnas_debito_credito_se_combinan_en_un_monto(tmp_path):
    """Buena parte de la banca no da un monto con signo, sino dos columnas."""
    clean, rejected = load(write(tmp_path, "extracto.csv", DEBIT_CREDIT_CSV))

    assert len(clean) == 2 and rejected.empty
    assert clean.loc[0, "amount"] == 1450.00     # crédito: entra dinero
    assert clean.loc[1, "amount"] == -89.50      # débito: sale dinero


def test_debit_positive_invierte_la_convencion(tmp_path):
    """Un libro contable usa los signos al revés que un extracto."""
    raw = _read_file(write(tmp_path, "libro.csv", DEBIT_CREDIT_CSV), "bank")
    clean, _ = normalize_transactions(raw, "bank", debit_negative=False)

    assert clean.loc[0, "amount"] == -1450.00
    assert clean.loc[1, "amount"] == 89.50


def test_acepta_alias_en_ingles_de_debito_y_credito(tmp_path):
    contenido = (
        "Date,Description,Money Out,Money In\n"
        "2026-08-01,SALARY,,3000.00\n"
        "2026-08-02,RENT,1200.00,\n"
    )
    clean, _ = load(write(tmp_path, "statement.csv", contenido))

    assert list(clean["amount"]) == [3000.00, -1200.00]


def test_un_debito_ya_negativo_sigue_siendo_salida(tmp_path):
    """Si el banco escribe -89,50 en la columna de cargos, sigue siendo cargo."""
    contenido = "Fecha,Concepto,Cargo,Abono\n2026-08-02,AWS,-89.50,\n"
    clean, _ = load(write(tmp_path, "extracto.csv", contenido))

    assert clean.loc[0, "amount"] == -89.50


def test_fila_con_ambas_celdas_vacias_se_rechaza(tmp_path):
    contenido = "Fecha,Concepto,Debito,Credito\n2026-08-01,SIN IMPORTE,,\n"
    clean, rejected = load(write(tmp_path, "extracto.csv", contenido))

    assert clean.empty
    assert rejected.loc[0, "reason"] == "reason.invalid_amount"


def test_si_hay_monto_y_debito_credito_gana_el_monto(tmp_path):
    contenido = (
        "Fecha,Concepto,Importe,Debito,Credito\n"
        "2026-08-01,DEPOSITO,999.00,,1450.00\n"
    )
    clean, _ = load(write(tmp_path, "extracto.csv", contenido))

    assert clean.loc[0, "amount"] == 999.00


def test_combine_debit_credit_directo():
    import pandas as pd

    raw = pd.DataFrame({"D": ["100.00", ""], "C": ["", "250.00"]})
    resolved = {"debit": "D", "credit": "C"}
    originales, montos = combine_debit_credit(raw, resolved)

    assert list(montos) == [-100.00, 250.00]
    assert "débito=100.0" in originales.iloc[0]


# --------------------------------------------------------------------------
# Separador regional y BOM
# --------------------------------------------------------------------------

def test_delimitador_punto_y_coma(tmp_path):
    """El separador por defecto en España y buena parte de LatAm."""
    contenido = "Fecha;Descripcion;Importe\n2026-08-01;DEPOSITO;1.450,00\n"
    clean, _ = load(write(tmp_path, "extracto.csv", contenido))

    assert len(clean) == 1
    assert clean.loc[0, "amount"] == 1450.00


def test_bom_al_inicio_del_archivo(tmp_path):
    """Excel antepone un BOM invisible que pega basura al primer encabezado."""
    contenido = "﻿Fecha,Descripcion,Importe\n2026-08-01,DEPOSITO,1450.00\n"
    clean, _ = load(write(tmp_path, "extracto.csv", contenido))

    assert len(clean) == 1
    assert clean.loc[0, "description"] == "DEPOSITO"


def test_bom_y_punto_y_coma_juntos(tmp_path):
    contenido = "﻿Fecha;Descripcion;Importe\n2026-08-01;DEPOSITO;1.450,00\n"
    clean, _ = load(write(tmp_path, "extracto.csv", contenido))

    assert clean.loc[0, "amount"] == 1450.00


def test_delimitador_tabulacion(tmp_path):
    contenido = "Fecha\tDescripcion\tImporte\n2026-08-01\tDEPOSITO\t1450.00\n"
    clean, _ = load(write(tmp_path, "extracto.tsv", contenido))

    assert clean.loc[0, "amount"] == 1450.00


def test_archivo_en_latin1_no_rompe(tmp_path):
    contenido = "Fecha,Descripción,Importe\n2026-08-01,TRANSFERENCIA ANÓNIMA,1450.00\n"
    clean, _ = load(write(tmp_path, "extracto.csv", contenido, encoding="latin-1"))

    assert len(clean) == 1


# --------------------------------------------------------------------------
# Filas de encabezado antes de la tabla
# --------------------------------------------------------------------------

HEADER_NOISE_CSV = (
    "BANCO EJEMPLO S.A.\n"
    "Extracto de cuenta 4821\n"
    "Periodo: 01/08/2026 - 31/08/2026\n"
    "\n"
    "Fecha,Descripcion,Monto\n"
    "2026-08-01,DEPOSITO,1450.00\n"
    "2026-08-02,AWS,-89.50\n"
)


def test_filas_de_cortesia_antes_de_la_tabla(tmp_path):
    """Casi todos los bancos ponen su nombre y el período antes de los datos.

    Antes esto reventaba con un error críptico de pandas:
    'Error tokenizing data. C error: Expected 1 fields in line 5, saw 3'.
    """
    clean, rejected = load(write(tmp_path, "extracto.csv", HEADER_NOISE_CSV))

    assert len(clean) == 2 and rejected.empty
    assert list(clean["amount"]) == [1450.00, -89.50]


def test_encabezado_precedido_de_ruido_y_con_punto_y_coma(tmp_path):
    contenido = (
        "BANCO EJEMPLO S.A.\n"
        "Cuenta;4821\n"
        "\n"
        "Fecha;Concepto;Cargo;Abono\n"
        "2026-08-01;DEPOSITO;;1450,00\n"
    )
    clean, _ = load(write(tmp_path, "extracto.csv", contenido))

    assert clean.loc[0, "amount"] == 1450.00


def test_sniff_detecta_delimitador_y_filas_a_omitir(tmp_path):
    destino = write(tmp_path, "extracto.csv", HEADER_NOISE_CSV)
    delimiter, skiprows, encoding = sniff_csv_layout(destino)

    assert delimiter == ","
    assert skiprows == 4
    assert encoding == "utf-8-sig"


def test_sniff_no_confunde_una_fila_de_datos_con_el_encabezado(tmp_path):
    """La fila elegida debe ser la de rótulos, no la primera con 3 campos."""
    destino = write(tmp_path, "extracto.csv", HEADER_NOISE_CSV)
    _, skiprows, _ = sniff_csv_layout(destino)
    clean, _ = load(destino)

    assert skiprows == 4
    assert "DEPOSITO" in list(clean["description"])   # los datos no se perdieron


# --------------------------------------------------------------------------
# Errores que deben seguir siendo claros
# --------------------------------------------------------------------------

def test_sin_ninguna_columna_de_importe_el_error_menciona_debito_y_credito(tmp_path):
    contenido = "Fecha,Descripcion\n2026-08-01,DEPOSITO\n"

    with pytest.raises(ReconciliationInputError) as exc:
        load(write(tmp_path, "extracto.csv", contenido))

    mensaje = str(exc.value)
    assert "amount" in mensaje
    assert "debito" in mensaje and "credito" in mensaje


def test_archivo_sin_nada_reconocible_sigue_fallando_con_mensaje_util(tmp_path):
    contenido = "columna_rara,otra_cosa\n1,2\n"

    with pytest.raises(ReconciliationInputError, match="Faltan columnas"):
        load(write(tmp_path, "extracto.csv", contenido))


# --------------------------------------------------------------------------
# Extremo a extremo
# --------------------------------------------------------------------------

def test_concilia_un_extracto_realista_contra_un_libro_limpio(tmp_path):
    """Extracto con las tres rarezas a la vez, libro en formato prolijo."""
    extracto = (
        "﻿BANCO EJEMPLO S.A.\n"
        "Extracto mensual\n"
        "\n"
        "Fecha;Concepto;Cargo;Abono\n"
        "2026-08-01;DEPOSITO CLIENTE;;1.450,00\n"
        "2026-08-02;SERVICIOS AWS;89,50;\n"
    )
    libro = (
        "date,description,amount,invoice_ref\n"
        "2026-08-01,Cobro cliente,1450.00,INV-1\n"
        "2026-08-02,AWS hosting,-89.50,INV-2\n"
    )
    bank_path = write(tmp_path, "extracto.csv", extracto)
    ledger_path = write(tmp_path, "libro.csv", libro)

    bank, ledger, rejected = load_transactions(bank_path, ledger_path)

    assert len(bank) == 2 and len(ledger) == 2 and rejected.empty

    from reconciliation.matching import reconcile
    results = reconcile(bank, ledger)

    assert len(results["matches"]) == 2
    assert results["only_in_bank"].empty and results["only_in_ledger"].empty
