"""mobile_page.py - страница /m: просмотр и управление с телефона (управление - по PIN-коду).
Пока управление заблокировано, кнопок нет (при STOP_NEEDS_PIN = True нет и кнопки СТОП).
С любых адресов, кроме самого ПК, открывается только /m (остальные страницы возвращают 403).
На ПК: devices_button() в шапке - список устройств, отключение по IP, список заблокированных.
"""
import asyncio
import ipaddress
import json
import time
from pathlib import Path

MOBILE_CSS = """
.m-wrap { width:100%; max-width:520px; margin:0 auto; display:flex; flex-direction:column; gap:12px; }
.m-grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; width:100%; }
.m-big { font-size:56px; font-weight:800; line-height:1.0; }
.m-mid { font-size:30px; font-weight:700; line-height:1.1; }
.m-badge { text-align:center; font-size:26px; font-weight:800; border-radius:14px; padding:14px; }
.m-btn { font-weight:800; font-size:20px; border-radius:14px; height:64px; width:100%; }
"""

UNLOCK_SEC = 120              # через столько секунд без действий управление блокируется снова
MAX_FAILS = 5                 # неверных PIN подряд до временной блокировки адреса
LOCK_SEC = 60
STOP_NEEDS_PIN = True         # True: кнопка СТОП появляется только после ввода PIN
ONLINE_SEC = 8                # устройство считается подключённым, если страница отвечала не позже N секунд назад
SLIDER_PAUSE = 0.6            # пауза после движения ползунка, затем уставка отправляется

_fails = {}                   # ip -> (число ошибок, время, до которого заблокирован)
_viewers = {}                 # id страницы -> данные устройства
_blocked = set()              # заблокированные IP (хранятся в blocked_ips.json)
_log = None                   # функция журнала панели (задаётся в register)
_C = {}                       # цвета панели
_block_path = None

def lan_urls(port):
    out = []
    try:
        import ifaddr
        for ad in ifaddr.get_adapters():
            for ip in ad.ips:
                if ip.is_IPv4 and not str(ip.ip).startswith(("127.", "169.254.")):
                    out.append((ad.nice_name, f"http://{ip.ip}:{port}/m"))
    except Exception as e:
        print("Не удалось получить список адресов:", repr(e))
    return out

def is_local(host):
    return host in ("127.0.0.1", "::1", "localhost")

def load_pin(path):
    try:
        pin = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return pin if len(pin) >= 4 else ""

def check_pin(ip, entered, expected, now=None):
    """(успех, сообщение). MAX_FAILS ошибок подряд -> блокировка адреса на LOCK_SEC секунд."""
    now = time.time() if now is None else now
    n, locked_until = _fails.get(ip, (0, 0.0))
    if now < locked_until:
        return False, f"Слишком много попыток. Подожди {int(locked_until - now) + 1} с"
    if expected and entered == expected:
        _fails.pop(ip, None)
        return True, ""
    n += 1
    if n >= MAX_FAILS:
        _fails[ip] = (0, now + LOCK_SEC)
        return False, f"Неверный PIN. Адрес заблокирован на {LOCK_SEC} с"
    _fails[ip] = (n, 0.0)
    return False, f"Неверный PIN (попытка {n} из {MAX_FAILS})"

def device_name(ua):
    u = (ua or "").lower()
    if "iphone" in u: return "iPhone"
    if "ipad" in u: return "iPad"
    if "android" in u: return "Android"
    if "windows" in u: return "Windows"
    if "macintosh" in u or "mac os" in u: return "Mac"
    if "linux" in u: return "Linux"
    return "Браузер"

def fmt_dur(sec):
    sec = int(sec)
    return f"{sec} с" if sec < 60 else f"{sec // 60} мин" if sec < 3600 else f"{sec // 3600} ч {sec % 3600 // 60} мин"

def viewers(now=None):
    """Список подключённых устройств страницы /m."""
    now = time.time() if now is None else now
    out = [{"ip": v["ip"], "device": v["device"], "dur": fmt_dur(now - v["since"]),
            "unlocked": v["unlocked"]} for v in _viewers.values() if now - v["seen"] <= ONLINE_SEC]
    return sorted(out, key=lambda x: x["ip"])

