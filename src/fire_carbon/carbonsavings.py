import pandas as pd
import os
from pathlib import Path
import arcpy
from arcpy.sa import *
import numpy as np
import glob


CARBON_STOCK_DIR = None   # set in main() from config (paths.carbonstock_dir)

# TJ suffix labels - must match suffixes used in raster_for_carbonstock.py
TJ_LABELS = {"": "standard", "_tjlow": "tjlow", "_tjhigh": "tjhigh"}

# Each CBP variant is paired with one TJ scenario in addition to standard
# low CBP variant → tjlow, high CBP variant → tjhigh
VARIANT_TJ_PAIR = {
    "low":  "_tjlow",
    "high": "_tjhigh",
}


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def get_carbon_years(year, tflag):
    """
    Return all carbon projection years available for a given starting year
    and tflag. Excludes TJ-suffixed rasters to avoid duplicate year entries.
    """
    pattern = str(CARBON_STOCK_DIR / f"carbon_{year}_fire_*_{tflag}.tif")
    files   = glob.glob(pattern)

    years = sorted(set(
        int(Path(f).stem.split("_")[3])
        for f in files
        if not any(Path(f).stem.endswith(s)
                   for s in ("_tjlow", "_tjhigh"))
    ))
    return years


def load_carbon_rasters(year, carbon_year, tj_suffix=""):
    """
    Load the four carbon rasters for a given starting year, carbon year,
    and optional trajectory change suffix.

    tj_suffix : "" (standard), "_tjlow", or "_tjhigh"
                Applied only to fire rasters; nofire rasters are always standard.

    Returns
    -------
    carbon_fire_1, carbon_fire_0, carbon_nofire_1, carbon_nofire_0
        arcpy Raster objects.
    """
    rasters = {}

    for tflag_val, scenario in [(1, "fire"), (0, "fire"),
                                 (1, "nofire"), (0, "nofire")]:
        key = f"{scenario}_{tflag_val}"

        # TJ suffix only applies to fire scenario rasters
        suffix = tj_suffix if scenario == "fire" else ""

        p = CARBON_STOCK_DIR / (
            f"carbon_{year}_{scenario}_{carbon_year}_{tflag_val}{suffix}.tif"
        )
        if not p.exists():
            raise FileNotFoundError(f"Missing carbon raster: {p}")

        rasters[key] = arcpy.sa.Raster(str(p))

    return (
        rasters["fire_1"],
        rasters["fire_0"],
        rasters["nofire_1"],
        rasters["nofire_0"],
    )


def compute_carbon_stock_impact(carbon_fire_1, carbon_fire_0,
                                carbon_nofire_1, carbon_nofire_0,
                                phi_filter):
    """
    Compute the carbon stock impact raster and its five components.

    ΔC = (C_nofire_0 - C_fire_0) - φ × (C_nofire_1 - C_fire_1)

    tflag 0 = baseline (no treatment)
    tflag 1 = project  (with treatment)

    Returns
    -------
    cs_carbon       : arcpy Raster — final carbon stock impact (ΔC)
    components      : dict of arcpy Rasters for the five decomposed terms
    """
    nofire_proj_phi  = carbon_nofire_1 * phi_filter   # C_nofire_1 × φ
    fire_proj_phi    = carbon_fire_1   * phi_filter   # C_fire_1   × φ

    cs_carbon = (
        (carbon_nofire_0 - carbon_fire_0)
        - (nofire_proj_phi - fire_proj_phi)
    )

    components = {
        "C_nofire_proj"         : carbon_nofire_1,   # C(nofire-project) raw
        "C_nofire_proj_phi"     : nofire_proj_phi,   # C(nofire-project) × φ
        "C_nofire_base"         : carbon_nofire_0,   # C(nofire-baseline) raw
        "C_fire_proj_phi"       : fire_proj_phi,     # C(fire-project)   × φ
        "C_fire_base"           : carbon_fire_0,     # C(fire-baseline)  raw
    }

    return cs_carbon, components


