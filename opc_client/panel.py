"""panel.py - панель привода: OPC UA (S7-1200), главный экран + страница параметров + история в CSV.
Запуск: start_panel.bat или python panel.py  ->  http://127.0.0.1:8080  (параметры: /settings)
Не запускай одновременно с control.py (оба пишут heartbeat в регистр [17]).
DEMO = True - режим без ПЛК (история в этом режиме не пишется).
"""
import asyncio, csv, math, struct, time, traceback
from collections import deque
from datetime import datetime
from pathlib import Path
from nicegui import app, ui
from asyncua import Client, ua

DEMO = False
URL = "opc.tcp://192.168.1.2:4840"
BASE = 3                      # HoldingReg[n] = ns=4;i=3+n
ARRAY_NODE = "ns=4;i=2"
MAX_HZ = 50.0
CMD_FWD, CMD_REV, CMD_STOP, CMD_RESET = 1, 2, 256, 512
HB_REG = 17
ACC_REG, DEC_REG = 19, 21     # float32 в двух регистрах: [19,20] разгон, [21,22] торможение
T_MIN, T_MAX = 0.1, 600.0     # допустимое время, с (уточни по руководству частотника)

HISTORY_DIR = Path(__file__).resolve().parent / "history"
IDLE_PERIOD = 10              # в простое пишем строку раз в N секунд, при работе - каждую секунду
DECIMAL_COMMA = True          # True: десятичная запятая (русский Excel), False: точка
HB_OK_SEC = 3.0               # heartbeat считается живым, если запись проходила не позже N секунд назад

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
           border-bottom:1px solid {BORDER}; padding:10px 0; }}
