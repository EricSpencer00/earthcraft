"""Bound task-owned child IO and retain the persistent lock until child exit."""
import fcntl
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import time

REPLY_LIMIT = 64 * 2**20
ERROR_LIMIT = 4096


class ChildNotReaped(RuntimeError):
    pass


class ReadDeadline(TimeoutError):
    pass


def durable_json(path, value):
    raw = json.dumps(value, sort_keys=True).encode()
    if len(raw) > 256 * 2**10:
        raise ValueError('Exporter scheduling metadata exceeds bound')
    tmp = path.with_name(path.name + '.partial')
    with tmp.open('wb') as out:
        out.write(raw); out.flush(); os.fsync(out.fileno())
    os.replace(tmp, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def acquire_lock(control, expected_inode=None):
    path = control / 'hf-export.lock'
    flags = os.O_RDWR | os.O_NOFOLLOW
    if expected_inode is None:
        flags |= os.O_CREAT
    fd = os.open(path, flags, 0o600)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd); raise ValueError('Exporter lock must be a regular file')
    if expected_inode is not None and os.fstat(fd).st_ino != expected_inode:
        os.close(fd); raise ValueError('Persistent export lock inode changed')
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        os.close(fd); raise
    return fd


def checked_read(path, limit):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Absolute generated path required')
    for parent in [*path.parents, path]:
        if parent.is_symlink():
            raise ValueError('Generated path symlink forbidden')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError('Bounded regular generated file required')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit or len(raw) != info.st_size:
            raise ValueError('Generated read changed or exceeds bound')
        return raw
    finally:
        os.close(fd)


def stop_and_reap(child):
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        child.wait(timeout=2)
    except subprocess.TimeoutExpired:
        # Do not LOCK_UN. The surviving child retains the inherited descriptor.
        raise ChildNotReaped('Owned IO child not reaped; export lock retained by child')


def run_child(command, request, lock_fd, seconds):
    """Bound stdin/stdout/stderr/time; kill and reap before returning on timeout."""
    child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, pass_fds=(lock_fd,),
                             start_new_session=True)
    deadline = time.monotonic() + seconds
    pending = memoryview(json.dumps(request).encode())
    output, errors = bytearray(), bytearray()
    selector = selectors.DefaultSelector()
    for stream, event, label in [(child.stdin, selectors.EVENT_WRITE, 'input'),
                                 (child.stdout, selectors.EVENT_READ, 'output'),
                                 (child.stderr, selectors.EVENT_READ, 'error')]:
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, event, label)
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReadDeadline('Generated IO deadline exceeded')
            for key, _ in selector.select(min(remaining, .2)):
                stream, kind = key.fileobj, key.data
                if kind == 'input':
                    try:
                        sent = os.write(stream.fileno(), pending[:16384])
                    except BrokenPipeError:
                        sent = len(pending)
                    pending = pending[sent:]
                    if not pending:
                        selector.unregister(stream); stream.close()
                else:
                    data = os.read(stream.fileno(), 16384)
                    if not data:
                        selector.unregister(stream); stream.close(); continue
                    target, bound = (output, REPLY_LIMIT) if kind == 'output' else (errors, ERROR_LIMIT)
                    target.extend(data)
                    if len(target) > bound:
                        raise ValueError('Owned IO child protocol exceeds bound')
        child.wait(timeout=max(.001, deadline-time.monotonic()))
        if child.returncode:
            # Children emit only a structured generated-data error, never contents.
            message = json.loads(errors or b'{}').get('error', 'Generated IO child failed')
            raise ValueError(message)
        return json.loads(output)
    except subprocess.TimeoutExpired:
        stop_and_reap(child)
        raise ReadDeadline('Generated IO deadline exceeded')
    except BaseException:
        stop_and_reap(child)
        raise
    finally:
        selector.close()
        for stream in (child.stdin, child.stdout, child.stderr):
            if not stream.closed:
                stream.close()
