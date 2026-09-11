"""Read a public ZIP through strict, bounded HTTP ranges; never fetch the archive."""
import io
import urllib.request


class RangeReader(io.RawIOBase):
    def __init__(self, url, budget=64*1024**2):
        self.url, self.budget, self.transferred, self.position = url, budget, 0, 0
        with urllib.request.urlopen(urllib.request.Request(url, method='HEAD'), timeout=20) as response:
            self.length = int(response.headers['Content-Length'])
            self.etag = response.headers.get('ETag')
            if response.headers.get('Accept-Ranges') != 'bytes' or not self.etag:
                raise ValueError('Server must support byte ranges and stable ETag')

    def seekable(self): return True
    def readable(self): return True
    def tell(self): return self.position

    def seek(self, offset, whence=0):
        if whence not in (0,1,2): raise ValueError('Invalid whence')
        position = offset + (0 if whence == 0 else self.position if whence == 1 else self.length)
        if not 0 <= position <= self.length: raise ValueError('Seek outside archive')
        self.position = position
        return position

    def read(self, size=-1):
        if size < 0: size = self.length-self.position
        size = min(size, self.length-self.position)
        if size == 0: return b''
        if size > 16*1024**2 or self.transferred+size > self.budget:
            raise ValueError('HTTP range download budget exceeded')
        start, end = self.position, self.position+size-1
        request = urllib.request.Request(self.url, headers={'Range': f'bytes={start}-{end}',
            'If-Match': self.etag, 'Accept-Encoding': 'identity'})
        with urllib.request.urlopen(request, timeout=30) as response:
            expected = f'bytes {start}-{end}/{self.length}'
            if response.status != 206 or response.headers.get('Content-Range') != expected:
                raise ValueError('Server ignored/changed range: refusing full archive')
            data = response.read(size+1)
        if len(data) != size: raise ValueError('Truncated or oversized range')
        self.transferred += size
        self.position += size
        return data
