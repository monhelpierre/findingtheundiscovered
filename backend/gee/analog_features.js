/**
 * Earth Analog Explorer: per-cell features from Google Earth Engine.
 *
 * Computes, for every grid cell, the same features the `analog` pipeline computes from
 * SRTM/HLS, but from global datasets on Google's servers, so any place on Earth can be scored
 * without downloading imagery.
 *
 * HOW TO RUN
 *   1. Open https://code.earthengine.google.com (needs an Earth Engine account/project; free for
 *      non-commercial use), paste this whole file, edit SETTINGS below, click Run.
 *   2. Open the Tasks tab and click RUN on each "analogfeat_..." export. Files land in
 *      Google Drive/<DRIVE_FOLDER>/ as analogfeat_<site>.tif (several tiles for the whole Earth).
 *   3. Download them, then on your computer:
 *        analog region create <site id> --bbox W S E N        (same id and bbox as in SITES)
 *        analog import <folder with the analogfeat_*.tif files> --run
 *      The candidates, tiers and criteria then appear in the web app like any other region.
 *
 * OUTPUT: one float32 GeoTIFF per site, EPSG:4326, aligned to the same grid as
 * `analog region create` (origin = the bbox's west/north edge, cell = CELL_DEG), with 16 bands.
 * BAND ORDER IS THE CONTRACT with backend/src/analog/process.py (GEE_BANDS): Earth Engine's
 * GeoTIFF exports don't keep band names. Change both together.
 *
 *   band            definition (per cell)                                   source
 *   elev_mean       mean elevation (m)                                      NASADEM 30 m
 *   slope_mean      mean of 30 m slope (deg)                                NASADEM 30 m
 *   slope_p75       75th percentile of 30 m slope (deg)                     NASADEM 30 m
 *   roughness       standard deviation of elevation (m)                     NASADEM 30 m
 *   relief          max - min elevation (m)                                 NASADEM 30 m
 *   albedo          white-sky shortwave albedo, median (0-1)                MODIS MCD43A3
 *   ndvi            NDVI, median                                            MODIS MOD13A1
 *   water_fraction  share of 30 m pixels that are water >= 50% of the time  JRC Global Surface Water
 *   temp_range      median of (day - night) land surface temperature (K)    MODIS MOD11A2
 *   clay_index      SWIR 1.6 um / SWIR 2.1 um reflectance (clays absorb)    MODIS MCD43A4 NBAR
 *   ferric_index    red / blue reflectance (iron oxides)                    MODIS MCD43A4 NBAR
 *   gentle_slope_frac share of land pixels with slope <= 15 deg (water and   NASADEM 30 m +
 *                   ocean excluded): operations-safe ground                 JRC water mask
 *   permafrost_class 3 frozen at the surface, 2 within 50 cm, 1 at 50-100 cm, ERA5-Land monthly
 *                   0 not frozen: a soil layer whose warmest month (2015-2024 soil temperature
 *                   mean) stays below 0 C is permanently frozen               (~9 km)
 *   passable_2km    share of ground at 0-15 deg within 2 km (spacesuit)      NASADEM 30 m, 1 km
 *   passable_10km   ... within 10 km (unpressurised rover)                   NASADEM 30 m, 1 km
 *   passable_50km   ... within 50 km (pressurised rover)                     NASADEM 30 m, 5 km
 *
 * The last four bands come from the Artemis III Moon-base profile of the sister project
 * (NASA Space Apps 2026, Sherbrooke): same definitions, so both projects' numbers compare.
 * Files exported before they were added (11 or 12 bands) still import.
 *
 * The terrain definitions are the same as the pipeline's (rasterops.terrain_to_grid): statistics
 * of the 30 m DEM inside each cell. The surface bands come from MODIS (500 m - 1 km) instead of
 * HLS (30 m): same quantities, coarser, and available everywhere. EMIT minerals are not included
 * (EMIT only covers arid land); use the 1 km regional pipeline for minerals.
 *
 * WHOLE EARTH (SITES = []): the land is split into one export task per continent (CONTINENTS
 * below), because a single global task is too heavy and can fail after a long run. Daily MODIS
 * products are sampled on 4 days per month (enough for a robust median, ~8x less work).
 * If a task still fails, open it in the Tasks tab, copy the error, and make CONTINENTS smaller.
 */

// ============================== SETTINGS ==============================

// Sites to compute: same id and [WEST, SOUTH, EAST, NORTH] (degrees) you'll give to
// `analog region create`. Leave the list EMPTY to compute all land between 56 S and 60 N
// (NASADEM's coverage) on a coarser grid.
var SITES = [
  // {id: 'namib-sossusvlei', bbox: [15.0, -25.0, 15.6, -24.4]},
  // {id: 'altiplano-uyuni', bbox: [-68.2, -20.6, -67.6, -20.0]},
];

