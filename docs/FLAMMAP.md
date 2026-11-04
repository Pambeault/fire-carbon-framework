# FlamMap MTT (manual step)

The conditional burn probability (CBP) was computed in the **FlamMap 6**
interface (version 6.2.10.0), not from the scripts. Run this step after
`landscape` (step 6) and before `carbon_stock` / `carbon_savings`.

## 1. Build the landscape (manual, in FlamMap)

The pipeline does **not** create the FlamMap landscape file (`.lcp`). It only
writes the input rasters; the landscape is assembled by hand in FlamMap.
Step 6 writes these rasters to `paths.landscape_dir/<year>/` (EPSG:3005, cell
size `gis.resolution_m`):

| FlamMap theme | File | Units |
|---|---|---|
| Elevation | `dem_filled_<res>.tif` | m |
| Slope | `slope_<res>.tif` | degrees |
| Aspect | `aspect_<res>.tif` | degrees azimuth |
| Fuel model | `fuel_model_<assumption>_<tflag>.tif` | Scott & Burgan code |
| Canopy cover | `cover_<target_year>_<tflag>.tif` | % |
| Canopy height | `height_<target_year>_<tflag>.tif` | m × 10 |
| Canopy base height | `cbh_<target_year>_<tflag>.tif` | m × 10 |
| Canopy bulk density | `cbd_<target_year>_<tflag>.tif` | kg m⁻³ × 100 |

`tflag` 0 = baseline, 1 = treated. In FlamMap, create one landscape per year
and tflag from these GeoTIFFs (*Landscape → Create landscape from GeoTIFFs*),
assigning each file to its theme and checking the units above. Elevation,
slope and aspect are the same for both tflags.

## 2. Run MTT

Settings used in the paper (Analysis_Sensitivity):

| Parameter | Low estimate | High estimate |
|---|---|---|
| Wind speed (km h⁻¹ @ 10 m) | 23 | 29 |
| Wind direction (azimuth) (°) | 320 | 170 |
| Fuel moisture | Very dry | Very dry |
| Treatment fuel model assumption | Moderate | Moderate + burnable |
| Resolution (m) | 80 | 80 |
| Simulation time (min) | 2400 | 2400 |

"Treatment fuel model assumption" corresponds to the `treated_low` /
`treated_high` columns of `data/supporting/fuel_type_conversion.csv`. They
differ only for non-fuel (N) cells, which stay non-burnable under "moderate"
(99) and become burnable grass (101) under "moderate + burnable". Build the
treated landscape with the matching `landscape.fuel_assumption`.

### Fuel moisture

Fixed fuel moisture used. Columns: fuel model, 1-h, 10-h, 100-h, live herbaceous,
live woody (%). Save as a text file (`.fms`) and load it in FlamMap:

```
101 3 4 5 30 60
102 3 4 5 30 60
104 3 4 5 30 60
161 3 4 5 30 60
182 3 4 5 30 60
183 3 4 5 30 60
185 3 4 5 30 60
186 3 4 5 30 60
202 3 4 5 30 60
181 3 4 5 30 60
201 3 4 5 30 60
```

### Canopy characteristics

- Foliar moisture content = 100%
- Crown fire calculation method = Scott/Reinhardt (2001)

### MTT – Ignitions

- Random = 1500. The ignitions were generated once and reused between runs with
  the *From fire list file* option.

### MTT – Inputs

- Resolution = 80
- Maximum simulation time = 2400
- Interval for minimum travel path = 500 (default)
- Spot probability = 0 (default)
- Spotting delay = 0 (default)
- Lateral search depth = 6 (default)
- Vertical search depth = 4 (default)

These settings are also recorded in the `flammap` section of `config.yaml`.

These values are inputs to a sensitivity analysis, not fixed constants. Vary them
for your own study area.

## 3. Save the outputs

Export the burn-probability grid of each run as GeoTIFF to `paths.cbp_dir`, named

```
{year}_{scenario}_{sim_time}_{tflag}.tif      e.g. 2022_high_2400_1.tif
```

where `scenario` is a name from `project.scenarios` (`low`, `high`). Step 10
(`carbon_savings`) pairs each `_1` file with its `_0` file.
