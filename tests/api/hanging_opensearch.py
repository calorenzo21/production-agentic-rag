"""An OpenSearch that hangs, at the network's edge, for the API tests of what the API does while it waits for OpenSearch."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit


class HangingOpenSearch:
    """
    OpenSearch at a host where it refuses every connection, as if it were down, until it hangs: it then accepts
    connections, but never answers a request.
    """

    def __init__(self, host: str) -> None:
        address = urlsplit(host)
        self._hostname = address.hostname
        self._port = address.port

    @asynccontextmanager
    async def hanging(self) -> AsyncIterator[asyncio.Event]:
        """
        Hang until the block ends, then drop every connection, and refuse new ones again, so that whatever waits for an
        answer fails at once. The event is set once OpenSearch is asked a request.
        """
        asked = asyncio.Event()
        connections: list[asyncio.StreamWriter] = []

        async def never_answer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            connections.append(writer)
            try:
                await reader.readline()
                asked.set()
                # Until the connection is dropped, at either end.
                await reader.read()
            finally:
                # Also when the client gave up first, as a connection accepted after the block ended is dropped here alone.
                writer.close()

        server = await asyncio.start_server(never_answer, self._hostname, self._port)
        try:
            yield asked
        finally:
            server.close()
            for writer in connections:
                writer.close()
            # Until every connection is dropped.
            await server.wait_closed()
