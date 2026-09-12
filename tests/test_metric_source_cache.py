import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from metric_source_cache import SourceCacheBusy, source_from_cache, supertile_grid


class MetricSourceCacheTests(unittest.TestCase):
    def test_lattice_alignment_groups_sixteen_tiles(self):
        frame={'crs':'EPSG:3857'}
        grid,col,row=supertile_grid({'west':-17952,'north':4641,'size':256},
                                    frame,-18976,4897)
        self.assertEqual(grid,{'crs':'EPSG:3857','west':-17952,'north':4897,'size':1024})
        self.assertEqual((col,row),(0,256))

    def test_parent_is_prepared_once_and_children_are_cropped(self):
        calls=[]
        def prepare(path,size,grid,way_index=None):
            calls.append(('prepare',grid))
            path.mkdir()
            (path/'sources.json').write_text(json.dumps(grid))
            for name in ('rasters.npz','usgs-elevation.tif','osm-ways.json','cook-buildings-2022.json'):
                (path/name).write_bytes(b'x')
        def crop(parent,destination,col,row,size):
            calls.append(('crop',col,row,size))
            destination.mkdir()
        frame={'crs':'EPSG:3857'};tile={'west':0,'north':1024,'size':256}
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);cache=root/'cache'
            source_from_cache(tile,frame,root/'a',cache,0,1024,
                              prepare_fn=prepare,crop_fn=crop)
            source_from_cache({**tile,'west':256},frame,root/'b',cache,0,1024,
                              prepare_fn=prepare,crop_fn=crop)
        self.assertEqual(sum(call[0]=='prepare' for call in calls),1)
        self.assertEqual([call[1:] for call in calls if call[0]=='crop'],
                         [(0,0,256),(256,0,256)])

    def test_busy_parent_is_deferred_after_bounded_wait(self):
        frame={'crs':'EPSG:3857'};tile={'west':0,'north':1024,'size':256}
        with tempfile.TemporaryDirectory() as folder, patch(
                'metric_source_cache.fcntl.lockf',side_effect=BlockingIOError(11,'busy')):
            with self.assertRaises(SourceCacheBusy):
                source_from_cache(tile,frame,Path(folder)/'child',Path(folder)/'cache',0,1024,
                                  lock_wait_seconds=0)


if __name__=='__main__':unittest.main()
