"""history_page.py - страница /history: просмотр CSV-истории (график, итоги, события, направление вращения)."""
import csv
from datetime import datetime
from pathlib import Path

MAX_POINTS = 2500             # максимум точек на графике (остальное усредняется)
MAX_EVENTS = 200
GAP_SEC = 5                   # разрыв между строками больше N секунд не считается временем работы
FMT = "%Y-%m-%d %H:%M:%S"
MOVING_HZ = 0.05              # выше этой частоты считаем, что вал вращается

TOOLTIP_JS = """params => {
  const p = params[0];
  let s = '<b>' + p.axisValueLabel + '</b>';
  params.forEach(x => {
    const v = (x.value === null || x.value === undefined) ? '-' : x.value;
    s += '<br/>' + x.marker + x.seriesName + ': <b>' + v + '</b>';
  });
  const d = p.data;
  if (d && d.dir) { s += '<br/><span style="color:' + d.col + '">&#9679;</span> <b>' + d.dir + '</b>'; }
  return s;
}"""

def list_dates(history_dir):
    dates = []
    for p in Path(history_dir).glob("drive_*.csv"):
        d = p.stem.replace("drive_", "")
        try:
            datetime.strptime(d, "%Y-%m-%d"); dates.append(d)
        except ValueError:
            pass
    return sorted(dates, reverse=True)

def _fl(s):
    try:
        return float(str(s).replace(",", ".").strip())
    except ValueError:
        return None

def read_drive(path):
    """Список словарей: t(datetime), mode, sp, f, i, u, alarm."""
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rd = csv.reader(fh, delimiter=";")
        header = next(rd, None)
        if not header:
            return rows
        ix = {name: k for k, name in enumerate(header)}
        def col(r, name):
            k = ix.get(name)
            return r[k] if k is not None and k < len(r) else ""
        for r in rd:
            try:
                t = datetime.strptime(col(r, "время"), FMT)
            except ValueError:
                continue
            rows.append({"t": t, "mode": col(r, "режим"), "sp": _fl(col(r, "уставка_Гц")),
                         "f": _fl(col(r, "частота_Гц")), "i": _fl(col(r, "ток_А")),
                         "u": _fl(col(r, "напряжение_В")),
                         "alarm": int(_fl(col(r, "авария")) or 0)})
    return rows

def read_events(path):
    out = []
    p = Path(path)
    if not p.exists():
        return out
    with open(p, encoding="utf-8-sig", newline="") as fh:
        rd = csv.reader(fh, delimiter=";")
        next(rd, None)
        for r in rd:
            if len(r) >= 2:
                out.append((r[0][11:19] if len(r[0]) >= 19 else r[0], r[1]))
    return out

def direction(mode, f):
    """(текст, ключ, цвет). Режим - это команда из регистра [0], а не измеренное направление."""
    moving = (f or 0) > MOVING_HZ
    if mode == "РЕВЕРС":
        return "Назад (реверс)", "rev", "#ffb84d"
    if mode == "ВПЕРЁД":
        return "Вперёд", "fwd", "#3ddc84"
    if mode == "СТОП":
        return ("Торможение (команда СТОП)", "brk", "#ff5d6c") if moving else ("Остановлен", "stop", "#7b869e")
    return "Нет данных", "unk", "#7b869e"

def _pick_mode(chunk):
    modes = {c["mode"] for c in chunk}
    for m in ("РЕВЕРС", "ВПЕРЁД", "СТОП"):
        if m in modes:
            return m
    return chunk[0]["mode"]

def downsample(rows, max_points=MAX_POINTS):
    n = len(rows)
    if n <= max_points:
        return rows
    step = n // max_points + 1
    out = []
    for k in range(0, n, step):
        chunk = rows[k:k + step]
        def avg(key):
            v = [c[key] for c in chunk if c[key] is not None]
            return sum(v) / len(v) if v else None
        out.append({"t": chunk[0]["t"], "mode": _pick_mode(chunk), "sp": avg("sp"), "f": avg("f"),
                    "i": avg("i"), "u": avg("u"), "alarm": max(c["alarm"] for c in chunk)})
    return out

def reverse_segments(labels, rows):
    """Пары (начало, конец) подписей оси для участков, где активен реверс."""
    segs, start = [], None
    for lab, r in zip(labels, rows):
        if r["mode"] == "РЕВЕРС":
            if start is None:
                start = lab
            last = lab
        elif start is not None:
            segs.append((start, last)); start = None
    if start is not None:
        segs.append((start, last))
    return segs

