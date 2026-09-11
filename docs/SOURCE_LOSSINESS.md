# Source-by-source loss analysis

These failure modes describe how the representations can lose information;
they are not measurements of a particular pilot location.

Evaluate one source at a time before fusion. Separate **availability**, **measurement error**, **missing information**, **inference error**, and **Minecraft representation error**. More sources can add contradictions as well as information.

## 1. Google Maps rendered map

**Potential value:** location context, labelled roads, visible mapped features.

**Loss:** a rendered map contains cartographic choices. Line width represents styling, not necessarily road width. Labels cover features, zoom changes detail, and screenshots discard the underlying feature geometry and metadata. A screenshot in a map projection is not a uniform metres-to-pixels reference across all locations. Search/address pins are not automatically building centroids or surveyed points.

**Individual experiment if appropriate data rights are available:** preserve viewport bounds and zoom/projection; compare at least 20 visible control points to independent vector/survey data. Test two zoom levels. Quantify outline displacement and missing topology before trying any vision model.

**Admission rule:** prefer source vectors for geometry. Treat screenshot extraction as a separate low-confidence observation; do not infer physical widths from rendered road strokes.

**Access:** current Google Maps Platform terms restrict scraping, caching, and creation of content from Maps content. An API key alone does not resolve this use. Do not build the first pipeline around it. [Google terms, §3.2.3](https://cloud.google.com/maps-platform/terms).

## 2. Google Earth aerial/oblique imagery

**Potential value:** roofs, property layout, visible textures, broad spatial context.

**Loss:** imagery may be a mosaic across dates, resolutions, and exposures. Oblique views shift apparent roof position relative to the ground; shadows resemble surfaces; trees obscure roofs and paths. Orthorectification can still leave building-related distortions. Screenshot pixels often lack the original camera intrinsics, pose, and ground sampling distance. Sharpening/upscaling cannot restore missing observations.

**Individual experiment if permitted:** distinguish orthographic/orthorectified aerial views from perspective oblique captures. Register ground controls first. Score roof outlines separately from ground footprints; inspect apparent alignment as viewing angle changes. Freeze acquisition date where known and mark it unknown otherwise.

**Admission rule:** aerial appearance can guide roof material and land cover, but absolute height requires other evidence or an independently validated reconstruction. Do not extrude a displaced roofprint as if it were the wall boundary.

**Access:** assess Earth separately from Maps Platform; an available view is not a reusable data license. Google's Earth-specific terms also restrict creating products based on Earth. [Earth additional terms](https://maps.google.com/intl/en_all/help/terms_maps-earth/). The open imagery route remains usable without this candidate.

## 3. Google Earth / Maps photogrammetric 3D mesh

**Potential value:** visible surface geometry and textures could reduce reconstruction work if obtained under suitable rights.

**Loss:** a display mesh may have multiple levels of detail, holes, floating fragments, melted façades, fused vegetation, baked shadows, and inconsistent detail. Mesh surfaces are not semantic solid buildings. Roof and tree surfaces may be connected; glass and narrow objects may disappear. View-dependent streaming makes a screenshot or partial capture incomplete.

**Individual experiment if permitted:** inspect native mesh resolution and coordinate transforms; compare two levels of detail; test triangle coverage, watertightness, ground/roof residuals, and connected components. Never assume absence of a triangle means free space. Record any hole-filling separately.

**Admission rule:** voxelize a small known building before an area. Show the effect of surface versus solid filling. Keep surface uncertainty and source LOD. Large mesh extraction is not part of the initial executable route under standard Google terms.

## 4. Street View panoramas

**Potential value:** façade appearance, door/window arrangement, storefronts, street furniture, and visible near-ground details.

**Loss:** panoramas stitch camera observations. Seams, parallax, motion, blur, camera-height uncertainty, occlusion, and capture-date changes make them imperfect geometry. Equirectangular pixels do not have uniform perspective intrinsics. GPS/heading metadata can be imprecise. A close car can hide an entire entrance; a blank region is not proof the building lacks an entrance.

**Individual experiment if permitted:** use original camera metadata when available; convert panorama regions to documented perspective crops; mask seams and transient objects. Associate crops with the correct façade using projected visibility and verify a sample manually. Evaluate visible material/window attributes separately from depth. Test geographically distinct camera positions, not just different headings from one panorama.

**Admission rule:** accept visible semantic observations first. Depth-based geometry requires scale anchors and cross-position consistency. An unseen rear façade stays unknown. An inferred floor count cannot silently become a measured building height.

**Access:** Google's standard restrictions also cover Street View acquisition and derived content. Local inference does not change that source's reuse permissions. User-owned street photography and appropriately licensed street imagery are separate adapters below.

## 5. OpenStreetMap vectors

**Potential value:** vector building outlines, road networks, water, paths, land use, and occasional height/material/roof attributes.

**Loss:** completeness, freshness, accuracy, and tagging vary by feature. Road centre lines need explicit or inferred width to become surfaces. Heights can be absent; levels are not metres. Building relations and parts may overlap or be missing. Bridges and tunnels can cross in plan view without intersecting physically. An edit timestamp need not be the physical capture date.

**Individual experiment:** measure footprint displacement/IoU against independent controls; count missing buildings and missing height/width fields; inspect invalid polygons and part relationships; verify bridge/tunnel topology. Evaluate observed attributes separately from defaults introduced by the importer.

**Admission rule:** preserve source IDs and tags. Use vectors as the initial structural scaffold where validated. Every missing width/height fallback receives an inferred label.

**Access:** OSM provides data under ODbL and requires attribution. Keep source notices and derivative data lineage with the export; determine applicable share-alike obligations before distributing data or worlds. Standard map tile servers are not the bulk vector API. [OSM copyright](https://www.openstreetmap.org/copyright).

## 6. Overture buildings

**Potential value:** complementary building coverage and a structured attribute/source schema.

**Loss:** some outlines represent roofs rather than ground walls; height fields may be missing. Conflation does not make every source equally accurate. Overture can incorporate OSM, so agreement between the two is not automatically independent evidence. Building parts and parent buildings require careful handling.

**Individual experiment:** compare OSM-only to Overture-only using the same holdout. Report additional coverage, conflicting footprints, height completeness, and shared lineage. Match features by geometry and source IDs without duplicating buildings.

**Admission rule:** add missing valid coverage or better-supported attributes; do not blindly union polygons or vote two copies of the same source against an independent survey.

**Access:** the buildings theme is published under ODbL. Pin its release and preserve sources. Google Open Buildings appearing in an open dataset is distinct from scraping Google Maps imagery. [Overture buildings guide](https://docs.overturemaps.org/guides/buildings/).

## 7. Public LiDAR and terrain models

**Potential value:** ground elevations and, with appropriate point-cloud returns, roofs/canopies and other observed surfaces.

**Loss:** a bare-earth digital terrain model deliberately removes buildings and vegetation. A digital surface model includes visible elevated surfaces but does not identify them automatically. Airborne LiDAR can miss vertical walls, undersides, glass, water surfaces, and narrow features. Classification errors, acquisition age, point density, vertical datum, and units matter. A one-metre output raster is not proof of one-metre source accuracy.

**Individual experiment:** retain classification and source accuracy metadata; inspect ground and non-ground returns separately; check control-point residuals and no-data coverage. Derive roof height relative to local ground rather than subtracting unrelated datums. Check sloped sites and building edges where interpolation is least trustworthy.

**Admission rule:** use validated ground returns for terrain. Use validated roof returns for roof surfaces. Do not lift an entire building from a bare-earth DEM or treat canopy height as roof height.

**Access:** USGS 3DEP products are free and without use restrictions; coverage, age, and product type still need checking for the selected AOI. Elsewhere select the relevant national/municipal provider and inspect that dataset's terms. [USGS 3DEP products](https://www.usgs.gov/3d-elevation-program/about-3dep-products-services?qt-science_support_page_related_con=0).

## 8. Licensed orthophotos / municipal imagery

**Potential value:** georeferenced roof colour, road surface, sidewalks, tree cover, and fine land-cover detail.

**Loss:** the same aerial occlusion, shadows, roof displacement, and temporal mismatch problems apply. Provider resolution varies. Interpolation to a finer raster produces more pixels, not more observations. A roof's photographic colour mixes material, light, weather, and image processing.

**Individual experiment:** compare a fixed set of roof/road/canopy masks to manual labels; report ground sampling distance, capture date, registration error, and shadow coverage. Separate shadowed from unshadowed material predictions.

**Admission rule:** use material families and ground masks supported by resolution. Do not create windows or small rooftop structures below source resolution. Check provider rights per dataset before implementing fetch.

## 9. User-owned / licensed street photos, including Mapillary

**Potential value:** the same visible façade attributes as Street View, with a viable route through user-owned captures or suitable licenses.

**Loss:** inconsistent camera calibration, orientation, lighting, coverage, GPS quality, and compression. Photo sequences may have insufficient baseline/overlap for reconstruction. Mapillary coverage does not imply all façades are visible. A video supplies many correlated frames; it is not thousands of independent observations.

**Individual experiment:** start with 20 labelled façade crops, then 100 if useful. Preserve EXIF/intrinsics where available, but exclude private metadata from a future public artifact. Deduplicate near-identical frames and identify capture positions. Compare visible semantic extraction to hand labels and, separately, registered geometry to controls.

**Admission rule:** ingest user-owned images first when available. For Mapillary, verify API access, current platform conditions, attribution, and the image's exact license version. Its help documentation describes imagery under CC-BY-SA; that alone is not a complete analysis of every downstream artifact's obligations. [Mapillary licensing](https://help.mapillary.com/hc/en-us/articles/115001770409-CC-BY-SA-license-for-open-data).

## 10. Local model predictions

**Potential value:** semantic interpretation and useful geometric priors from incomplete observations.

**Loss:** model resizing can erase small details; quantization may alter predictions; prompts can encourage plausible invention. A metric-depth output remains an estimate. A model can mistake reflections for windows or repeat a stereotyped façade. Unknown model training data is not evidence for the real pilot building.

**Individual experiment:** test known-answer crops with visible labels; measure accepted-prediction precision and abstention coverage. Include glass, shadows, occluded walls, unusual roofs, and images from unseen buildings. Compare quantization/runtime variants only on the same frozen inputs. Measure M1 Max timings directly.

**Admission rule:** schema-valid output is only the first gate. Reject unsupported geometry or ungrounded detail. Persist raw outputs and accepted/rejected decisions so a new model can be compared without reacquiring data.

## 11. Fusion loss

Alignment, resampling, and conflict resolution introduce their own errors. Do not mix source frames until transformed; do not average across incompatible dates; do not count related upstream sources as independent. Preserve the original observation even after selecting another value.

For every feature, record separately: source uncertainty, registration residual, inference error where measured, and voxelization change. Do not sum them as independent Gaussian errors without evidence of independence. Report per-layer error distributions and visible failure examples.

## 12. Minecraft conversion loss

Metre cells lose sub-metre detail. Diagonal walls become stair steps, thin structures can vanish, and vanilla blocks have discrete materials and collision shapes. Slabs/stairs help some geometry but are not arbitrary mesh voxels. An entrance narrower than a block raises a conflict between exact dimensions and playability.

Use a synthetic fixture before real data: a diagonal building, pitched roof, narrow alley, sloped road, bridge, water edge, and doorway. Compare continuous geometry to exported occupancy. The default preserves geometry and reports inaccessible spaces; an optional accessibility adjustment must be a named, measured deviation.

## 13. Source scorecard template

Each pilot source gets its own record before fusion:

| Field | Required answer |
|---|---|
| Source/release/assets | Exact identifiers and checksums |
| Reuse/access | Terms URL, license version, authentication needs, allowed cache/export |
| Capture date | Exact/range/unknown; retrieval date is separate |
| Coverage | Selected-area fraction and visible façade fraction |
| Resolution vs accuracy | Both, or explicit unknown |
| Geometry | CRS, units, datum, camera pose and residual where applicable |
| Missing information | A specific list, including no-data areas |
| Holdout metrics | Independent controls, count, distribution, failures |
| Proposed use | Which attributes this source may influence |
| Decision | Admit, admit for limited attributes, or exclude with reason |

Evaluate the source before asking whether AI can fill its gaps. An unavailable observation cannot be recovered merely by increasing model size.
