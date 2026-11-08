import pandas as pd
import math
import numpy as np
import sqlite3
import os
import glob
from pathlib import Path
import subprocess


# ------------------------------------------------------------------ #
# 0. Utilities
# ------------------------------------------------------------------ #
def get_df_from_excel(file_path):
    return pd.read_excel(file_path, header=0, index_col=False)

# ------------------------------------------------------------------ #
# 1. Working functions
# ------------------------------------------------------------------ #
def batch_consumed(df, w):
    # w = config fofem.wildfire
    # # ------------------------------------------------------------------
    # # 2.0 Generate DF for fofem input
    # # ------------------------------------------------------------------
    # Go one variable at a time
    # FVS_Fuels_Metric surface loads are in Mg/ha, but FOFEM expects tons/acre.
    # Convert them here; crown fuels are converted separately where needed.
    MGHA_TO_TONS_ACRE = 0.40468564 / 0.90718474   # 1 Mg/ha = 0.4461 short tons/acre
    surface_cols = ["Surface_Litter", "Surface_Duff", "Surface_lt76",
                    "Surface_ge76", "Surface_Herb", "Surface_Shrub"]
    df = df.copy()
    df[surface_cols] = df[surface_cols] * MGHA_TO_TONS_ACRE
    df_batch_consumed = pd.DataFrame()
    df_batch_consumed['Plot Id'] = df['Stand_year']     # No blank spaces
    df_batch_consumed['Litter'] = round(df['Surface_Litter'], 2)          # (T/ac)
    df_batch_consumed['1 Hour'] = df['Surface_lt76']*w['fine_fuel_split']['one_hr']           # 0-1/4in (T/ac) -> distribution based on collected data
    df_batch_consumed['10 Hour'] = df['Surface_lt76'] *w['fine_fuel_split']['ten_hr']         # 1/4 - 1in (T/ac) -> distribution based on collected data
    df_batch_consumed['10 Hour Moisture'] = str(w['moisture_10hr'])    # Range 3 - 298% -> based on very dry defaults
    df_batch_consumed['100 Hour'] = df['Surface_lt76']*w['fine_fuel_split']['hundred_hr']             # 1-3in (T/ac) > distribution based on collected data
    df_batch_consumed['1000 Hour'] = df['Surface_ge76']           # >3in (T/ac)
    df_batch_consumed['1000 Hour Moisture'] = str(w['moisture_1000hr'])  # Range 1 - 300% -> based on very dry defaults
    df_batch_consumed['1000 Hour Percent Rotten'] = str(w['pct_rotten_1000hr'])    # 0-100% -> between managed/younger stand and mature forest
    df_batch_consumed['1000 Hour Weight Distribution'] = w['weight_distribution_1000hr']   # Options are "Even, "Right, "Left", "End", "Center"
    df_batch_consumed['Duff'] = round(df['Surface_Duff'],1)                            # Ranges 0 - 356.79 (T/ac) NOTE: if duff =0, then duff depth =0
    df_batch_consumed['Duff Moisture'] = w['duff_moisture']                   # Range 10-197.2% -> based on very dry defaults
    df_batch_consumed['Duff Depth'] = round(df['Surface_Duff'] *907.185/4046.86/w['duff_bulk_density_kg_m3']*39.37, 2)    # inches - range 0-999in -> translate ton/ac to kg/m3 then divided by bulk density of 50 kg/m3 and translate back to inches
    # df_batch_consumed['Duff Moisture Method'] = 'Adj_NFDR'            # method to measure. "Entire", "Lower", "NFDR", "Adj_NFDR"
    df_batch_consumed['Herbaceous'] = round(df['Surface_Herb'], 2)                      # (T/ac)
    df_batch_consumed['Shrub'] = round(df['Surface_Shrub'], 2)                           # (T/ac)
    # df_batch_consumed['Crown Foliage_1'] = df_fuels['Standing_Foliage']*0.75                   # (T/ac) -> scaling factor to remove non-available foliage !!! Previous - was giving too large of a value
    # df_batch_consumed['Crown Branch_1'] = df_fuels['Standing_Foliage']*0.75*1.3                    # (T/ac) -> ratio from crown foliage adapted for mixed conifers !!! Previous - was giving too large of a value
    height = np.where(
        df["Stratum_2_Nom_Ht"] > 0,
        (df["Stratum_1_Nom_Ht"] + df["Stratum_2_Nom_Ht"]) / 2,
        df["Stratum_1_Nom_Ht"])
    crown_total = df['Canopy_Density'] * abs(height-df['Canopy_Ht']) *4046.86 /907.2 # Crown_total was partitionned into foliage (80%) and branches (20%) as FOFEM does
    # Stands with no live trees have no StrClass row, so height (and crown_total) is NaN.
    # FOFEM rejects a blank crown foliage, so these stands get 0 crown fuels.
    crown_total = crown_total.fillna(0)
    df_batch_consumed['Crown Foliage'] = round(crown_total *w['crown_foliage_fraction'], 2) # (T/ac) -> 80% of total (derived from CBD)
    df_batch_consumed['Crown Branch'] = round(crown_total *w['crown_branch_fraction'], 2) # (T/ac) -> 20% of total (derived from CBD)
    df_batch_consumed['Percent of Crown Burn'] = round(df['PTorch_Sev'] * 100, 0)     # PTorch_Sev is a 0-1 probability; FOFEM expects 0-100 %
    df_batch_consumed['Region'] = w['region']                          # “InteriorWest”, “PacificWest”, “NorthEast”, “SouthEast”
    df_batch_consumed['Cover Group'] = w['cover_group']                    # Grass -> GG Used in the Chilcotin as the main driver of fire/ Shrub -> SG / Shrub-Chaparral -> SGC / Sagebrush -> SB / Ponderosa pine -> PN / Pocosin -> PC / Balsam, black, red, white spruce -> BBS / Red, jack pine -> RJP / White pine hemlock -> WPH
    df_batch_consumed['Season'] = w['season']                        # “Spring”, “Summer”, “Fall”, “Winter”
    df_batch_consumed['Fuel Category'] = w['fuel_category']                 # “Natural”, “Piles”, “Slash”
    df_batch_consumed['Soil Family'] = w['soil_family']                    # “Loamy-Skeletal” -> used in chilcotin, “Fine-Silt”, “Fine”, “Coarse-Silt”, “CoarseLoamy” Use “NA” to indicate none available, in which case no soil heating will be calculated for the record
    df_batch_consumed['Soil Moisture'] = str(w['soil_moisture'])                   # range 0-25% -> based on very dry defaults

    return df_batch_consumed

