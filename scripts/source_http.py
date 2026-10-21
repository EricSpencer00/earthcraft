"""Polite, bounded HTTP range reads from public source servers.

Every range request to a host takes one of a fixed number of connection slots
shared by all worker processes on the machine (file locks under the cache
root), so four workers never open more than ``EARTHCRAFT_HOST_CONNECTIONS``
(default 4) connections to one publisher. Transient failures (timeouts,
resets, 429 and 5xx) back off exponentially with jitter and honour
Retry-After; a changed archive (412 on If-Match) is never retried.

A host that refuses or times out on two consecutive connections is marked down
for ``HOST_DOWN_SECONDS`` for every worker on the machine (a marker beside the
slot locks). Requests to it then fail at once with ``HostUnavailable`` (a
URLError, so the tile is deferred, not failed), instead of each tile spending
minutes in retries against a dead server: an ISGS outage made one scan tile
wait 427 of its 538 seconds.
"""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import random
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_RANGE = 16 * 2**20
DEFAULT_CONNECTIONS = 4
HOST_DOWN_SECONDS = 900


class HostUnavailable(urllib.error.URLError):
    """The host failed recent connections; skip it until its down marker expires."""


def connections():
    try:
        return max(1, min(16, int(os.environ.get('EARTHCRAFT_HOST_CONNECTIONS', DEFAULT_CONNECTIONS))))
    except ValueError:
        return DEFAULT_CONNECTIONS


def slots_root():
    configured = os.environ.get('EARTHCRAFT_HOST_SLOTS')
    if configured:
        return Path(configured)
    import regional_cache
    cache = regional_cache.active()
    return (cache.root if cache is not None else Path(tempfile.gettempdir())) / '.locks' / 'hosts'


@contextmanager
def host_slot(url, limit=None, poll=0.2):
    """Hold one of ``limit`` machine-wide connection slots for the URL's host."""
    limit = limit or connections()
    host = urllib.parse.urlsplit(url).hostname or 'unknown'
    root = slots_root()
    root.mkdir(parents=True, exist_ok=True)
    start = random.randrange(limit)
    while True:
        for offset in range(limit):
            handle = (root / f'{host}.{(start + offset) % limit}.lock').open('a+')
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                handle.close()
                continue
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
                handle.close()
            return
        time.sleep(poll)


def _down_marker(url):
    host = urllib.parse.urlsplit(url).hostname or 'unknown'
    return slots_root() / f'{host}.down', host


def check_host(url, now=time.time):
    """Raise HostUnavailable while another request marked this host down."""
    marker, host = _down_marker(url)
    try:
        until = float(marker.read_text())
    except (OSError, ValueError):
        return
    if now() < until:
        raise HostUnavailable(f'{host} is unreachable (marked down until {time.strftime("%H:%M:%SZ", time.gmtime(until))})')


def mark_host_down(url, seconds=HOST_DOWN_SECONDS, now=time.time):
    marker, _ = _down_marker(url)
    marker.parent.mkdir(parents=True, exist_ok=True)
    temporary = marker.with_name(marker.name + f'.{os.getpid()}')
    temporary.write_text(str(now() + seconds))
    temporary.replace(marker)


def connection_failure(error):
    """A failure to reach the server at all (not an HTTP status from it)."""
    if isinstance(error, urllib.error.HTTPError):
        return False
    return isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError))


def retryable(error):
    if isinstance(error, urllib.error.HTTPError):
        return error.code == 429 or error.code >= 500
    return isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError, OSError))


def get_range(url, start, end, etag, length, attempts=6, timeout=60, sleep=time.sleep):
    """Bytes start..end (inclusive) of one archive version; strict 206 + Content-Range."""
    size = end - start + 1
    if not 0 < size <= MAX_RANGE:
        raise ValueError('Bounded HTTP range required')
    unreachable = 0
    for attempt in range(attempts):
        check_host(url)
        try:
            with host_slot(url):
                request = urllib.request.Request(url, headers={
                    'Range': f'bytes={start}-{end}', 'If-Match': etag, 'Accept-Encoding': 'identity',
                    'User-Agent': 'earthcraft-pipeline (+https://github.com/EricSpencer00/earthcraft)'})
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    if response.status != 206 or response.headers.get('Content-Range') != f'bytes {start}-{end}/{length}':
                        raise ValueError('Server ignored/changed range: refusing full archive')
                    data = response.read(size + 1)
            if len(data) != size:
                raise ConnectionError('Truncated range')
            return data
        except urllib.error.HTTPError as error:
            if error.code == 412:
                raise ValueError('Remote archive version changed (If-Match failed)') from error
            if not retryable(error) or attempt == attempts - 1:
                raise
            wait = error.headers.get('Retry-After') if error.headers else None
            delay = float(wait) if wait and wait.isdigit() else None
        except ValueError:
            raise
        except Exception as error:  # noqa: BLE001 - classified below
            unreachable = unreachable + 1 if connection_failure(error) else 0
            if unreachable >= 2:
                mark_host_down(url)
                raise HostUnavailable(f'{urllib.parse.urlsplit(url).hostname} unreachable: {error}') from error
            if not retryable(error) or attempt == attempts - 1:
                raise
            delay = None
        sleep(min(300.0, delay if delay is not None else 2 ** attempt + random.random()))
    raise RuntimeError('unreachable')
