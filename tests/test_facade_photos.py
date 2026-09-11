import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from facade_photos import validate_titles, normalized_capture


class PhotoBatchTests(unittest.TestCase):
    def test_capture_metadata_normalizes_without_guessing(self):
        self.assertEqual(normalized_capture('Taken on\u00a022 November 2013, 03:57'),['2013-11-22']*2)
        self.assertEqual(normalized_capture('2022-03-21 07:55'),['2022-03-21']*2)
        self.assertEqual(normalized_capture('2022-03'),['2022-03-01','2022-03-31'])
        for bad in [None,'about 2022','Uploaded 2022-01-01','Taken on 32 November 2013, 03:57']:
            self.assertIsNone(normalized_capture(bad))
    def test_bounded_explicit_files(self):
        valid=[['tower-2022','File:Example.jpg']]
        self.assertEqual(validate_titles(valid),valid)
        for bad in [[],valid*4,[['../outside','File:Example.jpg']],
                    [['valid','https://example.com/file']],
                    [['valid','File:One.jpg|File:Two.jpg']],valid*2,
                    [['a','File:One.jpg'],['b','File:One.jpg']],['bad']]:
            with self.subTest(bad=bad),self.assertRaises(ValueError):validate_titles(bad)


if __name__=='__main__':unittest.main()