def collect_batches_for_f(io_root, f, switch_treatment):
    f_dir = io_root / str(f)

    # Only this flag's batches: batch_{f}_{j}_{switch_treatment}.csv
    # (the old "*/batch_*.csv" also picked up the other flag's batches)
    # "-fix" batches (from rerun_rejected) are read last, so a regular batch wins
    # if a stand appears in both.
    batch_files = sorted(f_dir.glob(f"*/batch_{f}_*_{switch_treatment}.csv"),
                         key=lambda p: ("-fix" in p.name, str(p)))

    if not batch_files:
        print(f"[WARN] No batch files found for f={f}")
        return None

    dfs = []
    for bf in batch_files:
        try:
            df = pd.read_csv(bf)
            if not df.empty:
                dfs.append(df)
        except Exception as e:
            print(f"[SKIP] {bf}: {e}")

    if not dfs:
        print(f"[WARN] All batch files empty for f={f}")
        return None

    master_df = pd.concat(dfs, ignore_index=True)
    master_df = master_df.drop_duplicates(subset="Std", keep="first")

    out_path = f_dir / f"master_{f}_{switch_treatment}.csv"
    master_df.to_csv(out_path, index=False)

    print(f"[OK] wrote {out_path} ({len(master_df)} rows)")
    return out_path

def collect_all_f_masters(io_root, switch_treatment):
    master_files = sorted(io_root.glob(f"*/master_*_{switch_treatment}.csv"))

    if not master_files:
        raise RuntimeError("No per-f master files found")

    dfs = []
    for mf in master_files:
        try:
            df = pd.read_csv(mf)
            if not df.empty:
                df["source_f"] = mf.parent.name  # optional provenance
                dfs.append(df)
        except Exception as e:
            print(f"[SKIP] {mf}: {e}")

    final_df = pd.concat(dfs, ignore_index=True)

    out_path = io_root / f"master_all_{switch_treatment}.csv"
    final_df.to_csv(out_path, index=False)

    print(f"[FINAL] wrote {out_path} ({len(final_df)} rows)")

