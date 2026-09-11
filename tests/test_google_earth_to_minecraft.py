import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from google_earth_to_minecraft import main, occupied_cells, parse_kml_bytes, write_datapack, local_metre_coordinates

KML = b'''<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><name>Asymmetric fixture</name><ExtendedData><Data name="height_m"><value>3</value></Data></ExtendedData><Polygon><outerBoundaryIs><LinearRing><coordinates>-87.00000,41.00000,0 -86.99995,41.00000,0 -86.99995,41.00003,0 -86.99998,41.00003,0 -86.99998,41.00006,0 -87.00000,41.00006,0 -87.00000,41.00000,0</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></Document></kml>'''

class GoogleEarthImportTests(unittest.TestCase):
    def test_geographic_north_is_negative_minecraft_z(self):
        east, south = local_metre_coordinates(-86.9999, 41.0001, -87, 41)
        self.assertGreater(east, 0)
        self.assertLess(south, 0)

    def test_asymmetric_polygon_keeps_orientation_scale_and_height(self):
        buildings = parse_kml_bytes(KML)
        cells, manifest = occupied_cells(buildings)
        footprint = {(x, z) for x, _, z in cells}
        self.assertEqual({y for _, y, _ in cells}, {0, 1, 2})
        self.assertGreater(len(footprint), 10)
        # Longitude maps to Minecraft X (east), latitude to Z (north).  This
        # deliberately has unequal arms so a swap/mirror is observable.
        self.assertEqual(max(x for x, _ in footprint) - min(x for x, _ in footprint), 3)
        self.assertEqual(max(z for _, z in footprint) - min(z for _, z in footprint), 5)
        self.assertEqual(manifest['buildings'][0]['height_source'], 'ExtendedData')

    def test_datapack_contains_only_deterministic_build_commands_and_manifest(self):
        cells, manifest = occupied_cells(parse_kml_bytes(KML))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'fixture.zip'
            write_datapack(output, cells, manifest, 'stone_bricks')
            with zipfile.ZipFile(output) as archive:
                commands = archive.read('data/earthcraft/function/build.mcfunction').decode().splitlines()
                report = json.loads(archive.read('earthcraft-manifest.json'))
            self.assertLessEqual(len(commands), len(cells))
            self.assertTrue(all(line.startswith('fill ') for line in commands))
            self.assertEqual(report['inference'], 'none; deterministic geometry only')
            self.assertEqual(report['minecraft_commands'], len(commands))

    def test_missing_height_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'no usable building height'):
            parse_kml_bytes(KML.replace(b'<ExtendedData><Data name="height_m"><value>3</value></Data></ExtendedData>', b''))

    def test_cli_writes_new_pack_with_input_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'fixture.kml'
            output = Path(directory) / 'fixture.zip'
            source.write_bytes(KML)
            with patch.object(sys, 'argv', ['google_earth_to_minecraft.py', str(source), '--output', str(output)]):
                self.assertEqual(main(), 0)
            with zipfile.ZipFile(output) as archive:
                report = json.loads(archive.read('earthcraft-manifest.json'))
            self.assertEqual(report['input_sha256'], hashlib.sha256(KML).hexdigest())

if __name__ == '__main__':
    unittest.main()