// Cell size in degrees. 0.01 (~1 km) matches `analog region create`'s default;
// 0.05 (~5 km) for the whole Earth keeps the export (and later the scoring) manageable.
var CELL_DEG = SITES.length ? 0.01 : 0.05;

// Period for the MODIS medians (albedo, NDVI, temperature, reflectance ratios).
var START = '2024-01-01';
var END = '2025-01-01';

var DRIVE_FOLDER = 'analog_gee';

// ======================================================================

var BANDS = ['elev_mean', 'slope_mean', 'slope_p75', 'roughness', 'relief', 'albedo', 'ndvi',
             'water_fraction', 'temp_range', 'clay_index', 'ferric_index', 'gentle_slope_frac',
             'permafrost_class', 'passable_2km', 'passable_10km', 'passable_50km'];
var GENTLE_SLOPE_DEG = 15;

// Whole-Earth mode: one export per box (all land 56 S - 60 N, NASADEM's coverage).
var CONTINENTS = [
  {id: 'north_america', bbox: [-170, 15, -50, 60]},
  {id: 'south_america', bbox: [-120, -56, -30, 15]},
  {id: 'europe', bbox: [-25, 35, 45, 60]},
  {id: 'africa', bbox: [-20, -36, 55, 35]},
  {id: 'asia_west', bbox: [45, 0, 100, 60]},
  {id: 'asia_east', bbox: [100, -11, 180, 60]},
  {id: 'oceania', bbox: [110, -48, 180, -11]}
];

// Daily products: keep days 1, 9, 17 and 25 of each month.
function sampleDays(col) {
  return col.filter(ee.Filter.inList('day', [1, 9, 17, 25]));
}

// Median of a collection, keeping the collection's native projection so that
// reduceResolution below averages native pixels (a plain median() loses the projection).
function medianNative(col, bands) {
  var proj = ee.Image(col.first()).select(bands[0]).projection();
  return col.select(bands).median().setDefaultProjection(proj);
}

// Aggregate fine pixels into the output cells with a reducer (mean, max, percentile...).
function toCells(img, reducer, crsTransform) {
  return img
    .reduceResolution({reducer: reducer, maxPixels: 65535})
    .reproject({crs: 'EPSG:4326', crsTransform: crsTransform});
}

// 30 m inputs (DEM, slope, water): for coarse cells (whole Earth, ~5.5 km) one step would ask
// Earth Engine for ~34,000 pixels per cell x 65,536 cells per tile, over its 2^31-pixel limit
// ("Number of pixels requested ... exceeds the maximum allowed"). So aggregate in two steps,
// 30 m -> ~300 m -> cell. mean, max and min stay exact; p75 becomes the mean of the 300 m
// blocks' 75th percentiles (a close approximation). Site mode (~1 km cells) uses one step.
var TWO_STEP = CELL_DEG > 0.02;
var MID_SCALE_M = 300;
// Exports are computed in tiles of SHARD x SHARD output cells, and every tile asks for all the 30 m
// pixels under it. The default tile (256 cells) at 0.05 deg is ~1,400 km wide = ~2.2 billion 30 m
// pixels, over Earth Engine's 2^31 limit even with the two-step aggregation. 32 cells at 0.05 deg
// (~180 km, ~35 million pixels) is well inside it; 1 km cells keep the default.
var SHARD = Math.max(16, Math.min(256, Math.round(1.6 / CELL_DEG)));
function fine30(img, kind, crsTransform) {
  var first = {mean: ee.Reducer.mean(), max: ee.Reducer.max(), min: ee.Reducer.min(),
               p75: ee.Reducer.percentile([75])}[kind];
  if (!TWO_STEP) {
    return toCells(img, first, crsTransform);
  }
  var mid = img.reduceResolution({reducer: first, maxPixels: 1024})
    .reproject({crs: 'EPSG:4326', scale: MID_SCALE_M});
  var second = {mean: ee.Reducer.mean(), max: ee.Reducer.max(), min: ee.Reducer.min(),
                p75: ee.Reducer.mean()}[kind];
  return toCells(mid, second, crsTransform);
}

// Permafrost depth class from ERA5-Land soil temperature (layers 0-7, 7-28, 28-100 cm), 2015-2024:
// a layer whose warmest monthly mean stays below 0 C is permanently frozen.
function permafrostClass() {
  var col = ee.ImageCollection('ECMWF/ERA5_LAND/MONTHLY_AGGR').filterDate('2015-01-01', '2025-01-01')
    .select(['soil_temperature_level_1', 'soil_temperature_level_2', 'soil_temperature_level_3'],
            ['t1', 't2', 't3']);
  var clim = ee.ImageCollection(ee.List.sequence(1, 12).map(function (m) {
    return col.filter(ee.Filter.calendarRange(m, m, 'month')).mean();
  }));
  var frozen = clim.max().setDefaultProjection(col.first().projection()).lt(273.15);
  return ee.Image(0).where(frozen.select('t3'), 1).where(frozen.select('t2'), 2)
    .where(frozen.select('t1'), 3).setDefaultProjection(col.first().projection());
}

