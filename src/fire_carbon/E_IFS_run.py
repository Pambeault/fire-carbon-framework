import math
import os
import glob
import sqlite3
import subprocess
import pandas as pd
import numpy as np
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

# All settings come from config.yaml (executables.*, fofem.prescribed, emissions).
# These module-level values are set in main().
FOFEM_EXE     = None
FOFEM_WORKDIR = None
TARGET_YEAR   = 2025


# ─────────────────────────────────────────────────────────────────────────────
# CO2e CONVERSION
# ─────────────────────────────────────────────────────────────────────────────

def co2eq(df, ecfg):
    """
    Convert FOFEM emissions outputs to CO2-equivalent following REM Equation 6.3.
    Consistent with fofem_to_raster.py co2eq function.!!!Should be. but this has been updated

    FOFEM output column 'Std' contains StandID_Year.
    Gas columns (CO2F, CO2S, etc.) are in lbs/acre.
    FlaCon, SmoCon are in tons/acre.
    Output unit: tons CO2e / acre (matching existing pipeline).
    """
    # config: emissions.gwp
    GWP_CH4 = ecfg["gwp"]["CH4"]
    GWP_CO = ecfg["gwp"]["CO"]
    GWP_NMVOC = ecfg["gwp"]["NMVOC"]
    GWP_N2O = ecfg["gwp"]["N2O"]
    GWP_PM25 = ecfg["gwp"]["PM25"]

    # Coerce all emission columns to numeric
    emission_cols = ["CO2F", "CO2S", "CH4F", "CH4S", "COF", "COS",
                     "PM25F", "PM25S", "FlaCon", "SmoCon"]
    for col in emission_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        else:
            print(f"  ⚠ Column '{col}' not found in FOFEM output - using 0")
            df[col] = 0.0

    df["co2eq"] = (
        ((df["CO2F"] + df["CO2S"])/2000
         + (df["CH4F"] + df["CH4S"])/2000 * GWP_CH4
         + (df["COF"]  + df["COS"])/2000  * GWP_CO
         + (df["PM25F"] + df["PM25S"])/2000 * GWP_PM25)
        + (df["FlaCon"] + df["SmoCon"]) * GWP_NMVOC * (ecfg["nmvoc_ef"] / 1000)
        + (df["FlaCon"] + df["SmoCon"]) * GWP_N2O   * (ecfg["n2o_ef"] / 1_000_000)
    )
    return df


# ─────────────────────────────────────────────────────────────────────────────
# FUEL LOAD BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_fofem_input(df, p):
    # p = config fofem.prescribed
    """
    Build FOFEM input DataFrame for prescribed spring burn.
    Crown foliage, crown branch and percent crown burn are all zero
    since IFS does not burn trees.
    Surface fuel columns pulled from FVS_Fuels_Metric.
    """
    out = pd.DataFrame()
    out["Plot Id"]   = df["Stand_year"]
    out["Litter"]    = round(df["Surface_Litter"], 2)
    out["1 Hour"]    = round(df["Surface_lt76"] * p["fine_fuel_split"]["one_hr"], 2)
    out["10 Hour"]   = round(df["Surface_lt76"] * p["fine_fuel_split"]["ten_hr"], 2)
    out["10 Hour Moisture"]  = p["moisture_10hr"]
    out["100 Hour"]  = round(df["Surface_lt76"] * p["fine_fuel_split"]["hundred_hr"], 2)
    out["1000 Hour"] = round(df["Surface_ge76"], 2)
    out["1000 Hour Moisture"]              = p["moisture_1000hr"]
    out["1000 Hour Percent Rotten"]        = str(p["pct_rotten_1000hr"])
    out["1000 Hour Weight Distribution"]   = p["weight_distribution_1000hr"]
    out["Duff"]          = df["Surface_Duff"]/p["duff_load_divisor"]   # config fofem.prescribed.duff_load_divisor
    out["Duff Moisture"] = p["duff_moisture"]
    out["Duff Depth"]    = round(
        df["Surface_Duff"] * 907.185 / 4046.86 / p["duff_bulk_density_kg_m3"] * 39.37, 2
    )
    out["Herbaceous"] = round(df["Surface_Herb"], 2)
    out["Shrub"]      = round(df["Surface_Shrub"], 2)

    # No crown involvement for prescribed grassland burn
    out["Crown Foliage"]        = 0.0
    out["Crown Branch"]         = 0.0
    out["Percent of Crown Burn"] = 0

    out["Region"]        = p["region"]
    out["Cover Group"]   = p["cover_group"]
    out["Season"]        = p["season"]
    out["Fuel Category"] = p["fuel_category"]
    out["Soil Family"]   = p["soil_family"]
    out["Soil Moisture"] = p["soil_moisture"]

    return out


# ─────────────────────────────────────────────────────────────────────────────
# FOFEM RUNNER
# ─────────────────────────────────────────────────────────────────────────────

