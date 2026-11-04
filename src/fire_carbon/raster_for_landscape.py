import arcpy
from arcpy import env
from arcpy.sa import *
import os
import pandas as pd
import textwrap
import numpy as np


def interpolate_nodata(arr):
    """Interpolate only NaN values linearly along rows and columns."""
    import pandas as pd
    import numpy as np

    arr_copy = arr.copy().astype(float)  # ensure float for interpolation
    nrows, ncols = arr_copy.shape

    # Interpolate row-wise
    for i in range(nrows):
        row = pd.Series(arr_copy[i, :])
        # interpolate only NaNs, other values stay the same
        arr_copy[i, :] = row.interpolate(limit_direction='both').to_numpy()

    # Interpolate column-wise
    for j in range(ncols):
        col = pd.Series(arr_copy[:, j])
        # interpolate only NaNs, other values stay the same
        arr_copy[:, j] = col.interpolate(limit_direction='both').to_numpy()

    # Replace any remaining NaNs with a default value (e.g., -9999)
    arr_copy = np.nan_to_num(arr_copy, nan=-9999)

    return arr_copy


def topo_tiffs(m, dem_layer_name, boundary_layer_name, output_folder, resolution, target_crs):

    dem = m.listLayers(dem_layer_name)[0]
    boundary = m.listLayers(boundary_layer_name)[0]

    # === Step 1: Project DEM ===
    dem_projected = os.path.join("in_memory", "dem_proj")
    arcpy.management.ProjectRaster(dem, dem_projected, target_crs,
                                   resampling_type="BILINEAR",
                                   cell_size=f"{resolution}")

    # === Step 2: Clip to Boundary ===
    dem_clipped = ExtractByMask(dem_projected, boundary)

    # === Step 2b: Fill NoData ===
    dem_array = arcpy.RasterToNumPyArray(dem_clipped).astype(np.float32)
    dem_array[dem_array == dem_clipped.noDataValue] = np.nan
    dem_interpolated = interpolate_nodata(dem_array)

    # 🔹 Convert safely to Int16
    dem_interpolated = np.nan_to_num(dem_interpolated, nan=-9999)
    dem_interpolated = np.clip(dem_interpolated, -32768, 32767)
    dem_interpolated = np.around(dem_interpolated).astype(np.int16)

    dem_filled_raster = arcpy.NumPyArrayToRaster(
        dem_interpolated,
        lower_left_corner=arcpy.Point(dem_clipped.extent.XMin, dem_clipped.extent.YMin),
        x_cell_size=dem_clipped.meanCellWidth,
        y_cell_size=dem_clipped.meanCellHeight,
        value_to_nodata=-9999
    )
    # 🔹 Assign spatial reference
    arcpy.management.DefineProjection(dem_filled_raster, target_crs)
    dem_filled_raster.save(os.path.join(output_folder, f"dem_filled_{resolution}.tif"))

    # === Step 3: Derive Slope & Aspect ===
    slope = os.path.join(output_folder, f"slope_{resolution}.tif")
    aspect = os.path.join(output_folder, f"aspect_{resolution}.tif")

    slope_array = arcpy.RasterToNumPyArray(Slope(dem_filled_raster, output_measurement="DEGREE")).astype(np.float32)
    slope_array[slope_array == -9999] = np.nan
    slope_interpolated = interpolate_nodata(slope_array)

    # 🔹 Fix NaN and clip to 0–100 degrees
    slope_interpolated = np.nan_to_num(slope_interpolated, nan=0)
    slope_interpolated = np.clip(slope_interpolated, 0, 100)
    slope_interpolated = np.around(slope_interpolated).astype(np.int16)

    slope_raster = arcpy.NumPyArrayToRaster(
        slope_interpolated,
        lower_left_corner=arcpy.Point(dem_filled_raster.extent.XMin, dem_filled_raster.extent.YMin),
        x_cell_size=dem_filled_raster.meanCellWidth,
        y_cell_size=dem_filled_raster.meanCellHeight,
        value_to_nodata=-9999
    )
    # 🔹 Assign spatial reference
    arcpy.management.DefineProjection(slope_raster, target_crs)
    slope_raster.save(slope)

    aspect_raster = Aspect(dem_filled_raster)
    aspect_array = arcpy.RasterToNumPyArray(aspect_raster).astype(np.float32)
    aspect_array[aspect_array == aspect_raster.noDataValue] = np.nan
    aspect_interpolated = interpolate_nodata(aspect_array)

    # 🔹 Safe conversion to Int16
    aspect_interpolated = np.nan_to_num(aspect_interpolated, nan=-9999)
    aspect_interpolated = np.clip(aspect_interpolated, -32768, 32767)
    aspect_interpolated = np.around(aspect_interpolated).astype(np.int16)
    aspect_interpolated[aspect_interpolated == -1] = 0  # replace -1 with 0

    aspect_fixed_raster = arcpy.NumPyArrayToRaster(
        aspect_interpolated,
        lower_left_corner=arcpy.Point(aspect_raster.extent.XMin, aspect_raster.extent.YMin),
        x_cell_size=aspect_raster.meanCellWidth,
        y_cell_size=aspect_raster.meanCellHeight,
        value_to_nodata=-9999
    )
    # 🔹 Assign spatial reference
    arcpy.management.DefineProjection(aspect_fixed_raster, target_crs)
    aspect_fixed_raster.save(aspect)