def stats(rows):
    fs = [r["f"] for r in rows if r["f"] is not None]
    is_ = [r["i"] for r in rows if r["i"] is not None]
    run, rev, alarms, prev_alarm = 0.0, 0.0, 0, 0
    for a, b in zip(rows, rows[1:]):
        dt = (b["t"] - a["t"]).total_seconds()
        if (a["f"] or 0) > MOVING_HZ and (b["f"] or 0) > MOVING_HZ and 0 < dt <= GAP_SEC:
            run += dt
            if a["mode"] == "РЕВЕРС":
                rev += dt
    for r in rows:
        if r["alarm"] != 0 and prev_alarm == 0:
            alarms += 1
        prev_alarm = r["alarm"]
    return {"max_f": max(fs) if fs else 0.0, "max_i": max(is_) if is_ else 0.0,
            "run_sec": int(run), "rev_sec": int(rev), "alarms": alarms, "n": len(rows)}

def fmt_hms(sec):
    return f"{sec // 3600:d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"

def register(css, history_dir, link_html, C):
    from nicegui import ui
    history_dir = Path(history_dir)

    @ui.page("/history")
    def history():
        ui.add_css(css)
        with ui.element("div").classes("card").style(
                "width:100%;display:flex;justify-content:space-between;align-items:center"):
            with ui.row().style("gap:18px;align-items:center"):
                ui.button("←  НАЗАД", on_click=lambda: ui.navigate.to("/")) \
                    .style(f"background:{C['BORDER']}!important;color:{C['TXT']};font-weight:700;border-radius:10px")
                ui.label("ИСТОРИЯ").style("font-size:20px;font-weight:700")
            opc_lbl = ui.html("").style("font-size:14px")

        dates = list_dates(history_dir)
        with ui.element("div").classes("card").style("width:100%;display:flex;gap:16px;align-items:center;flex-wrap:wrap"):
            ui.label("Дата").classes("cap")
            sel = ui.select(dates, value=dates[0] if dates else None).props("outlined dark dense") \
                .style("min-width:180px")
            ui.button("ОБНОВИТЬ", on_click=lambda: reload_dates()) \
                .style(f"background:{C['BORDER']}!important;color:{C['TXT']};font-weight:700;border-radius:10px")
            ui.button("СКАЧАТЬ CSV", on_click=lambda: download()) \
                .style(f"background:{C['BORDER']}!important;color:{C['TXT']};font-weight:700;border-radius:10px")
            info = ui.label("").classes("cap")

        stat_boxes = {}
        with ui.element("div").style("display:grid;grid-template-columns:repeat(5,1fr);gap:14px;width:100%"):
            for key, title, color in (("run", "ВРЕМЯ РАБОТЫ", C["GREEN"]), ("rev", "ИЗ НИХ В РЕВЕРСЕ", C["AMBER"]),
                                      ("f", "МАКС. ЧАСТОТА", C["BLUE"]), ("i", "МАКС. ТОК", C["PURPLE"]),
                                      ("a", "АВАРИЙ", C["RED"])):
                with ui.element("div").classes("card"):
                    ui.label(title).classes("cap")
                    stat_boxes[key] = (ui.html("").style("margin-top:6px"), color)

        with ui.element("div").classes("card").style("width:100%"):
            ui.label("ЧАСТОТА И ТОК (колесо мыши - масштаб; жёлтая заливка - реверс; "
                     "направление показывается при наведении)").classes("cap")
            chart = ui.echart({
                "animation": False,
                "tooltip": {"trigger": "axis", ":formatter": TOOLTIP_JS},
                "legend": {"textStyle": {"color": C["TXT"]}, "left": 0, "top": 0},
                "grid": {"left": 50, "right": 50, "top": 40, "bottom": 70},
                "dataZoom": [{"type": "inside"}, {"type": "slider", "height": 22, "bottom": 10}],
                "xAxis": {"type": "category", "data": [], "axisLabel": {"color": C["DIM"]}},
                "yAxis": [
                    {"type": "value", "min": 0, "name": "Гц", "nameTextStyle": {"color": C["DIM"]},
                     "axisLabel": {"color": C["DIM"]}, "splitLine": {"lineStyle": {"color": C["BORDER"]}}},
                    {"type": "value", "min": 0, "name": "А", "nameTextStyle": {"color": C["DIM"]},
                     "axisLabel": {"color": C["DIM"]}, "splitLine": {"show": False}}],
                "series": [
                    {"name": "Частота, Гц", "type": "line", "showSymbol": False, "data": [],
                     "lineStyle": {"color": C["BLUE"], "width": 2}, "itemStyle": {"color": C["BLUE"]},
                     "markArea": {"silent": True, "data": []}},
                    {"name": "Уставка, Гц", "type": "line", "showSymbol": False, "data": [],
                     "lineStyle": {"color": C["AMBER"], "width": 1, "type": "dashed"},
                     "itemStyle": {"color": C["AMBER"]}},
                    {"name": "Ток, А", "type": "line", "yAxisIndex": 1, "showSymbol": False, "data": [],
                     "lineStyle": {"color": C["PURPLE"], "width": 2}, "itemStyle": {"color": C["PURPLE"]}},
                ]}).style("height:400px;width:100%")

        with ui.element("div").classes("card").style("width:100%"):
            ui.label("СОБЫТИЯ ЗА ДЕНЬ (новые сверху)").classes("cap")
            table = ui.table(columns=[
                {"name": "t", "label": "Время", "field": "t", "align": "left"},
                {"name": "m", "label": "Событие", "field": "m", "align": "left"}],
                rows=[], row_key="k", pagination=10).props("dark flat dense").style("width:100%;background:transparent")

        def set_stat(key, text, unit=""):
            box, color = stat_boxes[key]
            u = f'<span class="unit">{unit}</span>' if unit else ""
            box.set_content(f'<span class="big" style="color:{color}">{text}</span>{u}')

        def load():
            d = sel.value
            if not d:
                info.set_text("Файлов истории пока нет: запусти двигатель и подожди минуту.")
                for k in stat_boxes: set_stat(k, "—")
                return
            try:
                rows = read_drive(history_dir / f"drive_{d}.csv")
            except Exception as e:
                info.set_text(f"Не удалось прочитать файл: {e!r}"); return
            st = stats(rows)
            small = downsample(rows)
            info.set_text(f"{st['n']} строк" + (f", на графике {len(small)} точек" if len(small) < st["n"] else ""))
            set_stat("run", fmt_hms(st["run_sec"]))
            set_stat("rev", fmt_hms(st["rev_sec"]))
            set_stat("f", f"{st['max_f']:.1f}", "Гц")
            set_stat("i", f"{st['max_i']:.2f}", "А")
            set_stat("a", str(st["alarms"]))
            labels = [r["t"].strftime("%H:%M:%S") for r in small]
            fdata = []
            for r in small:
                text, _key, col = direction(r["mode"], r["f"])
                fdata.append({"value": None if r["f"] is None else round(r["f"], 2), "dir": text, "col": col})
            chart.options["xAxis"]["data"] = labels
            chart.options["series"][0]["data"] = fdata
            chart.options["series"][0]["markArea"]["data"] = [
                [{"xAxis": a, "itemStyle": {"color": "rgba(255,184,77,0.18)"}}, {"xAxis": b}]
                for a, b in reverse_segments(labels, small)]
            chart.options["series"][1]["data"] = [None if r["sp"] is None else round(r["sp"], 1) for r in small]
            chart.options["series"][2]["data"] = [None if r["i"] is None else round(r["i"], 2) for r in small]
            chart.update()
            ev = read_events(history_dir / f"events_{d}.csv")
            table.rows = [{"k": k, "t": t, "m": m} for k, (t, m) in enumerate(reversed(ev[-MAX_EVENTS:]))]
            table.update()

        def reload_dates():
            ds = list_dates(history_dir)
            sel.set_options(ds, value=sel.value if sel.value in ds else (ds[0] if ds else None))
            load()

        def download():
            if not sel.value:
                ui.notify("Нет файла", type="warning"); return
            p = history_dir / f"drive_{sel.value}.csv"
            try:
                ui.download.file(p)
            except AttributeError:
                ui.download(p.read_bytes(), p.name)

        sel.on_value_change(lambda e: load())
        load()
        opc_lbl.set_content(link_html())
        ui.timer(2.0, lambda: opc_lbl.set_content(link_html()))
