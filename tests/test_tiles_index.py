"""tiles/v2/index.json: merge rules and tree pagination."""
import io
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import tiles_index as ti  # noqa: E402


class Response(io.BytesIO):
    def __init__(self, body, link=None):
        super().__init__(json.dumps(body).encode())
        self.headers = {'Link': link} if link else {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Retry:
    def call(self, fn, what=''):
        return fn()


class TilesIndexTest(unittest.TestCase):
    def test_parse_rejects_junk(self):
        self.assertEqual(ti.parse(b'nope')['tiles'], {})
        self.assertEqual(ti.parse(None)['tiles'], {})
        raw = ti.dumps(ti.merge(ti.empty(), {'1_-2': [0, 10]}, 100))
        self.assertEqual(ti.parse(raw)['tiles'], {'1_-2': [0, 10]})

    def test_merge_keeps_existing_and_newest(self):
        index = ti.merge(ti.empty(), {'1_2': [0, 10], '3_4': [1, 20]}, 50)
        index = ti.merge(index, {'1_2': [2, 30], '3_4': [0, 5], '-5_6': [-1, 40]}, 60)
        self.assertEqual(index['tiles'], {'-5_6': [-1, 40], '1_2': [2, 30], '3_4': [1, 20]})
        self.assertEqual(index['updated_utc'], '1970-01-01T00:01:00Z')
        self.assertEqual(index['schema'], ti.SCHEMA)
        with self.assertRaises(ValueError):
            ti.merge(index, {'../x': [0, 1]}, 1)

    def test_quality_code(self):
        self.assertEqual(ti.quality_code('LQ'), 0)
        self.assertEqual(ti.quality_code(None), -1)

    def test_list_tiles_follows_link_pages(self):
        base = 'https://hub.test'
        pages = {
            f'{base}/api/datasets/o/r/tree/main/tiles/v2?expand=true': (
                [{'type': 'directory', 'path': 'tiles/v2/1_2', 'lastCommit': {'date': '2026-10-17T05:28:47.000Z'}},
                 {'type': 'file', 'path': 'tiles/v2/index.json'}], f'<{base}/next?cursor=a>; rel="next"'),
            f'{base}/next?cursor=a': ([{'type': 'directory', 'path': 'tiles/v2/-3_4', 'lastCommit': {}}], None)}

        class Reader:
            endpoint, repo, revision, retry = base, 'o/r', 'main', Retry()

            def _open(self, url):
                return Response(*pages[url])
        self.assertEqual(ti.list_tiles(Reader()), {'1_2': 1792214927, '-3_4': 0})


if __name__ == '__main__':
    unittest.main()
