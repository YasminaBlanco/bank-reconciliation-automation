"""Tests del punto de entrada CLI: pipeline, argumentos y códigos de salida."""

from __future__ import annotations

import openpyxl
import pytest

import reconcile
from reconcile import EXIT_EXCEPTIONS_FOUND, EXIT_INPUT_ERROR, EXIT_OK, main, run


def test_run_devuelve_el_resultado_y_escribe_el_excel(tmp_path, demo_paths):
    bank_path, ledger_path = demo_paths
    destino = tmp_path / "reporte.xlsx"

    salida = run(bank_path, ledger_path, destino, quiet=True)

    assert destino.exists()
    assert salida["out_path"] == destino
    assert set(salida) == {"results", "summary", "rejected", "flagged", "out_path"}
    assert salida["flagged"] > 0            # los datos de ejemplo traen excepciones
    assert len(salida["rejected"]) == 2


def test_run_respeta_el_idioma(tmp_path, demo_paths):
    bank_path, ledger_path = demo_paths
    destino = tmp_path / "report.xlsx"

    run(bank_path, ledger_path, destino, lang="en", quiet=True)

    assert "Summary" in openpyxl.load_workbook(destino).sheetnames


def test_run_con_ventana_amplia_empareja_mas(tmp_path, demo_paths):
    """El pago de INITECH tiene 3 días de desfase: con ventana 0 no cuadra."""
    bank_path, ledger_path = demo_paths

    estrecho = run(bank_path, ledger_path, tmp_path / "a.xlsx", date_window=0, quiet=True)
    amplio = run(bank_path, ledger_path, tmp_path / "b.xlsx", date_window=3, quiet=True)

    assert len(amplio["results"]["matches"]) > len(estrecho["results"]["matches"])


def test_main_termina_bien_con_los_datos_de_ejemplo(tmp_path, demo_paths):
    bank_path, ledger_path = demo_paths

    codigo = main([
        "--bank", str(bank_path), "--ledger", str(ledger_path),
        "--out", str(tmp_path / "r.xlsx"), "--quiet",
    ])

    assert codigo == EXIT_OK


def test_main_devuelve_codigo_2_si_hay_excepciones_y_se_pide(tmp_path, demo_paths):
    """Permite que Cloud Scheduler o un CI disparen una alerta."""
    bank_path, ledger_path = demo_paths

    codigo = main([
        "--bank", str(bank_path), "--ledger", str(ledger_path),
        "--out", str(tmp_path / "r.xlsx"), "--quiet", "--fail-on-exceptions",
    ])

    assert codigo == EXIT_EXCEPTIONS_FOUND


def test_main_sin_fail_on_exceptions_devuelve_cero_aunque_haya_excepciones(tmp_path, demo_paths):
    bank_path, ledger_path = demo_paths

    codigo = main([
        "--bank", str(bank_path), "--ledger", str(ledger_path),
        "--out", str(tmp_path / "r.xlsx"), "--quiet",
    ])

    assert codigo == EXIT_OK


def test_main_con_archivo_inexistente_devuelve_codigo_1(tmp_path):
    codigo = main([
        "--bank", str(tmp_path / "no.csv"), "--ledger", str(tmp_path / "tampoco.csv"),
        "--out", str(tmp_path / "r.xlsx"), "--quiet",
    ])

    assert codigo == EXIT_INPUT_ERROR


def test_main_rechaza_ventana_negativa(tmp_path, demo_paths):
    bank_path, ledger_path = demo_paths

    codigo = main([
        "--bank", str(bank_path), "--ledger", str(ledger_path),
        "--out", str(tmp_path / "r.xlsx"), "--date-window", "-1", "--quiet",
    ])

    assert codigo == EXIT_INPUT_ERROR


def test_main_con_columnas_faltantes_devuelve_codigo_1(tmp_path):
    malo = tmp_path / "malo.csv"
    malo.write_text("columna_rara,otra\n1,2\n", encoding="utf-8")

    codigo = main([
        "--bank", str(malo), "--ledger", str(malo),
        "--out", str(tmp_path / "r.xlsx"), "--quiet",
    ])

    assert codigo == EXIT_INPUT_ERROR


def test_main_imprime_el_reporte_salvo_que_se_pida_quiet(tmp_path, demo_paths, capsys):
    bank_path, ledger_path = demo_paths
    argumentos = ["--bank", str(bank_path), "--ledger", str(ledger_path),
                  "--out", str(tmp_path / "r.xlsx")]

    main(argumentos)
    assert "RECONCILIACIÓN" in capsys.readouterr().out

    main(argumentos + ["--quiet"])
    assert capsys.readouterr().out == ""


def test_los_logs_en_json_son_parseables(tmp_path, demo_paths, capsys):
    import json

    bank_path, ledger_path = demo_paths
    main(["--bank", str(bank_path), "--ledger", str(ledger_path),
          "--out", str(tmp_path / "r.xlsx"), "--quiet", "--json-logs"])

    lineas = [l for l in capsys.readouterr().err.splitlines() if l.strip()]
    assert lineas
    for linea in lineas:
        registro = json.loads(linea)
        assert "severity" in registro and "message" in registro


@pytest.mark.parametrize("argumento", ["--bank", "--ledger", "--out", "--lang"])
def test_el_parser_expone_las_opciones_principales(argumento):
    parser = reconcile.build_parser()
    assert any(argumento in accion.option_strings for accion in parser._actions)


def test_lang_invalido_es_rechazado_por_el_parser():
    with pytest.raises(SystemExit):
        reconcile.build_parser().parse_args(["--lang", "fr"])
