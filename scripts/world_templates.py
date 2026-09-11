"""Resolve existing non-geographic world metadata without requiring bulk storage."""
from pathlib import Path
from local_paths import bulk_path


def height_template(root):
    candidates=[Path(root)/'worlds/Earthcraft-Chicago-Photo-Atlas-v2/datapacks/earthcraft_height',
                bulk_path('chicago','worlds','loop-pilot','Arnis World 1','datapacks','arnis_tall')]
    for path in candidates:
        if (path/'pack.mcmeta').is_file() and list(path.rglob('overworld.json')):
            return path
    raise ValueError('No local or external height metadata template available')
