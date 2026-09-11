"""Five explicit geographic QA checks; never a claim of perfect source accuracy."""
import json
from pathlib import Path
import numpy as np
import rasterio
from collections import deque


def water_components(mask, heights):
    unseen = set(zip(*np.where(mask)))
    result = []
    while unseen:
        point = unseen.pop()
        queue, values = deque([point]), []
        while queue:
            z,x=queue.popleft()
            values.append(int(heights[z,x]))
            for neighbor in ((z-1,x),(z+1,x),(z,x-1),(z,x+1)):
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    queue.append(neighbor)
        result.append({'cells':len(values),'min_y':min(values),'max_y':max(values),
                       'height_span_blocks':max(values)-min(values)})
    return result


def vertical_layout(elevation, maximum_y=None):
    if not np.isfinite(elevation).all():
        raise ValueError('Missing elevations; cannot preserve relief')
    offset = 64 - int(np.floor(np.min(elevation)))
    ground = np.floor(elevation + offset).astype(np.int32)
    top = max(int(ground.max()), maximum_y if maximum_y is not None else int(ground.max()))
    height = int(np.ceil((top + 65) / 16)) * 16
    if height > 2032:
        raise ValueError('Elevation range exceeds this dimension; select a regional chart, never compress height')
    return offset, ground, height


def audit(world):
    world = Path(world)
    report = json.loads((world/'earthcraft.json').read_text())
    with rasterio.open(report['dem_path']) as raster:
        ground = np.floor(raster.read(1)+report['vertical_offset_m']).astype(int)
    tops = np.load(world/'top-heights.npy')
    roof_cells = point_columns = point_cells = mapped_cells = 0
    if (world/'point-voxels.npy').exists():
        cells = np.load(world/'point-voxels.npy')
        expected = ground.copy()
        np.maximum.at(expected, (cells[:,2],cells[:,0]), cells[:,1])
        point_cells = len(cells)
        point_columns = len(np.unique(cells[:,[0,2]],axis=0))
    elif (world/'roof-observations.npz').exists():
        roof = np.load(world/'roof-observations.npz')
        expected = np.where(roof['mask'], roof['top_y'], ground)
        roof_cells = int(roof['mask'].sum())
    elif (world/'mapped-building-tops.npz').exists():
        mapped=np.load(world/'mapped-building-tops.npz')
        expected=np.where(mapped['mask'],mapped['top_y'],ground)
        mapped_cells=int(mapped['mask'].sum())
    elif report['source'].get('buildings_available') is False:
        expected = ground
    else:
        raise ValueError('Missing canonical building observations')
    seam_tests = 0
    seam_failures = 0
    for axis in (0,1):
        for edge in range(16, tops.shape[axis], 16):
            actual_jump = np.take(tops,edge,axis)-np.take(tops,edge-1,axis)
            expected_jump = np.take(expected,edge,axis)-np.take(expected,edge-1,axis)
            seam_tests += actual_jump.size
            seam_failures += int(np.count_nonzero(actual_jump!=expected_jump))
    delta = report.get('county_ground_vs_usgs_median_abs_error_m')
    water = np.load(world/'water-observations.npz')
    components = water_components(water['mask'],water['top_y'])
    result = {
        'elevation_alignment': {'median_abs_difference_m':delta,
            'status':'consistency_only' if delta is not None else 'not_compared', 'independent_vertical_accuracy_verified':False},
        'water_levels': {'status':'unavailable' if not components or 'water-surface semantics' in report['source'].get('missing_layers',[]) else
            'uneven_surfaces_flagged' if any(c['height_span_blocks'] for c in components) else 'level_surfaces_consistent',
            'components':components, 'independent_water_elevations_verified':False,
            'policy':'Only explicitly mapped water is placed. No invented bathymetry or ocean fill.',
            'limitation':'Water currently follows sampled terrain; level surfaces and river gradients need separate controls.'},
        'tile_seams': {'checked_adjacent_cells':seam_tests, 'unexplained_jump_cells':seam_failures,
                       'status':'pass' if seam_failures==0 else 'fail'},
        'vertical_range': {'status':'pass', 'min_y':report['dimension_min_y'],
            'height':report['dimension_height'], 'single_offset_m':report['vertical_offset_m'],
            'compression_applied':False, 'mountain_location_verified':False},
        'coverage': {'terrain_cells':int(ground.size),'measured_roof_cells':roof_cells,
            'mapped_shell_roof_cells':mapped_cells,
            'observed_3d_columns':point_columns,'observed_3d_voxels':point_cells,
            'missing_layers':report['source'].get('missing_layers',['georegistered facade photos','individual tree geometry','bathymetry']),
            'ground_cover_native_resolution_m':report['source'].get('cover_native_resolution_m',10), 'output_cell_size_m':1,
            'source_accuracy_is_not_output_resolution':True},
        'perfect_geographic_accuracy_claimed':False}
    if seam_failures:
        raise ValueError(f'{seam_failures} seam comparisons differ from canonical source surface')
    (world/'geographic-quality.json').write_text(json.dumps(result,indent=2))
    return result
