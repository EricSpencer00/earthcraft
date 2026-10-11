"""Publish a small validated block-evidence page beside the existing progress atlas."""
import argparse
from html import escape
import json
from pathlib import Path
import shutil
from urllib.parse import urlsplit

from headless_blocks import require, validate_bundle


def build_site(bundle, output):
    bundle,output=Path(bundle),Path(output)
    report=validate_bundle(bundle)
    source,render=report['source'],report['render']
    total=sum(t['actual_non_air_blocks'] for t in report['tiles'])
    height_note=('All sampled native heightmap columns agree with decoded blocks.'
                 if report['checks']['native_heightmaps']=='verified' else
                 'Available native heightmap columns agree with decoded blocks; coverage is partial.')
    rows=''.join(f'<tr><th scope="row">{escape(t["tile"])}</th><td>{escape(t["quality"])}</td>'
                 f'<td>{t["actual_chunks"]:,}</td><td>{t["actual_non_air_blocks"]:,}</td>'
                 f'<td>{t["heightmap_columns_checked"]:,}</td></tr>' for t in report['tiles'])
    credits=[]
    for credit in source['attribution']:
        label=escape(credit['source']);license=credit['license'];url=credit.get('url','')
        if credit['source']=='OpenStreetMap contributors':label='© '+label
        # Keep the historical receipt unchanged while linking to USDA's current page.
        if url=='https://www.fsa.usda.gov/resources/programs/naip-imagery':
            url='https://www.fpacbc.usda.gov/geospatial-services/customer-services/naip-coverage-map'
        require(not url or (urlsplit(url).scheme=='https' and urlsplit(url).netloc),'Unsafe attribution URL')
        label=f'<a href="{escape(url,quote=True)}">{label}</a>' if url else label
        # Receipt declarations remain exact; omit internal bookkeeping from credits.
        license={'US Government Public Domain':'Public domain',
                 'Source metadata retained locally; attribution required':'',
                 'Per upstream terrain source':''}.get(license,license)
        terms=f' — {escape(license)}' if license else ''
        if credit['source']=='Terrain Tiles' and credit['license']=='Per upstream terrain source':
            terms=' — <a href="https://github.com/tilezen/joerd/blob/master/docs/attribution.md">Upstream credits</a>'
        credits.append(f'<li>{label}{terms}</li>')
    content=f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Actual block evidence · Earthcraft</title><meta name="description" content="A headless view of actual archived Earthcraft blocks, with hashes and unresolved accuracy checks.">
<link rel="stylesheet" href="style.css"></head><body><main>
<header><a href="../">← Earthcraft atlas</a><a href="https://github.com/EricSpencer00/earthcraft">GitHub</a></header>
<h1>Block evidence</h1>
<p class="metrics">{source['audited_tiles']:,} archived 512 m {'tile' if source['audited_tiles']==1 else 'tiles'} · {source['actual_chunks']:,} chunks · {total:,} non-air blocks</p>
<figure class="scene"><a href="blocks.png"><img src="blocks.png" width="1280" height="960" alt="An orthographic cube view of actual occupied blocks in a clipped {render['crop_size_m']}-metre tile window."></a>
<figcaption>Tile {escape(render['tile'])} · {render['crop_size_m']} m crop</figcaption></figure>
<p class="limits">Cube shapes and colors are illustrative; game textures and liquid heights are not modeled. Absolute vertical datum, survey accuracy, facade color, and total world coverage are unverified. LQ buildings may be inferred or extruded; no Minecraft visual check was performed.</p>
<details class="audit"><summary>Audit details</summary>
<p>Archive and region hashes, chunk coordinates, block palettes, and sampled chunk coverage were checked. {height_note} The crop contains {render['occupied_voxels_in_render_window']:,} occupied voxels and {render['visible_faces']:,} visible cube faces. Cut faces appear at the crop boundary.</p>
<figure class="overview"><img src="topdown.png" width="512" height="512" alt="Topmost block identities across the sampled tile, with the rendered crop outlined in red." loading="lazy"><figcaption>Topmost block identities · Red outline: rendered window</figcaption></figure>
<h2 id="sample-coverage">Sample coverage</h2><div class="table-wrap" tabindex="0" role="region" aria-labelledby="sample-coverage"><table><caption>Each tile is 512 m square at one block per metre.</caption><thead><tr><th scope="col">Tile</th><th scope="col">Quality</th><th scope="col">Chunks</th><th scope="col">Non-air blocks</th><th scope="col">Heightmap columns checked</th></tr></thead><tbody>{rows}</tbody></table></div>
<p>Analyzed <time>{escape(report['observed_utc'])}</time>; archive generation and installation dates are not established by this analysis.</p>
<dl class="provenance"><dt>Archive SHA-256</dt><dd><code>{source['archive_sha256']}</code></dd><dt>Pinned source revision</dt><dd><code>{source['revision']}</code></dd><dt>Renderer SHA-256</dt><dd><code>{render['renderer_sha256']}</code></dd><dt>Image SHA-256</dt><dd><code>{render['png_sha256']}</code></dd></dl>
<p><a href="report.json">Analysis receipt (JSON)</a> · <a href="https://github.com/EricSpencer00/earthcraft/blob/main/docs/HEADLESS_EVIDENCE.md">Reproduce this analysis</a></p>
</details>
<footer><h2>Sources</h2><ul>{''.join(credits)}</ul></footer>
</main></body></html>'''
    assets=Path(__file__).resolve().parents[1]/'dashboard'
    css=(assets/'evidence.css').read_text()
    output.mkdir(parents=True,exist_ok=True)
    fonts=output/'fonts';fonts.mkdir(exist_ok=True)
    for name in ['source-serif-4-latin.woff2','libre-franklin-latin.woff2',
                 'sourceserif4-OFL.txt','librefranklin-OFL.txt']:
        shutil.copyfile(assets/'assets/fonts'/name,fonts/name)
    for name in ['blocks.png','topdown.png','report.json']:shutil.copyfile(bundle/name,output/name)
    (output/'index.html').write_text(content)
    (output/'style.css').write_text(css+'\n')
    return {'audited_tiles':source['audited_tiles'],'actual_chunks':source['actual_chunks'],
            'actual_non_air_blocks':total,'page_bytes':sum(p.stat().st_size for p in output.rglob('*') if p.is_file())}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(build_site(args.bundle,args.output)))