// Passable ground within 2, 10 and 50 km: the walking range of a spacesuit, an unpressurised rover
// and a pressurised rover. `gentle` is the 30 m land mask at 0-15 deg; averaged to 1 km (and 5 km for
// 50 km) first, so the neighbourhoods stay small (radius of 2-10 pixels).
function passable(gentle) {
  var p1k = ee.Projection('EPSG:4326').atScale(1000);
  var p5k = ee.Projection('EPSG:4326').atScale(5000);
  var g300 = gentle.unmask(0).reduceResolution({reducer: ee.Reducer.mean(), maxPixels: 1024})
    .reproject({crs: 'EPSG:4326', scale: MID_SCALE_M});
  var g1k = g300.reduceResolution({reducer: ee.Reducer.mean(), maxPixels: 1024}).reproject(p1k);
  var g5k = g1k.reduceResolution({reducer: ee.Reducer.mean(), maxPixels: 64}).reproject(p5k);
  function within(img, metres, proj) {
    return img.reduceNeighborhood({reducer: ee.Reducer.mean(),
                                   kernel: ee.Kernel.circle({radius: metres, units: 'meters'})})
      .reproject(proj);
  }
  return [within(g1k, 2000, p1k), within(g1k, 10000, p1k), within(g5k, 50000, p5k)];
}

function features(crsTransform) {
  // --- terrain: statistics of the 30 m DEM inside each cell ---
  var dem = ee.Image('NASA/NASADEM_HGT/001').select('elevation');
  var slope = ee.Terrain.slope(dem);
  var z = dem.toDouble();
  var elevMean = fine30(z, 'mean', crsTransform);
  var slopeMean = fine30(slope, 'mean', crsTransform);
  var slopeP75 = fine30(slope, 'p75', crsTransform);
  // Standard deviation as sqrt(mean(z^2) - mean(z)^2): exact in both modes, like the pipeline.
  var roughness = fine30(z.multiply(z), 'mean', crsTransform)
    .subtract(elevMean.multiply(elevMean)).max(0).sqrt();
  var relief = fine30(z, 'max', crsTransform).subtract(fine30(z, 'min', crsTransform));
  // Operations-safe ground: share of LAND pixels at 0-15 deg. Water (JRC, water >= 50% of the time)
  // and the ocean (flat, elevation set to one value) are masked out so they don't count as flat ground.
  var land = ee.Image('JRC/GSW1_4/GlobalSurfaceWater').select('occurrence').unmask(0).lt(50)
    .and(slope.gt(0));
  var gentle = slope.lte(GENTLE_SLOPE_DEG).updateMask(land);

  // --- surface ---
  var albedoCol = sampleDays(ee.ImageCollection('MODIS/061/MCD43A3').filterDate(START, END)
    .map(function (im) { return im.set('day', ee.Date(im.get('system:time_start')).get('day')); }));
  var albedo = medianNative(albedoCol, ['Albedo_WSA_shortwave']).multiply(0.001);

  var ndviCol = ee.ImageCollection('MODIS/061/MOD13A1').filterDate(START, END);
  var ndvi = medianNative(ndviCol, ['NDVI']).multiply(0.0001);

  var occurrence = ee.Image('JRC/GSW1_4/GlobalSurfaceWater').select('occurrence');
  var isWater = occurrence.unmask(0).gte(50);

  var lstCol = ee.ImageCollection('MODIS/061/MOD11A2').filterDate(START, END);
  var lstProj = ee.Image(lstCol.first()).select('LST_Day_1km').projection();
  var tempRange = lstCol.map(function (im) {
    return im.select('LST_Day_1km').subtract(im.select('LST_Night_1km')).multiply(0.02)
      .rename('temp_range');
  }).median().setDefaultProjection(lstProj);

  // Band-ratio mineral hints, as in the pipeline's HLS step (process.py), from MODIS NBAR:
  // Band1 = red, Band3 = blue, Band6 = SWIR 1.6 um, Band7 = SWIR 2.1 um.
  var nbarBands = ['Nadir_Reflectance_Band1', 'Nadir_Reflectance_Band3',
                   'Nadir_Reflectance_Band6', 'Nadir_Reflectance_Band7'];
  var nbarCol = sampleDays(ee.ImageCollection('MODIS/061/MCD43A4').filterDate(START, END)
    .map(function (im) { return im.set('day', ee.Date(im.get('system:time_start')).get('day')); }));
  var nbar = medianNative(nbarCol, nbarBands).multiply(0.0001);
  var red = nbar.select('Nadir_Reflectance_Band1');
  var blue = nbar.select('Nadir_Reflectance_Band3');
  var swir1 = nbar.select('Nadir_Reflectance_Band6');
  var swir2 = nbar.select('Nadir_Reflectance_Band7');
  var clay = swir1.divide(swir2).updateMask(swir2.gt(0.01)).clamp(0, 5);
  var ferric = red.divide(blue).updateMask(blue.gt(0.01)).clamp(0, 10);

  var pass = passable(gentle);
  // Cells smaller than the source (1 km or 5 km): nearest value; larger: mean.
  function coarse(img) {
    return img.reduceResolution({reducer: ee.Reducer.mean(), maxPixels: 4096})
      .reproject({crs: 'EPSG:4326', crsTransform: crsTransform});
  }

  return ee.Image.cat([
    elevMean, slopeMean, slopeP75, roughness, relief,
    toCells(albedo, ee.Reducer.mean(), crsTransform),
    toCells(ndvi, ee.Reducer.mean(), crsTransform),
    fine30(isWater, 'mean', crsTransform),
    toCells(tempRange, ee.Reducer.mean(), crsTransform),
    toCells(clay, ee.Reducer.mean(), crsTransform),
    toCells(ferric, ee.Reducer.mean(), crsTransform),
    fine30(gentle, 'mean', crsTransform),
    permafrostClass().reproject({crs: 'EPSG:4326', crsTransform: crsTransform}),
    coarse(pass[0]), coarse(pass[1]), coarse(pass[2])
  ]).rename(BANDS).toFloat();
}

