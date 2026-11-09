import asyncio, struct
from asyncua import Client, ua

URL = "opc.tcp://192.168.1.2:4840"
BASE = 3            # узел HoldingReg[0] = ns=4;i=3, [n] = i=3+n
MAX_HZ = 20.0       # программный лимит уставки

CMD_FWD, CMD_STOP, CMD_RESET = 1, 256, 512

def to_float(hi, lo):
    return struct.unpack(">f", struct.pack(">HH", hi, lo))[0]

def from_float(x):
    return struct.unpack(">HH", struct.pack(">f", x))

class Drive:
    def __init__(self, client):
        self.c = client
        self.arr = client.get_node("ns=4;i=2")

    async def write_reg(self, idx, value):
        node = self.c.get_node(f"ns=4;i={BASE + idx}")
        vt = await node.read_data_type_as_variant_type()
        await node.write_value(ua.DataValue(ua.Variant(value, vt)))

    async def regs(self):
        return await self.arr.read_value()

    async def status(self):
        r = await self.regs()
        cmd = r[0]
        st = "СТОП" if cmd & 0x100 else "ВПЕРЁД" if cmd & 1 else "РЕВЕРС" if cmd & 2 else "?"
        return (f"{st} | уст {to_float(r[1], r[2]):.2f} Гц | "
                f"факт {to_float(r[3], r[4]):.2f} Гц | "
                f"ток {to_float(r[6], r[7]):.2f} А | "
                f"U {to_float(r[8], r[9]):.1f} В | авария {r[11]}")

    async def set_freq(self, hz):
        if not 0.0 <= hz <= MAX_HZ:
            print(f"Отказ: уставка должна быть 0..{MAX_HZ} Гц")
            return
        hi, lo = from_float(hz)
        await self.write_reg(1, hi)
        await self.write_reg(2, lo)

    async def start(self):
        r = await self.regs()
        if r[11] != 0:
            print(f"Отказ: активна авария {r[11]}. Сначала сброс (команда a).")
            return
        if to_float(r[1], r[2]) > MAX_HZ:
            print("Отказ: уставка выше лимита")
            return
        await self.write_reg(0, CMD_FWD)

    async def stop(self):
        await self.write_reg(0, CMD_STOP)

    async def reset(self):
        await self.write_reg(0, CMD_RESET)

HELP = "s <Гц> уставка | 1 пуск вперёд | 0 стоп | a сброс аварии | p статус | q выход"

async def heartbeat(drive):
    n = 0
    while True:
        n = n % 65535 + 1
        await drive.write_reg(17, n)
        await asyncio.sleep(1)

async def main():
    async with Client(url=URL) as client:
        d = Drive(client)
        hb = asyncio.create_task(heartbeat(d))
        print(HELP)
        print(await d.status())
        try:
            while True:
                line = (await asyncio.to_thread(input, "> ")).strip().split()
                if not line:
                    continue
                cmd = line[0]
                if cmd == "q":
                    break
                elif cmd == "s" and len(line) == 2:
                    await d.set_freq(float(line[1].replace(",", ".")))
                elif cmd == "1":
                    await d.start()
                elif cmd == "0":
                    await d.stop()
                elif cmd == "a":
                    await d.reset()
                elif cmd != "p":
                    print(HELP)
                    continue
                await asyncio.sleep(1.5)
                print(await d.status())
        finally:
            hb.cancel()
            await d.stop()
            await d.write_reg(17, 0)
            print("Двигатель остановлен, выход")

asyncio.run(main())