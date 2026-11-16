"""mobile_page.py - просмотр с телефона: страница /m (только чтение) и защита от управления по сети."""
from collections import deque

MOBILE_CSS = """
.m-wrap { width:100%; max-width:520px; margin:0 auto; display:flex; flex-direction:column; gap:12px; }
.m-grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; width:100%; }
.m-big { font-size:56px; font-weight:800; line-height:1.0; }
.m-mid { font-size:30px; font-weight:700; line-height:1.1; }
.m-badge { text-align:center; font-size:26px; font-weight:800; border-radius:14px; padding:14px; }
"""

def lan_urls(port):
    """Адреса ПК в локальных сетях (для ввода на телефоне)."""
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

def register(css, port, state, trend_f, trend_i, events, link_html, hb_html, C):
    from nicegui import app, ui
    from starlette.responses import PlainTextResponse

    @app.middleware("http")
    async def view_only_guard(request, call_next):
        host = request.client.host if request.client else ""
        p = request.url.path
        if is_local(host) or p == "/m" or p.startswith("/m/") or p.startswith("/_nicegui") \
                or p.startswith("/static") or p == "/favicon.ico":
            return await call_next(request)
        return PlainTextResponse("Только просмотр: открой /m", status_code=403)

    print("\nПросмотр с телефона (телефон и ПК в одной сети Wi-Fi):")
    for name, url in lan_urls(port):
        print(f"  {name}: {url}")
    print()

    def card_html(label, value, color, unit=""):
        u = f'<span class="unit">{unit}</span>' if unit else ""
        return (f'<div class="cap">{label}</div>'
                f'<div style="margin-top:6px"><span class="m-mid" style="color:{color}">{value}</span>{u}</div>')

    @ui.page("/m")
    def mobile():
        ui.add_css(css + MOBILE_CSS)
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
            ui.label("Режим просмотра. Управление доступно только на ПК.").classes("cap") \
                .style("text-align:center;padding:4px")

        def refresh():
            s = state
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
