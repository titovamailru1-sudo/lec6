"""Локальный журнал, денежные расчёты и обмен версиями записей."""

import csv
import json
import os
import re
import tempfile
from datetime import date, datetime
from pathlib import Path
from time import time_ns
from uuid import UUID, uuid4

CATEGORIES = {
    "expense": ("Продукты", "Транспорт", "Жильё", "Здоровье", "Развлечения", "Другое"),
    "income": ("Зарплата", "Продажи", "Подработка", "Подарки", "Другое"),
}
KIND_LABEL = {"expense": "Расход", "income": "Доход"}
MAX_MINOR = 99_999_999_999


def parse_amount(text):
    text = text.strip().replace(",", ".")
    if not re.fullmatch(r"[0-9]{1,9}(?:\.[0-9]{1,2})?", text):
        raise ValueError("Введите сумму от 0,01 ₽ с точностью до двух знаков.")
    whole, _, fraction = text.partition(".")
    minor = int(whole) * 100 + int(fraction.ljust(2, "0"))
    if not 0 < minor <= MAX_MINOR:
        raise ValueError("Сумма должна быть больше нуля.")
    return minor


def money(minor):
    whole, fraction = divmod(abs(minor), 100)
    sign = "−" if minor < 0 else ""
    grouped = f"{whole:,}".replace(",", " ")
    return f"{sign}{grouped},{fraction:02d} ₽"


def validate(record):
    if not isinstance(record, dict):
        raise ValueError("Некорректная запись.")
    try:
        record_id = record["id"]
        revision = record["revision"]
        change = record["change"]
        deleted = record["deleted"]
        if str(UUID(record_id)) != record_id or str(UUID(change)) != change:
            raise ValueError
        if type(revision) is not int or not 0 < revision < 2**63 or type(deleted) is not bool:
            raise ValueError
        result = {"id": record_id, "revision": revision, "change": change, "deleted": deleted}
        if not deleted:
            kind, category = record["kind"], record["category"]
            note, minor, day = record["note"], record["minor"], record["date"]
            if category not in CATEGORIES[kind] or not isinstance(note, str) or not 1 <= len(note.strip()) <= 120:
                raise ValueError
            if type(minor) is not int or not 0 < minor <= MAX_MINOR or date.fromisoformat(day).isoformat() != day:
                raise ValueError
            result.update(kind=kind, category=category, note=note.strip(), minor=minor, date=day)
        return result
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Некорректная запись.") from exc


def merge(existing, changes):
    combined = dict(existing)
    for new in changes:
        current = combined.get(new["id"])
        if current is None or (new["revision"], new["change"]) > (current["revision"], current["change"]):
            combined[new["id"]] = new
    return combined


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as temp:
        temp_path = Path(temp.name)
        try:
            json.dump(data, temp, ensure_ascii=False, indent=2)
            temp.flush()
            os.fsync(temp.fileno())
            temp.close()
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)


class Ledger:
    def __init__(self, folder):
        self.path = Path(folder) / "ledger.json"
        state = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.records = merge({}, map(validate, state.get("records", [])))
        self.last_sync = state.get("last_sync", "")
        self.config = state.get("config", {})

    def _commit(self, records, last_sync=None, config=None):
        last_sync = self.last_sync if last_sync is None else last_sync
        config = self.config if config is None else config
        write_json(self.path, {"records": list(records.values()), "last_sync": last_sync, "config": config})
        self.records, self.last_sync, self.config = records, last_sync, config

    def _change(self, record_id, **fields):
        previous = self.records.get(record_id)
        now = max(time_ns(), previous["revision"] + 1 if previous else 1)
        record = validate({"id": record_id, "revision": now, "change": str(uuid4()), **fields})
        self._commit(merge(self.records, [record]))

    def save(self, kind, category, note, amount, day, record_id=None):
        note = note.strip()
        if category not in CATEGORIES[kind]:
            raise ValueError("Выберите категорию операции.")
        if not 1 <= len(note) <= 120:
            raise ValueError("Введите описание длиной до 120 символов.")
        minor = parse_amount(amount)
        day = day.strip()
        try:
            if date.fromisoformat(day).isoformat() != day:
                raise ValueError
        except ValueError as exc:
            raise ValueError("Введите дату в формате ГГГГ-ММ-ДД.") from exc
        self._change(record_id or str(uuid4()), kind=kind, category=category, note=note,
                     minor=minor, date=day, deleted=False)

    def delete(self, record_id):
        self._change(record_id, deleted=True)

    def apply_sync(self, records):
        last_sync = datetime.now().astimezone().strftime("%d.%m.%Y в %H:%M")
        self._commit(merge(self.records, map(validate, records)), last_sync=last_sync)

    def set_config(self, url, token):
        last_sync = self.last_sync if self.config.get("url") == url else ""
        self._commit(self.records, last_sync=last_sync, config={"url": url, "token": token})

    def operations(self):
        return sorted((rec for rec in self.records.values() if not rec["deleted"]),
                      key=lambda rec: (rec["date"], rec["revision"], rec["id"]), reverse=True)

    def export(self, path):
        def safe_cell(value):
            return "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else value

        with path.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.writer(output, delimiter=";")
            writer.writerow(["Дата", "Тип", "Категория", "Описание", "Сумма, руб."])
            for rec in self.operations():
                whole, fraction = divmod(rec["minor"], 100)
                writer.writerow([rec["date"], KIND_LABEL[rec["kind"]], rec["category"],
                                 safe_cell(rec["note"]), f"{whole},{fraction:02d}"])