def run_fofem_single(row, idx, batch_dir, switch_treatment):
    """
    Write a single-row FOFEM input, run FOFEM, return output DataFrame row
    or None if the run failed or produced no output.
    """
    in_path  = batch_dir / f"in_ifs_{idx}_{switch_treatment}.csv"
    out_path = batch_dir / f"out_ifs_{idx}_{switch_treatment}.txt"
    run_path = batch_dir / f"run_ifs_{idx}_{switch_treatment}.txt"
    err_path = batch_dir / f"err_ifs_{idx}_{switch_treatment}.txt"

    row.to_csv(in_path, sep=",", index=False, header=False)

    cmd = [
        str(FOFEM_EXE), "C",
        str(in_path), str(out_path),
        str(run_path), str(err_path),
        "H",
    ]
    subprocess.run(cmd, cwd=str(FOFEM_WORKDIR),
                   capture_output=True, text=True)

    if not out_path.exists():
        return None

    df_out = pd.read_csv(out_path, sep=",", engine="python")
    if len(df_out) < 2:
        return None

    return df_out.iloc[[1]]


def run_fofem_batches(df_fofem_in, io_dir, switch_treatment, batch_size=1000):
    """
    Run FOFEM for every row in df_fofem_in, collecting outputs into batches.
    Returns concatenated output DataFrame.
    """
    n          = len(df_fofem_in)
    num_batches = math.ceil(n / batch_size)
    all_results = []

    for j in range(num_batches):
        batch_dir = io_dir / str(j)
        batch_dir.mkdir(parents=True, exist_ok=True)
        batch_rows = []

        for i in range(batch_size):
            idx = j * batch_size + i
            if idx >= n:
                break

            row = df_fofem_in.iloc[[idx]]
            result = run_fofem_single(row, idx, batch_dir, switch_treatment)
            if result is not None:
                batch_rows.append(result)

        if batch_rows:
            batch_df = pd.concat(batch_rows, ignore_index=True)
            batch_path = batch_dir / f"batch_ifs_{j}_{switch_treatment}.csv"
            batch_df.to_csv(batch_path, index=False)
            all_results.append(batch_df)
            print(f"  [OK] batch {j}: {len(batch_df)} rows")

    if not all_results:
        return pd.DataFrame()

    return pd.concat(all_results, ignore_index=True)


# ─────────────────────────────────────────────────────────────────────────────
# FUEL LOADER
# ─────────────────────────────────────────────────────────────────────────────

