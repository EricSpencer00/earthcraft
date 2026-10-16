"""Read-only spatial lookup of unchanged OSM ways in one frozen metric CRS.

The R-tree only finds candidates. Original float64 bounds make the final test;
source coordinates, tags, closure and file order are preserved exactly.
"""
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import shutil
import sqlite3

import osmium
from pyproj import CRS,Transformer


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def signature(path):
    stat=Path(path).stat()
    return {'size':stat.st_size,'mtime_ns':stat.st_mtime_ns}


def build_index(source,destination,crs,reserve_bytes=0,max_bytes=4*2**30):
    source,destination=Path(source),Path(destination)
    partial=destination.with_suffix('.sqlite.building')
    if destination.exists() or partial.exists():raise FileExistsError('Preserve existing OSM index or interrupted build')
    destination.parent.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(destination.parent).free<reserve_bytes+max_bytes:raise ValueError('OSM index storage reserve reached')
    before=signature(source);source_hash=sha(source);crs=CRS.from_user_input(crs).to_wkt()
    projection=Transformer.from_crs(4326,crs,always_xy=True)
    db=sqlite3.connect(partial)
    try:
        db.execute('PRAGMA journal_mode=OFF') # Disposable build; published DB is immutable.
        db.execute('PRAGMA max_page_count='+str(max_bytes//4096))
        db.execute('CREATE TABLE metadata (value TEXT NOT NULL)')
        db.execute('CREATE TABLE ways (seq INTEGER PRIMARY KEY,minx REAL,maxx REAL,miny REAL,maxy REAL,payload TEXT)')
        db.execute('CREATE VIRTUAL TABLE bounds USING rtree(seq,minx,maxx,miny,maxy)')
        class Index(osmium.SimpleHandler):
            count=0
            def way(self,w):
                coords=[(node.lon,node.lat) for node in w.nodes if node.location.valid()]
                if not coords:return
                x,y=projection.transform(*zip(*coords));bounds=(min(x),max(x),min(y),max(y))
                if not all(math.isfinite(v) for v in bounds):raise ValueError('Non-finite OSM coordinate')
                self.count+=1
                payload={'id':w.id,'tags':dict(w.tags),'coordinates':coords,'closed':w.nodes[0].ref==w.nodes[-1].ref}
                db.execute('INSERT INTO ways VALUES (?,?,?,?,?,?)',(self.count,*bounds,json.dumps(payload)))
                db.execute('INSERT INTO bounds VALUES (?,?,?,?,?)',(self.count,*bounds))
                if self.count%200000==0:print(f'Indexed {self.count:,} unchanged OSM ways',flush=True)
        handler=Index();handler.apply_file(str(source),locations=True,idx='flex_mem')
        if signature(source)!=before or sha(source)!=source_hash:raise ValueError('OSM source changed while indexing')
        metadata={'version':1,'source':str(source.resolve()),'source_sha256':source_hash,
            'source_stat':before,'crs':crs,'ways':handler.count,'inference_used':False,'source_order_preserved':True}
        db.execute('INSERT INTO metadata VALUES (?)',(json.dumps(metadata),));db.commit()
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Invalid OSM index')
        if db.execute("SELECT rtreecheck('bounds')").fetchone()[0]!='ok':raise ValueError('Invalid OSM spatial index')
    finally:db.close()
    partial.rename(destination)
    receipt={'index_sha256':sha(destination),'metadata':metadata}
    destination.with_suffix('.json').write_text(json.dumps(receipt,indent=2))
    return receipt


class MetricWayIndex:
    """Read-only, verified view of one frozen index.

    The PBF an index was built from may be absent. The regional state extracts
    died with the external drive, and the indexes are now fetched from the
    dataset (sources/indexes/). Without the PBF the index's own recorded source
    identity (SHA-256, size and mtime, written when the index was built from
    that exact file) is trusted instead of a re-hash. The index itself is still
    verified against its receipt (index_sha256) on open and stat-checked on
    every select, and receipts say which kind of check was made. When the PBF is
    present the original strict checks apply unchanged.
    """
    def __init__(self,path,source,crs,*,dataset_path=None):
        self.path=Path(path).resolve();self.dataset_path=dataset_path
        receipt=json.loads(self.path.with_suffix('.json').read_text())
        if sha(self.path)!=receipt['index_sha256']:raise ValueError('Frozen OSM index changed')
        self.index_sha256=receipt['index_sha256']
        self.db=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)
        try:
            self.meta=json.loads(self.db.execute('SELECT value FROM metadata').fetchone()[0])
            if self.meta!=receipt['metadata'] or self.meta['version']!=1:raise ValueError('OSM index receipt mismatch')
            self.source_present=source is not None and Path(source).exists()
            if self.source_present:
                self.source=Path(source).resolve()
                if self.meta['source']!=str(self.source) or sha(self.source)!=self.meta['source_sha256']:
                    raise ValueError('OSM source differs from indexed observations')
                self.source_stat=signature(self.source)
            else:
                self.source,self.source_stat=None,None
            if self.meta['crs']!=CRS.from_user_input(crs).to_wkt():raise ValueError('OSM index uses another coordinate frame')
            self.index_stat=signature(self.path)
        except Exception:self.db.close();raise

    @property
    def identity(self):
        """Portable provenance for receipts: dataset path and hashes, no local paths."""
        return {'index':self.dataset_path or self.path.name,'index_sha256':self.index_sha256,
                'source_file':PurePosixPath(self.meta['source']).name,'source_sha256':self.meta['source_sha256'],
                'source_stat':self.meta['source_stat'],'ways':self.meta['ways'],'index_version':self.meta['version'],
                'source_check':'re-hashed' if self.source_present else 'recorded in index; PBF not present'}

    def select(self,source,crs,west,north,size):
        if self.source_present:
            if source is None or Path(source).resolve()!=self.source:raise ValueError('OSM query source/frame mismatch')
        elif source is not None and str(source)!=self.meta['source']:
            raise ValueError('OSM query source/frame mismatch')
        if CRS.from_user_input(crs).to_wkt()!=self.meta['crs']:raise ValueError('OSM query source/frame mismatch')
        if (self.source_present and signature(self.source)!=self.source_stat) or signature(self.path)!=self.index_stat:
            raise ValueError('Frozen OSM index/source changed during run')
        east,south=west+size,north-size
        rows=self.db.execute('SELECT w.payload,w.minx,w.maxx,w.miny,w.maxy FROM bounds b JOIN ways w ON w.seq=b.seq '
            'WHERE b.minx<=? AND b.maxx>=? AND b.miny<=? AND b.maxy>=? ORDER BY w.seq',(east,west,north,south))
        return [json.loads(payload) for payload,minx,maxx,miny,maxy in rows
            if not (maxx<west or minx>east or maxy<south or miny>north)]

    def covers(self,west,north,size):
        """True if any indexed way meets the window (the exact test select() applies)."""
        east,south=west+size,north-size
        return self.db.execute('SELECT 1 FROM bounds b JOIN ways w ON w.seq=b.seq WHERE b.minx<=? AND b.maxx>=? '
            'AND b.miny<=? AND b.maxy>=? AND NOT (w.maxx<? OR w.minx>? OR w.maxy<? OR w.miny>?) LIMIT 1',
            (east,west,north,south,west,east,south,north)).fetchone() is not None

    def close(self):self.db.close()