def load_blocked():
    global _blocked
    try:
        _blocked = set(json.loads(Path(_block_path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        _blocked = set()

def save_blocked():
    try:
        Path(_block_path).write_text(json.dumps(sorted(_blocked), indent=1), encoding="utf-8")
    except OSError as e:
        print("Не удалось сохранить blocked_ips.json:", repr(e))

def block_ip(ip):
    """(успех, сообщение). Локальный адрес ПК заблокировать нельзя."""
    try:
        ip = str(ipaddress.ip_address(ip.strip()))
    except ValueError:
        return False, "Неверный IP-адрес"
    if is_local(ip):
        return False, "Адрес самого ПК блокировать нельзя"
    if ip in _blocked:
        return False, "Адрес уже заблокирован"
    _blocked.add(ip); save_blocked()
    if _log:
        _log(f"Устройство заблокировано: {ip}", _C.get("RED", "#ff5d6c"))
    return True, ""

def unblock_ip(ip):
    if ip in _blocked:
        _blocked.discard(ip); save_blocked()
        if _log:
            _log(f"Устройство разблокировано: {ip}", _C.get("GREEN", "#3ddc84"))

def sweep(now=None):
    """Убирает отключившиеся устройства и пишет в журнал 'Устройство отключено'."""
    now = time.time() if now is None else now
    for k, v in list(_viewers.items()):
        if now - v["seen"] > ONLINE_SEC:
            _viewers.pop(k, None)
            alive = any(w["ip"] == v["ip"] and now - w["seen"] <= ONLINE_SEC for w in _viewers.values())
            if not alive and _log:
                _log(f"Устройство отключено: {v['ip']} ({v['device']})", _C.get("DIM", "#7b869e"))

def devices_button(border, txt, card, dim="#7b869e", green="#3ddc84", red="#ff5d6c"):
    """Кнопка в шапке ПК: число устройств; по клику - список, отключение по IP, заблокированные адреса."""
    from nicegui import ui

    def show():
        with ui.dialog() as dlg, ui.card().style(f"background:{card};min-width:480px;border:1px solid {border}"):
            @ui.refreshable
            def body():
                ui.label("ПОДКЛЮЧЁННЫЕ УСТРОЙСТВА (страница /m)").style(f"color:{dim};font-size:13px")
                vs = viewers()
                if not vs:
                    ui.label("Сейчас никого нет.").style(f"color:{txt}")
                for v in vs:
                    access = "управление" if v["unlocked"] else "просмотр"
                    with ui.row().style("align-items:center;width:100%;justify-content:space-between;flex-wrap:nowrap"):
                        ui.label(f'{v["ip"]}   {v["device"]}   {v["dur"]}   {access}') \
                            .style(f'color:{green if v["unlocked"] else txt};font-size:15px')
                        ui.button("ОТКЛЮЧИТЬ", on_click=lambda ip=v["ip"]: do_block(ip)).props("flat dense").style(f"color:{red}")
                ui.separator()
                ui.label("ЗАБЛОКИРОВАННЫЕ АДРЕСА").style(f"color:{dim};font-size:13px")
                if not _blocked:
                    ui.label("Список пуст.").style(f"color:{txt}")
                for ip in sorted(_blocked):
                    with ui.row().style("align-items:center;width:100%;justify-content:space-between;flex-wrap:nowrap"):
                        ui.label(ip).style(f"color:{txt};font-size:15px")
                        ui.button("ВЕРНУТЬ", on_click=lambda ip=ip: do_unblock(ip)).props("flat dense").style(f"color:{green}")
                with ui.row().style("align-items:center;width:100%;flex-wrap:nowrap"):
                    ip_in = ui.input(placeholder="например 192.168.0.25").props("outlined dark dense").style("flex:1")
                    ui.button("ЗАБЛОКИРОВАТЬ", on_click=lambda: do_block(ip_in.value or "")).props("flat dense").style(f"color:{red}")

            def do_block(ip):
                ok, msg = block_ip(ip)
                if not ok:
                    ui.notify(msg, type="warning")
                body.refresh()

            def do_unblock(ip):
                unblock_ip(ip)
                body.refresh()

            body()
            with ui.row():
                ui.button("Обновить", on_click=body.refresh).props("flat")
                ui.button("Закрыть", on_click=dlg.close).props("flat")
        dlg.open()

    btn = ui.button("УСТРОЙСТВА: 0", on_click=show) \
        .style(f"background:{border}!important;color:{txt};font-weight:700;border-radius:10px")

    def upd():
        n = len(viewers())
        btn.set_text(f"УСТРОЙСТВА: {n}" + (f" / ЗАБЛ.: {len(_blocked)}" if _blocked else ""))
    upd()
    ui.timer(2.0, upd)
    return btn

def register(css, port, state, trend_f, trend_i, events, link_html, hb_html, C,
             pending_cmds, slider_edit, log, max_hz, pin_file):
    global _log, _C, _block_path
    from nicegui import app, ui
    from starlette.requests import Request
    from starlette.responses import PlainTextResponse

    _log, _C = log, C
    _block_path = Path(pin_file).resolve().parent / "blocked_ips.json"
    load_blocked()
    pin = load_pin(pin_file)

    async def _sweeper():
        while True:
            await asyncio.sleep(2)
            try:
                sweep()
            except Exception as e:
                print("sweep:", repr(e))
    app.on_startup(lambda: asyncio.create_task(_sweeper()))

    @app.middleware("http")
    async def view_only_guard(request, call_next):
        host = request.client.host if request.client else ""
        p = request.url.path
        if host in _blocked and not is_local(host):
            return PlainTextResponse("Доступ отключён администратором", status_code=403)
        if is_local(host) or p == "/m" or p.startswith("/m/") or p.startswith("/_nicegui") \
                or p.startswith("/static") or p == "/favicon.ico":
            return await call_next(request)
        return PlainTextResponse("Доступна только страница /m", status_code=403)

    print("\nТелефон (телефон и ПК в одной сети Wi-Fi):")
    for name, url in lan_urls(port):
        print(f"  {name}: {url}")
    print("  Управление с телефона:", "включено (PIN из phone_pin.txt)" if pin
          else "ВЫКЛЮЧЕНО - создай opc_client\\phone_pin.txt с PIN (минимум 4 символа)")
    print(f"  Заблокированных адресов: {len(_blocked)}\n")

    def card_html(label, value, color, unit=""):
        u = f'<span class="unit">{unit}</span>' if unit else ""
        return (f'<div class="cap">{label}</div>'
                f'<div style="margin-top:6px"><span class="m-mid" style="color:{color}">{value}</span>{u}</div>')

    @ui.page("/m")
    def mobile(request: Request):
        ip = request.client.host if request.client else "?"
        ua = request.headers.get("user-agent", "")
        cid = ui.context.client.id
        dev = device_name(ua)
        ui.add_css(css + MOBILE_CSS)
        ctl = {"until": 0.0}
        edit = {"v": None, "t": 0.0}

        def unlocked():
            return time.time() < ctl["until"]

        def touch():
            ctl["until"] = time.time() + UNLOCK_SEC

        def send(name, text, *arg):
            if ip in _blocked:
                return
            if name != "stop" or STOP_NEEDS_PIN:
                if not unlocked():
                    ui.notify("Сначала введи PIN", type="warning"); return
                touch()
            if not state["link"]:
                ui.notify("Нет связи с ПЛК", type="negative"); return
            log(f"Телефон ({ip}): {text}", C["AMBER"])
            pending_cmds.append((name, *arg))

        now0 = time.time()
        if not any(w["ip"] == ip and now0 - w["seen"] <= ONLINE_SEC for w in _viewers.values()):
            log(f"Подключено устройство: {ip} ({dev})", C["BLUE"])
        _viewers[cid] = {"ip": ip, "device": dev, "since": now0, "seen": now0, "unlocked": False}
        try:
            ui.context.client.on_disconnect(lambda: _viewers.get(cid, {}).update(seen=0))
        except Exception:
            pass

        with ui.element("div").classes("m-wrap"):
            with ui.element("div").classes("card").style("display:flex;justify-content:space-between;align-items:center"):
                ui.label("ПРИВОД").style("font-size:20px;font-weight:700")
                with ui.column().style("gap:2px;align-items:flex-end"):
                    opc_lbl = ui.html("").style("font-size:13px")
                    hb_lbl = ui.html("").style("font-size:13px")
            badge = ui.label("").classes("m-badge")
            with ui.element("div").classes("card"):
                ui.label("ЧАСТОТА").classes("cap")
                freq_lbl = ui.html("").style("margin-top:6px")
                sp_lbl = ui.html("").style("margin-top:8px")

            stop_btn = ui.button("СТОП", on_click=lambda: send("stop", "стоп")).classes("m-btn") \
                .style(f"background:{C['RED']}!important;color:#fff")

            lock_card = ui.element("div").classes("card")
            with lock_card:
                ui.label("УПРАВЛЕНИЕ ЗАБЛОКИРОВАНО").classes("cap")
                if pin:
                    pin_in = ui.input(label="PIN", password=True, password_toggle_button=True) \
                        .props("outlined dark dense").style("width:100%;margin-top:8px")

                    def do_unlock():
                        ok, msg = check_pin(ip, pin_in.value or "", pin)
                        pin_in.set_value("")
                        if ok:
                            touch(); log(f"Телефон ({ip}): управление разблокировано", C["AMBER"])
                        else:
                            ui.notify(msg, type="negative")
                    pin_in.on("keydown.enter", do_unlock)
                    ui.button("РАЗБЛОКИРОВАТЬ", on_click=do_unlock).classes("m-btn") \
                        .style(f"background:{C['BORDER']}!important;color:{C['TXT']};margin-top:10px")
                else:
                    ui.label("Управление с телефона выключено: на ПК не задан PIN (файл phone_pin.txt).") \
                        .classes("cap").style("margin-top:8px")

            ctl_card = ui.element("div").classes("card")
            with ctl_card:
                with ui.row().style("justify-content:space-between;width:100%;align-items:center"):
                    ui.label("УПРАВЛЕНИЕ").classes("cap")
                    left_lbl = ui.label("").classes("cap")
                with ui.element("div").classes("m-grid").style("margin-top:10px"):
                    ui.button("ПУСК", on_click=lambda: send("start", "пуск")).classes("m-btn") \
                        .style(f"background:{C['GREEN']}!important;color:#0b0f19")
                    ui.button("РЕВЕРС", on_click=lambda: send("reverse", "реверс")).classes("m-btn") \
                        .style(f"background:{C['BORDER']}!important;color:{C['TXT']}")
                    ui.button("СБРОС", on_click=lambda: send("reset", "сброс аварии")).classes("m-btn") \
                        .style(f"background:{C['BORDER']}!important;color:{C['TXT']}")
                    ui.button("ЗАБЛОКИРОВАТЬ", on_click=lambda: ctl.update(until=0.0)).classes("m-btn") \
                        .props("flat").style(f"color:{C['DIM']}")
                ui.label(f"Уставка, Гц (0–{max_hz:g})").classes("cap").style("margin-top:14px")

                def on_slider(e):
                    if not unlocked() or abs(e.value - state["setpoint"]) < 0.05:
                        return
                    touch()
                    edit["v"], edit["t"] = round(e.value, 1), time.time()
                slider = ui.slider(min=0, max=max_hz, step=0.1, value=round(state["setpoint"], 1),
                                   on_change=on_slider).props("color=amber label-always").style("margin-top:30px")

            def flush_slider():
                if edit["v"] is not None and time.time() - edit["t"] > SLIDER_PAUSE:
                    hz, edit["v"] = edit["v"], None
                    edit["t"] = time.time()
                    send("freq", f"изменение уставки {hz:.1f} Гц", hz)

            with ui.element("div").classes("m-grid"):
                cur_box = ui.html("").classes("card")
                vol_box = ui.html("").classes("card")
                alm_box = ui.html("").classes("card")
                tm_box = ui.html("").classes("card")
            with ui.element("div").classes("card"):
                ui.label("ТРЕНД (2 мин)").classes("cap")
                trend = ui.echart({
                    "animation": False, "legend": {"textStyle": {"color": C["TXT"]}, "left": 0, "top": 0},
                    "grid": {"left": 36, "right": 10, "top": 34, "bottom": 22},
                    "xAxis": {"type": "category", "data": [], "axisLabel": {"color": C["DIM"]}},
                    "yAxis": {"type": "value", "min": 0, "axisLabel": {"color": C["DIM"]},
                              "splitLine": {"lineStyle": {"color": C["BORDER"]}}},
                    "series": [
                        {"name": "Гц", "type": "line", "showSymbol": False, "data": [],
                         "lineStyle": {"color": C["BLUE"], "width": 3}, "itemStyle": {"color": C["BLUE"]}},
                        {"name": "А", "type": "line", "showSymbol": False, "data": [],
                         "lineStyle": {"color": C["PURPLE"], "width": 3}, "itemStyle": {"color": C["PURPLE"]}}],
                }).style("height:170px;width:100%")
            with ui.element("div").classes("card"):
                ui.label("СОБЫТИЯ").classes("cap")
                log_box = ui.html("").style("margin-top:6px")

        last_sp = {"v": None}

        def refresh():
            s = state
            if ip in _blocked:
                ui.run_javascript("location.reload()")
                return
            u = unlocked()
            v = _viewers.get(cid)
            if v is None:
                v = _viewers[cid] = {"ip": ip, "device": dev, "since": time.time(), "seen": 0, "unlocked": False}
            v["seen"], v["unlocked"] = time.time(), u
            running = s["mode"] in ("ВПЕРЁД", "РЕВЕРС") or s["freq"] > 0.05
            if u and running:
                touch()
            lock_card.set_visibility(not u)
            ctl_card.set_visibility(u)
            stop_btn.set_visibility(u or not STOP_NEEDS_PIN)
            if u:
                left_lbl.set_text("автоблокировка отключена, пока двигатель работает" if running
                                  else f"автоблокировка через {int(ctl['until'] - time.time())} с")
                idle = edit["v"] is None and time.time() - edit["t"] > 3
                if s["link"] and idle and last_sp["v"] != s["setpoint"]:
                    slider.value = round(s["setpoint"], 1); last_sp["v"] = s["setpoint"]
            opc_lbl.set_content(link_html()); hb_lbl.set_content(hb_html())
            if not s["link"]:
                badge.set_text("НЕТ СВЯЗИ"); badge.style(f"background:#2a1519;color:{C['RED']};border:1px solid {C['RED']}")
            elif s["mode"] == "ВПЕРЁД":
                badge.set_text("ВПЕРЁД"); badge.style(f"background:#14301f;color:{C['GREEN']};border:1px solid {C['GREEN']}")
            elif s["mode"] == "СТОП":
                badge.set_text("СТОП"); badge.style(f"background:#2a1519;color:{C['RED']};border:1px solid {C['RED']}")
            else:
                badge.set_text(s["mode"]); badge.style(f"background:#2a2415;color:{C['AMBER']};border:1px solid {C['AMBER']}")
            freq_lbl.set_content(f'<span class="m-big">{s["freq"]:.1f}</span><span class="unit">Гц</span>')
            sp_lbl.set_content(f'<span class="cap">Уставка</span> '
                               f'<span style="color:{C["AMBER"]};font-weight:700;font-size:20px">{s["setpoint"]:.1f} Гц</span>')
            cur_box.set_content(card_html("ТОК", f'{s["current"]:.2f}', C["PURPLE"], "А"))
            vol_box.set_content(card_html("НАПРЯЖЕНИЕ", f'{s["voltage"]:.1f}', C["BLUE"], "В"))
            a = s["alarm"]
            alm_box.set_content(card_html("АВАРИЯ", "нет" if a == 0 else f"код {a}",
                                          C["GREEN"] if a == 0 else C["RED"]))
            acc = "—" if s["acc"] is None else f'{s["acc"]:.1f}'
            dec = "—" if s["dec"] is None else f'{s["dec"]:.1f}'
            tm_box.set_content(f'<div class="cap">РАЗГОН / ТОРМОЖ., с</div><div style="margin-top:6px">'
                               f'<span class="m-mid" style="color:{C["AMBER"]}">{acc} / {dec}</span></div>')
            trend.options["xAxis"]["data"] = list(range(-len(trend_f) + 1, 1))
            trend.options["series"][0]["data"] = list(trend_f)
            trend.options["series"][1]["data"] = list(trend_i)
            trend.update()
            log_box.set_content("".join(
                f'<div style="padding:6px 0;font-size:14px"><span class="cap" style="margin-right:8px">{t}</span>'
                f'<span class="dot" style="background:{col}"></span>{m}</div>' for t, m, col in events))
        refresh()
        ui.timer(1.0, refresh)
        ui.timer(0.3, flush_slider)
