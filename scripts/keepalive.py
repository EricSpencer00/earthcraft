"""HTTP GET over one persistent connection per host per thread.

urllib.request opens a new TCP + TLS connection for every request; a tile's lidar survey makes
~50-100 requests to the same bucket, and the handshakes alone were ~4.5 s a tile (profiled
2026-11-13). Errors raise urllib.error.HTTPError / OSError like urlopen, so callers' retry
logic is unchanged; redirects fall back to urllib.
"""
import http.client
import threading
import urllib.error
import urllib.parse
import urllib.request

_local = threading.local()


def _connection(scheme, host, timeout):
    pool = getattr(_local, 'pool', None)
    if pool is None:
        pool = _local.pool = {}
    key = (scheme, host)
    if key not in pool:
        cls = http.client.HTTPSConnection if scheme == 'https' else http.client.HTTPConnection
        pool[key] = cls(host, timeout=timeout)
    return pool[key]


def get(url, limit, timeout=60, headers=None):
    """Body bytes (at most limit + 1, so callers can detect an oversize response)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ('http', 'https'):
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read(limit + 1)
    path = parts.path + ('?' + parts.query if parts.query else '')
    request_headers = {'User-Agent': 'earthcraft', **(headers or {})}
    for attempt in (0, 1):  # a pooled connection the server already closed: reconnect once
        connection = _connection(parts.scheme, parts.netloc, timeout)
        try:
            connection.request('GET', path, headers=request_headers)
            response = connection.getresponse()
            body = response.read(limit + 1)
            if response.length:  # unread remainder (oversize): this connection cannot be reused
                connection.close()
        except (http.client.RemoteDisconnected, http.client.BadStatusLine, BrokenPipeError,
                ConnectionResetError, http.client.CannotSendRequest, http.client.ResponseNotReady):
            connection.close()
            _local.pool.pop((parts.scheme, parts.netloc), None)
            if attempt:
                raise
            continue
        except OSError:
            connection.close()
            _local.pool.pop((parts.scheme, parts.netloc), None)
            raise
        if response.status in (301, 302, 303, 307, 308):
            with urllib.request.urlopen(url, timeout=timeout) as redirected:
                return redirected.read(limit + 1)
        if response.status != 200:
            raise urllib.error.HTTPError(url, response.status, response.reason, response.headers, None)
        length = response.getheader('Content-Length')
        if length is not None and len(body) < min(int(length), limit + 1):
            raise http.client.IncompleteRead(body)
        return body