def run_fofem_batches(
    df_fofem_in,
    f,
    switch_treatment,
    IODIR,
    batch_size,
    fofem_exe,
    fofem_workdir,
):
    df_size = len(df_fofem_in)
    num_batches = math.ceil(df_size / batch_size)

    for j in range(num_batches):
        batch_dir = IODIR / str(f) / str(j)
        batch_dir.mkdir(parents=True, exist_ok=True)

        df_batch = []

        for i in range(batch_size):
            idx = j * batch_size + i
            if idx >= df_size:
                break

            # 3.1 Write single-row FOFEM input
            row = df_fofem_in.iloc[[idx]]

            InPath = batch_dir / f"in_{f}_{idx}_{switch_treatment}.csv"
            row.to_csv(InPath, sep=",", index=False, header=False)

            # 3.2 Run FOFEM
            OutPath = batch_dir / f"out_{f}_{idx}_{switch_treatment}.txt"
            RunPath = batch_dir / f"run_{f}_{idx}_{switch_treatment}.txt"
            ErrPath = batch_dir / f"err_{f}_{idx}_{switch_treatment}.txt"

            cmd = [
                fofem_exe,
                "C",
                str(InPath),
                str(OutPath),
                str(RunPath),
                str(ErrPath),
                "H",
            ]

            subprocess.run(
                cmd,
                cwd=fofem_workdir,
                capture_output=True,
                text=True,
            )

            # 4. Read FOFEM output
            if not OutPath.exists():
                continue

            df_temp = pd.read_csv(OutPath, sep=",", engine="python")
            if len(df_temp) < 2:
                continue

            df_batch.append(df_temp.iloc[[1]])

        # 5. Write batch output
        if df_batch:
            df_batch_df = pd.concat(df_batch, ignore_index=True)
            BatchPath = batch_dir / f"batch_{f}_{j}_{switch_treatment}.csv"
            df_batch_df.to_csv(BatchPath, sep=",", index=False)
            print(f"[OK] wrote {BatchPath}")

def rerun_rejected(IODIR, switch_treatment,
                   fofem_exe,
                   fofem_workdir):
    """
    Re-run only the stands FOFEM rejected for a missing crown foliage value
    (stands with no live trees). Their saved one-row inputs are re-used with
    crown foliage and crown branch set to 0, so FOFEM does not need to run
    again for every stand.

    Outputs go to batch_{f}_{j}-fix_{flag}.csv next to the regular batches;
    collect_batches_for_f picks them up. Re-collect the masters afterwards.
    """
    IODIR = Path(IODIR)
    CROWN_FOLIAGE, CROWN_BRANCH, CROWN_BURN = 15, 16, 17   # 0-based columns of the input row

    err_files = sorted(IODIR.glob(f"*/*/err_*_{switch_treatment}.txt"))
    fixed, failed = 0, []
    by_dir = {}
    for err in err_files:
        if "Crown Foliage value is missing" not in err.read_text(errors="ignore"):
            continue
        in_path = err.with_name(err.name.replace("err_", "in_", 1)).with_suffix(".csv")
        if not in_path.exists():
            failed.append(err.name)
            continue

        row = pd.read_csv(in_path, header=None)
        row[CROWN_FOLIAGE] = row[CROWN_FOLIAGE].fillna(0)
        row[CROWN_BRANCH]  = row[CROWN_BRANCH].fillna(0)
        row[CROWN_BURN]    = row[CROWN_BURN].fillna(0)

        stem = in_path.stem + "_fix"
        fix_in  = in_path.with_name(stem + ".csv")
        fix_out = in_path.with_name(stem.replace("in_", "out_", 1) + ".txt")
        fix_run = in_path.with_name(stem.replace("in_", "run_", 1) + ".txt")
        fix_err = in_path.with_name(stem.replace("in_", "err_", 1) + ".txt")
        row.to_csv(fix_in, sep=",", index=False, header=False)

        subprocess.run([str(fofem_exe), "C", str(fix_in), str(fix_out),
                        str(fix_run), str(fix_err), "H"],
                       cwd=str(fofem_workdir), capture_output=True, text=True)

        if not fix_out.exists():
            failed.append(err.name)
            continue
        df_temp = pd.read_csv(fix_out, sep=",", engine="python")
        if len(df_temp) < 2:
            failed.append(err.name)
            continue
        by_dir.setdefault(in_path.parent, []).append(df_temp.iloc[[1]])
        fixed += 1

    for batch_dir, rows in by_dir.items():
        f, j = batch_dir.parent.name, batch_dir.name
        out = batch_dir / f"batch_{f}_{j}-fix_{switch_treatment}.csv"
        pd.concat(rows, ignore_index=True).to_csv(out, sep=",", index=False)
        print(f"[OK] wrote {out} ({len(rows)} rows)")

    print(f"[FIX] flag {switch_treatment}: {fixed} rejected stands re-run, {len(failed)} still failing")
    if failed:
        print("      still failing:", failed[:10])
    return fixed, failed


