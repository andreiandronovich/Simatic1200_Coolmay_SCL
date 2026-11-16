"""history_page.py - страница /history: просмотр CSV-истории (график, итоги, аварии, события, направление вращения)."""
import csv
from bisect import bisect_left, bisect_right
from datetime import datetime
from pathlib import Path

from drive_alarms import alarm_symbol, alarm_text

MAX_POINTS = 2500             # максимум точек на графике (остальное усредняется)
MAX_EVENTS = 5000             # максимум событий в таблице (старые не обрезаем: аварию надо находить)
GAP_SEC = 5                   # разрыв между строками больше N секунд не считается временем работы
FMT = "%Y-%m-%d %H:%M:%S"
MOVING_HZ = 0.05              # выше этой частоты считаем, что вал вращается
ZOOM_POINTS = 30              # при клике по аварии/событию показываем +-N точек вокруг него
ALARM_MARKERS = ("Состояние привода", "Ошибки и предупреждения", "Пуск запрещён", "Отказ")

TOOLTIP_JS = """params => {
  const p = params[0];
  let s = '<b>' + p.axisValueLabel + '</b>';
  params.forEach(x => {
    const v = (x.value === null || x.value === undefined) ? '-' : x.value;
    s += '<br/>' + x.marker + x.seriesName + ': <b>' + v + '</b>';
  });
  const d = p.data;
  if (d && d.dir) { s += '<br/><span style="color:' + d.col + '">&#9679;</span> <b>' + d.dir + '</b>'; }
  if (d && d.alm) { s += '<br/><span style="color:#ff5d6c">&#9679;</span> <b>Авария: ' + d.alm + '</b>'; }
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

def alarm_events(rows):
    """Аварии по полным данным: смена кода тоже начинает новую запись.
    [{code, t_start, t_end, open}]; open=True - авария не снята до конца записи."""
    out, cur = [], None
    for r in rows:
        a = r["alarm"]
        if cur is not None and a != cur["code"]:
            cur["t_end"] = r["t"]; out.append(cur); cur = None
        if a != 0 and cur is None:
            cur = {"code": a, "t_start": r["t"], "t_end": None, "open": False}
    if cur is not None:
        cur["t_end"] = rows[-1]["t"]; cur["open"] = True; out.append(cur)
    return out

def time_index(times, t):
    """Индекс точки графика, в которую попадает момент t (последняя точка не позже t)."""
    if not times:
        return 0
    return max(0, min(len(times) - 1, bisect_right(times, t) - 1))

def is_alarm_event(msg):
    return msg.startswith(ALARM_MARKERS)

def filter_events(ev, newest_first=True, only_alarms=False, text=""):
    """ev - список (время, сообщение) в порядке записи. Возвращает отфильтрованный и отсортированный список."""
    q = (text or "").strip().lower()
    out = [e for e in ev if (not only_alarms or is_alarm_event(e[1])) and (not q or q in e[1].lower() or q in e[0])]
    out = out[-MAX_EVENTS:]
    return list(reversed(out)) if newest_first else out

def stats(rows):
    fs = [r["f"] for r in rows if r["f"] is not None]
    is_ = [r["i"] for r in rows if r["i"] is not None]
    run, rev = 0.0, 0.0
    for a, b in zip(rows, rows[1:]):
        dt = (b["t"] - a["t"]).total_seconds()
        if (a["f"] or 0) > MOVING_HZ and (b["f"] or 0) > MOVING_HZ and 0 < dt <= GAP_SEC:
            run += dt
            if a["mode"] == "РЕВЕРС":
                rev += dt
    return {"max_f": max(fs) if fs else 0.0, "max_i": max(is_) if is_ else 0.0,
            "run_sec": int(run), "rev_sec": int(rev), "alarms": len(alarm_events(rows)), "n": len(rows)}

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
            with ui.row().style("width:100%;justify-content:space-between;align-items:center"):
                ui.label("ЧАСТОТА И ТОК (колесо мыши - масштаб; жёлтая заливка - реверс; красная - авария; "
                         "красная линия - момент аварии)").classes("cap")
                ui.button("ПОКАЗАТЬ ВСЁ", on_click=lambda: zoom_all()).props("flat dense") \
                    .style(f"color:{C['TXT']};font-weight:700")
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
                     "markArea": {"silent": True, "data": []},
                     "markLine": {"silent": True, "symbol": ["none", "none"], "data": [],
                                  "label": {"show": True, "formatter": "{b}", "color": C["RED"],
                                            "fontWeight": "bold", "position": "end"}}},
                    {"name": "Уставка, Гц", "type": "line", "showSymbol": False, "data": [],
                     "lineStyle": {"color": C["AMBER"], "width": 1, "type": "dashed"},
                     "itemStyle": {"color": C["AMBER"]}},
                    {"name": "Ток, А", "type": "line", "yAxisIndex": 1, "showSymbol": False, "data": [],
                     "lineStyle": {"color": C["PURPLE"], "width": 2}, "itemStyle": {"color": C["PURPLE"]}},
                ]}).style("height:400px;width:100%")

        with ui.element("div").classes("card").style("width:100%"):
            ui.label("АВАРИИ ЗА ДЕНЬ (клик по строке - показать на графике; клик по заголовку - сортировка)").classes("cap")
            alarm_table = ui.table(columns=[
                {"name": "t", "label": "Время", "field": "t", "align": "left", "sortable": True},
                {"name": "d", "label": "Длительность", "field": "d", "align": "left", "sortable": True},
                {"name": "c", "label": "Код", "field": "c", "align": "left", "sortable": True},
                {"name": "m", "label": "Описание", "field": "m", "align": "left", "sortable": True}],
                rows=[], row_key="k", pagination=10).props('dark flat dense no-data-label="Аварий нет"') \
                .style("width:100%;background:transparent")

        with ui.element("div").classes("card").style("width:100%"):
            ui.label("СОБЫТИЯ ЗА ДЕНЬ (клик по строке - показать на графике)").classes("cap")
            with ui.row().style("gap:16px;align-items:center;margin:8px 0;flex-wrap:wrap"):
                order = ui.toggle(["Новые сверху", "Старые сверху"], value="Новые сверху").props("dense")
                only_alm = ui.checkbox("Только аварии")
                search = ui.input(placeholder="Поиск по событиям").props("outlined dark dense clearable") \
                    .style("min-width:240px")
                ev_count = ui.label("").classes("cap")
            table = ui.table(columns=[
                {"name": "t", "label": "Время", "field": "t", "align": "left", "sortable": True},
                {"name": "m", "label": "Событие", "field": "m", "align": "left", "sortable": True}],
                rows=[], row_key="k", pagination=10).props("dark flat dense").style("width:100%;background:transparent")

        data = {"events": [], "alarms": [], "labels": []}

        def set_stat(key, text, unit=""):
            box, color = stat_boxes[key]
            u = f'<span class="unit">{unit}</span>' if unit else ""
            box.set_content(f'<span class="big" style="color:{color}">{text}</span>{u}')

        def zoom_all():
            try:
                chart.run_chart_method("dispatchAction", {"type": "dataZoom", "start": 0, "end": 100})
            except Exception as e:
                ui.notify(f"Масштаб: {e!r}", type="warning")

        def zoom_to(tstr):
            labels = data["labels"]
            if not labels:
                return
            i = min(bisect_left(labels, tstr), len(labels) - 1)
            a, b = max(0, i - ZOOM_POINTS), min(len(labels) - 1, i + ZOOM_POINTS)
            n = max(1, len(labels) - 1)
            try:
                chart.run_chart_method("dispatchAction", {"type": "dataZoom", "start": 100 * a / n, "end": 100 * b / n})
            except Exception as e:
                ui.notify(f"Масштаб: {e!r}", type="warning")

        def render_events():
            sel_rows = filter_events(data["events"], order.value == "Новые сверху", only_alm.value, search.value)
            table.rows = [{"k": k, "t": t, "m": m} for k, (t, m) in enumerate(sel_rows)]
            table.update()
            ev_count.set_text(f"показано {len(sel_rows)} из {len(data['events'])}")

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
            times = [r["t"] for r in small]
            data["labels"] = labels
            fdata = []
            for r in small:
                text, _key, col = direction(r["mode"], r["f"])
                fdata.append({"value": None if r["f"] is None else round(r["f"], 2), "dir": text, "col": col,
                              "alm": alarm_text(r["alarm"]) if r["alarm"] else ""})
            alarms = alarm_events(rows)
            area = [[{"xAxis": a, "itemStyle": {"color": "rgba(255,184,77,0.18)"}}, {"xAxis": b}]
                    for a, b in reverse_segments(labels, small)]
            lines, arows = [], []
            for k, ev in enumerate(alarms):
                i0, i1 = time_index(times, ev["t_start"]), time_index(times, ev["t_end"])
                area.append([{"xAxis": labels[i0], "itemStyle": {"color": "rgba(255,93,108,0.20)"}}, {"xAxis": labels[i1]}])
                lines.append({"xAxis": labels[i0], "name": alarm_symbol(ev["code"]),
                              "lineStyle": {"color": C["RED"], "width": 2, "type": "solid"}})
                dur = int((ev["t_end"] - ev["t_start"]).total_seconds())
                arows.append({"k": k, "t": ev["t_start"].strftime("%H:%M:%S"),
                              "d": ("до конца записи" if ev["open"] else fmt_hms(dur)),
                              "c": alarm_symbol(ev["code"]), "m": alarm_text(ev["code"])})
            chart.options["xAxis"]["data"] = labels
            chart.options["series"][0]["data"] = fdata
            chart.options["series"][0]["markArea"]["data"] = area
            chart.options["series"][0]["markLine"]["data"] = lines
            chart.options["series"][1]["data"] = [None if r["sp"] is None else round(r["sp"], 1) for r in small]
            chart.options["series"][2]["data"] = [None if r["i"] is None else round(r["i"], 2) for r in small]
            chart.update()
            alarm_table.rows = arows
            alarm_table.update()
            data["events"] = read_events(history_dir / f"events_{d}.csv")
            render_events()

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

        alarm_table.on("rowClick", lambda e: zoom_to(e.args[1]["t"]))
        table.on("rowClick", lambda e: zoom_to(e.args[1]["t"]))
        order.on_value_change(lambda e: render_events())
        only_alm.on_value_change(lambda e: render_events())
        search.on_value_change(lambda e: render_events())
        sel.on_value_change(lambda e: load())
        load()
        opc_lbl.set_content(link_html())
        ui.timer(2.0, lambda: opc_lbl.set_content(link_html()))
