import os
import psutil


class ProcessMonitor:
    def __init__(self):
        self.processes = {}

    def sample(self):
        result, current = [], {}
        for process in psutil.process_iter():
            try:
                identity = (process.pid, process.create_time())
                process = self.processes.get(identity, process)
                current[identity] = process
                with process.oneshot():
                    result.append({"pid": process.pid, "user": process.username(),
                                   "cpu": round(process.cpu_percent(), 1), "ram": process.memory_info().rss,
                                   "command": " ".join(process.cmdline()) or process.name(),
                                   "created": identity[1], "owned": process.uids().real == os.getuid() and process.pid != os.getpid()})
            except (psutil.Error, OSError):
                continue
        self.processes = current
        return result


def terminate(pid, created):
    if type(pid) is not int or pid <= 1 or pid == os.getpid():
        raise ValueError("Invalid process ID.")
    process = psutil.Process(pid)
    if process.uids().real != os.getuid() or process.uids().effective != os.getuid():
        raise PermissionError("Only your own processes may be terminated. Use the terminal for administrative tasks.")
    if abs(process.create_time() - float(created)) > 0.001:
        raise ValueError("The process identity changed. Refresh the list.")
    process.terminate()
