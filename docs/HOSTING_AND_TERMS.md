# Hosting and terms

Reviewed 2026-10-16. Plain-language notes, not legal advice. Each item has a verdict and what we changed.

## How the site loads blocks

- The 3D viewer (`dashboard/blocks.html`) and the map's cell preview read raw Anvil region files from the
  Hugging Face dataset `EricSpencer00/earthcraft` with HTTP Range requests.
- Reads are pinned to one dataset commit (`PINNED_REVISION` in `dashboard/anvil.mjs`). Content at a commit
  never changes, so results are reproducible. Use `?revision=main` to read the latest data.
- The Water Tower view (chunk 2,2, radius 8, 289 chunks) is served from this site:
  `dashboard/cache/water-tower.bin` (633,641 bytes) plus `water-tower.json` (index: chunk -> offset,
  length, timestamp; source region sha256; HF revision). The records are byte-identical copies from the region
  files. Rebuild with:
  `node scripts/build_view_cache.mjs --revision <HF commit>`, then set `PINNED_REVISION` to the same commit
  (`tests/view_cache.mjs` checks they match).
- Other places still use Range reads. The client merges nearby chunks into one request, caches region
  headers, reuses the signed CDN URL from the first redirect (skips one `302` per read), and runs at most 6
  requests at a time.

Measured against live HF data in a browser (Brave, 2026-10-16):

| View | Before | After |
| --- | --- | --- |
| Water Tower, radius 8 | 147 HF reads + 147 redirects, 3.4 MB | 0 HF requests; 2 site files, 0.64 MB |
| Water Tower, radius 3 (default) | 38 reads + 38 redirects, 0.55 MB | 0 HF requests (same pack) |
| Chunk 40,40, radius 2 (outside pack) | 20 reads + 20 redirects | 20 reads + 1 redirect |

Rendered output was the same (identical chunk and block counts).

## Verdicts

### Hugging Face: storage and serving the site from `resolve` URLs — OK

- Public storage is free but "Best-effort" for free accounts; beyond a few gigabytes, use it "responsibly by
  uploading content that offers genuine value to other users"
  ([storage limits](https://huggingface.co/docs/hub/storage-limits)). A ~42 GB public world with a dataset card
  fits that, but HF may ask for a PRO plan if it grows a lot.
- Folders must stay under 10k files ("cannot exceed 10k files per folder"); the `world/region/<bx>/` buckets
  already do this. Recommended under 100k files per repo — keep an eye on it as the world grows.
- `/resolve/` URLs are the "Resolvers" bucket, with the highest limits: 3,000 per 5 minutes per IP for anonymous
  users ([rate limits](https://huggingface.co/docs/hub/rate-limits)). Each visitor reads from their own IP, so one
  visitor used about 294 of those per Water Tower load before this change and 0 now.
- The [Terms of Service](https://huggingface.co/terms-of-service) do not ban linking to files from other sites.
  You stay "solely responsible for the Content you post", and HF can suspend service. There is no published
  bandwidth cap for public downloads.
- Required change: none. Done anyway: pack cache, pinned revision, fewer redirects, lower concurrency.

### GitHub Pages: hosting the cache — OK

- Limits: "Published GitHub Pages sites may be no larger than 1 GB", a *soft* 100 GB/month bandwidth limit,
  and it is not a free host for an online business
  ([Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits)).
- The cache adds about 0.64 MB. The deploy workflow already fails if the site is over 20 MiB. 100 GB/month is
  about 150,000 full Water Tower loads.
- The [Acceptable Use Policies](https://docs.github.com/en/site-policy/acceptable-use-policies/github-acceptable-use-policies)
  allow throttling for "significantly excessive" bandwidth. A small cache for the site's own page is normal use.
  Do not put the whole world on Pages.
- Required change: publish `dashboard/cache/` in `progress-pages.yml` (done).

### Minecraft EULA and Usage Guidelines — OK, with a disclaimer

- The [Usage Guidelines](https://www.minecraft.net/en-us/usage-guidelines) say "Do not redistribute our games or
  any alterations of our games or game files". We publish world saves that we made, not game files or textures.
  The viewer uses its own colors, with no Mojang textures or assets.
- The guidelines ask for a prominent disclaimer: "NOT AN OFFICIAL MINECRAFT [PRODUCT]. NOT APPROVED BY OR
  ASSOCIATED WITH MOJANG OR MICROSOFT".
- Do not sell access to the world or charge for it.
- Required change: add the disclaimer to the site (both pages) and to `NOTICE` (done). It should also go on the HF
  dataset card (README), which is not in this repo.

### OpenStreetMap ODbL — OK after attribution fix; one follow-up

- Required: credit "OpenStreetMap and its contributors" and say the data is under the ODbL; "If you alter or build
  upon our data, you may distribute the result only under the same license"
  ([OSM copyright](https://www.openstreetmap.org/copyright)).
- The rendered 3D view is a Produced Work, so share-alike does not apply to it. But the
  [Produced Work guideline](https://osmfoundation.org/wiki/Licence/Community_Guidelines/Produced_Work_-_Guideline)
  says the underlying database must also be published under the ODbL. Region files can be mined for building
  outlines, so treat the published world and this cache as a derivative database under the ODbL.
- Before: the map page credited OSM, but the block viewer only linked to `NOTICE`, and nothing said "ODbL".
- Required change (done): "Map data © OpenStreetMap contributors, available under the ODbL" on both pages,
  and an ODbL note in `NOTICE` for the world data and the cache.
- Follow-up (HF, outside this repo): the dataset card says `license: other`. State that the world data is under
  the ODbL 1.0 because it includes OSM data, with OSM attribution and the Minecraft disclaimer. USGS 3DEP lidar
  is public domain, and crediting it is courtesy.
