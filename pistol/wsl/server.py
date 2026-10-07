"""Authenticated, loopback-only aiohttp agent running as the Linux user."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path
import secrets
import signal
import socket

from aiohttp import WSMsgType, web
import psutil

from ..config import PistolError, atomic_json
from . import chambers, files, ports, processes, services, system
from .bootstrap import read, ticks

CONFIG = web.AppKey("config", dict)
SOCKETS = web.AppKey("sockets", set)
METRICS = web.AppKey("metrics", dict)
MONITOR = web.AppKey("monitor", processes.ProcessMonitor)
STOP = web.AppKey("stop", asyncio.Event)
STOPPING = web.AppKey("stopping", asyncio.Event)
WEB = Path(__file__).with_name("web")


def authorized(token, expected):
    return isinstance(token, str) and bool(token) and secrets.compare_digest(token.encode(), expected.encode())


def origins(config):
    return {f"http://localhost:{config['port']}", f"http://127.0.0.1:{config['port']}"}


@web.middleware
async def security(request, handler):
    config = request.app[CONFIG]
    origin = request.headers.get("Origin")
    if "http://" + request.host not in origins(config):
        raise web.HTTPForbidden(text="Invalid localhost Host header.")
    if origin is not None and origin not in origins(config):
        raise web.HTTPForbidden(text="Cross-origin requests are not allowed.")
    if request.path == "/ws":
        if origin not in origins(config):
            raise web.HTTPForbidden(text="A same-origin WebSocket is required.")
    elif request.path.startswith("/api/"):
        header = request.headers.get("Authorization", "")
        if not authorized(header.removeprefix("Bearer ") if header.startswith("Bearer ") else "", config["token"]):
            raise web.HTTPUnauthorized(text="Reopen this console with pistol wsl gui to authenticate.")
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except (FileExistsError,):
        return web.json_response({"error": "File exists or changed since opening. Choose another name or reopen it."}, status=409)
    except (PermissionError, psutil.AccessDenied) as exc:
        return web.json_response({"error": str(exc) or "Linux permission denied."}, status=403)
    except (FileNotFoundError, psutil.NoSuchProcess):
        return web.json_response({"error": "File or process no longer exists. Refresh and retry."}, status=404)
    except (OSError, ValueError, TypeError, KeyError, PistolError, asyncio.TimeoutError) as exc:
        return web.json_response({"error": str(exc) or "Operation timed out; check the distro and try again."}, status=400)


async def headers(request, response):
    response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                             "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
                             "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                             "connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"})


async def static(request):
    name = request.match_info.get("asset", "index.html")
    allowed = {"index.html", "app.js", "styles.css", "vendor/xterm.js", "vendor/xterm.css", "vendor/addon-fit.js"}
    if name not in allowed:
        raise web.HTTPNotFound()
    return web.FileResponse(WEB / name)


async def api(request):
    section = request.match_info["section"]
    config = request.app[CONFIG]
    if request.method == "GET":
        if section == "health":
            return web.json_response({"instance": config["instance"], "pid": os.getpid()})
        if section == "system":
            return web.json_response(request.app[METRICS]["system"])
        if section == "processes":
            return web.json_response(request.app[METRICS]["processes"])
        if section == "ports":
            return web.json_response(await ports.listing())
        if section == "services":
            return web.json_response(await services.listing(request.query.get("scope", "system")))
        if section == "chambers":
            return web.json_response(await asyncio.to_thread(chambers.listing, config))
        if section in {"files", "text"}:
            path = request.query.get("path", str(Path.home()))
            return web.json_response(await asyncio.to_thread(files.listing if section == "files" else files.read_text, path))
        if section == "download":
            path = files.validate_path(request.query["path"])
            with files.open_regular(path) as fd:
                from urllib.parse import quote
                response = web.StreamResponse(headers={"Content-Type": "application/octet-stream",
                    "Content-Disposition": "attachment; filename*=UTF-8''" + quote(Path(path).name, safe="")})
                await response.prepare(request)
                while chunk := await asyncio.to_thread(os.read, fd, 65536):
                    await response.write(chunk)
                await response.write_eof()
                return response
    elif request.method == "POST":
        if section == "upload":
            data = await request.read()
            await asyncio.to_thread(files.create, request.query["path"], data)
            return web.json_response({"ok": True})
        value = await request.json()
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object.")
        if section == "shutdown":
            if value.get("instance") != config["instance"]:
                raise ValueError("Console identity changed; refresh its status.")
            request.app[STOPPING].set()
            await shutdown(request.app)
            async with asyncio.timeout(5):
                while request.app[SOCKETS]:
                    await asyncio.sleep(0.05)
            asyncio.get_running_loop().call_later(0.1, request.app[STOP].set)
            return web.json_response({"instance": config["instance"]})
        elif section == "files":
            operation = value["action"]
            path = value["path"]
            if operation == "create":
                await asyncio.to_thread(files.create, path, b"", value.get("directory") is True)
            elif operation == "rename":
                await asyncio.to_thread(files.rename, path, value["destination"])
            elif operation == "delete":
                await asyncio.to_thread(files.delete, path, value.get("confirmed"))
            elif operation == "save":
                return web.json_response(await asyncio.to_thread(files.save_text, path, value["text"], value["revision"], value.get("confirmed")))
            else:
                raise ValueError("Unknown file action.")
        elif section == "processes":
            if value.get("confirmed") is not True:
                raise ValueError("Confirm process termination.")
            await asyncio.to_thread(processes.terminate, value["pid"], value["created"])
        elif section == "services":
            if value.get("confirmed") is not True:
                raise ValueError("Confirm service action.")
            await services.action(value["service"], value["action"], value.get("scope", "system"))
        else:
            raise web.HTTPNotFound()
        return web.json_response({"ok": True})
    raise web.HTTPNotFound()


async def terminal(request):
    from .terminal import TerminalSession
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=65536, compress=False)
    await ws.prepare(request)
    session = None
    tasks = []
    try:
        auth = await ws.receive_json(timeout=5)
        if not isinstance(auth, dict) or not authorized(auth.get("token"), request.app[CONFIG]["token"]):
            await ws.close(code=1008, message=b"Authentication required")
            return ws
        if request.app[STOPPING].is_set() or len(request.app[SOCKETS]) >= 12:
            await ws.close(code=1013, message=b"Terminal session limit reached")
            return ws
        request.app[SOCKETS].add(ws)
        session = TerminalSession()
        session.resize(auth.get("rows", 24), auth.get("cols", 80))

        async def output():
            while data := await session.read():
                await ws.send_bytes(data)
            await ws.send_json({"type": "exit", "message": "Shell exited. Start a new terminal to reconnect."})

        async def inputs():
            async for message in ws:
                if message.type == WSMsgType.BINARY:
                    await session.write(message.data)
                elif message.type == WSMsgType.TEXT:
                    value = json.loads(message.data)
                    if not isinstance(value, dict):
                        raise ValueError("Invalid terminal message.")
                    if value.get("type") == "resize":
                        session.resize(value.get("rows"), value.get("cols"))
                    elif value.get("type") == "input" and isinstance(value.get("data"), str):
                        await session.write(value["data"].encode())
                    else:
                        raise ValueError("Unknown terminal message.")
        tasks = [asyncio.create_task(output()), asyncio.create_task(inputs())]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except (ValueError, TypeError, OSError, asyncio.TimeoutError, ConnectionError):
        await ws.close(code=1008, message=b"Terminal disconnected or invalid message")
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if session:
            await session.close()
        request.app[SOCKETS].discard(ws)
        await ws.close()
    return ws


async def metrics_context(app):
    app[METRICS].update(system=system.snapshot(app[CONFIG]), processes=app[MONITOR].sample())
    async def sample():
        while True:
            await asyncio.sleep(2)
            app[METRICS]["system"] = system.snapshot(app[CONFIG])
            app[METRICS]["processes"] = await asyncio.to_thread(app[MONITOR].sample)
    task = asyncio.create_task(sample())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def shutdown(app):
    await asyncio.gather(*(ws.close(code=1001, message=b"Console stopping") for ws in list(app[SOCKETS])))


def create_app(config):
    app = web.Application(middlewares=[security], client_max_size=files.MAX_UPLOAD)
    app[CONFIG], app[SOCKETS], app[METRICS], app[MONITOR] = config, set(), {}, processes.ProcessMonitor()
    app[STOP] = asyncio.Event()
    app[STOPPING] = asyncio.Event()
    app.on_response_prepare.append(headers)
    app.on_shutdown.append(shutdown)
    app.cleanup_ctx.append(metrics_context)
    app.router.add_get("/", static)
    app.router.add_get("/assets/{asset:.*}", static)
    app.router.add_get("/ws", terminal)
    app.router.add_route("GET", "/api/{section}", api)
    app.router.add_route("POST", "/api/{section}", api)
    return app


async def serve(config):
    if os.getuid() == 0:
        raise RuntimeError("Run the console as a normal Linux user.")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        for port in config["ports"]:
            try:
                sock.bind(("127.0.0.1", port))
                break
            except OSError:
                continue
        else:
            raise RuntimeError("All candidate loopback ports are occupied. Retry pistol wsl gui.")
        sock.setblocking(False)
        config["port"] = sock.getsockname()[1]
        app = create_app(config)
        runner = web.AppRunner(app, access_log=None, shutdown_timeout=5)
        await runner.setup()
        try:
            await web.SockSite(runner, sock).start()
            state_path = Path(config["state_path"])
            for _ in range(100):
                if read(state_path).get("instance") == config["instance"]:
                    break
                await asyncio.sleep(0.05)
            else:
                raise RuntimeError("Bootstrap did not publish process state.")
            state = {key: config[key] for key in ("distro", "version", "instance", "token", "port")}
            state.update(pid=os.getpid(), start_ticks=ticks(os.getpid()))
            atomic_json(state_path, state)
            stopped = app[STOP]
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, stopped.set)
            await stopped.wait()
        finally:
            await runner.cleanup()
    finally:
        sock.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--instance", required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if config["instance"] != args.instance:
        raise ValueError("Agent instance mismatch.")
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(serve(config))


if __name__ == "__main__":
    main()