function exportArea(id, bbox) {
  var w = bbox[0], s = bbox[1], e = bbox[2], n = bbox[3];
  // Grid origin at the bbox's north-west corner, like `analog region create`.
  var crsTransform = [CELL_DEG, 0, w, 0, -CELL_DEG, n];
  var img = features(crsTransform);
  var safe = String(id).replace(/[^A-Za-z0-9_-]/g, '_');
  Export.image.toDrive({
    image: img,
    description: 'analogfeat_' + safe,
    folder: DRIVE_FOLDER,
    fileNamePrefix: 'analogfeat_' + safe,
    region: ee.Geometry.Rectangle([w, s, e, n], null, false),
    crs: 'EPSG:4326',
    crsTransform: crsTransform,
    shardSize: SHARD,
    maxPixels: 1e10,
    fileFormat: 'GeoTIFF'
  });
  return img;
}

var preview;
if (SITES.length) {
  SITES.forEach(function (site) {
    var img = exportArea(site.id, site.bbox);
    preview = preview || {img: img, bbox: site.bbox};
    print('Export queued: analogfeat_' + site.id + ' ' + JSON.stringify(site.bbox) + ', cell ' + CELL_DEG + ' deg');
  });
} else {
  CONTINENTS.forEach(function (c) {
    var img = exportArea('earth_' + c.id, c.bbox);
    preview = preview || {img: img, bbox: c.bbox};
    print('Export queued: analogfeat_earth_' + c.id + ' ' + JSON.stringify(c.bbox) + ', cell ' + CELL_DEG + ' deg');
  });
  print('Whole Earth: ' + CONTINENTS.length + ' exports (one per continent). Run them all, then ' +
        'import all the files together.');
}

// Quick look in the map panel (computed on the fly; the exports are what count). Only a small box
// (at most 2 x 2 deg): drawing a whole continent from 30 m data would hit the same pixel limit.
var b = preview.bbox;
var cx = (b[0] + b[2]) / 2, cy = (b[1] + b[3]) / 2;
var pw = Math.min(b[2] - b[0], 2) / 2, ph = Math.min(b[3] - b[1], 2) / 2;
var previewBox = ee.Geometry.Rectangle([cx - pw, cy - ph, cx + pw, cy + ph], null, false);
Map.centerObject(previewBox, 8);
Map.addLayer(preview.img.select('slope_mean').clip(previewBox), {min: 0, max: 20, palette: ['f7fbff', '08306b']}, 'slope_mean');
Map.addLayer(preview.img.select('ndvi').clip(previewBox), {min: 0, max: 0.6, palette: ['a6611a', 'f5f5f5', '018571']}, 'ndvi', false);
Map.addLayer(preview.img.select('temp_range').clip(previewBox), {min: 0, max: 40, palette: ['2c7bb6', 'ffffbf', 'd7191c']}, 'temp_range', false);
print('Bands (order matters for analog import): ' + BANDS.join(', '));
print('Next: Tasks tab -> RUN each export, download from Google Drive/' + DRIVE_FOLDER +
      ', then `analog import <folder> --run`.');
