"""Byte-preserving TCP relays with destinations chosen by trusted startup code."""
import asyncio
from contextlib import suppress


async def listen(host, port, target_host, target_port):
    async def forward(reader, writer):
        remote = None
        try:
            upstream, remote = await asyncio.wait_for(
                asyncio.open_connection(target_host, target_port), timeout=10)

            async def pump(source, destination):
                while data := await source.read(65536):
                    destination.write(data)
                    await destination.drain()
                if destination.can_write_eof():
                    destination.write_eof()

            await asyncio.gather(pump(reader, remote), pump(upstream, writer))
        except (OSError, TimeoutError, ConnectionError):
            pass
        finally:
            for connection in (remote, writer):
                if connection:
                    connection.close()
                    with suppress(OSError):
                        await connection.wait_closed()

    return await asyncio.start_server(forward, host, port)


async def bridge():
    async with await listen('0.0.0.0', 8787, 'moyai', 8787), \
            await listen('0.0.0.0', 4000, 'litellm', 4000):
        await asyncio.Event().wait()


if __name__ == '__main__':
    asyncio.run(bridge())
