"""One real controlling Linux PTY per authenticated WebSocket."""
import asyncio
import errno
import fcntl
import os
import pty
import pwd
import signal
import struct
import subprocess
import sys
import termios

import psutil


class TerminalSession:
    def __init__(self):
        self.master, slave = pty.openpty()
        try:
            self.process = subprocess.Popen([sys.executable, "-m", "pistol.wsl.terminal", "--child"],
                                            stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
                                            cwd=os.path.expanduser("~"))
        except BaseException:
            os.close(self.master)
            raise
        finally:
            os.close(slave)
        os.set_blocking(self.master, False)
        self.closed = False

    def resize(self, rows, cols):
        if type(rows) is not int or type(cols) is not int or not 1 <= rows <= 500 or not 1 <= cols <= 1000:
            raise ValueError("Invalid terminal dimensions.")
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    async def _ready(self, write=False):
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        def ready():
            if not future.done():
                future.set_result(None)
        add = loop.add_writer if write else loop.add_reader
        remove = loop.remove_writer if write else loop.remove_reader
        add(self.master, ready)
        try:
            await future
        finally:
            remove(self.master)

    async def read(self):
        while True:
            try:
                return os.read(self.master, 16384)
            except BlockingIOError:
                await self._ready()
            except OSError as exc:
                if exc.errno == errno.EIO:
                    return b""
                raise

    async def write(self, data):
        view = memoryview(data)
        while view:
            try:
                view = view[os.write(self.master, view):]
            except BlockingIOError:
                await self._ready(write=True)

    async def close(self):
        if self.closed:
            return
        self.closed = True
        # Foreground programs have their own process groups within this PTY session.
        # Snapshot identities before reaping the shell; never terminate an unrelated PID.
        owned = []
        for process in psutil.process_iter():
            try:
                if os.getsid(process.pid) == self.process.pid:
                    process.create_time()
                    owned.append(process)
            except (ProcessLookupError, PermissionError, psutil.Error):
                continue
        for process in owned:
            try:
                process.send_signal(signal.SIGHUP)
            except psutil.Error:
                pass
        os.close(self.master)
        await asyncio.sleep(0.15)
        for process in owned:
            try:
                if process.is_running():
                    process.kill()
            except psutil.Error:
                pass
        await asyncio.to_thread(self.process.wait, 3)


def child():
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    user = pwd.getpwuid(os.getuid())
    shell = user.pw_shell or "/bin/sh"
    environment = os.environ.copy()
    environment.update(TERM="xterm-256color", COLORTERM="truecolor", HOME=user.pw_dir,
                       USER=user.pw_name, LOGNAME=user.pw_name, SHELL=shell)
    # The shell should use the distro's own Python and project environments.
    for key in ("PYTHONPATH", "VIRTUAL_ENV", "PYTHONHOME"):
        environment.pop(key, None)
    os.execve(shell, [shell, "-l"], environment)


if __name__ == "__main__":
    child()
