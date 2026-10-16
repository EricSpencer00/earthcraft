"""Write guard for detail-layer jobs: hard byte cap plus a free-space floor.

Every file a detail job writes goes through one ``WriteGuard``. Each write
checks the remaining budget and the destination volume's free space first,
refuses to overwrite an existing file, and writes through a temporary name so
an interrupted job never leaves a truncated artifact under the final name.
"""
import os
import shutil
from pathlib import Path


class WriteGuard:
    def __init__(self, root, max_bytes, free_floor_bytes):
        self.root = Path(root).resolve()
        self.max_bytes = int(max_bytes)
        self.free_floor = int(free_floor_bytes)
        self.written = 0
        self.files = []

    def _check(self, path, size):
        path = Path(path).resolve()
        if self.root not in path.parents:
            raise PermissionError(f'{path} is outside the guarded root {self.root}')
        if path.exists():
            raise FileExistsError(f'Refusing to overwrite {path}; write each file once')
        if self.written + size > self.max_bytes:
            raise OSError(f'Byte cap reached: {self.written}+{size} > {self.max_bytes}')
        volume = path.parent
        while not volume.exists():
            volume = volume.parent
        free = shutil.disk_usage(volume).free
        if free - size < self.free_floor:
            raise OSError(f'Free-space floor: {free} bytes free, floor {self.free_floor}')
        return path

    def write_bytes(self, path, data):
        path = self._check(path, len(data))
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + '.partial')
        with open(temp, 'xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
        self.written += len(data)
        self.files.append((str(path), len(data)))
        return path

    def write_text(self, path, text):
        return self.write_bytes(path, text.encode())
