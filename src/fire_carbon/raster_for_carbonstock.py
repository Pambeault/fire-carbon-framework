import arcpy
import os
import pandas as pd
import numpy as np


# Defaults; overwritten from config.yaml (carbon_stock.*) in main()
TC_TO_TCO2E = 3.6667
SKIP_YEAR   = 2022
SHEET_NAME  = "Full Carbon Report"
FIRE_YEAR   = 2025

# ─────────────────────────────────────────────────────────────────────────────
# TRAJECTORY CHANGE PARAMETERS
# tjlow  : conservative scenario (lower τ, higher α → fewer stands, milder slowdown)
# tjhigh : severe scenario       (higher τ, lower α → more stands, stronger slowdown)
# ─────────────────────────────────────────────────────────────────────────────
TJ_SCENARIOS = {
    "tjlow":  {"tau": 0.4, "alpha": 0.7},
    "tjhigh": {"tau": 0.5, "alpha": 0.6},
}


# ─────────────────────────────────────────────────────────────────────────────
# POLYGON ID RASTER
# ─────────────────────────────────────────────────────────────────────────────

def polygon_id_to_raster(vri_src, resolution):
    """
    Rasterize the POLYGON_ID field of the VRI layer once.
    Returns the in-memory raster path.
    """
    ras_mem = r"in_memory\ras_polygon_id"

    if arcpy.Exists(ras_mem):
        arcpy.management.Delete(ras_mem)

    arcpy.conversion.PolygonToRaster(
        vri_src, "POLYGON_ID", ras_mem,
        cell_assignment="CELL_CENTER",
        cellsize=resolution,
    )
    return ras_mem


# ─────────────────────────────────────────────────────────────────────────────
# CARBON RASTER WRITER
# ─────────────────────────────────────────────────────────────────────────────

def map_carbon_to_raster(ras_polygon_id, df_year, out_folder,
                         year, starting_year, tflag, scenario, target_crs,
                         resolution, suffix=""):
    """
    Use the POLYGON_ID raster as a spatial template and replace each
    POLYGON_ID value with its corresponding tCO2e carbon value via numpy lookup.
    Save the result as a GeoTIFF.

    Returns the carbon numpy array for reuse by trajectory change functions.
    """
    carbon_map = dict(zip(df_year["StandID"], df_year["Total_Stand_Carbon_tco2e"]))

    id_arr     = arcpy.RasterToNumPyArray(ras_polygon_id).astype(np.int32)
    carbon_arr = np.full(id_arr.shape, np.nan, dtype=np.float32)

    for stand_id, carbon_val in carbon_map.items():
        carbon_arr[id_arr == int(stand_id)] = carbon_val

    desc       = arcpy.Describe(ras_polygon_id)
    lower_left = arcpy.Point(desc.extent.XMin, desc.extent.YMin)

    raster_out = arcpy.NumPyArrayToRaster(
        carbon_arr,
        lower_left_corner=lower_left,
        x_cell_size=resolution,
        y_cell_size=resolution,
        value_to_nodata=np.nan,
    )
    arcpy.management.DefineProjection(raster_out, target_crs)

    out_tif = os.path.join(
        out_folder,
        f"carbon_{starting_year}_{scenario}_{year}_{tflag}{suffix}.tif"
    )
    raster_out.save(out_tif)
    print(f"  ✓ Saved: carbon_{starting_year}_{scenario}_{year}_{tflag}{suffix}.tif")

    return carbon_arr


# ─────────────────────────────────────────────────────────────────────────────
# TRAJECTORY CHANGE
# ─────────────────────────────────────────────────────────────────────────────

