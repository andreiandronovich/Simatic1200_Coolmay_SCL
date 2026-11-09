"""panel.py - этап 2: панель подключена к ПЛК по OPC UA (логика из monitor.py и control.py).
Запуск:  python panel.py   ->   http://127.0.0.1:8080
ВАЖНО: не запускай одновременно с control.py - у обоих свой heartbeat в регистр [17].
DEMO = True включает режим без ПЛК (для проверки внешнего вида).
"""
import asyncio, math, struct, time, traceback
from collections import deque
from datetime import datetime
from nicegui import app, ui
from asyncua import Client, ua

DEMO = False
URL = "opc.tcp://192.168.1.2:4840"
BASE = 3                      # HoldingReg[n] = ns=4;i=3+n
ARRAY_NODE = "ns=4;i=2"
MAX_HZ = 20.0
CMD_FWD, CMD_STOP, CMD_RESET = 1, 256, 512
HB_REG = 17

BG, CARD, BORDER = "#0f1420", "#1a2133", "#2a3350"
TXT, DIM = "#e8ecf5", "#7b869e"
GREEN, RED, BLUE, AMBER, PURPLE = "#3ddc84", "#ff5d6c", "#4da3ff", "#ffb84d", "#a78bfa"
TREND_SEC = 120

CSS = f"""
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
"""

# ---------- общее состояние (создаётся один раз, общее для всех вкладок) ----------
state = {"freq": 0.0, "setpoint": 0.0, "current": 0.0, "voltage": 0.0,
         "mode": "?", "alarm": 0, "link": False}
trend_f, trend_i = deque(maxlen=TREND_SEC), deque(maxlen=TREND_SEC)
events = deque(maxlen=5)
pending_cmds = deque()        # ("start",), ("stop",), ("reset",), ("freq", hz)
slider_edit = {"value": None, "t": 0.0}

def log(msg, color):
    events.appendleft((datetime.now().strftime("%H:%M:%S"), msg, color))

def to_float(hi, lo):
    return struct.unpack(">f", struct.pack(">HH", hi, lo))[0]

def from_float(x):
    return struct.unpack(">HH", struct.pack(">f", x))

def mode_of(cmd):
    return "СТОП" if cmd & 0x100 else "ВПЕРЁД" if cmd & 1 else "РЕВЕРС" if cmd & 2 else "?"

# ---------- связь с ПЛК ----------
class Drive:
    def __init__(self, client):
        self.c = client
        self.arr = client.get_node(ARRAY_NODE)
        self.vt = {}

    async def write_reg(self, idx, value):
        node = self.c.get_node(f"ns=4;i={BASE + idx}")
        if idx not in self.vt:
            self.vt[idx] = await node.read_data_type_as_variant_type()
        await node.write_value(ua.DataValue(ua.Variant(value, self.vt[idx])))

    async def regs(self):
        return await self.arr.read_value()

    async def set_freq(self, hz):
        if not 0.0 <= hz <= MAX_HZ:
            log(f"Отказ: уставка 0..{MAX_HZ} Гц", RED); return
        hi, lo = from_float(hz)
        await self.write_reg(1, hi); await self.write_reg(2, lo)
        log(f"Уставка {hz:.1f} Гц", AMBER)

    async def start(self):
        r = await self.regs()
        if r[11] != 0:
            log(f"Отказ: авария {r[11]}, сначала сброс", RED); return
        if to_float(r[1], r[2]) > MAX_HZ:
            log("Отказ: уставка выше лимита", RED); return
        await self.write_reg(0, CMD_FWD); log("Пуск вперёд", GREEN)

    async def stop(self):
        await self.write_reg(0, CMD_STOP); log("Стоп (панель)", RED)

    async def reset(self):
        await self.write_reg(0, CMD_RESET); log("Сброс аварии", AMBER)

def apply_regs(r):
    state.update(mode=mode_of(r[0]), setpoint=to_float(r[1], r[2]), freq=to_float(r[3], r[4]),
                 current=to_float(r[6], r[7]), voltage=to_float(r[8], r[9]), alarm=int(r[11]))

