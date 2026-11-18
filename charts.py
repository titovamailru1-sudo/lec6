"""Фигуры Matplotlib для экрана аналитики."""

from collections import defaultdict
from datetime import date

from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, MaxNLocator

GREEN = "#137760"
CORAL = "#bd5645"
INK = "#172f28"
MUTED = "#506860"


def style(axis, horizontal=False):
    axis.set_axisbelow(True)
    axis.grid(axis="x" if horizontal else "y", color="#e7eeea", linewidth=0.8)
    axis.spines[:].set_visible(False)
    axis.tick_params(axis="both", which="both", length=0, pad=5, labelsize=9, colors=MUTED)
    value_axis = axis.xaxis if horizontal else axis.yaxis
    value_axis.set_major_locator(MaxNLocator(4))
    value_axis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))


def by_category(figure, operations):
    for index, (kind, title, color) in enumerate(
        (("income", "Доходы по категориям", GREEN), ("expense", "Расходы по категориям", CORAL)), start=1
    ):
        axis = figure.add_subplot(2, 1, index)
        totals = defaultdict(int)
        for operation in operations:
            if operation["kind"] == kind:
                totals[operation["category"]] += operation["minor"]
        axis.set_title(title, loc="left", color=INK, fontsize=11, fontweight="semibold", pad=8)
        if totals:
            items = sorted(totals.items(), key=lambda pair: (pair[1], pair[0]))
            axis.barh([name for name, _ in items], [minor / 100 for _, minor in items], color=color, height=0.6)
            axis.set_xlabel("₽", loc="right", color=MUTED, fontsize=9)
            style(axis, horizontal=True)
        else:
            axis.set_axis_off()
            axis.text(0, 0.70, "Нет операций", transform=axis.transAxes, color=MUTED, fontsize=10)


def by_month(figure, operations):
    totals = defaultdict(lambda: defaultdict(int))
    for operation in operations:
        totals[operation["date"][:7]][operation["kind"]] += operation["minor"]
    latest = max(totals, default=date.today().isoformat()[:7])
    year, month = map(int, latest.split("-"))
    current = year * 12 + month - 1
    months = range(max(12, current - 5), current + 1)
    keys = [f"{number // 12:04d}-{number % 12 + 1:02d}" for number in months]
    names = [f"{key[5:]}.{key[:4]}" for key in keys]
    axis = figure.add_subplot()
    positions = range(len(keys))
    axis.bar([i - 0.19 for i in positions], [totals[key]["income"] / 100 for key in keys],
             width=0.36, color=GREEN, label="Доходы")
    axis.bar([i + 0.19 for i in positions], [totals[key]["expense"] / 100 for key in keys],
             width=0.36, color=CORAL, label="Расходы")
    axis.set_xticks(positions, names, rotation=35, ha="right")
    axis.set_ylabel("₽", color=MUTED, rotation=0, labelpad=12)
    axis.legend(loc="upper left", frameon=False, ncols=2, labelcolor=INK, fontsize=9)
    axis.margins(y=0.25)
    style(axis)


def make_figure(mode, operations):
    figure = Figure(figsize=(4, 4), facecolor="white", layout="constrained")
    if mode == "category":
        by_category(figure, operations)
    else:
        by_month(figure, operations)
    return figure
