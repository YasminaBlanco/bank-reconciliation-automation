"""Motor de reconciliación banco <-> libro contable.

Estrategia (en dos fases, siempre dentro de la misma cuenta):

  Fase 1 - Coincidencias exactas: pares cuyo monto difiere como máximo
  `amount_tolerance` y cuya fecha difiere como máximo `date_window` días.

  Fase 2 - Discrepancias: sobre lo que quedó sin emparejar, pares del
  MISMO SIGNO cuya diferencia cae dentro del umbral de discrepancia
  (el mayor entre `discrepancy_abs` y `discrepancy_pct` % del monto).

En ambas fases los pares se evalúan de mejor a peor (menor diferencia de
monto, luego menor desfase de fechas) y se asignan uno a uno. Esto evita
el emparejamiento arbitrario "primer candidato del día", que producía
discrepancias falsas al cruzar, por ejemplo, una comisión con un cobro.

Lo que no cae en ninguna fase no se fuerza: queda como partida suelta en
`only_in_bank` u `only_in_ledger`.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Valores por defecto: pensados para extractos bancarios reales.
AMOUNT_TOLERANCE = 0.01   # dos montos iguales al centavo
DATE_WINDOW_DAYS = 3      # ACH / cheques suelen acreditar en 1-3 días
DISCREPANCY_ABS = 50.0    # diferencia máxima en valor absoluto
DISCREPANCY_PCT = 5.0     # ... o en porcentaje del monto, lo que sea mayor

MATCH_COLUMNS = [
    "account", "bank_date", "ledger_date", "day_gap", "bank_description",
    "ledger_description", "amount", "difference", "invoice_ref",
]
DISCREPANCY_COLUMNS = [
    "account", "bank_date", "ledger_date", "day_gap", "bank_description",
    "bank_amount", "ledger_description", "ledger_amount", "difference", "invoice_ref",
]
ONLY_BANK_COLUMNS = ["account", "date", "description", "amount"]
ONLY_LEDGER_COLUMNS = ["account", "date", "description", "amount", "invoice_ref"]

_PAIR_COLUMNS = [
    "bank_id", "ledger_id", "account_key", "bank_date", "ledger_date",
    "bank_amount", "ledger_amount", "amount_diff", "abs_diff", "day_gap", "abs_gap",
]


def _empty_pairs() -> pd.DataFrame:
    return pd.DataFrame(columns=_PAIR_COLUMNS)


def candidate_pairs(bank: pd.DataFrame, ledger: pd.DataFrame, date_window: int) -> pd.DataFrame:
    """Todos los pares (banco, libro) de la misma cuenta dentro de la ventana.

    En vez de un producto cartesiano, hace un merge por igualdad para cada
    desfase posible (-window..+window). Cada par aparece exactamente una vez,
    en el desfase que corresponde a su diferencia de fechas.
    """
    if bank.empty or ledger.empty:
        return _empty_pairs()

    left = bank[["_id", "account_key", "date", "amount"]].rename(
        columns={"_id": "bank_id", "date": "bank_date", "amount": "bank_amount"}
    )
    base = ledger[["_id", "account_key", "date", "amount"]].rename(
        columns={"_id": "ledger_id", "date": "ledger_date", "amount": "ledger_amount"}
    )

    frames = []
    for offset in range(-date_window, date_window + 1):
        right = base.copy()
        right["_join_date"] = right["ledger_date"] + pd.Timedelta(days=offset)
        merged = left.merge(
            right,
            left_on=["account_key", "bank_date"],
            right_on=["account_key", "_join_date"],
            how="inner",
        )
        if not merged.empty:
            frames.append(merged.drop(columns=["_join_date"]))

    if not frames:
        return _empty_pairs()

    pairs = pd.concat(frames, ignore_index=True)
    pairs["amount_diff"] = (pairs["bank_amount"] - pairs["ledger_amount"]).round(2)
    pairs["abs_diff"] = pairs["amount_diff"].abs()
    pairs["day_gap"] = (pairs["bank_date"] - pairs["ledger_date"]).dt.days
    pairs["abs_gap"] = pairs["day_gap"].abs()
    return pairs[_PAIR_COLUMNS]


def _assign_greedy(pairs: pd.DataFrame, used_bank: set, used_ledger: set) -> list:
    """Asigna pares de mejor a peor, uno a uno (un movimiento, un asiento).

    `mergesort` mantiene el orden estable para que el resultado sea
    determinista ante empates.
    """
    if pairs.empty:
        return []
    ordered = pairs.sort_values(
        ["abs_diff", "abs_gap", "bank_id", "ledger_id"], kind="mergesort"
    )
    assigned = []
    for pair in ordered.itertuples(index=False):
        if pair.bank_id in used_bank or pair.ledger_id in used_ledger:
            continue
        used_bank.add(pair.bank_id)
        used_ledger.add(pair.ledger_id)
        assigned.append(pair)
    return assigned


def discrepancy_threshold(bank_amount, ledger_amount, abs_limit: float, pct_limit: float):
    """Umbral para considerar dos montos 'el mismo movimiento con error'.

    Se toma el mayor entre el límite absoluto y el porcentaje sobre el
    monto de mayor magnitud (usar el mayor evita que $0.50 y $50 se
    emparejen por un 5% calculado sobre el monto chico).
    """
    base = np.maximum(np.abs(bank_amount), np.abs(ledger_amount))
    return np.maximum(abs_limit, base * pct_limit / 100.0)


def reconcile(
    bank: pd.DataFrame,
    ledger: pd.DataFrame,
    amount_tolerance: float = AMOUNT_TOLERANCE,
    date_window: int = DATE_WINDOW_DAYS,
    discrepancy_abs: float = DISCREPANCY_ABS,
    discrepancy_pct: float = DISCREPANCY_PCT,
) -> dict:
    """Concilia extracto bancario contra libro contable.

    Espera frames ya normalizados por `loader` (columnas `_id`, `account`,
    `account_key`, `date`, `description`, `amount`, `invoice_ref`).

    Devuelve un dict con los DataFrames `matches`, `discrepancies`,
    `only_in_bank` y `only_in_ledger`.
    """
    if date_window < 0:
        raise ValueError("date_window no puede ser negativo.")
    if amount_tolerance < 0:
        raise ValueError("amount_tolerance no puede ser negativo.")

    bank = bank.reset_index(drop=True)
    ledger = ledger.reset_index(drop=True)

    pairs = candidate_pairs(bank, ledger, date_window)
    used_bank: set = set()
    used_ledger: set = set()

    # --- Fase 1: coincidencias exactas ---
    exact_pairs = pairs[pairs["abs_diff"] <= amount_tolerance] if not pairs.empty else pairs
    exact = _assign_greedy(exact_pairs, used_bank, used_ledger)

    # --- Fase 2: discrepancias de monto (mismo signo, dentro del umbral) ---
    if pairs.empty:
        discrepancy_candidates = pairs
    else:
        remaining = pairs[
            ~pairs["bank_id"].isin(used_bank) & ~pairs["ledger_id"].isin(used_ledger)
        ]
        if remaining.empty:
            discrepancy_candidates = remaining
        else:
            same_sign = np.sign(remaining["bank_amount"]) == np.sign(remaining["ledger_amount"])
            within = remaining["abs_diff"] <= discrepancy_threshold(
                remaining["bank_amount"], remaining["ledger_amount"],
                discrepancy_abs, discrepancy_pct,
            )
            discrepancy_candidates = remaining[same_sign & within]
    discrepancies = _assign_greedy(discrepancy_candidates, used_bank, used_ledger)

    bank_by_id = bank.set_index("_id")
    ledger_by_id = ledger.set_index("_id")

    match_rows = [
        {
            "account": bank_by_id.at[p.bank_id, "account"],
            "bank_date": p.bank_date.date(),
            "ledger_date": p.ledger_date.date(),
            "day_gap": int(p.day_gap),
            "bank_description": bank_by_id.at[p.bank_id, "description"],
            "ledger_description": ledger_by_id.at[p.ledger_id, "description"],
            "amount": p.bank_amount,
            "difference": round(p.amount_diff, 2),
            "invoice_ref": ledger_by_id.at[p.ledger_id, "invoice_ref"],
        }
        for p in exact
    ]
    discrepancy_rows = [
        {
            "account": bank_by_id.at[p.bank_id, "account"],
            "bank_date": p.bank_date.date(),
            "ledger_date": p.ledger_date.date(),
            "day_gap": int(p.day_gap),
            "bank_description": bank_by_id.at[p.bank_id, "description"],
            "bank_amount": p.bank_amount,
            "ledger_description": ledger_by_id.at[p.ledger_id, "description"],
            "ledger_amount": p.ledger_amount,
            "difference": round(p.amount_diff, 2),
            "invoice_ref": ledger_by_id.at[p.ledger_id, "invoice_ref"],
        }
        for p in discrepancies
    ]

    only_bank = bank[~bank["_id"].isin(used_bank)].copy()
    only_ledger = ledger[~ledger["_id"].isin(used_ledger)].copy()
    for frame in (only_bank, only_ledger):
        if not frame.empty:
            frame["date"] = frame["date"].dt.date

    logger.info(
        "Reconciliación: %d coincidencias, %d discrepancias, %d solo en banco, "
        "%d solo en libro.",
        len(match_rows), len(discrepancy_rows), len(only_bank), len(only_ledger),
    )

    return {
        "matches": pd.DataFrame(match_rows, columns=MATCH_COLUMNS),
        "discrepancies": pd.DataFrame(discrepancy_rows, columns=DISCREPANCY_COLUMNS),
        "only_in_bank": _select(only_bank, ONLY_BANK_COLUMNS),
        "only_in_ledger": _select(only_ledger, ONLY_LEDGER_COLUMNS),
    }


def _select(frame: pd.DataFrame, columns) -> pd.DataFrame:
    """Proyecta columnas conservando el esquema aunque el frame esté vacío."""
    if frame.empty:
        return pd.DataFrame(columns=columns)
    return frame[columns].reset_index(drop=True)
