import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from http_zip_range import RangeReader


class Response(io.BytesIO):
    def __init__(self, data=b'', status=200, headers=None):
        super().__init__(data); self.status=status; self.headers=headers or {}


class RangeTests(unittest.TestCase):
    def reader(self):
        with patch('urllib.request.urlopen',return_value=Response(headers={
                'Content-Length':'100','Accept-Ranges':'bytes','ETag':'version1'})):
            return RangeReader('https://example.org/public.zip',budget=10)

    def test_refuses_whole_archive_and_ignored_range(self):
        reader=self.reader()
        with self.assertRaises(ValueError): reader.read()
        with patch('urllib.request.urlopen',return_value=Response(b'whole archive')):
            with self.assertRaises(ValueError): reader.read(5)
        self.assertEqual(reader.tell(),0)

    def test_exact_range_and_budget(self):
        reader=self.reader();reader.seek(-5,2)
        with patch('urllib.request.urlopen',return_value=Response(b'12345',206,{'Content-Range':'bytes 95-99/100'})):
            self.assertEqual(reader.read(5),b'12345')
        self.assertEqual(reader.transferred,5)
        reader.seek(0)
        with self.assertRaises(ValueError): reader.read(6)


if __name__=='__main__':unittest.main()
