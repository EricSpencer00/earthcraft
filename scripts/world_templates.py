"""Resolve the non-geographic world templates every metric world is built from.

Two small local assets: the vanilla chunk/level template
('chicago-water-tower-64/Arnis World 1') and the tall-world height datapack
('Earthcraft-Chicago-Photo-Atlas-v2/datapacks/earthcraft_height'). They live in
the repository's untracked worlds/ directory, or in EARTHCRAFT_WORLD_TEMPLATES
(a directory laid out the same way), which the regional generator points at its
control directory. The template level.dat holds a real player compound, so the
templates are never published.
"""
import os
from pathlib import Path

CHUNK_TEMPLATE = Path('chicago-water-tower-64/Arnis World 1')
HEIGHT_TEMPLATE = Path('Earthcraft-Chicago-Photo-Atlas-v2/datapacks/earthcraft_height')


def templates_root(root):
    configured = os.environ.get('EARTHCRAFT_WORLD_TEMPLATES')
    return Path(configured).expanduser() if configured else Path(root) / 'worlds'


def chunk_template(root):
    path = templates_root(root) / CHUNK_TEMPLATE
    if not (path / 'level.dat').is_file() or not (path / 'region/r.0.0.mca').is_file():
        raise ValueError(f'World chunk template missing: {path}')
    return path


def height_template(root):
    path = templates_root(root) / HEIGHT_TEMPLATE
    if (path / 'pack.mcmeta').is_file() and list(path.rglob('overworld.json')):
        return path
    raise ValueError(f'Height datapack template missing: {path}')
