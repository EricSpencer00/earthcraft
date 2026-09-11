"""Resolve local bulk and imagery workspaces without hard-coded home paths."""
import os
from pathlib import Path


def _configured(name, default):
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default


def bulk_root():
    return _configured('EARTHCRAFT_BULK_ROOT', Path.home() / 'EarthcraftData')


def bulk_path(*parts):
    return bulk_root().joinpath(*parts)


def imagery_root():
    return _configured('EARTHCRAFT_IMAGERY_ROOT', bulk_path('imagery-proof'))
