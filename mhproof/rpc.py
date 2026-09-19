"""Full-duplex JSON-lines RPC; a held tool never blocks the input reader."""
import asyncio
import json


class RPCError(RuntimeError):
    def __init__(self, error):
        self.code = error.get("code")
        super().__init__(json.dumps(error))


class RPC:
    def __init__(self, process, request_handler, notification_handler, jsonrpc=True, trace=None):
        self.process = process
        self.request_handler = request_handler
        self.notification_handler = notification_handler
        self.jsonrpc = jsonrpc
        self.trace = trace
        self.next_id = 0
        self.pending = {}
        self.tasks = set()
        self.dead = asyncio.Event()
        self.failure = None
        self.reader = asyncio.create_task(self.read())

    def packet(self, **fields):
        return {**({"jsonrpc": "2.0"} if self.jsonrpc else {}), **fields}

    async def write(self, packet):
        if self.dead.is_set():
            raise ConnectionError(self.failure or "Harness RPC closed")
        if self.trace:
            self.trace.record("rpc_out", packet)
        self.process.stdin.write((json.dumps(packet) + "\n").encode())
        await self.process.stdin.drain()

    async def request(self, method, params, timeout=120, on_sent=None):
        self.next_id += 1
        ident = self.next_id
        future = asyncio.get_running_loop().create_future()
        self.pending[ident] = future
        try:
            await self.write(self.packet(id=ident, method=method, params=params))
            if on_sent:
                await on_sent()
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            if self.trace:
                self.trace.record("rpc_timeout", {"id": ident, "method": method, "timeout": timeout})
            raise
        finally:
            self.pending.pop(ident, None)

    async def notify(self, method, params=None):
        await self.write(self.packet(method=method, params=params or {}))

    async def answer(self, packet):
        try:
            result = await self.request_handler(packet["method"], packet.get("params", {}))
            response = self.packet(id=packet["id"], result=result)
        except Exception as e:
            response = self.packet(id=packet["id"], error={"code": -32603, "message": str(e)})
        try:
            await self.write(response)
        except (ConnectionError, BrokenPipeError):
            pass

    async def read(self):
        try:
            while line := await self.process.stdout.readline():
                try:
                    packet = json.loads(line)
                except ValueError:
                    if self.trace:
                        self.trace.record("invalid_rpc_line", {"line": line.decode("utf-8", errors="replace")})
                    raise
                if self.trace:
                    self.trace.record("rpc_in", packet)
                if "method" in packet:
                    if "id" in packet:
                        task = asyncio.create_task(self.answer(packet))
                        self.tasks.add(task)
                        task.add_done_callback(self.tasks.discard)
                    else:
                        await self.notification_handler(packet["method"], packet.get("params", {}))
                elif packet.get("id") in self.pending:
                    future = self.pending[packet["id"]]
                    if not future.done():
                        if "error" in packet:
                            future.set_exception(RPCError(packet["error"]))
                        else:
                            future.set_result(packet.get("result", {}))
        except Exception as e:
            self.failure = str(e)
        finally:
            self.failure = self.failure or "Harness closed stdout"
            self.dead.set()
            if self.trace:
                self.trace.record("rpc_closed", {"reason": self.failure})
            for future in list(self.pending.values()):
                if not future.done():
                    future.set_exception(ConnectionError(self.failure))

    async def close(self):
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        await self.reader
