"""Список компаний для анализа.

ИНН берутся с Мосбиржи (ISS): эмитенты облигаций, которые были допущены к торгам с MIN_YEAR года и позже
(включая уже выбывшие выпуски; см. test.py). Отчётность по этим ИНН потом читается из RFSD (main.py -> rfsd.py).
Основной идентификатор компании - 'inn'; в 'ticker' кладётся один из SECID выпусков эмитента, в 'name' -
название эмитента с биржи.
Прежний сбор ИНН по акциям (TQBR) сохранён в collect_companies_data_moex().
"""
import time

import pandas as pd
import requests

from config import MIN_YEAR

OUTPUT_FILENAME = "moex_bond_issuers.csv"
ISS = "https://iss.moex.com/iss"


def _fetch(session, path, block, params=None, pause=0.3):
    """Постраничная выгрузка блока ISS -> DataFrame (имена колонок в нижнем регистре)."""
    rows, start = [], 0
    while True:
        p = {"start": start, "iss.meta": "off", **(params or {})}
        for attempt in range(3):
            try:
                r = session.get(f"{ISS}/{path}.json", params=p, timeout=30)
                r.raise_for_status()
                break
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(3)
        blk = r.json()[block]
        if not blk["data"]:
            break
        rows += [dict(zip(blk["columns"], x)) for x in blk["data"]]
        start += len(blk["data"])
        time.sleep(pause)
    df = pd.DataFrame(rows)
    df.columns = [c.lower() for c in df.columns]
    return df


def collect_companies_data(years=None, limit=None, save_to=OUTPUT_FILENAME):
    """-> [{'ticker': 'RU000A0JX0J2', 'inn': '7707083893', 'name': 'Эмитент'}, ...]
    по эмитентам облигаций Мосбиржи, допущенным к торгам с MIN_YEAR года и позже (одна запись на ИНН).

    years  - не используется (оставлен для совместимости); отсечка берётся из MIN_YEAR;
    limit  - взять только первые N ИНН (для отладки);
    save_to - CSV со списком эмитентов (None = не сохранять).
    """
    print("1. Сбор эмитентов облигаций с Мосбиржи...")
    with requests.Session() as s:
        ref = _fetch(s, "securities", "securities", {"engine": "stock", "market": "bonds"})
        lst = _fetch(s, "history/engines/stock/markets/bonds/listing", "securities")
    if ref.empty or lst.empty:
        print("Ошибка: MOEX ISS не вернул данные.")
        return []

    # даты допуска/последних торгов (имена колонок на Мосбирже содержат from/till)
    c_from = next((c for c in lst.columns if "from" in c), None)
    c_till = next((c for c in lst.columns if "till" in c), None)
    if not c_till:
        print(f"Ошибка: в листинге нет колонки с датой окончания торгов: {lst.columns.tolist()}")
        return []
    lst[c_till] = pd.to_datetime(lst[c_till], errors="coerce")
    agg_spec = {"last_trade": (c_till, "max")}
    if c_from:
        lst[c_from] = pd.to_datetime(lst[c_from], errors="coerce")
        agg_spec["first_trade"] = (c_from, "min")
    agg = lst.groupby("secid").agg(**agg_spec).reset_index()
    # торговались в MIN_YEAR или позже (включая уже выбывшие бумаги)
    agg = agg[agg["last_trade"] >= pd.Timestamp(MIN_YEAR, 1, 1)]

    full = agg.merge(ref, on="secid", how="left")
    if "emitent_inn" not in full.columns:
        print(f"Ошибка: в справочнике бумаг нет emitent_inn: {ref.columns.tolist()}")
        return []
    full["emitent_inn"] = full["emitent_inn"].astype("string").str.strip()
    no_inn = full["emitent_inn"].isna() | ~full["emitent_inn"].str.fullmatch(r"\d{10}|\d{12}", na=False)
    print(f"Выпусков с {MIN_YEAR} г.: {len(full)}, без корректного ИНН эмитента: {int(no_inn.sum())}")
    full = full[~no_inn]

    name_col = "emitent_title" if "emitent_title" in full.columns else "shortname"
    df = (full.sort_values("last_trade", ascending=False)
              .groupby("emitent_inn", as_index=False)
              .agg(ticker=("secid", "first"), name=(name_col, "first"),
                   n_issues=("secid", "nunique"),
                   first_trade=("first_trade", "min") if "first_trade" in full.columns else ("last_trade", "min"),
                   last_trade=("last_trade", "max"))
              .rename(columns={"emitent_inn": "inn"})
              .sort_values("inn", ignore_index=True))
    print(f"Найдено {len(df)} уникальных ИНН эмитентов облигаций.")

    if save_to:
        try:
            df.to_csv(save_to, index=False, sep=";", encoding="utf-8-sig")
            print(f"Список эмитентов сохранён в файл: {save_to}")
        except PermissionError:
            print(f"[ОШИБКА!] Закройте файл {save_to} и запустите программу заново.")

    if limit:
        df = df.head(int(limit))
    return [{"ticker": r.ticker, "inn": r.inn, "name": r.name or ""} for r in df.itertuples()]