def load_fuels_for_treated_stands(fvs_dir, treated_ids, year):
    """
    Pull surface fuel loads at year 2025 from FVS nofire output databases
    (switch_treatment=0) for the treated stands only.

    Parameters
    ----------
    fvs_dir     : Path to FVS output folder (contains FVS_out_**_0.db files)
    treated_ids : set of str POLYGON_IDs that received IFS treatment
    year        : landscape year - used only for labelling

    Returns
    -------
    pd.DataFrame with columns needed by build_fofem_input
    """
    pattern = str(Path(fvs_dir) / "FVS_out_**_0.db")
    db_files = sorted(glob.glob(pattern))

    if not db_files:
        raise FileNotFoundError(f"No nofire FVS databases found in {fvs_dir}")

    print(f"  Found {len(db_files)} nofire FVS databases")

    fuels_list = []

    for db_path in db_files:
        with sqlite3.connect(db_path) as conn:
            df_fuels = pd.read_sql_query(
                f"SELECT * FROM FVS_Fuels_Metric WHERE Year = {TARGET_YEAR};", conn
            )

        if df_fuels.empty:
            continue

        df_fuels["StandID"] = df_fuels["StandID"].astype(str).str.strip()

        # Keep only treated stands
        df_fuels = df_fuels[df_fuels["StandID"].isin(treated_ids)].copy()

        if df_fuels.empty:
            continue

        df_fuels["Stand_year"] = (
            df_fuels["StandID"] + "_" + df_fuels["Year"].astype(str)
        )
        fuels_list.append(df_fuels)

    if not fuels_list:
        raise RuntimeError("No fuel data found for any treated stand.")

    df_all = pd.concat(fuels_list, ignore_index=True)
    print(f"  Fuel rows loaded for treated stands: {len(df_all)}")
    return df_all


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main(cfg, landscape_year):
    """
    Estimate E_IFS (prescribed burn emissions) in Mg CO2e for a given
    landscape year.

    Parameters
    ----------
    cfg            : Config  loaded config.yaml
    landscape_year : int     landscape (VRI) year, e.g. 2022
    """
    global FOFEM_EXE, FOFEM_WORKDIR, TARGET_YEAR
    FOFEM_EXE     = Path(cfg.exe("fofem"))
    FOFEM_WORKDIR = Path(cfg.exe("fofem_workdir"))
    TARGET_YEAR   = cfg["fvs"]["target_year"]
    pcfg    = cfg["fofem"]["prescribed"]
    fvs_dir = cfg.path("fvs_dir", year=landscape_year)
    io_dir  = Path(cfg.path("fofem_ifs_dir"))  / str(landscape_year)
    out_dir = Path(cfg.path("results_dir"))
    io_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    switch_treatment = 0   # nofire scenario databases
    TREATMENT_XLS = Path(cfg.treatment_table(landscape_year))

    # ── 1. Load treatment intersection ───────────────────────────────────────
    print(f"\n=== E_IFS estimation: landscape year {landscape_year} ===")

    df_treat = pd.read_excel(TREATMENT_XLS)
    df_treat["POLYGON_ID"] = df_treat["POLYGON_ID"].astype(str).str.strip()
    treated_ids = set(df_treat["POLYGON_ID"].values)
    print(f"  Treated stands: {len(treated_ids)}")

    # ── 2. Load fuel data for treated stands from FVS nofire databases ───────
    print("\n  Loading fuel data from FVS nofire outputs...")
    df_fuels = load_fuels_for_treated_stands(fvs_dir, treated_ids, landscape_year)

    # ── 3. Build FOFEM input ─────────────────────────────────────────────────
    df_fofem_in = build_fofem_input(df_fuels, pcfg)

    fofem_in_path = io_dir / f"fofem_ifs_input_{landscape_year}.csv"
    df_fofem_in.to_csv(fofem_in_path, index=False)
    print(f"  FOFEM input written: {fofem_in_path} ({len(df_fofem_in)} rows)")

    # ── 4. Run FOFEM ─────────────────────────────────────────────────────────
    print("\n  Running FOFEM...")
    df_fofem_out = run_fofem_batches(
        df_fofem_in, io_dir, switch_treatment, batch_size=cfg["fofem"]["batch_size"]
    )

    if df_fofem_out.empty:
        print("  ⚠ No FOFEM output produced. Check inputs and FOFEM installation.")
        return

    fofem_out_path = io_dir / f"fofem_ifs_output_{landscape_year}.csv"
    df_fofem_out.to_csv(fofem_out_path, index=False)
    print(f"  FOFEM output written: {fofem_out_path} ({len(df_fofem_out)} rows)")

    # ── 5. Convert to CO2e ───────────────────────────────────────────────────
    print("\n  Computing CO2e...")
    df_fofem_out.columns = df_fofem_out.columns.str.strip()
    df_fofem_out = co2eq(df_fofem_out, cfg["emissions"])

    # ── 6. Extract StandID from FOFEM output 'Std' column ────────────────────
    # FOFEM renames 'Plot Id' to 'Std' in the output CSV
    # Std format is StandID_Year (e.g. 12345678_2025)
    df_fofem_out[["StandID", "Year"]] = (
        df_fofem_out["Std"]
        .astype(str)
        .str.rsplit("_", n=1, expand=True)
    )
    df_fofem_out["StandID"] = df_fofem_out["StandID"].astype(str).str.strip()

    # ── 7. Join treatment percentage and area ────────────────────────────────
    df_result = df_fofem_out.merge(
        df_treat[["POLYGON_ID", "POLY_HA", "INTERSECT_HA", "PERCENT"]],
        left_on="StandID",
        right_on="POLYGON_ID",
        how="left",
    )

    missing = df_result["PERCENT"].isna().sum()
    if missing > 0:
        print(f"  ⚠ {missing} FOFEM rows could not be matched to treatment file")

    # co2eq is in tons CO2e/acre - convert to Mg CO2e/ha then scale by treated area
    # 1 short ton = 0.90718474 Mg; 1 acre = 0.40468564 ha  ->  tons/acre x 2.2417 = Mg/ha
    TONS_ACRE_TO_MG_HA = 0.90718474 / 0.40468564
    df_result["treated_fraction"] = (df_result["PERCENT"] / 100).clip(0, 1)
    df_result["E_IFS_MgCO2e"] = (
            df_result["co2eq"]
            * TONS_ACRE_TO_MG_HA  # tons/acre -> Mg/ha
            * df_result["POLY_HA"]  # Mg/ha -> Mg per polygon
            * df_result["treated_fraction"]  # scale to treated fraction only
    )

    total_eifs = df_result["E_IFS_MgCO2e"].sum()
    print(f"\n  Total E_IFS: {total_eifs:.2f} Mg CO2e")

    # ── 8. Summary ───────────────────────────────────────────────────────────
    df_summary = pd.DataFrame([{
        "landscape_year"       : landscape_year,
        "n_treated_stands"     : df_result["StandID"].nunique(),
        "total_treated_ha"     : df_result["INTERSECT_HA"].sum(),
        "E_IFS_MgCO2e"        : round(total_eifs, 2),
        "E_IFS_tCO2e_per_ha"  : round(
            total_eifs / df_result["INTERSECT_HA"].sum(), 4
        ) if df_result["INTERSECT_HA"].sum() > 0 else None,
    }])

    # ── 9. Save results ───────────────────────────────────────────────────────
    out_xlsx = out_dir / f"E_IFS_{landscape_year}.xlsx"
    with pd.ExcelWriter(str(out_xlsx), engine="openpyxl") as writer:
        df_summary.to_excel( writer, sheet_name="Summary",      index=False)
        df_result.to_excel(  writer, sheet_name="Stand_Detail",  index=False)
        df_fofem_in.to_excel(writer, sheet_name="FOFEM_Input",   index=False)

    print(f"\n  [DONE] Results written to {out_xlsx}")