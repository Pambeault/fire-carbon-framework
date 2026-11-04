import pandas as pd
import subprocess
import os
from pathlib import Path
import arcpy
from arcpy import env
from arcpy.sa import *
import textwrap
import numpy as np

def co2eq(df, ecfg):
    ### Equation 6.3 from REM: 𝑈𝑊𝑃𝑅,𝑖 = 𝐶𝑂2𝑖 + (𝐶𝐻4𝑖 × 𝐺𝑊𝑃𝐶𝐻4) + (𝑚𝑏,𝑖 × (0.16 106 ⁄ ) × 𝐺𝑊𝑃𝑁2𝑂) + (𝐶𝑂𝑖 × 𝐺𝑊𝑃𝐶𝑂)+ (𝑃𝑀2.5𝑖 × 𝐺𝑊𝑃𝑃𝑀2.5) + (𝑁𝑀𝑉𝑂𝐶𝑖 × 𝐺𝑊𝑃𝑁𝑀𝑉𝑂𝐶)
    #### Greenhouse warming potential constants
    # config: emissions.gwp
    GWP_CH4 = ecfg["gwp"]["CH4"]
    GWP_CO = ecfg["gwp"]["CO"]
    GWP_NMVOC = ecfg["gwp"]["NMVOC"]
    GWP_N2O = ecfg["gwp"]["N2O"]
    GWP_PM25 = ecfg["gwp"]["PM25"]

    # Units are in ton/ac
    df["co2eq"] = ( ((df["CO2F"] + df["CO2S"])/2000 +
                   (df["CH4F"] + df["CH4S"])/2000 * GWP_CH4 +
                   (df["COF"] + df["COS"])/2000 * GWP_CO +
                   (df["PM25F"] + df["PM25S"])/2000 * GWP_PM25) +
                   (df["FlaCon"] + df["SmoCon"]) * GWP_NMVOC * (ecfg["nmvoc_ef"]/1000) +
                   (df["FlaCon"] + df["SmoCon"]) * GWP_N2O * (ecfg["n2o_ef"]/1000000))

    return df


def vri_poly_match(cfg, df_year, year, switch_treatment, res):

    ## 3.1 - Arcgis setup
    arcpy.env.overwriteOutput = True
    target_crs = cfg.spatial_reference()
    raster_dir = cfg.path("emission_dir")
    os.makedirs(raster_dir, exist_ok=True)

    # Only the per-pixel raster is saved to disk
    raster_out = os.path.join(raster_dir, f"emission_{year}_{res}_{switch_treatment}.tif")

    ## 3.2 - get VRI polygon layer from current map
    m = cfg.open_map()
    vri_poly = m.listLayers(cfg.layer("vri", year))[0]

    # How many unique stands in FOFEM output for this year
    print(f"Year {year}: {len(df_year)} FOFEM stands")

    # Compare with VRI polygon count
    result = arcpy.management.GetCount(vri_poly)
    print(f"VRI polygons: {result[0]}")

    ## 3.3 - prepare df_year -> 'in memory' table (standID + co2eq only)
    df_join = df_year[["StandID", "co2eq"]].copy()
    join_table = r"in_memory\df_year_join"
    temp_csv = cfg.path("temp_dir", "df_year_join.csv", mkdir=True)
    df_join.to_csv(temp_csv, index=False)

    # After join, check how many matched
    result_joined = arcpy.management.GetCount(vri_poly)
    print(f"Polygons after KEEP_COMMON join: {result_joined[0]}")

    arcpy.conversion.TableToTable(
        temp_csv,
        "in_memory",
        "df_year_join"
    )

    ## 3.4 - Join df_year to VRI polygons
    arcpy.management.AddJoin(
        in_layer_or_view=vri_poly,
        in_field="POLYGON_ID",
        join_table=join_table,
        join_field="StandID",
        join_type="KEEP_COMMON"  # only polygons with emissions
    )

    ## 3.5 - Polygon -> Raster (density, tons CO2e/acre) kept in memory only
    arcpy.env.outputCoordinateSystem = target_crs
    raster_density_mem = r"in_memory\emission_density"

    arcpy.conversion.PolygonToRaster(
        in_features=vri_poly,
        value_field="df_year_join.co2eq",
        out_rasterdataset=raster_density_mem,
        cell_assignment="CELL_CENTER",
        priority_field=None,
        cellsize=res
    )

    ## 3.6 - Convert density to per-pixel total and save to disk
    # res is in metres (BC Albers EPSG:3005)
    cell_size_m = float(res)
    cell_area_ha = (cell_size_m ** 2) / 10000   # m² -> ha
    cell_area_ac = cell_area_ha * 2.47105        # ha -> acres

    raster_density = arcpy.Raster(raster_density_mem)
    SHORT_TONS_TO_MG = 0.907185
    raster_per_pixel = raster_density * cell_area_ac * SHORT_TONS_TO_MG  # US tons CO2e/acre * acres/pixel * US tons to MG -> MG CO2e/pixel

    raster_per_pixel.save(raster_out)

    ## 3.7 - Cleanup
    arcpy.management.RemoveJoin(vri_poly)
    arcpy.management.Delete(raster_density_mem)

    print(f"Per-pixel raster written to: {raster_out}")


def main(cfg, year_init, switch_treatment):
    '''
    1. Calculate Co2eq output using fofem output
    2. loop through the years (one raster per year)
        3. Pull polygon file with stand IDs and match
        5.Turn feature class in a raster
    '''

    # 1. Read the dataframe of the fofem output
    res = cfg.resolution
    IODIR = cfg.path("fofem_dir", "io", year=year_init)
    path_fofem = Path(IODIR) / f"master_all_{switch_treatment}.csv"
    df = pd.read_csv(path_fofem, header=0, index_col=False)

    ## 1.1 - divide variable Std to stand and year
    df[["StandID", "Year"]] = df["Std"].str.split("_", expand=True)

    df["StandID"] = df["StandID"].astype(int)
    df["Year"] = df["Year"].astype(int)

    ## 1.2 - Calculate the CO2 equivalent
    df = co2eq(df, cfg["emissions"])

    ## 1.3 - Save Excel for future ref
    path_fofem_processed = Path(IODIR) / f"processed_all_{switch_treatment}.csv"
    df.to_csv(path_fofem_processed, sep=",", index=False)

    # 2. - loop through the years
    ## 2.1 - List unique years to loop through
    unique_years = sorted(df["Year"].unique().tolist())
    for year in unique_years:
        ## 2.2 - Parse to keep only active year
        df_year = df[df["Year"] == year].copy()

        # 3. - Pull polygon file with stand IDs
        vri_poly_match(cfg, df_year, year_init, switch_treatment, res)