"""Run the fire–carbon framework step by step.

All paths and settings come from config.yaml (or the file given with --config).

Examples
--------
List the steps:
    python run_pipeline.py --list
Run one step for one landscape year:
    python run_pipeline.py --steps vri_processing --years 2022
Run everything up to the FlamMap landscape rasters:
    python run_pipeline.py --steps treatment export_vri vri_processing vri_to_fvs fvs_run landscape_fuel fvs_fire_run fvs_read fvs_fire_read landscape

FlamMap MTT is a MANUAL step between `landscape` and `carbon_stock`
(see docs/FLAMMAP.md). Several steps take hours (noted below). Each step only
needs the outputs of the steps before it, so you can run them one at a time.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from fire_carbon.config import load_config  # noqa: E402

TREATMENT_FLAGS = (0, 1)   # 0 = baseline (no treatment), 1 = with treatment


def step_treatment(cfg, year):
    from fire_carbon import treatment_intersection
    treatment_intersection.main(cfg, year)


def step_export_vri(cfg, year):
    from fire_carbon import Utilities
    Utilities.Get_table_from_layer(cfg.path("aprx"), cfg.map_name, cfg.layer("vri", year),
                                   cfg.path("raw_dir", f"VRI_RAW_{year}.xlsx", mkdir=True))


def step_vri_processing(cfg, year):
    from fire_carbon import VRI_processing
    VRI_processing.main(cfg, year)


def step_vri_to_fvs(cfg, year):
    from fire_carbon import VRI_to_FVS
    VRI_to_FVS.main(cfg, year)


def step_fvs_run(cfg, year):
    from fire_carbon import FVS_run
    for t in TREATMENT_FLAGS:
        FVS_run.main(cfg, year, t)


def step_landscape_fuel(cfg, year):
    from fire_carbon import raster_for_landscape
    raster_for_landscape.fuel_model_rasters(cfg, year)


def step_fvs_fire_run(cfg, year):
    from fire_carbon import FVS_fire_run
    for t in TREATMENT_FLAGS:
        FVS_fire_run.main(cfg, year, t)


def step_fvs_read(cfg, year):
    from fire_carbon import FVS_read
    for t in TREATMENT_FLAGS:
        FVS_read.main(cfg, year, t)


def step_fvs_fire_read(cfg, year):
    from fire_carbon import FVS_fire_read
    for t in TREATMENT_FLAGS:
        FVS_fire_read.main(cfg, year, t)


def step_landscape(cfg, year):
    from fire_carbon import raster_for_landscape
    raster_for_landscape.main(cfg, year)


def step_carbon_stock(cfg, year):
    from fire_carbon import raster_for_carbonstock
    raster_for_carbonstock.main(cfg, year)


def step_fofem(cfg, year):
    from fire_carbon import FVS_to_fofem
    for t in TREATMENT_FLAGS:
        FVS_to_fofem.main(cfg, year, t)


def step_fofem_raster(cfg, year):
    from fire_carbon import fofem_to_raster
    for t in TREATMENT_FLAGS:
        fofem_to_raster.main(cfg, year, t)


def step_eifs(cfg, year):
    from fire_carbon import E_IFS_run
    E_IFS_run.main(cfg, year)


def step_carbon_savings(cfg, year):
    from fire_carbon import carbonsavings
    carbonsavings.main(cfg, year)


# (name, function, description) in run order
STEPS = [
    ("treatment",      step_treatment,      "0   Treatment intersection: share of each VRI polygon that was treated"),
    ("export_vri",     step_export_vri,     "1   Export the VRI attribute table to Excel"),
    ("vri_processing", step_vri_processing, "2   Fill empty VRI polygons (IDW), add elevation/slope/aspect/x/y"),
    ("vri_to_fvs",     step_vri_to_fvs,     "3   Build FVS input databases (synthetic tree lists), ~1 h"),
    ("fvs_run",        step_fvs_run,        "4   FVS growth runs, no wildfire (+ prescribed burn if treated), ~3 h per flag"),
    ("landscape_fuel", step_landscape_fuel, "4.0 Fuel model rasters only (needed by fvs_fire_run with fvs.wildfire_fuel_model: landscape)"),
    ("fvs_fire_run",   step_fvs_fire_run,   "4.1 FVS runs with a severe wildfire"),
    ("fvs_read",       step_fvs_read,       "5   Read FVS outputs: carbon, potfire, structure reports"),
    ("fvs_fire_read",  step_fvs_fire_read,  "5.1 Read FVS wildfire outputs: carbon reports"),
    ("landscape",      step_landscape,      "6   FlamMap landscape rasters (topography, fuel model, canopy)"),
    # ---- MANUAL: FlamMap MTT, see docs/FLAMMAP.md ----
    ("carbon_stock",   step_carbon_stock,   "5.5 Carbon stock rasters through time (+ trajectory change)"),
    ("fofem",          step_fofem,          "7   FOFEM wildfire emissions from FVS fuels, 3-5 h"),
    ("fofem_raster",   step_fofem_raster,   "8   Emission rasters (Mg CO2e per pixel)"),
    ("eifs",           step_eifs,           "8.1 Emissions from the prescribed (IFS) burns"),
    ("carbon_savings", step_carbon_savings, "10  Avoided emissions and carbon-stock impact (needs FlamMap CBP rasters)"),
]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None, help="path to config.yaml (default: repo config.yaml)")
    ap.add_argument("--steps", nargs="+", help="step names to run, in the order given")
    ap.add_argument("--years", nargs="+", type=int, help="landscape years (default: project.landscape_years)")
    ap.add_argument("--list", action="store_true", help="list the steps and exit")
    args = ap.parse_args(argv)

    if args.list or not args.steps:
        print("Steps (run order):")
        for name, _, desc in STEPS:
            print(f"  {name:<15} {desc}")
            if name == "landscape":
                print(f"  {'(manual)':<15}     FlamMap MTT, see docs/FLAMMAP.md")
        return

    cfg = load_config(args.config)
    years = args.years or cfg["project"]["landscape_years"]
    lookup = {name: fn for name, fn, _ in STEPS}
    unknown = [s for s in args.steps if s not in lookup]
    if unknown:
        ap.error(f"unknown step(s): {unknown}. Use --list.")

    for step in args.steps:
        for year in years:
            print(f"\n######## {step} | landscape year {year} ########")
            lookup[step](cfg, year)


if __name__ == "__main__":
    main()
