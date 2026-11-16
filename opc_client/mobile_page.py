"""mobile_page.py - страница /m: просмотр и управление с телефона (управление - по PIN-коду).
Остановка (СТОП) доступна всегда. ПУСК/РЕВЕРС/СБРОС/уставка - только после ввода PIN из файла phone_pin.txt.
С любых адресов, кроме самого ПК, открывается только /m (остальные страницы возвращают 403).
"""
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
STOP_NEEDS_PIN = True        # True: и СТОП с телефона только после ввода PIN

_fails = {}                   # ip -> (число ошибок, время, до которого заблокирован)

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
    """(успех, сообщение). Ограничивает перебор: MAX_FAILS ошибок -> блокировка адреса на LOCK_SEC секунд."""
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

def register(css, port, state, trend_f, trend_i, events, link_html, hb_html, C,
             pending_cmds, slider_edit, log, max_hz, pin_file):
    from nicegui import app, ui
    from starlette.requests import Request
    from starlette.responses import PlainTextResponse

    pin = load_pin(pin_file)

    @app.middleware("http")
    async def view_only_guard(request, call_next):
        host = request.client.host if request.client else ""
        p = request.url.path
        if is_local(host) or p == "/m" or p.startswith("/m/") or p.startswith("/_nicegui") \
                or p.startswith("/static") or p == "/favicon.ico":
            return await call_next(request)
        return PlainTextResponse("Доступна только страница /m", status_code=403)

    print("\nТелефон (телефон и ПК в одной сети Wi-Fi):")
    for name, url in lan_urls(port):
        print(f"  {name}: {url}")
    print("  Управление с телефона:", "включено (PIN из phone_pin.txt)" if pin
          else "ВЫКЛЮЧЕНО - создай opc_client\\phone_pin.txt с PIN (минимум 4 символа)")
    print()

    def card_html(label, value, color, unit=""):
        u = f'<span class="unit">{unit}</span>' if unit else ""
        return (f'<div class="cap">{label}</div>'
                f'<div style="margin-top:6px"><span class="m-mid" style="color:{color}">{value}</span>{u}</div>')

    @ui.page("/m")
    def mobile(request: Request):
        ip = request.client.host if request.client else "?"
        ui.add_css(css + MOBILE_CSS)
        ctl = {"until": 0.0}                     # время окончания разблокировки (на этой странице)

        def unlocked():
            return time.time() < ctl["until"]

        def touch():
            ctl["until"] = time.time() + UNLOCK_SEC

        def send(name, text, *arg):
            if name != "stop" or STOP_NEEDS_PIN:
                if not unlocked():
                    ui.notify("Сначала введи PIN", type="warning"); return
                touch()
            if not state["link"]:
                ui.notify("Нет связи с ПЛК", type="negative"); return
            log(f"Телефон ({ip}): {text}", C["AMBER"])
            pending_cmds.append((name, *arg))

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

            ui.button("СТОП", on_click=lambda: send("stop", "стоп")).classes("m-btn") \
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
                    if abs(e.value - state["setpoint"]) < 0.05:
                        return
                    if not unlocked():
                        return
                    touch()
                    slider_edit["value"], slider_edit["t"] = round(e.value, 1), time.time()
                slider = ui.slider(min=0, max=max_hz, step=0.1, value=round(state["setpoint"], 1),
                                   on_change=on_slider).props("color=amber label-always").style("margin-top:30px")

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
            u = unlocked()
            lock_card.set_visibility(not u)
            ctl_card.set_visibility(u)
            if u:
                left_lbl.set_text(f"автоблокировка через {int(ctl['until'] - time.time())} с")
                if s["link"] and slider_edit["value"] is None and last_sp["v"] != s["setpoint"]:
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
