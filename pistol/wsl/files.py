"""Linux file operations with pinned directory descriptors and no symlink traversal."""
from contextlib import contextmanager
import ctypes
import errno
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import stat

MAX_TEXT = 2 * 1024 * 1024
MAX_UPLOAD = 32 * 1024 * 1024


def validate_path(value):
    if not isinstance(value, str) or not value.startswith("/") or "\\" in value or any(ord(c) < 32 for c in value):
        raise ValueError("Use an absolute Linux path without control characters or backslashes.")
    path = PurePosixPath(value)
    if ".." in path.parts:
        raise ValueError("Parent traversal is not allowed; use the directory's absolute path.")
    return "/" + str(path).lstrip("/")


@contextmanager
def parent_fd(value):
    path = PurePosixPath(validate_path(value))
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd, path.name or "."
    finally:
        os.close(fd)


@contextmanager
def open_regular(value, flags=os.O_RDONLY):
    with parent_fd(value) as (parent, name):
        fd = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=parent)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Only regular files can be opened or edited.")
        yield fd
    finally:
        os.close(fd)


def metadata(name, info):
    return {"name": name, "type": "link" if stat.S_ISLNK(info.st_mode) else
            "directory" if stat.S_ISDIR(info.st_mode) else "file" if stat.S_ISREG(info.st_mode) else "special",
            "size": info.st_size, "modified": info.st_mtime, "mode": stat.filemode(info.st_mode),
            "uid": info.st_uid, "gid": info.st_gid}


def listing(path):
    path = validate_path(path)
    with parent_fd(path) as (parent, name):
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        entries = []
        for name in os.listdir(fd):
            try:
                entries.append(metadata(name, os.stat(name, dir_fd=fd, follow_symlinks=False)))
            except FileNotFoundError:
                continue
        return {"path": path, "parent": str(PurePosixPath(path).parent),
                "entries": sorted(entries, key=lambda x: (x["type"] != "directory", x["name"].casefold()))}
    finally:
        os.close(fd)


def read_text(path):
    with open_regular(path) as fd:
        data = os.read(fd, MAX_TEXT + 1)
        if len(data) > MAX_TEXT or b"\0" in data:
            raise ValueError("Editor supports UTF-8 text files up to 2 MiB. Download this file instead.")
        return {"text": data.decode("utf-8"), "revision": hashlib.sha256(data).hexdigest(),
                "info": metadata(PurePosixPath(path).name, os.fstat(fd))}


def write_all(fd, data):
    view = memoryview(data)
    while view:
        count = os.write(fd, view)
        view = view[count:]


def create(path, data=b"", directory=False):
    if len(data) > MAX_UPLOAD:
        raise ValueError("Uploads are limited to 32 MiB.")
    if directory:
        with parent_fd(path) as (parent, name):
            os.mkdir(name, mode=0o700, dir_fd=parent)
    else:
        with open_regular(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL) as fd:
            write_all(fd, data)


def save_text(path, text, revision, confirmed):
    if confirmed is not True:
        raise ValueError("Confirm replacing this file's contents.")
    data = text.encode("utf-8")
    if len(data) > MAX_TEXT:
        raise ValueError("Text exceeds 2 MiB.")
    with open_regular(path, os.O_RDWR) as fd:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)
        existing = os.read(fd, MAX_TEXT + 1)
        if len(existing) > MAX_TEXT or hashlib.sha256(existing).hexdigest() != revision:
            raise FileExistsError("File changed since it was opened. Reopen it before saving.")
        os.lseek(fd, 0, os.SEEK_SET)
        write_all(fd, data)
        os.ftruncate(fd, len(data))
        os.fsync(fd)
    return {"revision": hashlib.sha256(data).hexdigest()}


def protect_delete(path):
    path = PurePosixPath(validate_path(path))
    home = PurePosixPath(Path.home().as_posix())
    protected = {"/", "/home", "/mnt", "/media", "/opt", "/srv", "/tmp", "/var/tmp"}
    trees = ("/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/boot", "/dev", "/proc", "/sys", "/run", "/var", "/root")
    parts = tuple(part.casefold() for part in path.parts)
    windows_system = (len(parts) >= 4 and parts[1] == "mnt" and len(parts[2]) == 1 and
                      (parts[3] in {"windows", "program files", "program files (x86)", "programdata",
                                    "$recycle.bin", "system volume information"} or
                       (parts[3] == "users" and len(parts) <= 5)))
    if (str(path) in protected or path == home or path in home.parents or
            windows_system or
            any(path == PurePosixPath(root) or path.is_relative_to(root) for root in trees) or
            path.parent in (PurePosixPath("/mnt"), PurePosixPath("/media")) or os.path.ismount(str(path))):
        raise PermissionError("This system location is protected. Use the terminal for intentional system administration.")


def delete(path, confirmed):
    if confirmed is not True:
        raise ValueError("Deletion requires confirmation.")
    protect_delete(path)
    with parent_fd(path) as (parent, name):
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            if not shutil.rmtree.avoids_symlink_attacks:
                raise PermissionError("Safe recursive deletion is unavailable on this platform.")
            shutil.rmtree(name, dir_fd=parent)
        else:
            os.unlink(name, dir_fd=parent)


def rename(source, destination):
    protect_delete(source)
    with parent_fd(source) as (src, name), parent_fd(destination) as (dst, target):
        libc = ctypes.CDLL(None, use_errno=True)
        try:
            renameat2 = libc.renameat2
        except AttributeError:
            raise ValueError("Safe rename is unavailable on this Linux version.") from None
        renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        if renameat2(src, os.fsencode(name), dst, os.fsencode(target), 1):  # RENAME_NOREPLACE
            code = ctypes.get_errno()
            if code == errno.EEXIST:
                raise FileExistsError("Destination already exists; choose another name.")
            raise OSError(code, os.strerror(code))
