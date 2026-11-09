"""panel.py - этап 1: внешний вид панели (демо-данные, без управления ПЛК).
Запуск:  pip install nicegui   ->   python panel.py   ->   http://127.0.0.1:8080
"""
import math, time
from collections import deque
from datetime import datetime
from nicegui import ui

# ---------- палитра (как на макете) ----------
BG, CARD, BORDER = "#0f1420", "#1a2133", "#2a3350"
TXT, DIM = "#e8ecf5", "#7b869e"
GREEN, RED, BLUE, AMBER, PURPLE = "#3ddc84", "#ff5d6c", "#4da3ff", "#ffb84d", "#a78bfa"
F_MAX = 20.0
TREND_SEC = 120

ui.add_css(f"""
body {{ background:{BG}; color:{TXT}; font-family:'Segoe UI',Arial,sans-serif; }}
.nicegui-content {{ padding:14px; gap:14px; }}
.card {{ background:{CARD}; border:1px solid {BORDER}; border-radius:14px; padding:16px 18px; }}
.cap {{ color:{DIM}; font-size:13px; letter-spacing:.5px; }}
.row-val {{ display:flex; justify-content:space-between; align-items:baseline;
           border-bottom:1px solid {BORDER}; padding:12px 0; }}
.big {{ font-size:30px; font-weight:700; }}
.unit {{ color:{DIM}; font-size:13px; margin-left:6px; }}
.dot {{ width:10px; height:10px; border-radius:50%; display:inline-block; margin-right:8px; }}
.grid3 {{ display:grid; grid-template-columns:1.25fr 1fr 1.05fr; gap:14px; width:100%; }}
.grid2 {{ display:grid; grid-template-columns:1.8fr 1fr; gap:14px; width:100%; }}
.btn {{ font-weight:700; font-size:16px; border-radius:10px; height:48px; }}
""")

state = {"freq": 0.0, "setpoint": 12.0, "current": 0.0, "voltage": 0.0,
         "running": False, "alarm": "нет", "link": True}
trend_f, trend_i = deque(maxlen=TREND_SEC), deque(maxlen=TREND_SEC)
events = deque(maxlen=5)

def log(msg, color):
    events.appendleft((datetime.now().strftime("%H:%M:%S"), msg, color))

def read_plc():
    """TODO этап 2: заменить на чтение из monitor.py (OPC UA). Сейчас - демо."""
    if state["running"]:
        state["freq"] += (state["setpoint"] - state["freq"]) * 0.15
    else:
        state["freq"] *= 0.85
    f = state["freq"]
    state["current"] = 0.0 if f < 0.1 else 5.5 - 0.04 * f + 0.3 * math.sin(time.time() / 3)
    state["voltage"] = f * 10.5

# ---------- шапка ----------
with ui.element("div").classes("card").style("width:100%;display:flex;justify-content:space-between;align-items:center"):
    ui.label("ПРИВОД  |  Simatic S7-1200 + Coolmay VFD").style("font-size:20px;font-weight:700")
    with ui.row().style("gap:28px"):
        opc_lbl = ui.html("").style("font-size:14px")
        hb_lbl = ui.html("").style("font-size:14px")

with ui.element("div").classes("grid3"):
    # --- частота ---
    with ui.element("div").classes("card"):
        ui.label("ЧАСТОТА, Гц").classes("cap")
        gauge = ui.echart({
            "series": [{
                "type": "gauge", "startAngle": 210, "endAngle": -30, "min": 0, "max": F_MAX,
                "radius": "95%", "progress": {"show": True, "width": 16, "itemStyle": {"color": BLUE}},
                "axisLine": {"lineStyle": {"width": 16, "color": [[1, "#273049"]]}},
                "pointer": {"show": False}, "axisTick": {"show": False}, "splitLine": {"show": False},
                "axisLabel": {"color": DIM, "distance": 22, "fontSize": 12},
                "detail": {"valueAnimation": True, "fontSize": 52, "fontWeight": "bold",
                           "color": TXT, "offsetCenter": [0, "0%"], "formatter": "{value}"},
                "title": {"offsetCenter": [0, "35%"], "color": DIM, "fontSize": 14},
                "data": [{"value": 0, "name": "факт"}],
            }]}).style("height:300px;width:100%")
        with ui.row().style("justify-content:space-between;width:100%"):
            ui.label("Уставка").classes("cap")
            sp_lbl = ui.label().style(f"color:{AMBER};font-size:18px;font-weight:700")

    # --- управление ---
    with ui.element("div").classes("card"):
        ui.label("УПРАВЛЕНИЕ").classes("cap")
        mode_lbl = ui.label().style("text-align:center;font-size:22px;font-weight:700;border-radius:10px;"
                                    "padding:10px;margin:12px 0")
        with ui.element("div").style("display:grid;grid-template-columns:1fr 1fr;gap:10px"):
            ui.button("ПУСК", on_click=lambda: (state.update(running=True), log("Пуск вперёд", GREEN))) \
                .classes("btn").style(f"background:{GREEN}!important;color:#0b0f19")
            ui.button("СТОП", on_click=lambda: (state.update(running=False), log("Стоп (панель)", RED))) \
                .classes("btn").style(f"background:{RED}!important;color:#fff")
            ui.button("РЕВЕРС").classes("btn").style(f"background:{BORDER}!important;color:{TXT}")
            ui.button("СБРОС").classes("btn").style(f"background:{BORDER}!important;color:{TXT}")
        ui.label("Уставка, Гц (0–20)").classes("cap").style("margin-top:18px")
        def on_sp(e):
            state["setpoint"] = round(e.value, 1)
            log(f"Уставка {state['setpoint']} Гц", AMBER)
        ui.slider(min=0, max=F_MAX, step=0.1, value=state["setpoint"], on_change=on_sp) \
            .props(f"color=amber label-always").style("margin-top:22px")

    # --- параметры ---
    with ui.element("div").classes("card"):
        ui.label("ПАРАМЕТРЫ").classes("cap")
        cur_lbl = ui.html("").style("width:100%")
        vol_lbl = ui.html("").style("width:100%")
        alm_lbl = ui.html("").style("width:100%")

