import pandas as pd
import re
import os
import numpy as np
import glob
import sqlite3

def pull_df_fvs(db_path: str, db_name: str):
    """Pull the entire FVS_Carbon_Metric table from one FVS output DB."""
    try:
        with sqlite3.connect(db_path) as conn:
            tbl_list = pd.read_sql_query(
                "SELECT name FROM sqlite_master WHERE type='table';", conn
            )["name"].tolist()

            if db_name not in tbl_list:
                print(f"⚠  Table {db_name} not found in {db_path}")
                return None

            df = pd.read_sql_query(f"SELECT * FROM {db_name};", conn)
            df["source_db"] = db_path
            return df

    except (sqlite3.DatabaseError, OSError) as exc:
        print(f"⚠  Could not read {db_path}: {exc}")
        return None

def main(cfg, year, switch_treatment):
    path_dir = cfg.path("fvs_dir", year=year)
    path_VRI = cfg.path("processed_dir", f"VRI_{year}_processed.xlsx")
    c_to_co2e = cfg["fvs"]["c_to_co2e"]
    target_year = cfg["fvs"]["target_year"]
    base_filename = "FVS_fire_out"

    # ------------------------------------------------------------------
    # 1. Build a glob pattern and collect all matching files
    # ------------------------------------------------------------------
    pattern = os.path.join(
        path_dir,
        f"{base_filename}_**_{switch_treatment}.db"
    )
    files = sorted(glob.glob(pattern))
    print(f"Found {len(files)} DBs for switch={switch_treatment}")

    if not files:
        raise FileNotFoundError(f"No files match: {pattern}")

    # ------------------------------------------------------------------
    # 2. Loop through each file in order to compile a big df
    # ------------------------------------------------------------------
    df_carbon_list   = []

    i = 0
    for filename in files:
        df_temp_carbon   = pull_df_fvs(filename, "FVS_Carbon_Metric")
        print(i)
        print(filename,
              len(df_temp_carbon),
              df_temp_carbon["StandID"].nunique(),
              df_temp_carbon["Year"].nunique())
        i += 1
        if df_temp_carbon is not None:
            df_carbon_list.append(df_temp_carbon)

    df_carbon   = pd.concat(df_carbon_list,   ignore_index=True)

    # ------------------------------------------------------------------
    # 3. Merge VRI areas
    # ------------------------------------------------------------------
    df_vri = pd.read_excel(path_VRI)
    df_vri_subset = df_vri[["POLYGON_ID", "POLYGON_AREA"]].copy()
    df_carbon["StandID"]        = df_carbon["StandID"].astype(str)
    df_vri_subset["POLYGON_ID"] = df_vri_subset["POLYGON_ID"].astype(str)

    df_carbon_final = df_carbon.merge(
        df_vri_subset,
        left_on="StandID",
        right_on="POLYGON_ID",
        how="left"
    )

    # ------------------------------------------------------------------
    # 3b. Diagnostic: flag stands with null POLYGON_ID / missing area
    # ------------------------------------------------------------------
    df_null_area = df_carbon_final[df_carbon_final["POLYGON_AREA"].isna()].copy()
    n_null = df_null_area["StandID"].nunique()
    print(f"\nStands with missing POLYGON_AREA: {n_null}")
    if n_null > 0:
        print("Affected StandIDs:")
        print(df_null_area["StandID"].unique())
        df_carbon_final["POLYGON_AREA"] = df_carbon_final["POLYGON_AREA"].fillna(0)

    # ------------------------------------------------------------------
    # 4. Total carbon stock per stand per year (all years retained)
    #    Total_Stand_Carbon stays in Mg C/ha (used by raster_for_carbonstock.py).
    #    New column Total_Carbon = Total_Stand_Carbon x POLYGON_AREA x 3.667 = Mg CO2e per stand.
    # ------------------------------------------------------------------


    df_carbon_final["Total_Carbon"] = (
        df_carbon_final["Total_Stand_Carbon"]
        * df_carbon_final["POLYGON_AREA"]
        * c_to_co2e
    )

    # ------------------------------------------------------------------
    # 5. Carbon stock summary by year (all years)
    # ------------------------------------------------------------------
    totals_by_year = (
        df_carbon_final.groupby("Year")["Total_Carbon"]
        .sum()
        .reset_index(name="Sum_Total_Carbon_CO2e")
    )
    print("\nTotal carbon by year (Mg CO2e):")
    print(totals_by_year.to_string(index=False))

    # ------------------------------------------------------------------
    # 6. E_IFS from FVS_Carbon_Metric: Carbon_Released_From_Fire x area
    #    Summed for year 2025 only (treatment burn year)
    #    Units: Carbon_Released_From_Fire (MgC/ha) x POLYGON_AREA (ha)
    #           = MgC per stand -> x 3.667 = Mg CO2e
    # ------------------------------------------------------------------
    df_eifs = df_carbon_final[df_carbon_final["Year"] == target_year].copy()
    df_eifs["Carbon_Released_MgC"] = (
        df_eifs["Carbon_Released_From_Fire"] * df_eifs["POLYGON_AREA"]
    )

    total_eifs_mgc  = df_eifs["Carbon_Released_MgC"].sum()
    total_eifs_co2e = total_eifs_mgc * c_to_co2e

    n_burning_stands = (df_eifs["Carbon_Released_From_Fire"] > 0).sum()

    print(f"\nE_IFS from Carbon_Released_From_Fire (year 2025):")
    print(f"  Stands with non-zero fire emissions: {n_burning_stands}")
    print(f"  Total: {total_eifs_mgc:.2f} MgC  |  {total_eifs_co2e:.2f} Mg CO2e")

    if switch_treatment == 0:
        print("  NOTE: switch_treatment=0 (baseline) - emissions should be zero")

    df_eifs_summary = pd.DataFrame([{
        "Variable"        : "E_IFS",
        "Switch_treatment": switch_treatment,
        "Year"            : target_year,
        "Value_MgC"       : round(total_eifs_mgc,  2),
        "Value_CO2e"      : round(total_eifs_co2e, 2),
        "Description"     : "Sum of Carbon_Released_From_Fire x POLYGON_AREA "
                            "from FVS_Carbon_Metric, year 2025"
    }])

    df_eifs_detail = df_eifs[
        ["StandID", "Year", "POLYGON_AREA",
         "Carbon_Released_From_Fire", "Carbon_Released_MgC"]
    ].copy()

    # ------------------------------------------------------------------
    # 7. Save to Excel
    # ------------------------------------------------------------------
    output_path_carbon   = os.path.join(path_dir, f"carbon_report_fire_{switch_treatment}.xlsx")


    with pd.ExcelWriter(output_path_carbon, engine="openpyxl") as xw:
        totals_by_year.to_excel( xw, sheet_name="Carbon Summary",    index=False)
        df_carbon_final.to_excel(xw, sheet_name="Full Carbon Report", index=False)
        df_eifs_summary.to_excel(xw, sheet_name="E_IFS Summary",      index=False)
        df_eifs_detail.to_excel( xw, sheet_name="E_IFS Detail",       index=False)
        if n_null > 0:
            df_null_area[["StandID", "source_db"]].drop_duplicates().to_excel(
                xw, sheet_name="Missing Area Stands", index=False
            )

    # df_potfire.to_excel( output_path_potfire,  index=False)
    # df_strclass.to_excel(output_path_strclass, index=False)

    print(f" • Excel written → {output_path_carbon}")