def get_scenarios_for_variant(variant):
    """
    Return the dict of {tj_suffix: tj_label} to run for a given CBP variant.
    Standard always runs. The paired TJ scenario is added if the variant
    is recognized in VARIANT_TJ_PAIR.
    """
    scenarios = {"": "standard"}

    paired_tj = VARIANT_TJ_PAIR.get(variant)
    if paired_tj:
        scenarios[paired_tj] = TJ_LABELS[paired_tj]
    else:
        print(f"  [WARN] No TJ pairing defined for variant '{variant}' "
              f"- running standard only")

    return scenarios


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main(cfg, year):
    """
    Compute avoided wildfire emissions and carbon stock impact.

    Carbon stock impact is computed for:
      - standard scenario (both CBP variants)
      - tjlow scenario    (low CBP variant only)
      - tjhigh scenario   (high CBP variant only)

    Output Excel sheets:
      Avoided_Emissions  : one row per CBP file
      Carbon_Stock_Impact: one row per CBP file × carbon year × TJ scenario,
                           including five decomposed carbon stock components
    """
    global CARBON_STOCK_DIR, VARIANT_TJ_PAIR
    res = cfg.resolution
    CARBON_STOCK_DIR = Path(cfg.path("carbonstock_dir"))
    VARIANT_TJ_PAIR  = {k: f"_{v}" for k, v in cfg["carbon_stock"]["scenario_tj_pair"].items()}

    # 0. Directories (re-runs replace the earlier result rasters)
    arcpy.env.overwriteOutput = True
    cbp_dir      = Path(cfg.path("cbp_dir"))
    emission_dir = Path(cfg.path("emission_dir"))
    result_dir   = Path(cfg.path("results_dir"))
    result_dir.mkdir(parents=True, exist_ok=True)

    # CBP rasters must be named as in flammap.output_name: {year}_{scenario}_{sim_time}_{tflag}.tif
    simtime   = cfg["flammap"]["sim_time_min"]
    pattern_0 = os.path.join(cbp_dir, f"{year}_*_{simtime}_0.tif")
    pattern_1 = os.path.join(cbp_dir, f"{year}_*_{simtime}_1.tif")
    files_0   = {Path(f).name: Path(f) for f in glob.glob(str(pattern_0))}
    files_1   = sorted(glob.glob(pattern_1))

    emission_results = []   # sheet 1
    carbonstock_rows = []   # sheet 2

    # 1. Loop over CBP treatment rasters
    for f1 in files_1:
        f1    = Path(f1)
        fname = f1.name

        fname_0 = fname.replace("_1.tif", "_0.tif")
        if fname_0 not in files_0:
            print(f"[SKIP] No matching baseline CBP raster for {fname}")
            continue

        f0 = files_0[fname_0]
        print(f"\n[INFO] Processing {fname}")

        # Variant label from filename: {year}_{variant}_{simtime}_1.tif
        parts   = Path(fname).stem.split("_")
        variant = parts[1]
        print(f"  Variant: {variant}")

        # 2. Residual risk ratio phi = CBP_project / CBP_baseline
        r1 = arcpy.sa.Raster(str(f1))
        r0 = arcpy.sa.Raster(str(f0))

        pixel_area_ha = (res * res) / 10000

        arr_r0 = arcpy.RasterToNumPyArray(r0, nodata_to_value=0)
        arr_r1 = arcpy.RasterToNumPyArray(r1, nodata_to_value=0)

        avg_fire_size_baseline  = np.sum(arr_r0) * pixel_area_ha
        avg_fire_size_treatment = np.sum(arr_r1) * pixel_area_ha

        # Clamp phi to [0, 1]
        phi        = r1 / r0
        phi_filter = Con(phi > 1, 1, Con(phi < 0, 0, phi))

        ratio_raster = result_dir / f"rr_ratio_{fname}"
        phi_filter.save(str(ratio_raster))

        # 3. Emission rasters
        emission_raster_0 = emission_dir / f"emission_{year}_{res}_0.tif"
        emission_raster_1 = emission_dir / f"emission_{year}_{res}_1.tif"

        if not emission_raster_0.exists():
            print(f"[SKIP] Missing baseline emission raster for {year}")
            continue
        if not emission_raster_1.exists():
            print(f"[SKIP] Missing treatment emission raster for {year}")
            continue

        # 4. Avoided wildfire emissions  ΔW = AFP_b × (W_b - φ × W_p)
        # Emission rasters from fofem_to_raster.py are already in Mg CO2e per pixel,
        # so no further area or unit conversion is applied. Result: Mg CO2e per pixel.
        e_0     = arcpy.sa.Raster(str(emission_raster_0))
        e_1     = arcpy.sa.Raster(str(emission_raster_1))
        cs_fire = e_0 - phi_filter * e_1

        cs_fire_path = result_dir / f"cs_fire_{fname}"
        cs_fire.save(str(cs_fire_path))

        arr_emi = arcpy.RasterToNumPyArray(cs_fire, nodata_to_value=0)
        sum_emi = np.sum(arr_emi)
        print(f"  Avoided emissions total: {sum_emi:.2f}")

        # Traceability: wildfire emissions and phi, over the same pixels as cs_fire
        # (cells where cs_fire is NoData, e.g. CBP_baseline = 0, are left out).
        # Emission rasters are Mg CO2e per pixel, so the sums are Mg CO2e.
        def _masked(ras):
            return arcpy.RasterToNumPyArray(ras + 0 * cs_fire, nodata_to_value=np.nan)

        sum_w_base = float(np.nansum(_masked(e_0)))
        sum_w_proj = float(np.nansum(_masked(e_1)))
        mean_phi   = float(np.nanmean(_masked(phi_filter)))
        print(f"  W_baseline: {sum_w_base:.2f} | W_project: {sum_w_proj:.2f} | "
              f"mean phi: {mean_phi:.4f}")

        emission_results.append({
            "file"                       : fname,
            "variant"                    : variant,
            "year"                       : year,
            "resolution"                 : res,
            "total_avoided_emissions"    : sum_emi,
            "sum_W_baseline"             : sum_w_base,      # wildfire emissions, baseline (Mg CO2e)
            "sum_W_project"              : sum_w_proj,      # wildfire emissions, project (Mg CO2e)
            "mean_phi"                   : mean_phi,        # pixel mean of phi (clamped 0-1)
            "avg_fire_size_baseline_ha"  : avg_fire_size_baseline,
            "avg_fire_size_treatment_ha" : avg_fire_size_treatment,
        })

        # ── 5. Carbon stock impact ────────────────────────────────────────────
        carbon_years = get_carbon_years(year, tflag=0)
        print(f"  Carbon projection years: {carbon_years}")

        # Determine which TJ scenarios to run for this variant
        scenarios_to_run = get_scenarios_for_variant(variant)
        print(f"  TJ scenarios for '{variant}': {list(scenarios_to_run.values())}")

        for carbon_year in carbon_years:
            print(f"  Carbon stock — year {carbon_year}")

            for tj_suffix, tj_label in scenarios_to_run.items():
                try:
                    (carbon_fire_1,
                     carbon_fire_0,
                     carbon_nofire_1,
                     carbon_nofire_0) = load_carbon_rasters(
                        year, carbon_year, tj_suffix=tj_suffix
                    )
                except FileNotFoundError as e:
                    print(f"    [SKIP] {tj_label}: {e}")
                    continue

                cs_carbon, components = compute_carbon_stock_impact(
                    carbon_fire_1, carbon_fire_0,
                    carbon_nofire_1, carbon_nofire_0,
                    phi_filter,
                )

                cs_carbon_path = (
                    result_dir
                    / f"cs_carbon_{year}_{carbon_year}_{variant}{tj_suffix}.tif"
                )
                cs_carbon.save(str(cs_carbon_path))

                # Convert all rasters to numpy using np.nan for nodata,
                # then mask to pixels where cs_carbon is valid.
                # This ensures component sums use the identical pixel set
                # as total_carbon_stock_impact, so they recombine correctly.
                # Carbon rasters are t CO2e per ha (raster_for_carbonstock.py), so the
                # pixel sums are multiplied by pixel_area_ha to give totals in Mg CO2e.
                arr_carbon = arcpy.RasterToNumPyArray(cs_carbon, nodata_to_value=np.nan)
                valid_mask = np.isfinite(arr_carbon)
                sum_carbon = float(np.nansum(arr_carbon)) * pixel_area_ha
                print(f"    [{tj_label}] carbon stock impact: {sum_carbon:.2f}")

                comp_sums = {}
                for key, raster in components.items():
                    arr = arcpy.RasterToNumPyArray(raster, nodata_to_value=np.nan)
                    arr[~valid_mask] = np.nan
                    comp_sums[key] = float(np.nansum(arr)) * pixel_area_ha

                carbonstock_rows.append({
                    "cbp_file"                  : fname,
                    "variant"                   : variant,
                    "starting_year"             : year,
                    "carbon_year"               : carbon_year,
                    "tj_scenario"               : tj_label,
                    "total_carbon_stock_impact" : sum_carbon,
                    # Five decomposed components
                    "sum_C_nofire_proj"         : comp_sums["C_nofire_proj"],
                    "sum_C_nofire_proj_phi"     : comp_sums["C_nofire_proj_phi"],
                    "sum_C_nofire_base"         : comp_sums["C_nofire_base"],
                    "sum_C_fire_proj_phi"       : comp_sums["C_fire_proj_phi"],
                    "sum_C_fire_base"           : comp_sums["C_fire_base"],
                })

    # 6. Save results to Excel
    df_emissions   = pd.DataFrame(emission_results)
    df_carbonstock = pd.DataFrame(carbonstock_rows)

    out_xlsx = result_dir / f"emissionsavings_{year}.xlsx"
    with pd.ExcelWriter(str(out_xlsx), engine="openpyxl") as writer:
        df_emissions.to_excel(  writer, sheet_name="Avoided_Emissions",  index=False)
        df_carbonstock.to_excel(writer, sheet_name="Carbon_Stock_Impact", index=False)

    print(f"\n[DONE] Results written to {out_xlsx}")