def main(cfg, year, switch_treatment):
    """
    1. Loop over the FVS output databases
        1.1 - Read the databases
        2. 0 - Fct(process database for fofem inputs in a df format)
        3.0 - Loop in smaller bunch (500 over 5000)
            3.1 - Fct(process df input into a singular csv file)
            3.2 - Fct(Run fofem using singular input file)

        4.0 Loop (Read ouputs (500) and arrange into a final df)
    Output the final df (all fofem run individually)
    """
    fcfg = cfg["fofem"]
    target_year = cfg["fvs"]["target_year"]
    IODIR = Path(cfg.path("fofem_dir", "io", year=year))
    IODIR.mkdir(parents=True, exist_ok=True)
    FVSDIR = Path(cfg.path("fvs_dir", year=year))

    # Build a glob pattern and collect every .db file
    base_filename = "FVS_out"
    pattern = os.path.join(
        FVSDIR,
        f"{base_filename}_**_{switch_treatment}.db"
    )

    files = sorted(glob.glob(pattern))
    print(f"Found {len(files)} DBs for switch={switch_treatment}")

    if not files:
        raise FileNotFoundError(f"No files match: {pattern}")

    # # ------------------------------------------------------------------
    # # 1. Loop through each db file
    # # ------------------------------------------------------------------
    for f, filename in enumerate(files):
        # # ------------------------------------------------------------------
        # # 1.1 read the databases and organize into one input databe
        # # ------------------------------------------------------------------

        # Table Names
        fuels_table = "FVS_Fuels_Metric"
        potfire_table = "FVS_PotFire_Metric"
        str_table = "FVS_StrClass_Metric"

        with sqlite3.connect(filename) as conn:
            df_fuels = pd.read_sql_query(
                f"SELECT * FROM {fuels_table} WHERE Year = {target_year};",
                conn
            )
        with sqlite3.connect(filename) as conn:
            df_potfire = pd.read_sql_query(
                f"SELECT * FROM {potfire_table} WHERE Year = {target_year};",
                conn
            )
        with sqlite3.connect(filename) as conn:
            df_str = pd.read_sql_query(
                f"SELECT * FROM {str_table} WHERE Removal_Code = 1 AND Year = {target_year};",
                conn
            )

        # Create unique ID and join into one df -> necessary for missing row in str table (when there are no trees)
        df_fuels["Stand_year"] = (
                df_fuels["StandID"].astype(str) + "_" + df_fuels["Year"].astype(str))

        df_potfire["Stand_year"] = (
                df_potfire["StandID"].astype(str) + "_" + df_potfire["Year"].astype(str))

        df_str["Stand_year"] = (
                df_str["StandID"].astype(str) + "_" + df_str["Year"].astype(str))

        df_FVS_out = (
            df_fuels
            .merge(df_potfire, on="Stand_year", how="left")
            .merge(df_str, on="Stand_year", how="left"))

        print(f, len(df_FVS_out))

        # # ------------------------------------------------------------------
        # # 2.0 generate df for fofem input
        # # ------------------------------------------------------------------
        df_fofem_in = batch_consumed(df_FVS_out, fcfg["wildfire"])

        print("df_fofem_in", len(df_fofem_in))

        df_in_path = IODIR / f"dg_in_{f}_{switch_treatment}.csv"
        df_fofem_in.to_csv(df_in_path, index=False)

        # ------------------------------------------------------------------
        # 3.0 iterate over smaller batch
        # ------------------------------------------------------------------
        run_fofem_batches(
            df_fofem_in=df_fofem_in,
            f=f,
            switch_treatment=switch_treatment,
            IODIR=IODIR,
            batch_size=fcfg["batch_size"],
            fofem_exe=cfg.exe("fofem"),
            fofem_workdir=Path(cfg.exe("fofem_workdir")),
        )


    # 6. Collect results
    print("[INFO] Collecting per-f master files")

    for f in range(len(files)):
        collect_batches_for_f(IODIR, f, switch_treatment)

    print("[INFO] Collecting global master file")
    collect_all_f_masters(IODIR, switch_treatment)

