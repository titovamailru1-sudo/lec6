"""Кроссплатформенный трекер финансов на Kivy."""

import argparse
import os
from datetime import date
from pathlib import Path
from threading import Thread

os.environ.setdefault("KIVY_NO_ARGS", "1")

from kivy.config import Config
from kivy.utils import platform

Config.set("input", "mouse", "mouse,disable_multitouch")
if platform not in ("android", "ios"):
    Config.set("graphics", "width", "448")
    Config.set("graphics", "height", "760")
    Config.set("graphics", "minimum_width", "360")
    Config.set("graphics", "minimum_height", "610")

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.lang import Builder
from kivy.properties import BooleanProperty, ObjectProperty, StringProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.popup import Popup
from kivy_garden.matplotlib.backend_kivyagg import FigureCanvasKivyAgg

from charts import make_figure
from ledger import CATEGORIES, KIND_LABEL, Ledger, money
from sync_api import endpoint, exchange

PROJECT = Path(__file__).resolve().parent


class TransactionRow(BoxLayout):
    record_id = StringProperty()
    note = StringProperty()
    metadata = StringProperty()
    amount = StringProperty()
    kind = StringProperty()


class Editor(Popup):
    kind = StringProperty("expense")
    category = StringProperty("Категория")
    record_id = StringProperty()
    error = StringProperty()

    def on_open(self):
        self.ids.day.text = date.today().isoformat()
        if self.record_id:
            record = App.get_running_app().ledger.records[self.record_id]
            self.kind = record["kind"]
            self.category = record["category"]
            whole, fraction = divmod(record["minor"], 100)
            self.ids.amount.text = f"{whole},{fraction:02d}"
            self.ids.note.text = record["note"]
            self.ids.day.text = record["date"]

    def choose_kind(self, kind):
        if self.kind != kind:
            self.kind = kind
            self.category = "Категория"

    def save(self):
        ledger = App.get_running_app().ledger
        try:
            ledger.save(self.kind, self.category, self.ids.note.text, self.ids.amount.text,
                        self.ids.day.text, self.record_id or None)
        except ValueError as exc:
            self.error = str(exc)
            return
        except OSError:
            self.error = "Не удалось сохранить операцию на устройстве."
            return
        App.get_running_app().refresh()
        self.dismiss()


class Notice(Popup):
    message = StringProperty()
    action_label = StringProperty("Закрыть")
    action = ObjectProperty(None, allownone=True)

    def confirm(self):
        self.dismiss()
        if self.action:
            self.action()


class FinanceView(BoxLayout):
    balance = StringProperty("0,00 ₽")
    income = StringProperty("0,00 ₽")
    expense = StringProperty("0,00 ₽")
    section = StringProperty("operations")
    chart_mode = StringProperty("category")
    filter_mode = StringProperty("all")
    empty = BooleanProperty(True)
    has_operations = BooleanProperty(False)
    negative = BooleanProperty(False)
    sync_busy = BooleanProperty(False)
    sync_status = StringProperty("Синхронизация ещё не выполнялась")
    url = StringProperty()
    token = StringProperty()


