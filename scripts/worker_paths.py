"""Print the scripts the pipeline workers run: the import closure of the entry points.

The deploy hook restarts the generator only when one of these changes, so a
site-only edit (for example progress_snapshot.py) does not drain the workers.
Python imports and file names quoted in the closure (subprocess entry points,
Node helpers) both count.
"""
import ast
import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENTRIES = ('regional_supervisor.py', 'regional_generate.py')
QUOTED = re.compile(r'''['"/]([A-Za-z0-9_]+\.(?:py|mjs|js))['"]''')


def closure(entries=ENTRIES, scripts=SCRIPTS):
    local = {p.name for p in scripts.iterdir() if p.is_file()}
    seen, todo = set(), [e for e in entries if e in local]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        text = (scripts / name).read_text()
        found = set(QUOTED.findall(text))
        if name.endswith('.py'):
            for node in ast.walk(ast.parse(text)):
                if isinstance(node, ast.Import):
                    found.update(a.name.split('.')[0] + '.py' for a in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    found.add(node.module.split('.')[0] + '.py')
        todo.extend(sorted(found & local - seen))
    return sorted(seen)


if __name__ == '__main__':
    print('\n'.join('scripts/' + name for name in closure(sys.argv[1:] or ENTRIES)))
