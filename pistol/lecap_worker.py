"""Internal detached worker. Run only through lecap_runtime.control()."""
from __future__ import annotations

import argparse
import logging
from logging.handlers import RotatingFileHandler
import time
import uuid

import psutil

from .config import PistolError
from . import lecap_runtime as runtime


def _parent_alive(launch):
    try:
        return abs(psutil.Process(launch["parent_pid"]).create_time() - launch["parent_created"]) < .001
    except (psutil.Error, KeyError, TypeError):
        return False


def run(instance: str, http_port=80, https_port=443) -> int:
    if uuid.UUID(instance).hex != instance:
        raise PistolError("Invalid LECAP worker instance.")
    launch = runtime.read_record("launch.json")
    if launch.get("instance") != instance or not _parent_alive(launch):
        raise PistolError("LECAP worker requires an active launch request.")
    with runtime.lock("worker.lock", timeout=0):
        logger = logging.getLogger("pistol.lecap.worker")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        runtime.log_path().parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(runtime.log_path(), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        record = {**runtime.identity(instance, http_port, https_port), "status": "starting"}
        runtime.write_record("state.json", record)
        proxy = None
        reason = "Stopped by request"
        needs_restart = False
        failed = False
        try:
            from .lecap import routes
            from .lecap_proxy import Proxy, fingerprint
            accesses = routes()
            if not accesses:
                raise PistolError("No enabled proxy routes")
            proxy = Proxy(accesses, http_port=http_port, https_port=https_port)
            proxy.start()
            listeners = [{"address": server.server_address[0], "port": server.server_port,
                          "https": server.tls_context is not None} for server in proxy.servers]
            record.update(status="ready", listeners=listeners,
                          http_port=next(s["port"] for s in listeners if not s["https"]),
                          https_port=next((s["port"] for s in listeners if s["https"]), None),
                          urls=[url for access in accesses for url in access.urls],
                          signatures=runtime.signatures(accesses),
                          routes=[{"name": access.domains[0], "fingerprint": fingerprint(access)} for access in accesses])
            runtime.write_record("state.json", record)
            logger.info("LECAP ready PID %s; listeners %s", record["pid"], listeners)
            acknowledgment_deadline = time.monotonic() + 20
            acknowledged = False
            last_refresh = last_config = time.monotonic()
            while True:
                if runtime.read_record("stop.json").get("instance") == instance:
                    break
                if not acknowledged:
                    acknowledged = runtime.read_record("ack.json").get("instance") == instance
                    if not acknowledged and (not _parent_alive(launch) or time.monotonic() > acknowledgment_deadline):
                        reason = "Launcher exited or did not acknowledge readiness"
                        break
                now = time.monotonic()
                if now - last_config >= 1:
                    if runtime.signatures(routes()) != record["signatures"]:
                        reason, needs_restart = "Configuration changed; restart required with pistol lecap --serve start", True
                        break
                    last_config = now
                if now - last_refresh >= 3600:
                    proxy.refresh_certificates()
                    last_refresh = now
                time.sleep(.05)
        except Exception as exc:
            reason, failed = str(exc), True
            logger.exception("LECAP worker failed: %s", exc)
        finally:
            if proxy:
                proxy.close()
            record.update(status="failed" if failed else "stopped", reason=reason, needs_restart=needs_restart)
            runtime.write_record("state.json", record)
            logger.info("LECAP stopped: %s", reason)
            handler.close()
            logger.removeHandler(handler)
        return 2 if failed else 0


def main():
    parser = argparse.ArgumentParser(description="Internal Pistol LECAP worker", allow_abbrev=False)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--http-port", type=int, default=80)
    parser.add_argument("--https-port", type=int, default=443)
    args = parser.parse_args()
    return run(args.instance, args.http_port, args.https_port)


if __name__ == "__main__":
    raise SystemExit(main())
