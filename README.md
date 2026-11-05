# Fire–carbon framework

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23984891.svg)](https://doi.org/10.5281/zenodo.23984891)

Code for the framework described in:

> **[PLACEHOLDER: AUTHORS] ([PLACEHOLDER: YEAR]). [PLACEHOLDER: PAPER TITLE]. *[PLACEHOLDER: JOURNAL]*. https://doi.org/[PLACEHOLDER: PAPER DOI]** *(in review)*

The framework estimates how much carbon is saved when fuel treatments (here,
Indigenous-led cultural / prescribed burning) reduce the probability and severity
of wildfire. It follows the *Reduced Emissions from Megafires (REM) Forecast
Methodology v1.0* and links four models:

```
VRI (BC forest inventory)
  └─► FVS  (stand growth, fuels, carbon; with and without treatment and wildfire)
        └─► FlamMap MTT  (conditional burn probability, baseline vs treated)   ← manual step (landscape + MTT)
        └─► FOFEM        (wildfire and prescribed-burn emissions)
              └─► Carbon savings (avoided emissions + carbon-stock impact, REM)
```

## Reproducing the process, not the exact results

This repository lets you **re-run the method**. It does not reproduce the
paper's numbers exactly, for three reasons:

1. **The treatment polygons are not shared.** The burned-area polygons
   (`WalkPoly`) are community data and are not distributed. To run the framework
   you need your own treatment polygons (see [docs/DATA.md](docs/DATA.md)).
2. **The synthetic tree lists and ignitions are random.** FVS inputs are generated from VRI
   stand attributes using random draws. The paper's runs were unseeded
   (`fvs.random_seed: null`), so each run differs slightly. Set a seed to make
   your own runs repeatable.
3. **The FlamMap landscape and MTT runs are manual.** The pipeline writes the
   landscape rasters, but the landscape file is built by hand in FlamMap and MTT
   is run in the FlamMap interface (see [docs/FLAMMAP.md](docs/FLAMMAP.md)).

The paper's final result rasters are archived separately (see **Results data** below).

This allows you to also run this analysis for another location and context.

### What to adapt before applying the framework elsewhere

All of these are set in [`config.yaml`](config.yaml):

| What | Config key | Why it matters |
|---|---|---|
| FVS variant, ecoregion, inventory year | `fvs.variant`, `fvs.ecoregion`, `fvs.inventory_year` | Growth and mortality equations are region-specific. The BC variant and `KAM-IDFxh1/04` fit the Stone study area only. |
| Species translation and tree allometry | `VRI_to_FVS.py` (`translate_species`, `dbh_to_height_species`, `height_to_cbh_species`) | Covers the species present in the study area only. Add your species. |
| Fuel-type conversion (Canadian FBP → US fuel models) | `data/supporting/fuel_type_conversion.csv` | This is an expert mapping for interior BC. Review it for your fuel types. |
| Treated fuel assumption | `landscape.fuel_assumption` (`low`/`high`) | The fuel model assigned to treated cells. |
| FlamMap weather, fuel moisture, simulation time | `flammap.*`, docs/FLAMMAP.md | Should be explored in a sensitivity analysis. |
| FVS prescribed burn and wildfire (SimFire) | `fvs.prescribed_burn`, `fvs.wildfire` | Season, wind, moisture and temperature of the simulated fires. |
| FOFEM moisture, region, cover group, soil | `fofem.wildfire`, `fofem.prescribed` | Consumption and emission assumptions. |
| Global warming potential (GWP) values | `emissions.gwp` | AR5 100-yr values, as in REM v1.0. |
| Post-fire trajectory change | `carbon_stock.trajectory_scenarios` | Delayed-regeneration assumption (τ, α). |
| Stands with FVS-FFE mortality crashes | `fvs.mortality_skip_stands` | Specific to these stands. Replace it with your own list (or leave it empty). |

## Requirements

| Software | Version used | Notes |
|---|---|---|
| Windows | 10/11 | FVS, FOFEM and FlamMap are Windows programs. |
| ArcGIS Pro + **Spatial Analyst** | 3.7 | Gives `arcpy`. Licensed software, not included. |
| Python (ArcGIS Pro conda env) | 3.13 | Clone `arcgispro-py3`. See [environment.yml](environment.yml). |
| FVS, BC variant (`FVSbc.exe`) | FS2022.4 (RV 20221118) | USDA Forest Service, free. |
| FlamMap | 6.2.10.0 | Missoula Fire Sciences Lab, free. |
| FOFEM | 6.7 | Missoula Fire Sciences Lab, free. |

Python packages (all included with ArcGIS Pro 3.7 except as noted):
`arcpy`, `pandas`, `numpy`, `openpyxl`, `PyYAML`.

```bash
conda create --name fire-carbon --clone arcgispro-py3
```

## Quick start

1. Install the software above and download the input data ([docs/DATA.md](docs/DATA.md)).
2. Build an ArcGIS Pro project with a map holding the layers listed in
   `gis.layers` (AOI, DEM, VRI per year, BCWS fuel types per year, treatment polygons).
3. Edit **`config.yaml`**: set `paths.root`, `paths.gdb`, `paths.aprx`, the
   `executables`, and review every modelling assumption.
4. Run the steps in order (from the ArcGIS Pro Python environment):

```bash
python run_pipeline.py --list
```

```bash
python run_pipeline.py --steps treatment export_vri vri_processing vri_to_fvs --years 2022
```

| # | Step | Script | Run time |
|---|---|---|---|
| 0 | `treatment` | treatment_intersection.py | minutes |
| 1 | `export_vri` | Utilities.py | minutes |
| 2 | `vri_processing` | VRI_processing.py | minutes |
| 3 | `vri_to_fvs` | VRI_to_FVS.py | ~1 h |
| 4 | `fvs_run` | FVS_run.py | ~3 h per treatment flag |
| 4.1 | `fvs_fire_run` | FVS_fire_run.py | hours |
| 5 | `fvs_read` | FVS_read.py | 10–15 min |
| 5.1 | `fvs_fire_read` | FVS_fire_read.py | 10–15 min |
| 6 | `landscape` | raster_for_landscape.py (landscape rasters only) | minutes |
| — | **Build landscape + run MTT in FlamMap (manual)** | [docs/FLAMMAP.md](docs/FLAMMAP.md) | hours |
| 5.5 | `carbon_stock` | raster_for_carbonstock.py | minutes |
| 7 | `fofem` | FVS_to_fofem.py | 3–5 h |
| 8 | `fofem_raster` | fofem_to_raster.py | ~5 min |
| 8.1 | `eifs` | E_IFS_run.py | minutes |
| 10 | `carbon_savings` | carbonsavings.py | minutes |

Treatment flag `0` = baseline (no treatment), `1` = with treatment. Steps that
depend on it run both flags.

## Repository layout

```
config.yaml                 master configuration (all paths + assumptions)
run_pipeline.py             runs the steps in order
src/fire_carbon/            pipeline modules (one per step) + config loader
data/supporting/            small files shipped with the code (fuel conversion table)
docs/DATA.md                where to download every input
docs/FLAMMAP.md             the manual FlamMap MTT step and the settings used
```

## Results data

The paper's final result rasters and summary tables (burn-probability ratios,
emission rasters, carbon-stock rasters, carbon savings) are archived on Zenodo
under CC-BY 4.0: **https://doi.org/[PLACEHOLDER: ZENODO DATA DOI]**. They contain no
treatment polygons.