def fuel_model_tiff(m, fuel_layer_name, treatment_layer_name, output_folder, resolution, switch_lowhigh,
                    path_dict):
    """Define fuel models compatible with FlamMap by translating BC fuel types
    into US standard fuel models. Outputs treated and untreated rasters in
    DEM CRS, aligned grid, and 16-bit signed integer format."""

    # --------------------------------------------------- 0. translate dict
    # Single conversion table (config: supporting.fuel_type_conversion).
    # Column treated_<switch_lowhigh> holds the treated-fuel assumption (landscape.fuel_assumption).
    df_dict = pd.read_csv(path_dict)
    raw_dict = dict(zip(df_dict["code_can"], df_dict["code_usa"]))
    can_to_us = {k.strip(): (None if pd.isna(v) else int(v)) for k, v in raw_dict.items()}

    # Build treated lookup: CAN code -> Treated Code (USA)
    raw_treated = dict(zip(df_dict["code_can"], df_dict[f"treated_{switch_lowhigh}"]))
    can_to_treated = {k.strip(): (None if pd.isna(v) else int(v)) for k, v in raw_treated.items()}

    # --------------------------------------------------- 1. get layers
    fuel_src = [lyr for lyr in m.listLayers() if lyr.name == fuel_layer_name][0]
    treat_src = [lyr for lyr in m.listLayers() if lyr.name == treatment_layer_name][0]

    # --------------------------------------------------- 2. ensure projection matches DEM
    dem_raster = os.path.join(output_folder, f"dem_filled_{resolution}.tif")

    dem_sr = arcpy.Describe(dem_raster).spatialReference
    print(dem_sr.name)
    if not dem_sr or dem_sr.name == "Unknown":
        raise ValueError(
            "DEM file does not have a valid spatial reference (coordinate system). Please ensure the DEM has a defined projection.")

    if arcpy.Describe(fuel_src).spatialReference.name == "Unknown":
        raise ValueError(f"The input layer '{fuel_layer_name}' does not have a valid spatial reference.")

    if arcpy.Describe(treat_src).spatialReference.name == "Unknown":
        raise ValueError(f"The input layer '{treatment_layer_name}' does not have a valid spatial reference.")

    fuel_fc_proj = r"in_memory\fuel_fc_proj"
    treat_fc_proj = r"in_memory\treat_fc_proj"

    arcpy.management.Project(fuel_src, fuel_fc_proj, dem_sr)
    arcpy.management.Project(treat_src, treat_fc_proj, dem_sr)

    # in-memory copy so we can add fields safely
    fuel_fc = r"in_memory\fuel_fc"
    arcpy.management.CopyFeatures(fuel_fc_proj, fuel_fc)

    if "US_fuel_type_code" not in [f.name for f in arcpy.ListFields(fuel_fc)]:
        arcpy.management.AddField(fuel_fc, "US_fuel_type_code", "SHORT")

    vals = [row[0] for row in arcpy.da.SearchCursor(fuel_fc, "FUEL_TYPE_CD")]

    codeblock = textwrap.dedent(f"""
    lookup = {can_to_us!r}
    def translate(code):
        if not code:
            return None
        key = str(code).strip().upper().replace("–", "-")
        return lookup.get(key, None)
    """)
    arcpy.management.CalculateField(
        fuel_fc, "US_fuel_type_code",
        expression="translate(!FUEL_TYPE_CD!)",
        expression_type="PYTHON3", code_block=codeblock
    )

    # Add treated US code field
    if "US_treated_code" not in [f.name for f in arcpy.ListFields(fuel_fc)]:
        arcpy.management.AddField(fuel_fc, "US_treated_code", "SHORT")

    codeblock_treated = textwrap.dedent(f"""
    lookup_treated = {can_to_treated!r}
    def translate_treated(code):
        if not code:
            return None
        key = str(code).strip().upper().replace("–", "-")
        return lookup_treated.get(key, None)
    """)
    arcpy.management.CalculateField(
        fuel_fc, "US_treated_code",
        expression="translate_treated(!FUEL_TYPE_CD!)",
        expression_type="PYTHON3", code_block=codeblock_treated
    )

    # --------------------------------------------------- 3. environment (snap / cellsize / extent)
    arcpy.env.snapRaster = dem_raster
    arcpy.env.cellSize = dem_raster
    arcpy.env.extent = dem_raster

    # --------------------------------------------------- 4. polygon → raster (untreated)
    ras_untreated = r"in_memory\fuel_untreated"
    arcpy.conversion.PolygonToRaster(
        fuel_fc, "US_fuel_type_code", ras_untreated,
        cell_assignment="CELL_CENTER"
    )

    # --------------------------------------------------- 5. treatment mask raster (1 = treated)
    ras_mask = r"in_memory\treat_mask"
    arcpy.conversion.PolygonToRaster(
        treat_fc_proj, "OBJECTID", ras_mask,
        cell_assignment="CELL_CENTER"
    )

    # --------------------------------------------------- 6. align rasters explicitly
    ras_untreated_aligned = arcpy.management.Resample(
        in_raster=ras_untreated,
        out_raster=r"in_memory\fuel_untreated_aligned",
        resampling_type="NEAREST"
    )[0]
    ras_mask_aligned = arcpy.management.Resample(
        in_raster=ras_mask,
        out_raster=r"in_memory\treat_mask_aligned",
        resampling_type="NEAREST"
    )[0]

    # --------------------------------------------------- 7. create treated raster: pixels in mask → treated fuel code
    ras_treated_vals = r"in_memory\fuel_treated_vals"
    arcpy.conversion.PolygonToRaster(
        fuel_fc, "US_treated_code", ras_treated_vals,
        cell_assignment="CELL_CENTER"
    )
    ras_treated_vals_aligned = arcpy.management.Resample(
        in_raster=ras_treated_vals,
        out_raster=r"in_memory\fuel_treated_vals_aligned",
        resampling_type="NEAREST"
    )[0]

    ras_treated = Con(IsNull(ras_mask_aligned), ras_untreated_aligned, ras_treated_vals_aligned)

    # --------------------------------------------------- 8. save both rasters as GeoTIFF (16-bit signed)
    os.makedirs(output_folder, exist_ok=True)
    arcpy.management.CopyRaster(
        ras_untreated_aligned,
        os.path.join(output_folder, f"fuel_model_{switch_lowhigh}_0.tif"),
        pixel_type="16_BIT_UNSIGNED", format="TIFF",
    )
    arcpy.management.CopyRaster(
        ras_treated,
        os.path.join(output_folder, f"fuel_model_{switch_lowhigh}_1.tif"),
        pixel_type="16_BIT_UNSIGNED", format="TIFF",
    )

    print("✓ Fuel type TIFFs written for treated and untreated.")


