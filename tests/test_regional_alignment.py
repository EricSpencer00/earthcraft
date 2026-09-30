import contextlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from metric_chart import chart
from regional_alignment import audit


class AlignmentTests(unittest.TestCase):
    def test_negative_region_and_slot_position_and_fractional_origin(self):
        chart_meta=chart(-87.62443,41.8972,64)
        frame={key:chart_meta[key] for key in ('crs','west','north')}
        frame.update(vertical_offset_m=-116,dimension_min_y=-64,dimension_height=1024)
        # Freeze the actual current save's origin rather than the centred chart.
        frame.update(west=-32,north=33)
        with tempfile.TemporaryDirectory() as directory:
            world=Path(directory);(world/'region').mkdir();(world/'region/r.-1.0.mca').touch()
            meta={'source':dict(crs=frame['crs'],west=-48,north=33,size=16),
                  'world_frame':frame,'world_offset_xz':[-16,0],'vertical_offset_m':-116}
            (world/'earthcraft.json').write_text(json.dumps(meta))
            with patch('regional_alignment.materialized',side_effect=contextlib.nullcontext),\
                 patch('regional_alignment.chunks',return_value=[(31,{'xPos':-1,'zPos':0},None)]):
                result=audit(frame,[world]);self.assertEqual(result['worlds'][0]['chunks'],1)
                meta['source']['west']=-48.5
                (world/'earthcraft.json').write_text(json.dumps(meta))
                with self.assertRaisesRegex(ValueError,'chunk placement'):audit(frame,[world])

    def test_rejects_vertical_translation_and_changed_anvil_slot(self):
        frame={'crs':chart(-87.62443,41.8972,64)['crs'],'west':-32,'north':33,
               'vertical_offset_m':-116,'dimension_min_y':-64,'dimension_height':1024}
        with tempfile.TemporaryDirectory() as directory:
            world=Path(directory);(world/'region').mkdir();(world/'region/r.-1.0.mca').touch()
            meta={'source':dict(crs=frame['crs'],west=-48,north=33,size=16),
                  'world_frame':frame,'world_offset_xz':[-16,0],'vertical_offset_m':-115}
            (world/'earthcraft.json').write_text(json.dumps(meta))
            with self.assertRaisesRegex(ValueError,'vertical translation'):audit(frame,[world])
            meta['vertical_offset_m']=-116;(world/'earthcraft.json').write_text(json.dumps(meta))
            with patch('regional_alignment.materialized',side_effect=contextlib.nullcontext),\
                 patch('regional_alignment.chunks',return_value=[(0,{'xPos':-1,'zPos':0},None)]):
                with self.assertRaisesRegex(ValueError,'slot/region'):audit(frame,[world])


if __name__=='__main__':unittest.main()