def apply_trajectory_change(
    ras_polygon_id,
    out_folder,
    starting_year,
    tflag,
    target_crs,
    carbon_fire_arrays,
    carbon_nofire_fire_year,
    tj_label,
    tau,
    alpha,
    resolution,
):
    """
    For pixels where C_fire(FIRE_YEAR) / C_nofire(FIRE_YEAR) < tau,
    apply trajectory change to all fire carbon rasters:

        C_tj(t) = C_fire(FIRE_YEAR) + alpha * (C_fire(t) - C_fire(FIRE_YEAR))

    Initial post-fire carbon stock at FIRE_YEAR is unchanged by definition.
    Outputs saved as carbon_{starting_year}_fire_{year}_{tflag}_{tj_label}.tif

    Returns a dict with pixel count, area (ha), and percentage of landscape affected,
    plus per-year carbon sums for diagnostic tracking.
    """
    suffix           = f"_{tj_label}"
    carbon_fire_year = carbon_fire_arrays[FIRE_YEAR]

    # ── Ratio diagnostic ─────────────────────────────────────────────────────
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(
            (carbon_nofire_fire_year > 0) & np.isfinite(carbon_nofire_fire_year),
            carbon_fire_year / carbon_nofire_fire_year,
            np.nan,
        )

    valid_ratios = ratio[np.isfinite(ratio)]
    print(f"  [{tj_label}] Ratio C_fire/C_nofire at FIRE_YEAR — "
          f"mean={np.nanmean(valid_ratios):.3f}, "
          f"min={np.nanmin(valid_ratios):.3f}, "
          f"max={np.nanmax(valid_ratios):.3f}, "
          f"pct below tau ({tau}): "
          f"{100 * np.sum(valid_ratios < tau) / len(valid_ratios):.1f}%")

    # ── Trajectory change mask ────────────────────────────────────────────────
    tj_mask = (ratio < tau) & np.isfinite(ratio)

    n_valid   = int(np.sum(np.isfinite(ratio)))
    n_pixels  = int(np.sum(tj_mask))
    pct       = 100 * n_pixels / n_valid if n_valid > 0 else 0.0

    pixel_area_ha = (resolution * resolution) / 10000
    tj_area_ha    = n_pixels * pixel_area_ha
    total_area_ha = n_valid  * pixel_area_ha

    print(f"  [{tj_label}] τ={tau}, α={alpha} → "
          f"{n_pixels} trajectory-change pixels "
          f"({tj_area_ha:.1f} ha, {pct:.1f}% of valid area)")

    desc       = arcpy.Describe(ras_polygon_id)
    lower_left = arcpy.Point(desc.extent.XMin, desc.extent.YMin)

    # ── Save trajectory change mask raster ───────────────────────────────────
    mask_arr    = tj_mask.astype(np.float32)
    mask_raster = arcpy.NumPyArrayToRaster(
        mask_arr,
        lower_left_corner=lower_left,
        x_cell_size=resolution,
        y_cell_size=resolution,
        value_to_nodata=-9999,
    )
    arcpy.management.DefineProjection(mask_raster, target_crs)
    mask_tif = os.path.join(
        out_folder,
        f"tj_mask_{starting_year}_{tflag}_{tj_label}.tif"
    )
    mask_raster.save(mask_tif)
    print(f"  ✓ TJ mask saved: tj_mask_{starting_year}_{tflag}_{tj_label}.tif")

    # ── Apply trajectory change and track per-year carbon sums ───────────────
    # Timestep-based compounding formulation:
    # std_increment is always computed from consecutive standard arrays (no tj feedback).
    # alpha scales the standard rate: the stand would have grown by X,
    # but instead grows by alpha*X (regrowth) or X/alpha (mortality).
    # The deficit compounds year over year without drift.
    yearly_carbon_summary = []

    sorted_years   = sorted(carbon_fire_arrays.keys())
    tj_carbon_prev = carbon_fire_arrays[FIRE_YEAR].copy()

    for i, year in enumerate(sorted_years):
        carbon_arr = carbon_fire_arrays[year]

        if year == FIRE_YEAR:
            tj_arr = carbon_arr.copy()
        else:
            prev_year     = sorted_years[i - 1]
            std_increment = carbon_arr - carbon_fire_arrays[prev_year]

            dampened_increment = np.where(
                std_increment >= 0,
                alpha * std_increment,
                std_increment / alpha,
            )

            no_tj_mask = ~tj_mask

            tj_arr                = np.full(carbon_arr.shape, np.nan, dtype=np.float32)
            tj_arr[no_tj_mask]    = carbon_arr[no_tj_mask]                                        # no TJ: standard value
            tj_arr[tj_mask]       = tj_carbon_prev[tj_mask] + dampened_increment[tj_mask]         # TJ: compounded dampened increment

        tj_carbon_prev = tj_arr.copy()

        # Diagnostic: compare sum before and after trajectory change
        sum_before = float(np.nansum(carbon_arr))
        sum_after  = float(np.nansum(tj_arr))
        pct_diff   = 100 * (sum_after - sum_before) / sum_before if sum_before != 0 else 0.0
        yearly_carbon_summary.append({
            "year"        : year,
            "sum_standard": round(sum_before, 2),
            "sum_tj"      : round(sum_after,  2),
            "diff"        : round(sum_after - sum_before, 2),
            "pct_diff"    : round(pct_diff, 2),
        })

        raster_out = arcpy.NumPyArrayToRaster(
            tj_arr,
            lower_left_corner=lower_left,
            x_cell_size=resolution,
            y_cell_size=resolution,
            value_to_nodata=np.nan,
        )
        arcpy.management.DefineProjection(raster_out, target_crs)

        out_tif = os.path.join(
            out_folder,
            f"carbon_{starting_year}_fire_{year}_{tflag}{suffix}.tif"
        )
        raster_out.save(out_tif)
        print(f"  ✓ Saved: carbon_{starting_year}_fire_{year}_{tflag}{suffix}.tif")

    return {
        "starting_year"        : starting_year,
        "tflag"                : tflag,
        "tj_label"             : tj_label,
        "tau"                  : tau,
        "alpha"                : alpha,
        "n_pixels_tj"          : n_pixels,
        "tj_area_ha"           : round(tj_area_ha,   2),
        "total_valid_area_ha"  : round(total_area_ha, 2),
        "pct_area_tj"          : round(pct,           2),
        "yearly_carbon_summary": yearly_carbon_summary,
    }


