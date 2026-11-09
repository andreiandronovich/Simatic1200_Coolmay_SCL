import asyncio
from asyncua import Client

URL = "opc.tcp://192.168.1.2:4840"

async def walk(node, depth=0, max_depth=3):
    for child in await node.get_children():
        name = (await child.read_browse_name()).Name
        try:
            val = await child.read_value()
            text = f"{val!r}"[:80]
        except Exception:
            text = "-"
        print("  " * depth + f"{name}  [{child.nodeid.to_string()}]  {text}")
        if depth < max_depth:
            await walk(child, depth + 1, max_depth)

async def main():
    async with Client(url=URL) as client:
        objects = client.nodes.objects
        for child in await objects.get_children():
            bn = await child.read_browse_name()
            print(f"Objects -> {bn.NamespaceIndex}:{bn.Name}  [{child.nodeid.to_string()}]")
            if bn.Name == "ServerInterfaces":
                await walk(child)

asyncio.run(main())