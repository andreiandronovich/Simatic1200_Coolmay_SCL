"""diagnostics_page.py - страница /diagnostics: диагностика связи (только чтение, ничего не пишет в ПЛК)."""
import struct
import time

# (первый индекс, число регистров, название, тип)
REG_MAP = [
    (0, 1, "Командное слово (команда, не факт)", "word"),
    (1, 2, "Уставка частоты, Гц", "float"),
    (3, 2, "Выходная частота, Гц", "float"),
    (5, 1, "—", "word"),
    (6, 2, "Выходной ток, А", "float"),
    (8, 2, "Напряжение, В", "float"),
    (10, 1, "—", "word"),
    (11, 1, "Код аварии", "word"),
    (12, 5, "—", "word"),
    (17, 1, "Heartbeat (пишет панель)", "word"),
    (18, 1, "—", "word"),
    (19, 2, "Время разгона, с", "float"),
    (21, 2, "Время торможения, с", "float"),
]

def to_float(hi, lo):
    return struct.unpack(">f", struct.pack(">HH", hi & 0xFFFF, lo & 0xFFFF))[0]

def build_rows(raw):
    """Строки таблицы по массиву регистров: индекс, название, WORD, HEX, значение."""
    rows = []
    n = len(raw)
    for first, cnt, name, kind in REG_MAP:
        for k in range(cnt):
            idx = first + k
            if idx >= n:
                continue
            w = int(raw[idx]) & 0xFFFF
            if kind == "float" and k == 0 and idx + 1 < n:
                dec, label = f"{to_float(w, int(raw[idx + 1])):.2f}", name
            elif kind == "float":
                dec, label = "", "  (младшее слово)"
            else:
                dec, label = (str(w) if cnt == 1 or k == 0 else ""), (name if k == 0 else "")
            rows.append({"idx": idx, "name": label, "word": w, "hex": f"{w:04X}", "val": dec})
    for idx in range(max([f + c for f, c, _, _ in REG_MAP]), n):
        w = int(raw[idx]) & 0xFFFF
        rows.append({"idx": idx, "name": "—", "word": w, "hex": f"{w:04X}", "val": ""})
    return rows

def ago(t):
    if not t:
        return "—"
    s = time.time() - t
    return f"{s:.0f} с назад" if s < 120 else f"{s / 60:.0f} мин назад"

def register(css, url, state, diag, link_html, hb_html, C):
    from nicegui import ui

    @ui.page("/diagnostics")
    def diagnostics():
        ui.add_css(css)
        with ui.element("div").classes("card").style(
                "width:100%;display:flex;justify-content:space-between;align-items:center"):
            with ui.row().style("gap:18px;align-items:center"):
                ui.button("←  НАЗАД", on_click=lambda: ui.navigate.to("/")) \
                    .style(f"background:{C['BORDER']}!important;color:{C['TXT']};font-weight:700;border-radius:10px")
                ui.label("ДИАГНОСТИКА СВЯЗИ").style("font-size:20px;font-weight:700")
            with ui.row().style("gap:28px"):
                opc_lbl = ui.html("").style("font-size:14px")
                hb_lbl = ui.html("").style("font-size:14px")

        boxes = {}
        with ui.element("div").style("display:grid;grid-template-columns:repeat(4,1fr);gap:14px;width:100%"):
            for key, title in (("opc", "OPC UA (ПК → ПЛК)"), ("hb", "HEARTBEAT"),
                               ("ex", "ОБМЕН"), ("er", "ПОСЛЕДНЯЯ ОШИБКА")):
                with ui.element("div").classes("card"):
                    ui.label(title).classes("cap")
                    boxes[key] = ui.html("").style("margin-top:8px;width:100%")

        with ui.element("div").classes("card").style("width:100%"):
            ui.label("РЕГИСТРЫ HoldingReg (только чтение)").classes("cap")
            table = ui.table(columns=[
                {"name": "idx", "label": "№", "field": "idx", "align": "left"},
                {"name": "name", "label": "Назначение", "field": "name", "align": "left"},
                {"name": "word", "label": "WORD", "field": "word", "align": "right"},
                {"name": "hex", "label": "HEX", "field": "hex", "align": "right"},
                {"name": "val", "label": "Значение", "field": "val", "align": "right"}],
                rows=[], row_key="idx", pagination=0).props("dark flat dense").style(
                "width:100%;background:transparent")
            ui.label("Зелёный OPC UA означает, что ПК читает ПЛК. Связь ПЛК → частотник здесь пока не видна: "
                     "для неё в массив нужно вынести отдельный признак.").classes("cap").style("margin-top:10px")

        def kv(label, value, color=None):
            col = f"color:{color};" if color else ""
            return (f'<div style="display:flex;justify-content:space-between;padding:4px 0">'
                    f'<span class="cap">{label}</span><span style="{col}font-weight:600">{value}</span></div>')

        def refresh():
            opc_lbl.set_content(link_html()); hb_lbl.set_content(hb_html())
            okc = C["GREEN"] if state["link"] else C["RED"]
            boxes["opc"].set_content(
                kv("Адрес", url) + kv("Состояние", "подключено" if state["link"] else "нет связи", okc) +
                kv("Последнее чтение", ago(diag["last_ok"])) + kv("Подключений", diag["reconnects"]))
            age = (time.time() - state["hb_t"]) if state["hb_t"] else None
            hbc = C["GREEN"] if (age is not None and age <= 3 and state["link"]) else C["RED"]
            boxes["hb"].set_content(
                kv("Счётчик", state["hb"], hbc) + kv("Последняя запись", ago(state["hb_t"])) +
                kv("Регистр", "HoldingReg[17]"))
            ms = "—" if diag["read_ms"] is None else f"{diag['read_ms']:.0f} мс"
            boxes["ex"].set_content(
                kv("Чтение массива", ms) + kv("Успешных опросов", diag["ok"]) +
                kv("Ошибок", diag["err"], C["RED"] if diag["err"] else None))
            boxes["er"].set_content(
                kv("Когда", ago(diag["last_error_t"])) +
                f'<div class="cap" style="margin-top:6px;word-break:break-word">{diag["last_error"] or "ошибок не было"}</div>')
            table.rows = build_rows(diag["raw"]) if diag["raw"] else []
            table.update()
        refresh()
        ui.timer(1.0, refresh)