with ui.element("div").classes("grid2"):
    with ui.element("div").classes("card"):
        ui.label("ТРЕНД (последние 2 мин)").classes("cap")
        trend = ui.echart({
            "animation": False, "legend": {"textStyle": {"color": TXT}, "left": 0, "top": 0},
            "grid": {"left": 40, "right": 16, "top": 36, "bottom": 24},
            "xAxis": {"type": "category", "data": [], "axisLabel": {"color": DIM}, "show": True},
            "yAxis": {"type": "value", "min": 0, "max": 22, "axisLabel": {"color": DIM},
                      "splitLine": {"lineStyle": {"color": BORDER}}},
            "series": [
                {"name": "Частота, Гц", "type": "line", "showSymbol": False, "data": [],
                 "lineStyle": {"color": BLUE, "width": 3}, "itemStyle": {"color": BLUE}},
                {"name": "Ток, А", "type": "line", "showSymbol": False, "data": [],
                 "lineStyle": {"color": PURPLE, "width": 3}, "itemStyle": {"color": PURPLE}},
            ]}).style("height:240px;width:100%")
    with ui.element("div").classes("card"):
        ui.label("ЖУРНАЛ СОБЫТИЙ").classes("cap")
        log_box = ui.html("").style("width:100%;margin-top:6px")

def row(label, value, color, unit=""):
    u = f'<span class="unit">{unit}</span>' if unit else ""
    return (f'<div class="row-val"><span class="cap" style="font-size:15px">{label}</span>'
            f'<span><span class="big" style="color:{color}">{value}</span>{u}</span></div>')

def refresh():
    read_plc()
    s = state
    gauge.options["series"][0]["data"][0]["value"] = round(s["freq"], 1)
    gauge.update()
    sp_lbl.set_text(f"{s['setpoint']:.1f} Гц")
    c = GREEN if s["link"] else RED
    opc_lbl.set_content(f'<span class="dot" style="background:{c}"></span><span style="color:{c}">'
                        f'OPC UA: {"подключено" if s["link"] else "нет связи"}</span>')
    hb_lbl.set_content(f'<span class="dot" style="background:{c}"></span><span style="color:{c}">Heartbeat: 1 с</span>')
    if s["running"]:
        mode_lbl.set_text("ВПЕРЁД")
        mode_lbl.style(f"background:#14301f;color:{GREEN};border:1px solid {GREEN}")
    else:
        mode_lbl.set_text("СТОП")
        mode_lbl.style(f"background:#2a1519;color:{RED};border:1px solid {RED}")
    cur_lbl.set_content(row("Ток", f"{s['current']:.2f}", PURPLE, "А"))
    vol_lbl.set_content(row("Напряжение", f"{s['voltage']:.1f}", BLUE, "В"))
    alm_lbl.set_content(row("Авария", s["alarm"], GREEN if s["alarm"] == "нет" else RED))
    trend_f.append(round(s["freq"], 2)); trend_i.append(round(s["current"], 2))
    trend.options["xAxis"]["data"] = list(range(-len(trend_f) + 1, 1))
    trend.options["series"][0]["data"] = list(trend_f)
    trend.options["series"][1]["data"] = list(trend_i)
    trend.update()
    log_box.set_content("".join(
        f'<div style="padding:7px 0;font-size:14px"><span class="cap" style="margin-right:10px">{t}</span>'
        f'<span class="dot" style="background:{col}"></span>{m}</div>' for t, m, col in events))

log("Подключено", GREEN)
ui.timer(1.0, refresh)
ui.run(title="Привод", host="127.0.0.1", port=8080, dark=True, reload=False)