class BalanceApp(App):
    title = "Баланс"
    categories = CATEGORIES

    def __init__(self, data_dir=None, **kwargs):
        super().__init__(**kwargs)
        self.data_dir = data_dir
        self.ledger = None

    def build(self):
        Window.clearcolor = (0.945, 0.965, 0.95, 1)
        self.ledger = Ledger(self.data_dir or self.user_data_dir)
        Builder.load_file(str(PROJECT / "finance.kv"))
        view = FinanceView()
        view.url = self.ledger.config.get("url", "")
        view.token = self.ledger.config.get("token", "")
        if self.ledger.last_sync:
            view.sync_status = f"Последнее обновление: {self.ledger.last_sync}"
        Clock.schedule_once(lambda dt: self.refresh())
        return view

    def on_pause(self):
        return True

    def refresh(self):
        view = self.root
        operations = self.ledger.operations()
        totals = {"income": 0, "expense": 0}
        rows = []
        for rec in operations:
            kind = rec["kind"]
            totals[kind] += rec["minor"]
            if view.filter_mode not in ("all", kind):
                continue
            rows.append({
                "record_id": rec["id"], "kind": kind, "note": rec["note"],
                "metadata": f'{rec["category"]}  ·  {date.fromisoformat(rec["date"]):%d.%m.%Y}',
                "amount": ("+" if kind == "income" else "−") + money(rec["minor"]),
            })
        balance = totals["income"] - totals["expense"]
        view.balance, view.income, view.expense = money(balance), money(totals["income"]), money(totals["expense"])
        view.negative = balance < 0
        view.has_operations = bool(operations)
        view.empty = not rows
        view.ids.operations_list.data = rows
        if view.section == "analytics":
            self.refresh_chart()

    def open_section(self, section):
        self.root.ids.pages.current = section
        self.root.section = section
        if section == "analytics":
            Clock.schedule_once(lambda dt: self.refresh_chart())

    def filter_operations(self, kind):
        self.root.filter_mode = kind
        self.refresh()

    def open_editor(self, record_id=""):
        if record_id and self.ledger.records.get(record_id, {}).get("deleted", True):
            return
        Editor(title="Изменить операцию" if record_id else "Новая операция", record_id=record_id).open()

    def delete_operation(self, editor):
        def remove():
            try:
                self.ledger.delete(editor.record_id)
            except OSError:
                editor.error = "Не удалось удалить операцию."
                return
            editor.dismiss()
            self.refresh()

        Notice(title="Удаление операции", message="Удалить эту операцию из журнала?",
               action_label="Удалить", action=remove).open()

    def show_chart(self, mode):
        self.root.chart_mode = mode
        self.refresh_chart()

    def refresh_chart(self):
        container = self.root.ids.chart
        container.clear_widgets()
        operations = self.ledger.operations()
        if operations:
            canvas = FigureCanvasKivyAgg(make_figure(self.root.chart_mode, operations))
            container.add_widget(canvas)

    def export_csv(self):
        default_dir = Path(self.data_dir or self.user_data_dir)
        filename = default_dir / f"operations-{date.today().isoformat()}.csv"
        try:
            default_dir.mkdir(parents=True, exist_ok=True)
            self.ledger.export(filename)
        except OSError:
            Notice(title="Ошибка экспорта", message="Не удалось сохранить CSV.").open()
            return
        Notice(title="CSV сохранён", message=str(filename)).open()

    def synchronize(self, url, token):
        view = self.root
        if view.sync_busy:
            return
        try:
            url = endpoint(url)
            token = token.strip()
            if len(token) < 16 or any(not 33 <= ord(char) <= 126 for char in token):
                raise ValueError("Введите токен доступа к серверу.")
            self.ledger.set_config(url, token)
        except ValueError as exc:
            view.sync_status = str(exc)
            return
        except OSError:
            view.sync_status = "Не удалось сохранить настройки."
            return
        view.sync_busy = True
        view.sync_status = "Обмен операциями…"
        snapshot = list(self.ledger.records.values())

        def worker():
            try:
                records = exchange(url, token, snapshot)
            except (ValueError, OSError) as exc:
                message = str(exc)
                Clock.schedule_once(lambda dt: self.sync_finished(error=message))
            else:
                Clock.schedule_once(lambda dt: self.sync_finished(records))

        Thread(target=worker, daemon=True).start()

    def sync_finished(self, records=None, error=None):
        view = self.root
        view.sync_busy = False
        if error:
            view.sync_status = error
            return
        try:
            self.ledger.apply_sync(records)
        except OSError:
            view.sync_status = "Не удалось записать операции на устройство."
            return
        view.sync_status = f"Журнал синхронизирован {self.ledger.last_sync}"
        self.refresh()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Баланс — учёт доходов и расходов.")
    parser.add_argument("--data-dir", type=Path, default=os.environ.get("FINANCE_DATA_DIR"),
                        help="Отдельная папка данных, например для второго профиля.")
    BalanceApp(data_dir=parser.parse_args().data_dir).run()