def stand_char_tiff(m, vri_layer_name, in_folder, out_folder, resolution, target_crs, target_year):
    """Create canopy-variable rasters for fvs.target_year only, per treatment flag."""

    # ------------------------------------------------------------------ helper
    def add_update_fields(fc, df_year, year):
        """Add cover/height/CBH/CBD to fc using UpdateCursor."""
        for fld in ("Cover", "Height", "cbh", "cbd"):
            if fld not in [f.name for f in arcpy.ListFields(fc)]:
                arcpy.management.AddField(fc, fld, "FLOAT")

        cover  = dict(zip(df_year["StandID"], df_year["Cover"]))
        height = dict(zip(df_year["StandID"], df_year["Height"]))
        cbh    = dict(zip(df_year["StandID"], df_year["Canopy_Ht"]))
        cbd    = dict(zip(df_year["StandID"], df_year["Canopy_Density"]))

        with arcpy.da.UpdateCursor(fc, ["POLYGON_ID", "Cover", "Height", "cbh", "cbd"]) as cur:
            for row in cur:
                sid    = row[0]
                row[1] = cover.get(sid, 0)
                row[2] = height.get(sid, 0)
                row[3] = cbh.get(sid, 0)
                row[4] = cbd.get(sid, 0)

                if row[2] == 0:
                    row[1] = row[3] = row[4] = 0
                elif row[4] == 0:
                    row[1] = row[2] = row[3] = 0
                elif row[1] == 0:
                    row[2] = row[3] = row[4] = 0

                cur.updateRow(row)

    # ------------------------------------------------------------------ locate VRI layer
    vri_src = [lyr for lyr in m.listLayers() if lyr.name == vri_layer_name][0]

    dem_raster = os.path.join(out_folder, f"dem_filled_{resolution}.tif")
    arcpy.env.snapRaster = dem_raster
    arcpy.env.cellSize   = dem_raster
    arcpy.env.extent     = dem_raster

    TARGET_YEAR = target_year

    # ------------------------------------------------------------------ loop over treatment flags
    for tflag in (0, 1):
        str_path = os.path.join(in_folder, f"strclass_report_{tflag}.xlsx")
        pot_path = os.path.join(in_folder, f"potfire_report_{tflag}.xlsx")

        df_str = pd.read_excel(str_path).iloc[1:]
        df_str = df_str[df_str["Removal_Code"] != 0]
        df_str = df_str[df_str["Year"] == TARGET_YEAR].copy()

        df_pot = pd.read_excel(pot_path).iloc[1:]
        df_pot = df_pot[df_pot["Year"] == TARGET_YEAR].copy()
        df_pot = df_pot[["StandID", "Year", "Canopy_Ht", "Canopy_Density"]]
        df_pot = (
            df_pot.sort_values(["StandID", "Year"])
            .drop_duplicates(subset=["StandID", "Year"], keep="first")
        )

        df_str["Height"] = np.where(
            df_str["Stratum_2_Nom_Ht"] > 0,
            (df_str["Stratum_1_Nom_Ht"] + df_str["Stratum_2_Nom_Ht"]) / 2,
            df_str["Stratum_1_Nom_Ht"],
        )

        c1 = df_str["Stratum_1_Crown_Cover"].fillna(0) / 100.0
        c2 = df_str["Stratum_2_Crown_Cover"].fillna(0) / 100.0
        df_str["Cover"] = ((1 - (1 - c1) * (1 - c2)) * 100).round(1)

        df_str = df_str[["StandID", "Year", "Cover", "Height"]]
        df_all = df_str.merge(df_pot, on=["StandID", "Year"], how="left")

        if df_all.empty:
            print(f"⚠ No data for year {TARGET_YEAR}, treatment {tflag} - skipping")
            continue

        print(f"Working on year {TARGET_YEAR}, treatment {tflag}")
        print(
            "Missing potfire rows:",
            df_all["Canopy_Density"].isna().sum(),
            "out of",
            len(df_all)
        )

        df_year = df_all.copy()

        vri_fc = r"in_memory\vri_fc"
        arcpy.management.CopyFeatures(vri_src, vri_fc)
        add_update_fields(vri_fc, df_year, TARGET_YEAR)

        for field, tag, scale, clip_max in [
            ("Cover",  "cover",  1,   100),
            ("Height", "height", 10,  200),
            ("cbh",    "cbh",    10,  100),
            ("cbd",    "cbd",    100, 200),
        ]:      # cbd * 100, clipped at 20000
            ras_mem = fr"in_memory\ras_{field}_{TARGET_YEAR}"
            arcpy.conversion.PolygonToRaster(
                vri_fc, field, ras_mem,
                cell_assignment="CELL_CENTER",
                cellsize=arcpy.env.cellSize
            )

            arr = arcpy.RasterToNumPyArray(ras_mem).astype(np.float32)
            arr[arr == -9999] = np.nan
            arr_interpolated = interpolate_nodata(arr)
            arr_interpolated = np.nan_to_num(arr_interpolated, nan=-9999)
            arr_interpolated *= scale
            arr_interpolated  = np.clip(arr_interpolated, 0, clip_max * scale)
            arr_interpolated  = np.around(arr_interpolated).astype(np.int16)

            lower_left = arcpy.Point(
                arcpy.Describe(ras_mem).extent.XMin,
                arcpy.Describe(ras_mem).extent.YMin
            )
            raster_interpolated = arcpy.NumPyArrayToRaster(
                arr_interpolated,
                lower_left_corner=lower_left,
                x_cell_size=arcpy.env.cellSize,
                y_cell_size=arcpy.env.cellSize,
                value_to_nodata=-9999
            )
            arcpy.management.DefineProjection(raster_interpolated, target_crs)

            out_tif = os.path.join(out_folder, f"{tag}_{TARGET_YEAR}_{tflag}.tif")
            raster_interpolated.save(out_tif)

        print(f"✓ year {TARGET_YEAR}, treat {tflag} done")



def main(cfg, year):
    """Write FlamMap landscape rasters for *year* to paths.landscape_dir/<year>."""
    resolution = cfg.resolution
    switch_lowhigh = cfg["landscape"]["fuel_assumption"]
    in_folder = cfg.path("fvs_dir", year=year)          # FVS strclass/potfire reports
    out_folder = cfg.path("landscape_dir", year=year)
    os.makedirs(out_folder, exist_ok=True)
    target_crs = cfg.spatial_reference()

    # Set environment
    arcpy.env.workspace = cfg.path("gdb")
    arcpy.env.overwriteOutput = True
    arcpy.CheckOutExtension("Spatial")

    # === Get map and layers ===
    m = cfg.open_map()

    topo_tiffs(m, cfg.layer("dem"), cfg.layer("aoi"), out_folder, resolution, target_crs)

    fuel_model_tiff(m, cfg.layer("fuel_types", year), cfg.layer("treatments"), out_folder, resolution,
                    switch_lowhigh, cfg.supporting("fuel_type_conversion"))

    stand_char_tiff(m, cfg.layer("vri", year), in_folder, out_folder, resolution, target_crs,
                    cfg["fvs"]["target_year"])
