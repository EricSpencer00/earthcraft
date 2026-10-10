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
        label=escape(credit['source']);license=escape(credit['license']);url=credit.get('url','')
        require(not url or (urlsplit(url).scheme=='https' and urlsplit(url).netloc),'Unsafe attribution URL')
        label=f'<a href="{escape(url,quote=True)}">{label}</a>' if url else label
        credits.append(f'<li>{label} — {license}</li>')
    content=f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Actual block evidence · Earthcraft</title><meta name="description" content="A headless view of actual archived Earthcraft blocks, with hashes and unresolved accuracy checks.">
<link rel="stylesheet" href="style.css"></head><body><main>
<header><a href="../">← Earthcraft atlas</a><a href="https://github.com/EricSpencer00/earthcraft">Source ↗</a></header>
<p class="eyebrow">Earthcraft / Headless evidence</p><h1>Built from stored blocks.</h1>
<p class="intro">A reproducible view of actual archived blocks. This analysis checks {source['audited_tiles']:,} existing archived {'tile' if source['audited_tiles']==1 else 'tiles'}; it does not add new world coverage.</p>
<dl class="metrics"><div><dt>Tiles audited</dt><dd>{source['audited_tiles']:,}</dd></div><div><dt>Chunks decoded</dt><dd>{source['actual_chunks']:,}</dd></div><div><dt>Non-air blocks</dt><dd>{total:,}</dd></div></dl>
<figure class="scene"><a href="blocks.png"><img src="blocks.png" width="1280" height="960" alt="An orthographic cube view of actual occupied blocks in a clipped {render['crop_size_m']}-metre tile window."></a>
<figcaption>Tile {escape(render['tile'])} · {render['crop_size_m']} m crop · {render['occupied_voxels_in_render_window']:,} occupied voxels · {render['visible_faces']:,} visible cube faces.</figcaption></figure>
<section class="grid"><div><h2>What this view verifies</h2><p>Archive and region hashes, chunk coordinates, block palettes, and complete sampled chunk coverage were checked. {height_note} Geometry is drawn from those cells. Cut faces appear at the crop boundary.</p>
<p>Cube shapes and display colors are illustrative. Blockstate-specific meshes, game textures, liquid heights, and facade photographs are not modeled.</p>
<h2>What remains unresolved</h2><p>Absolute vertical datum, independent survey accuracy, measured facade color, and global completeness still need evidence. LQ geometry may include inferred or extruded buildings. No Minecraft visual check was performed.</p></div>
<figure class="overview"><img src="topdown.png" width="512" height="512" alt="Topmost block identities across the sampled tile, with the rendered crop outlined in red." loading="lazy"><figcaption>Topmost block identities · Red outline: rendered window.</figcaption></figure></section>
<section><h2>Sample coverage</h2><div class="table-wrap"><table><caption>Each tile is 512 m square at one block per metre.</caption><thead><tr><th scope="col">Tile</th><th scope="col">Quality</th><th scope="col">Chunks</th><th scope="col">Non-air blocks</th><th scope="col">Heightmap columns checked</th></tr></thead><tbody>{rows}</tbody></table></div></section>
<section><h2>Evidence trail</h2><p>Analysis recorded <time>{escape(report['observed_utc'])}</time>. The source is a previously archived sample; this date records its analysis, not a new generation or installation.</p>
<dl class="provenance"><dt>Archive SHA-256</dt><dd><code>{source['archive_sha256']}</code></dd><dt>Pinned source revision</dt><dd><code>{source['revision']}</code></dd><dt>Renderer SHA-256</dt><dd><code>{render['renderer_sha256']}</code></dd><dt>Image SHA-256</dt><dd><code>{render['png_sha256']}</code></dd></dl>
<p><a href="report.json">Download the block-analysis receipt (JSON)</a> · <a href="https://github.com/EricSpencer00/earthcraft/blob/main/docs/HEADLESS_EVIDENCE.md">Reproduce and update this view</a></p></section>
<section><h2>Source credits</h2><p>The original source declarations are retained below. Their presence does not establish measured color or independent geometry accuracy for this LQ sample.</p><ul>{''.join(credits)}</ul></section>
<footer>No Minecraft or model inference was used for this analysis. Validated evidence changes update this page through bounded GitHub Actions.</footer>
</main></body></html>'''
    css='''*{box-sizing:border-box}body{margin:0;background:#edf2f1;color:#233e40;font:17px/1.65 system-ui,sans-serif}main{max-width:1120px;margin:auto;padding:32px 28px 60px}header{display:flex;justify-content:space-between;gap:20px;margin-bottom:56px}a{color:#236366;text-underline-offset:4px}a:focus-visible{outline:3px solid #aa582f;outline-offset:5px}.eyebrow{font-size:13px;letter-spacing:.13em;text-transform:uppercase}h1{font-size:clamp(38px,7vw,68px);line-height:1.1;margin:12px 0 24px;letter-spacing:-.035em}h2{font-size:25px;line-height:1.3;margin:30px 0 12px}.intro{max-width:700px;font-size:21px}.metrics{display:flex;flex-wrap:wrap;gap:20px 72px;margin:32px 0}.metrics dt{font-size:14px}.metrics dd{font-size:32px;line-height:1.3;margin:4px 0}figure{margin:0}.scene{background:#e9eff0;border:1px solid #c9d5d3;border-radius:12px;overflow:hidden;margin:32px 0}.scene img{display:block;width:100%;height:auto}figcaption{font-size:14px;padding:14px 18px;color:#40595b}.grid{display:grid;grid-template-columns:1.25fr 1fr;gap:40px;align-items:start}.overview{margin-top:30px}.overview img{width:100%;height:auto;image-rendering:pixelated;border:1px solid #c9d5d3}.overview figcaption{padding-left:0}.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;text-align:left}caption{text-align:left;font-size:14px;padding:0 0 14px}th,td{border-bottom:1px solid #cad6d4;padding:12px 16px 12px 0;white-space:nowrap}thead th{font-size:14px}.provenance{font-size:14px}.provenance dt{font-weight:600;margin-top:14px}.provenance dd{margin:4px 0;overflow-wrap:anywhere}code{font:13px/1.5 ui-monospace,monospace}time{overflow-wrap:anywhere}section{margin-bottom:36px}footer{border-top:1px solid #cad6d4;margin-top:48px;padding-top:24px;font-size:14px}@media(max-width:700px){main{padding:24px 18px 40px}header{margin-bottom:36px}.grid{grid-template-columns:1fr;gap:0}.metrics{gap:20px 32px}.metrics dd{font-size:27px}.overview{max-width:512px}}'''
    output.mkdir(parents=True,exist_ok=True)
    for name in ['blocks.png','topdown.png','report.json']:shutil.copyfile(bundle/name,output/name)
    (output/'index.html').write_text(content)
    (output/'style.css').write_text(css+'\n')
    return {'audited_tiles':source['audited_tiles'],'actual_chunks':source['actual_chunks'],
            'actual_non_air_blocks':total,'page_bytes':sum(p.stat().st_size for p in output.iterdir() if p.is_file())}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(build_site(args.bundle,args.output)))