.big {{ font-size:30px; font-weight:700; }}
.mid {{ font-size:24px; font-weight:700; }}
.unit {{ color:{DIM}; font-size:13px; margin-left:6px; }}
.dot {{ width:10px; height:10px; border-radius:50%; display:inline-block; margin-right:8px; }}
.grid3 {{ display:grid; grid-template-columns:1.25fr 1fr 1.05fr; gap:14px; width:100%; }}
.grid2 {{ display:grid; grid-template-columns:1.8fr 1fr; gap:14px; width:100%; }}
.grid2e {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; width:100%; }}
.btn {{ font-weight:700; font-size:16px; border-radius:10px; height:48px; }}
.chip {{ border-radius:8px; font-weight:600; }}
"""

# ---------- общее состояние ----------
state = {"freq": 0.0, "setpoint": 0.0, "current": 0.0, "voltage": 0.0,
         "mode": "?", "alarm": 0, "link": False, "acc": None, "dec": None,
         "hb": 0, "hb_t": 0.0}
trend_f, trend_i = deque(maxlen=TREND_SEC), deque(maxlen=TREND_SEC)
events = deque(maxlen=5)
pending_cmds = deque()        # ("start",), ("stop",), ("reverse",), ("reset",), ("freq", hz), ("acc", s), ("dec", s)
slider_edit = {"value": None, "t": 0.0}
_warned = {"history": False}

# ---------- история в CSV ----------
def _num(x, nd=2):
    if x is None:
        return ""
    s = f"{x:.{nd}f}"
    return s.replace(".", ",") if DECIMAL_COMMA else s

def _append_csv(path, header, row):
    HISTORY_DIR.mkdir(exist_ok=True)
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8-sig" if new else "utf-8") as f:
        w = csv.writer(f, delimiter=";")
        if new:
            w.writerow(header)
        w.writerow(row)

def write_history():
    if DEMO:
        return
    now = datetime.now()
    s = state
    path = HISTORY_DIR / f"drive_{now:%Y-%m-%d}.csv"
    header = ["время", "режим", "уставка_Гц", "частота_Гц", "ток_А", "напряжение_В",
              "авария", "разгон_с", "торможение_с"]
    row = [now.strftime("%Y-%m-%d %H:%M:%S"), s["mode"], _num(s["setpoint"], 1), _num(s["freq"], 2),
           _num(s["current"], 2), _num(s["voltage"], 1), s["alarm"], _num(s["acc"], 1), _num(s["dec"], 1)]
    try:
        _append_csv(path, header, row)
        _warned["history"] = False
    except Exception as e:
        if not _warned["history"]:
            _warned["history"] = True
            log("История: файл занят (закрой Excel)", RED)
            print("history error:", repr(e))

def write_event(msg):
    if DEMO:
        return
    now = datetime.now()
    try:
        _append_csv(HISTORY_DIR / f"events_{now:%Y-%m-%d}.csv", ["время", "событие"],
                    [now.strftime("%Y-%m-%d %H:%M:%S"), msg])
    except Exception as e:
        print("event log error:", repr(e))

def log(msg, color):
    events.appendleft((datetime.now().strftime("%H:%M:%S"), msg, color))
    write_event(msg)

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

    async def write_float(self, idx, x):
        hi, lo = from_float(x)
        await self.write_reg(idx, hi); await self.write_reg(idx + 1, lo)

    async def set_freq(self, hz):
        if not 0.0 <= hz <= MAX_HZ:
            log(f"Отказ: уставка 0..{MAX_HZ:g} Гц", RED); return
        await self.write_float(1, hz)
        log(f"Уставка {hz:.1f} Гц", AMBER)

    async def set_time(self, kind, sec):
        name, reg = ("Разгон", ACC_REG) if kind == "acc" else ("Торможение", DEC_REG)
        if not T_MIN <= sec <= T_MAX:
            log(f"Отказ: {name.lower()} {T_MIN:g}..{T_MAX:g} с", RED); return
        await self.write_float(reg, sec)
        log(f"{name} {sec:.1f} с", AMBER)

    async def start(self):
        r = await self.regs()
        if r[11] != 0:
            log(f"Отказ: авария {r[11]}, сначала сброс", RED); return
        if r[0] & 2 and to_float(r[3], r[4]) > 0.5:
            log("Пуск: сначала СТОП и дождитесь остановки", RED); return
        if to_float(r[1], r[2]) > MAX_HZ:
            log("Отказ: уставка выше лимита", RED); return
        await self.write_reg(0, CMD_FWD); log("Пуск вперёд", GREEN)

    async def reverse(self):
        r = await self.regs()
        if r[11] != 0:
            log(f"Отказ: авария {r[11]}, сначала сброс", RED); return
        if r[0] & 1 or to_float(r[3], r[4]) > 0.5:
            log("Реверс: сначала СТОП и дождитесь остановки", RED); return
        await self.write_reg(0, CMD_REV); log("Пуск назад", AMBER)

    async def stop(self):
        await self.write_reg(0, CMD_STOP); log("Стоп (панель)", RED)

    async def reset(self):
        await self.write_reg(0, CMD_RESET); log("Сброс аварии", AMBER)

def apply_regs(r):
    state.update(mode=mode_of(r[0]), setpoint=to_float(r[1], r[2]), freq=to_float(r[3], r[4]),
                 current=to_float(r[6], r[7]), voltage=to_float(r[8], r[9]), alarm=int(r[11]))
    if len(r) > DEC_REG + 1:
        state["acc"] = to_float(r[ACC_REG], r[ACC_REG + 1])
        state["dec"] = to_float(r[DEC_REG], r[DEC_REG + 1])

async def handle_cmds(d):
    if slider_edit["value"] is not None and time.time() - slider_edit["t"] > 0.4:
        hz, slider_edit["value"] = slider_edit["value"], None
        pending_cmds.append(("freq", hz))
    while pending_cmds:
        name, *arg = pending_cmds.popleft()
        if name == "start": await d.start()
        elif name == "stop": await d.stop()
        elif name == "reverse": await d.reverse()
        elif name == "reset": await d.reset()
        elif name == "freq": await d.set_freq(arg[0])
        elif name in ("acc", "dec"): await d.set_time(name, arg[0])

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
                    state["hb"], state["hb_t"] = n, time.time()
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
    state.update(link=True, setpoint=12.0, mode="СТОП", acc=5.0, dec=5.0)
    while True:
        state["hb"] += 1; state["hb_t"] = time.time()
        if slider_edit["value"] is not None and time.time() - slider_edit["t"] > 0.4:
            pending_cmds.append(("freq", slider_edit["value"])); slider_edit["value"] = None
        while pending_cmds:
            name, *arg = pending_cmds.popleft()
            if name == "start": run = True; state["mode"] = "ВПЕРЁД"; log("Пуск вперёд", GREEN)
            elif name == "stop": run = False; state["mode"] = "СТОП"; log("Стоп (панель)", RED)
            elif name == "freq": state["setpoint"] = arg[0]; log(f"Уставка {arg[0]:.1f} Гц", AMBER)
            elif name in ("acc", "dec"):
                state[name] = arg[0]; log(f"{'Разгон' if name == 'acc' else 'Торможение'} {arg[0]:.1f} с", AMBER)
        state["freq"] += ((state["setpoint"] if run else 0) - state["freq"]) * 0.15
        f = state["freq"]
        state["current"] = 0.0 if f < 0.1 else 5.5 + 0.3 * math.sin(time.time() / 3)
        state["voltage"] = f * 10.5
        await asyncio.sleep(1)

async def ticker():
    """Раз в секунду: тренд, события аварий и запись истории (один раз, независимо от числа вкладок)."""
    last_alarm, last_write = 0, 0.0
    while True:
        s = state
        trend_f.append(round(s["freq"], 2)); trend_i.append(round(s["current"], 2))
        if s["alarm"] != last_alarm:
            log(f"Авария {s['alarm']}" if s["alarm"] else "Авария снята", RED if s["alarm"] else GREEN)
            last_alarm = s["alarm"]
        if s["link"]:
            active = s["freq"] > 0.05 or s["mode"] in ("ВПЕРЁД", "РЕВЕРС")
            if active or time.time() - last_write >= IDLE_PERIOD:
                write_history(); last_write = time.time()
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

# ---------- общие элементы интерфейса ----------
def row(label, value, color, unit="", cls="big"):
    u = f'<span class="unit">{unit}</span>' if unit else ""
    return (f'<div class="row-val"><span class="cap" style="font-size:15px">{label}</span>'
            f'<span><span class="{cls}" style="color:{color}">{value}</span>{u}</span></div>')

def link_html():
    c = GREEN if state["link"] else RED
    return (f'<span class="dot" style="background:{c}"></span><span style="color:{c}">'
            f'OPC UA: {"подключено" if state["link"] else "нет связи"}</span>')

def hb_html():
    age = time.time() - state["hb_t"] if state["hb_t"] else None
    if not state["link"] or age is None:
        return f'<span class="dot" style="background:{RED}"></span><span style="color:{RED}">Heartbeat: нет</span>'
    ok = age <= HB_OK_SEC
    c = GREEN if ok else RED
    return (f'<span class="dot" style="background:{c}"></span><span style="color:{c}">'
            f'Heartbeat: {state["hb"]}</span>')

def fmt_t(x):
    return "—" if x is None else f"{x:.1f}"

# ---------- главная страница ----------
@ui.page("/")
def index():
    ui.add_css(CSS)
    with ui.element("div").classes("card").style("width:100%;display:flex;justify-content:space-between;align-items:center"):
        ui.label("ПРИВОД  |  Simatic S7-1200 + Coolmay VFD").style("font-size:20px;font-weight:700")
        with ui.row().style("gap:28px;align-items:center"):
            opc_lbl = ui.html("").style("font-size:14px")
            hb_lbl = ui.html("").style("font-size:14px")
            ui.button("⚙  ПАРАМЕТРЫ", on_click=lambda: ui.navigate.to("/settings")) \
                .style(f"background:{BORDER}!important;color:{TXT};font-weight:700;border-radius:10px")

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
                ui.button("РЕВЕРС", on_click=lambda: pending_cmds.append(("reverse",))) \
                    .classes("btn").style(f"background:{BORDER}!important;color:{TXT}")
                ui.button("СБРОС", on_click=lambda: pending_cmds.append(("reset",))) \
                    .classes("btn").style(f"background:{BORDER}!important;color:{TXT}")
            ui.label(f"Уставка, Гц (0–{MAX_HZ:g})").classes("cap").style("margin-top:18px")

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
            acc_lbl = ui.html("").style("width:100%")
            dec_lbl = ui.html("").style("width:100%")

    with ui.element("div").classes("grid2"):
        with ui.element("div").classes("card"):
            ui.label("ТРЕНД (последние 2 мин)").classes("cap")
            trend = ui.echart({
                "animation": False, "legend": {"textStyle": {"color": TXT}, "left": 0, "top": 0},
                "grid": {"left": 40, "right": 16, "top": 36, "bottom": 24},
                "xAxis": {"type": "category", "data": [], "axisLabel": {"color": DIM}},
                "yAxis": {"type": "value", "min": 0, "max": 50, "interval": 10, "axisLabel": {"color": DIM},
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
        opc_lbl.set_content(link_html())
        hb_lbl.set_content(hb_html())
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
        acc_lbl.set_content(row("Разгон", fmt_t(s["acc"]), AMBER, "с", "mid"))
        dec_lbl.set_content(row("Торможение", fmt_t(s["dec"]), AMBER, "с", "mid"))
        trend.options["xAxis"]["data"] = list(range(-len(trend_f) + 1, 1))
        trend.options["series"][0]["data"] = list(trend_f)
        trend.options["series"][1]["data"] = list(trend_i)
        trend.update()
        log_box.set_content("".join(
            f'<div style="padding:7px 0;font-size:14px"><span class="cap" style="margin-right:10px">{t}</span>'
            f'<span class="dot" style="background:{col}"></span>{m}</div>' for t, m, col in events))

    refresh()
    ui.timer(1.0, refresh)

# ---------- страница параметров ----------
@ui.page("/settings")
def settings():
    ui.add_css(CSS)
    with ui.element("div").classes("card").style("width:100%;display:flex;justify-content:space-between;align-items:center"):
        with ui.row().style("gap:18px;align-items:center"):
            ui.button("←  НАЗАД", on_click=lambda: ui.navigate.to("/")) \
                .style(f"background:{BORDER}!important;color:{TXT};font-weight:700;border-radius:10px")
            ui.label("ПАРАМЕТРЫ ПРИВОДА").style("font-size:20px;font-weight:700")
        opc_lbl = ui.html("").style("font-size:14px")

    live = {}

    def block(kind, title, reg_text):
        with ui.element("div").classes("card"):
            ui.label(title).classes("cap")
            with ui.row().style("justify-content:space-between;align-items:baseline;width:100%;margin:6px 0 14px"):
                ui.label("Сейчас в ПЛК").classes("cap")
                live[kind] = ui.html("").style("width:auto")
            with ui.row().style("gap:8px;align-items:center;width:100%;flex-wrap:nowrap"):
                num = ui.number(value=state[kind] if state[kind] else 5.0, min=T_MIN, max=T_MAX, step=0.5,
                                format="%.1f").props("outlined dark dense suffix=с input-class=text-h5") \
                    .style("flex:1")
                for step in (-1.0, +1.0):
                    ui.button(f"{step:+g}", on_click=lambda n=num, st=step: n.set_value(
                        round(min(T_MAX, max(T_MIN, (n.value or 0) + st)), 1))) \
                        .style(f"background:{BORDER}!important;color:{TXT};font-weight:700;border-radius:8px")
            ui.label("Быстрый выбор, с").classes("cap").style("margin-top:16px")
            with ui.row().style("gap:8px;margin-top:6px"):
                for v in (1, 3, 5, 10, 20, 30):
                    ui.button(f"{v}", on_click=lambda n=num, x=v: n.set_value(float(x))).classes("chip") \
                        .props("flat dense").style(f"background:{BORDER}!important;color:{TXT};min-width:44px")

            def apply(n=num, k=kind):
                if n.value is None or not T_MIN <= n.value <= T_MAX:
                    ui.notify(f"Допустимо {T_MIN:g}…{T_MAX:g} с", type="negative"); return
                if not state["link"]:
                    ui.notify("Нет связи с ПЛК", type="negative"); return
                pending_cmds.append((k, round(float(n.value), 1)))
                ui.notify("Отправлено в ПЛК", type="positive")
            ui.button("ПРИМЕНИТЬ", on_click=apply).classes("btn") \
                .style(f"background:{GREEN}!important;color:#0b0f19;width:100%;margin-top:22px")
            ui.label(reg_text).classes("cap").style("margin-top:12px")

    with ui.element("div").classes("grid2e"):
        block("acc", "ВРЕМЯ РАЗГОНА", f"Запись в HoldingReg[{ACC_REG}–{ACC_REG + 1}]")
        block("dec", "ВРЕМЯ ТОРМОЖЕНИЯ", f"Запись в HoldingReg[{DEC_REG}–{DEC_REG + 1}]")

    with ui.element("div").classes("card").style("width:100%"):
        ui.label("ИСТОРИЯ").classes("cap")
        ui.label(f"Данные пишутся в папку: {HISTORY_DIR}").style("margin-top:6px;font-size:14px")
        ui.label("drive_ГГГГ-ММ-ДД.csv — частота, ток, напряжение, авария; events_ГГГГ-ММ-ДД.csv — команды и события.") \
            .classes("cap").style("margin-top:4px")

    def refresh():
        opc_lbl.set_content(link_html())
        for k in ("acc", "dec"):
            live[k].set_content(f'<span class="big" style="color:{AMBER}">{fmt_t(state[k])}</span>'
                                f'<span class="unit">с</span>')
    refresh()
    ui.timer(1.0, refresh)

ui.run(title="Привод", host="127.0.0.1", port=8080, dark=True, reload=False)