## Known issues and notes for reviewers

The code reproduces the paper's calculations unchanged. The items below are
documented so that users can judge them, not silently "fixed":

- **Random tree lists.** `VRI_to_FVS.py` draws tree heights and diameters at
  random. The paper's runs were unseeded. Set `fvs.random_seed` for repeatable runs.
- **CBP file names.** FlamMap outputs must be saved as
  `{year}_{scenario}_{sim_time}_{tflag}.tif` for step 10 to find them.

## How to cite

If you use this code, please cite the paper above and the software:

> Ambeault, P. (2026). *Fire–carbon framework* [Computer software]. Zenodo. https://doi.org/10.5281/zenodo.23984891

This DOI always resolves to the latest version. Each release also has its own
DOI, listed on the Zenodo page.

A machine-readable citation is in [CITATION.cff](CITATION.cff).

## Licence

- Code: [MIT](LICENSE).
- Documentation and the archived results data: [CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/).
- Third-party data and software (VRI, BCWS fuel types, DEM, FVS, FlamMap,
  FOFEM, ArcGIS Pro) keep their own licences (see docs/DATA.md).

## Acknowledgements

The authors would like to acknowledge that the research took place on the unceded and ancestral territories of the Tsilqhot’in Peoples. We thank the Yunesit’in community and their Land for their hospitality.

This research was supported by Mitacs through the Mitacs Accelerate program, Gathering Voices Society, Real Estate Foundation of British Columbia, Environment and Climate Change Canada (ECCC) Indigenous-led Natural Climate Solutions fund, and the Natural Resources Canada (NRCAN) Build and Mobilize Foundational Wildland Fire Knowledge fund.

