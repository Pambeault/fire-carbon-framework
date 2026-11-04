"""
    Build a table of VRI polygons that intersect treatment polygons,
    including polygon area (ha), intersect area (ha) and percent overlap.
    The VRI layer must have a unique key named POLYGON_ID.
    """
import os
import pandas as pd
import numpy as np
import arcpy


def reproject(in_fc: str, target_sr: arcpy.SpatialReference, name: str):
    """Return a feature class in memory that is projected to target_sr."""
    if arcpy.Describe(in_fc).spatialReference.name == target_sr.name:
        return in_fc  # already in the right CRS
    out_fc = fr"in_memory\{name}"
    arcpy.management.Project(in_fc, out_fc, target_sr)
    return out_fc

def main(cfg, year: int):
    """Write Treatment_intersection_<year>.xlsx to paths.treatment_dir."""
    fc_vri = cfg.layer("vri", year)
    fc_treat = cfg.layer("treatments")
    arcpy.env.overwriteOutput = True
    arcpy.env.workspace = cfg.path("gdb")

    path_output = cfg.path("treatment_dir", f"Treatment_intersection_{year}.xlsx", mkdir=True)

    # ------------------------------------------------------------------
    # 0. Project copies (only if needed)
    # ------------------------------------------------------------------
    vri_sr = arcpy.Describe(fc_vri).spatialReference
    vri_proj = reproject(fc_vri, vri_sr, "vri_proj")
    treat_proj = reproject(fc_treat, vri_sr, "treat_proj")

    # ---- 1. Add area (ha) to VRI polygons -----------------------------
    arcpy.management.AddField(vri_proj, "POLY_HA", "DOUBLE")
    arcpy.management.CalculateGeometryAttributes(vri_proj,[["POLY_HA", "AREA"]], area_unit="HECTARES")

    # Extract POLYGON_ID & POLY_HA to pandas *now* (they never change)
    poly_arr = arcpy.da.TableToNumPyArray(vri_proj, ["POLYGON_ID", "POLY_HA"])
    df_vri = pd.DataFrame(poly_arr)  # master table


    # ---- 2. Intersect & get overlap area ------------------------------
    intersect_fc = r"in_memory\intersect_fc"
    arcpy.analysis.Intersect([vri_proj, treat_proj], intersect_fc, "ALL", "", "INPUT")

    if int(arcpy.management.GetCount(intersect_fc)[0]) == 0:
        print("No overlaps found")
    else:
        # area of each piece in ha
        arcpy.management.AddField(intersect_fc, "INTERSECT_HA", "DOUBLE")
        arcpy.management.CalculateGeometryAttributes(
            intersect_fc,
            [["INTERSECT_HA", "AREA"]],
            area_unit="HECTARES"
        )

    # Extract df from intersect
    int_arr = arcpy.da.TableToNumPyArray(intersect_fc, ["POLYGON_ID", "INTERSECT_HA"])
    df_int = pd.DataFrame(int_arr)

    # Merge into main table
    df_merged = df_vri.merge(df_int, on="POLYGON_ID", how="left")
    # -- drop polygons that had *no* intersection (INTERSECT_HA is NaN) --
    df_merged = df_merged.dropna(subset=["INTERSECT_HA"])

    # Calculate Percent overlap
    df_merged["PERCENT"] = (df_merged["INTERSECT_HA"] / df_merged["POLY_HA"] * 100).round(2)


    # ---- 6. Export to Excel ------------------------------------------
    df_merged.to_excel(path_output,index=False)
    print(f"  • Excel written → {path_output}")