async def handle_cmds(d):
    if slider_edit["value"] is not None and time.time() - slider_edit["t"] > 0.4:
        hz, slider_edit["value"] = slider_edit["value"], None
        pending_cmds.append(("freq", hz))
    while pending_cmds:
        name, *arg = pending_cmds.popleft()
        if name == "start": await d.start()
        elif name == "stop": await d.stop()
        elif name == "reset": await d.reset()
        elif name == "freq": await d.set_freq(arg[0])

async def plc_loop():
    n = 0
    while True:
        try:
            async with Client(url=URL, timeout=4) as client:
                d = Drive(client)
                state["link"] = True
                log("Подключено к ПЛК", GREEN)
                while True:
                    n = n % 65535 + 1
                    await asyncio.wait_for(d.write_reg(HB_REG, n), 5)
                    await asyncio.wait_for(handle_cmds(d), 5)
                    apply_regs(await asyncio.wait_for(d.regs(), 5))
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except BaseException:
            traceback.print_exc()
            if state["link"]:
                log("Связь с ПЛК потеряна", RED)
            state["link"] = False
            await asyncio.sleep(2)

async def demo_loop():
    run = False
    state.update(link=True, setpoint=12.0, mode="СТОП")
    while True:
        if slider_edit["value"] is not None and time.time() - slider_edit["t"] > 0.4:
            pending_cmds.append(("freq", slider_edit["value"])); slider_edit["value"] = None
        while pending_cmds:
            name, *arg = pending_cmds.popleft()
            if name == "start": run = True; state["mode"] = "ВПЕРЁД"; log("Пуск вперёд", GREEN)
            elif name == "stop": run = False; state["mode"] = "СТОП"; log("Стоп (панель)", RED)
            elif name == "freq": state["setpoint"] = arg[0]; log(f"Уставка {arg[0]:.1f} Гц", AMBER)
        state["freq"] += ((state["setpoint"] if run else 0) - state["freq"]) * 0.15
        f = state["freq"]
        state["current"] = 0.0 if f < 0.1 else 5.5 + 0.3 * math.sin(time.time() / 3)
        state["voltage"] = f * 10.5
        await asyncio.sleep(1)

async def ticker():
    """Раз в секунду: тренд и события аварий (один раз, независимо от числа открытых вкладок)."""
    last_alarm = 0
    while True:
        trend_f.append(round(state["freq"], 2)); trend_i.append(round(state["current"], 2))
        if state["alarm"] != last_alarm:
            log(f"Авария {state['alarm']}" if state["alarm"] else "Авария снята",
                RED if state["alarm"] else GREEN)
            last_alarm = state["alarm"]
        await asyncio.sleep(1)

_tasks = set()
async def on_startup():
    for coro in (demo_loop() if DEMO else plc_loop(), ticker()):
        t = asyncio.create_task(coro); _tasks.add(t)

async def on_shutdown():
    if DEMO: return
    try:
        async with Client(url=URL, timeout=3) as client:
            d = Drive(client)
            await d.write_reg(0, CMD_STOP); await d.write_reg(HB_REG, 0)
    except Exception as e:
        print("Не удалось остановить при выходе:", repr(e))

app.on_startup(on_startup)
app.on_shutdown(on_shutdown)

def row(label, value, color, unit=""):
    u = f'<span class="unit">{unit}</span>' if unit else ""
    return (f'<div class="row-val"><span class="cap" style="font-size:15px">{label}</span>'
            f'<span><span class="big" style="color:{color}">{value}</span>{u}</span></div>')

