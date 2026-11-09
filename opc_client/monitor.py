import asyncio, struct
from asyncua import Client

URL = "opc.tcp://192.168.1.2:4840"   # IP твоего ПЛК
HOLDING = "ns=4;i=2"

def to_float(hi, lo):
    return struct.unpack(">f", struct.pack(">HH", hi, lo))[0]

async def main():
    async with Client(url=URL) as client:
        node = client.get_node(HOLDING)
        while True:
            r = await node.read_value()
            cmd = r[0]
            state = "СТОП" if cmd & 0x100 else "ВПЕРЁД" if cmd & 1 else "РЕВЕРС" if cmd & 2 else "?"
            print(
                f"{state:7} | уст {to_float(r[1], r[2]):6.2f} Гц | "
                f"факт {to_float(r[3], r[4]):6.2f} Гц | "
                f"ток {to_float(r[6], r[7]):5.2f} А | "
                f"U {to_float(r[8], r[9]):6.1f} В | авария {r[11]}"
            )
            await asyncio.sleep(1)

asyncio.run(main())