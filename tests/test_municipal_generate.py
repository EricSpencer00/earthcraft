from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from municipal_generate import presentation_ways


class MunicipalPresentationTests(unittest.TestCase):
    def test_observations_are_never_replaced_and_estimates_are_separate(self):
        original = [{'id': 1, 'tags': {'building': 'yes', 'height': '17 m'}},
                    {'id': 2, 'tags': {'building': 'house', 'building:levels': '2'}},
                    {'id': 3, 'tags': {'highway': 'residential'}},
                    {'id': 4, 'tags': {'highway': 'service', 'width': '3 m'}}]
        result, estimates = presentation_ways(original)
        self.assertEqual(result[0]['tags']['height'], '17 m')
        self.assertEqual(result[3]['tags']['width'], '3 m')
        self.assertNotIn('height', original[1]['tags'])
        self.assertNotIn('width', original[2]['tags'])
        self.assertEqual(result[1]['tags']['height'], '6.0')
        self.assertEqual([row['osm_way'] for row in estimates], [2, 3])
        self.assertTrue(all(value['measured'] is False for row in estimates
                            for value in row['derived_attributes'].values()))


if __name__ == '__main__':
    unittest.main()