# ---------- интерфейс (строится заново для каждой открытой страницы) ----------
@ui.page("/")
def index():
    ui.add_css(CSS)
    with ui.element("div").classes("card").style("width:100%;display:flex;justify-content:space-between;align-items:center"):
        ui.label("ПРИВОД  |  Simatic S7-1200 + Coolmay VFD").style("font-size:20px;font-weight:700")
        with ui.row().style("gap:28px"):
            opc_lbl = ui.html("").style("font-size:14px")
            hb_lbl = ui.html("").style("font-size:14px")

    with ui.element("div").classes("grid3"):
        with ui.element("div").classes("card"):
            ui.label("ЧАСТОТА, Гц").classes("cap")
            gauge = ui.echart({
                "series": [{
                    "type": "gauge", "startAngle": 210, "endAngle": -30, "min": 0, "max": MAX_HZ,
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

        with ui.element("div").classes("card"):
            ui.label("УПРАВЛЕНИЕ").classes("cap")
            mode_lbl = ui.label().style("text-align:center;font-size:22px;font-weight:700;border-radius:10px;"
                                        "padding:10px;margin:12px 0")
            with ui.element("div").style("display:grid;grid-template-columns:1fr 1fr;gap:10px"):
                ui.button("ПУСК", on_click=lambda: pending_cmds.append(("start",))) \
                    .classes("btn").style(f"background:{GREEN}!important;color:#0b0f19")
                ui.button("СТОП", on_click=lambda: pending_cmds.append(("stop",))) \
                    .classes("btn").style(f"background:{RED}!important;color:#fff")
                ui.button("РЕВЕРС", on_click=lambda: ui.notify("Реверс пока не реализован в ПЛК", type="warning")) \
                    .classes("btn").style(f"background:{BORDER}!important;color:{TXT}")
                ui.button("СБРОС", on_click=lambda: pending_cmds.append(("reset",))) \
                    .classes("btn").style(f"background:{BORDER}!important;color:{TXT}")
            ui.label("Уставка, Гц (0–20)").classes("cap").style("margin-top:18px")

            def on_slider(e):
                if abs(e.value - state["setpoint"]) < 0.05:
                    return
                slider_edit["value"], slider_edit["t"] = round(e.value, 1), time.time()
            slider = ui.slider(min=0, max=MAX_HZ, step=0.1, value=round(state["setpoint"], 1),
                               on_change=on_slider) \
                .props("color=amber label-always").style("margin-top:38px")

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
                "xAxis": {"type": "category", "data": [], "axisLabel": {"color": DIM}},
                "yAxis": {"type": "value", "min": 0, "max": 20, "interval": 5, "axisLabel": {"color": DIM},
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

    last_sp = {"v": None}

    def refresh():
        s = state
        if s["link"] and slider_edit["value"] is None and last_sp["v"] != s["setpoint"]:
            slider.value = round(s["setpoint"], 1); last_sp["v"] = s["setpoint"]
        gauge.options["series"][0]["data"][0]["value"] = round(s["freq"], 1)
        gauge.update()
        sp_lbl.set_text(f"{s['setpoint']:.1f} Гц")
        c = GREEN if s["link"] else RED
        opc_lbl.set_content(f'<span class="dot" style="background:{c}"></span><span style="color:{c}">'
                            f'OPC UA: {"подключено" if s["link"] else "нет связи"}</span>')
        hb_lbl.set_content(f'<span class="dot" style="background:{c}"></span><span style="color:{c}">Heartbeat: 1 с</span>')
        if not s["link"]:
            mode_lbl.set_text("НЕТ СВЯЗИ"); mode_lbl.style(f"background:#2a1519;color:{RED};border:1px solid {RED}")
        elif s["mode"] == "ВПЕРЁД":
            mode_lbl.set_text("ВПЕРЁД"); mode_lbl.style(f"background:#14301f;color:{GREEN};border:1px solid {GREEN}")
        elif s["mode"] == "СТОП":
            mode_lbl.set_text("СТОП"); mode_lbl.style(f"background:#2a1519;color:{RED};border:1px solid {RED}")
        else:
            mode_lbl.set_text(s["mode"]); mode_lbl.style(f"background:#2a2415;color:{AMBER};border:1px solid {AMBER}")
        cur_lbl.set_content(row("Ток", f"{s['current']:.2f}", PURPLE, "А"))
        vol_lbl.set_content(row("Напряжение", f"{s['voltage']:.1f}", BLUE, "В"))
        alm_lbl.set_content(row("Авария", "нет" if s["alarm"] == 0 else f"код {s['alarm']}",
                                GREEN if s["alarm"] == 0 else RED))
        trend.options["xAxis"]["data"] = list(range(-len(trend_f) + 1, 1))
        trend.options["series"][0]["data"] = list(trend_f)
        trend.options["series"][1]["data"] = list(trend_i)
        trend.update()
        log_box.set_content("".join(
            f'<div style="padding:7px 0;font-size:14px"><span class="cap" style="margin-right:10px">{t}</span>'
            f'<span class="dot" style="background:{col}"></span>{m}</div>' for t, m, col in events))

    refresh()
    ui.timer(1.0, refresh)

ui.run(title="Привод", host="127.0.0.1", port=8080, dark=True, reload=False)