# ─────────────────────────────────────────────────────────────────────────────
# SCENARIO PROCESSOR
# ─────────────────────────────────────────────────────────────────────────────

def process_scenario(scenario, report_prefix, ras_polygon_id,
                     in_folder, out_folder, starting_year, target_crs,
                     resolution, apply_tj=False):
    """
    Loop over treatment flags and years for a given scenario (fire or nofire).

    report_prefix : "carbon_report_fire" or "carbon_report"
    apply_tj      : if True, also generate trajectory change rasters
                    (only meaningful for the fire scenario)
    """
    tj_summary_rows = []

    for tflag in (0, 1):
        report_path = os.path.join(in_folder, f"{report_prefix}_{tflag}.xlsx")

        if not os.path.exists(report_path):
            print(f"  ⚠ Report not found, skipping: {report_path}")
            continue

        print(f"\n=== [{scenario}] Treatment flag {tflag} — "
              f"reading {os.path.basename(report_path)} ===")

        df = pd.read_excel(report_path, sheet_name=SHEET_NAME)
        df.columns = df.columns.str.strip()
        df["Total_Stand_Carbon_tco2e"] = df["Total_Stand_Carbon"] * TC_TO_TCO2E

        years = sorted([y for y in df["Year"].unique() if y != SKIP_YEAR])
        print(f"  Years to process: {years}")

        # ── Diagnostic: compare mean carbon between tflag=0 and tflag=1 ──────
        fire_year_mean = df[df["Year"] == FIRE_YEAR]["Total_Stand_Carbon_tco2e"].mean()
        print(f"  Mean Total_Stand_Carbon_tco2e at FIRE_YEAR ({FIRE_YEAR}): "
              f"{fire_year_mean:.2f} tCO2e  [tflag={tflag}]")

        carbon_fire_arrays = {}

        for year in years:
            print(f"  Processing year {year}")

            df_year = (
                df[df["Year"] == year][["StandID", "Total_Stand_Carbon_tco2e"]]
                .copy()
                .drop_duplicates(subset="StandID", keep="first")
            )

            # Diagnostic: report per-year mean carbon
            print(f"    tflag={tflag}, year={year}, "
                  f"mean carbon={df_year['Total_Stand_Carbon_tco2e'].mean():.2f}, "
                  f"n stands={len(df_year)}")

            carbon_arr = map_carbon_to_raster(
                ras_polygon_id, df_year, out_folder,
                year, starting_year, tflag, scenario, target_crs,
                resolution,
            )

            if apply_tj and scenario == "fire":
                carbon_fire_arrays[year] = carbon_arr

        # ── Trajectory change (fire scenario only) ────────────────────────────
        if apply_tj and scenario == "fire" and FIRE_YEAR in carbon_fire_arrays:

            nofire_report = os.path.join(in_folder, f"carbon_report_{tflag}.xlsx")
            if not os.path.exists(nofire_report):
                print(f"  ⚠ No nofire report found for tflag={tflag}, skipping TJ")
                continue

            df_nofire = pd.read_excel(nofire_report, sheet_name=SHEET_NAME)
            df_nofire.columns = df_nofire.columns.str.strip()
            df_nofire["Total_Stand_Carbon_tco2e"] = (
                df_nofire["Total_Stand_Carbon"] * TC_TO_TCO2E
            )
            df_nofire_fy = (
                df_nofire[df_nofire["Year"] == FIRE_YEAR]
                [["StandID", "Total_Stand_Carbon_tco2e"]]
                .copy()
                .drop_duplicates(subset="StandID", keep="first")
            )

            # Build nofire numpy array for the fire year
            id_arr     = arcpy.RasterToNumPyArray(ras_polygon_id).astype(np.int32)
            nofire_arr = np.full(id_arr.shape, np.nan, dtype=np.float32)
            nofire_map = dict(zip(
                df_nofire_fy["StandID"],
                df_nofire_fy["Total_Stand_Carbon_tco2e"]
            ))
            for stand_id, val in nofire_map.items():
                nofire_arr[id_arr == int(stand_id)] = val

            # Diagnostic: compare fire and nofire arrays at FIRE_YEAR
            fire_arr_fy = carbon_fire_arrays[FIRE_YEAR]
            print(f"\n  [Diagnostic tflag={tflag}] At FIRE_YEAR={FIRE_YEAR}:")
            print(f"    C_fire  mean={np.nanmean(fire_arr_fy):.2f}, "
                  f"min={np.nanmin(fire_arr_fy):.2f}, "
                  f"max={np.nanmax(fire_arr_fy):.2f}")
            print(f"    C_nofire mean={np.nanmean(nofire_arr):.2f}, "
                  f"min={np.nanmin(nofire_arr):.2f}, "
                  f"max={np.nanmax(nofire_arr):.2f}")

            with np.errstate(divide="ignore", invalid="ignore"):
                ratio_diag = np.where(
                    (nofire_arr > 0) & np.isfinite(nofire_arr),
                    fire_arr_fy / nofire_arr,
                    np.nan,
                )
            valid = ratio_diag[np.isfinite(ratio_diag)]
            print(f"    Ratio C_fire/C_nofire — "
                  f"mean={np.nanmean(valid):.3f}, "
                  f"min={np.nanmin(valid):.3f}, "
                  f"max={np.nanmax(valid):.3f}")
            for tj_label, params in TJ_SCENARIOS.items():
                tau = params["tau"]
                n_below = int(np.sum(valid < tau))
                print(f"    Pixels below τ={tau} ({tj_label}): "
                      f"{n_below} ({100*n_below/len(valid):.1f}%)")

            # Apply both TJ scenarios
            for tj_label, params in TJ_SCENARIOS.items():
                print(f"\n  --- Trajectory change: {tj_label} ---")
                summary = apply_trajectory_change(
                    ras_polygon_id          = ras_polygon_id,
                    out_folder              = out_folder,
                    starting_year           = starting_year,
                    tflag                   = tflag,
                    target_crs              = target_crs,
                    carbon_fire_arrays      = carbon_fire_arrays,
                    carbon_nofire_fire_year = nofire_arr,
                    tj_label                = tj_label,
                    tau                     = params["tau"],
                    alpha                   = params["alpha"],
                    resolution              = resolution,
                )
                if summary:
                    tj_summary_rows.append(summary)

    # ── Write TJ area summary to Excel ───────────────────────────────────────
    if tj_summary_rows:
        # Flatten yearly_carbon_summary out of the summary rows for Excel
        summary_flat = [
            {k: v for k, v in row.items() if k != "yearly_carbon_summary"}
            for row in tj_summary_rows
        ]
        df_tj    = pd.DataFrame(summary_flat)
        out_xlsx = os.path.join(
            out_folder,
            f"tj_area_summary_{starting_year}.xlsx"
        )

        yearly_rows = []
        for row in tj_summary_rows:
            for yr_row in row["yearly_carbon_summary"]:
                yearly_rows.append({
                    "starting_year": row["starting_year"],
                    "tflag"        : row["tflag"],
                    "tj_label"     : row["tj_label"],
                    "tau"          : row["tau"],
                    "alpha"        : row["alpha"],
                    **yr_row,
                })
        df_yearly = pd.DataFrame(yearly_rows)

        with pd.ExcelWriter(out_xlsx, engine="openpyxl") as writer:
            df_tj.to_excel(writer, sheet_name="TJ_Area_Summary", index=False)
            df_yearly.to_excel(writer, sheet_name="Carbon_Sums_By_Year", index=False)

        print(f"\n  ✓ TJ area summary written: {out_xlsx}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main(cfg, starting_year):
    """
    Create total stand carbon rasters (tCO2e) for each projection year.

    Naming convention (base rasters):
        carbon_{starting_year}_{scenario}_{year}_{tflag}.tif

    Trajectory change rasters (fire scenario only, if apply_tj=True):
        carbon_{starting_year}_fire_{year}_{tflag}_tjlow.tif
        carbon_{starting_year}_fire_{year}_{tflag}_tjhigh.tif

    Trajectory change mask rasters (if apply_tj=True):
        tj_mask_{starting_year}_{tflag}_tjlow.tif
        tj_mask_{starting_year}_{tflag}_tjhigh.tif

    Trajectory change area summary (if apply_tj=True):
        tj_area_summary_{starting_year}.xlsx  (two sheets: area summary + carbon sums by year)

    Parameters
    ----------
    map_name        : ArcGIS Pro map name (e.g. "VRI approach")
    vri_layer_name  : layer name within that map (e.g. "VRI_2022_AOI")
    starting_year   : landscape fuel year (e.g. 2022)
    in_folder       : folder containing carbon_report_fire_0/1.xlsx and carbon_report_0/1.xlsx
    out_folder      : folder to write output rasters and summaries
    resolution      : raster cell size in metres (must match FlamMap landscape resolution)
    apply_tj        : if True, generate trajectory change rasters and diagnostics
    """
    global TC_TO_TCO2E, SKIP_YEAR, FIRE_YEAR, TJ_SCENARIOS
    ccfg = cfg["carbon_stock"]
    TC_TO_TCO2E    = ccfg["c_to_co2e"]
    SKIP_YEAR      = ccfg["skip_year"]
    FIRE_YEAR      = ccfg["fire_year"]
    TJ_SCENARIOS   = ccfg["trajectory_scenarios"]
    apply_tj       = ccfg["apply_trajectory_change"]
    map_name       = cfg.map_name
    vri_layer_name = cfg.layer("vri", starting_year)
    in_folder      = cfg.path("fvs_dir", year=starting_year)
    out_folder     = cfg.path("carbonstock_dir")

    resolution = float(cfg.resolution)
    target_crs = cfg.spatial_reference()

    # Snap/extent raster = landscape DEM written by step 6.
    dem_folder           = cfg.path("landscape_dir", year=starting_year)
    dem_raster           = os.path.join(dem_folder, f"dem_filled_{int(resolution)}.tif")
    arcpy.env.snapRaster = dem_raster
    arcpy.env.cellSize   = resolution   # numeric, not a raster path
    arcpy.env.extent     = dem_raster

    arcpy.env.overwriteOutput = True

    m       = cfg.open_map()
    vri_src = [lyr for lyr in m.listLayers() if lyr.name == vri_layer_name][0]

    os.makedirs(out_folder, exist_ok=True)

    print("Rasterizing POLYGON_ID...")
    ras_polygon_id = polygon_id_to_raster(vri_src, resolution)
    print("✓ POLYGON_ID raster ready")

    process_scenario(
        "fire",   "carbon_report_fire", ras_polygon_id,
        in_folder, out_folder, starting_year, target_crs, resolution,
        apply_tj=apply_tj,
    )
    process_scenario(
        "nofire", "carbon_report",      ras_polygon_id,
        in_folder, out_folder, starting_year, target_crs, resolution,
        apply_tj=False,
    )

    arcpy.management.Delete(ras_polygon_id)
    print("\n🎉 Carbon stock rasters complete.")