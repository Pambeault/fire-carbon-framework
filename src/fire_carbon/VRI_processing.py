"""
vri_processing.py
-----------------
Tools for converting VRI polygons to points, filling nulls via IDW,
adding species shortcuts, and attaching topographic attributes."""

from __future__ import annotations
import os
import arcpy
from arcpy.sa import Idw, ExtractValuesToPoints, Slope, Aspect, ExtractMultiValuesToPoints

arcpy.CheckOutExtension("Spatial")
arcpy.env.overwriteOutput = True


# ------------------------------------------------------------------ #
# 0. Utilities
# ------------------------------------------------------------------ #
def table_to_excel(in_table: str, out_xlsx: str) -> None:
    """export layer table to excel"""
    folder = os.path.dirname(out_xlsx)
    os.makedirs(folder, exist_ok=True)           # ensure destination exists

    # TableToExcel takes only positional args
    arcpy.conversion.TableToExcel(
        in_table,
        out_xlsx,
        "NAME"          # use field aliases as headers   # export domain descriptions if present
    )
    print(f"✅ Excel exported -> {out_xlsx}")


# ------------------------------------------------------------------ #
# 1. Convert polygons → points
# ------------------------------------------------------------------ #
def VRI_to_points(path_VRI, out_gdb):
    """Return (all_points_fc, empty_points_fc)."""

    base = os.path.splitext(os.path.basename(path_VRI))[0]
    all_pts = os.path.join(out_gdb, f"{base}_points")
    empty_pts = os.path.join(out_gdb, f"{base}_points_empty")

    # Centroid inside polygon
    arcpy.management.FeatureToPoint(path_VRI, all_pts, "INSIDE")

    # Select polygons where SPECIES_CD_1 is NULL
    arcpy.analysis.Select(all_pts, empty_pts, "SPECIES_CD_1 IS NULL")

    return all_pts, empty_pts


# ------------------------------------------------------------------ #
# 2. IDW fill for multiple fields
# ------------------------------------------------------------------ #
def IDW(vri_points: str, vri_empty: str, fields: list[str], out_gdb, power: float = 2) -> None:
    """Create in-memory IDW rasters for *fields* and extract to *vri_empty*."""
    # ------------------------------------------------------------------
    # 0) Make a *copy* of the empty-points FC so we don't overwrite input
    # ------------------------------------------------------------------
    base = os.path.splitext(os.path.basename(vri_empty))[0]
    vri_empty_filled = os.path.join(out_gdb, f"{base}_IDW")
    arcpy.management.CopyFeatures(vri_empty, vri_empty_filled)

    # ------------------------------------------------------------------
    # 1) Build IDW rasters (in memory) and a list for ExtractMultiValues
    # ------------------------------------------------------------------
    raster_field_pairs = []  # [[rasterPath, outputFieldName], ...]
    for fld in fields:
        print(f"• IDW({fld})")
        ras = Idw(vri_points, fld, power=power)
        ras_path = fr"in_memory\{fld}_idw"
        ras.save(ras_path)

        out_field = f"{fld}_IDW"
        raster_field_pairs.append([ras_path, out_field])

    # ------------------------------------------------------------------
    # 2) Push all rasters into the empty-points copy
    # ------------------------------------------------------------------
    ExtractMultiValuesToPoints(
        vri_empty_filled,
        raster_field_pairs,
        "BILINEAR"  # interpolation method
    )

    print(f"✓ IDW values written -> {vri_empty_filled}")
    return vri_empty_filled


# ------------------------------------------------------------------ #
# 3. Species shortcuts and % cover
# ------------------------------------------------------------------ #
def _species_codeblock(species_map: dict) -> str:
    """CalculateField code block: BCLCS_LEVEL_4 -> leading species
    (config: vri_processing.empty_polygon_species)."""
    return (
        f"lookup = {dict(species_map)!r}\n"
        "def get_species(species_code):\n"
        "    return lookup.get(species_code, None)  # None if no match\n"
    )

def get_species(vri_empties: str, species_map: dict) -> None:
    """Populate SPECIES_CD_1 nulls and set SPECIES_PCT_1 = 100."""

    # Calculate species alias
    arcpy.management.CalculateField(
        vri_empties,
        "SPECIES_CD_1",
        "get_species(!BCLCS_LEVEL_4!)",
        "PYTHON3",
        _species_codeblock(species_map),
    )

    # Set pct to 100 (simple constant calc)
    arcpy.management.CalculateField(
        vri_empties,
        "SPECIES_PCT_1",
        # ————— expression —————
        "100 if !SPECIES_CD_1! not in (None, '', ' ') else None",
        "PYTHON3"
    )

# ------------------------------------------------------------------ #
# 4. Match Empties to VRI data
# ------------------------------------------------------------------ #
def update_from_idw(pts_all: str,pts_empty_filled: str) -> None:
    """Transfer values from *pts_empty_filled* (subset with *_IDW* fields) into
    *pts_all* (full point FC) using *key_field* for the match.

    Copies these columns:
        • SPECIES_CD_1
        • SPECIES_PCT_1
        • Any field that ends with *_IDW  →  same name without suffix"""

    key_field= "POLYGON_ID"
    idw_suffix= "_IDW"

    # ------------------------------------------------------------------
    # 1. Build mapping  {source_field : dest_field}
    # ------------------------------------------------------------------
    transfer_map = {
        "SPECIES_CD_1": "SPECIES_CD_1",
        "SPECIES_PCT_1": "SPECIES_PCT_1",
    }

    # All *_IDW fields
    idw_fields = [
        f.name
        for f in arcpy.ListFields(pts_empty_filled)
        if f.name.endswith(idw_suffix)
    ]
    for src in idw_fields:
        dest = src.replace(idw_suffix, "")
        transfer_map[src] = dest

        # Add dest field if missing in pts_all
        if dest not in [f.name for f in arcpy.ListFields(pts_all)]:
            arcpy.management.AddField(pts_all, dest, "DOUBLE")

    # ------------------------------------------------------------------
    # 2. Build lookup dict  {POLYGON_ID: {dest: value, ...}}
    # ------------------------------------------------------------------
    src_fields = [key_field] + list(transfer_map.keys())
    lookup = {}
    with arcpy.da.SearchCursor(pts_empty_filled, src_fields) as cur:
        for row in cur:
            pid = row[0]
            lookup[pid] = {
                transfer_map[src]: val for src, val in zip(src_fields[1:], row[1:])
            }

    # ------------------------------------------------------------------
    # 3. Update pts_all where PID matches
    # ------------------------------------------------------------------
    dest_fields = [key_field] + list(transfer_map.values())
    null_like = {None, "", " "}

    with arcpy.da.UpdateCursor(pts_all, dest_fields) as cur:
        for row in cur:
            pid = row[0]
            if pid not in lookup:
                continue

            changed = False
            update_vals = lookup[pid]
            # Start dest index at 1 (skip key)
            for i, dest in enumerate(dest_fields[1:], start=1):
                src_val = update_vals.get(dest)
                if src_val in null_like:
                    continue  # nothing to copy
                if row[i] in null_like:      # only fill if dest is currently NULL
                    row[i] = src_val
                    changed = True
            if changed:
                cur.updateRow(row)

    print("✓ Attributes transferred from *_IDW* points into master layer.")

# ------------------------------------------------------------------ #
# 5. Topography & geometry
# ------------------------------------------------------------------ #
def get_topo(vri_points: str, path_dem: str, out_gdb: str) -> str:
    """Extract topo variables (elev, slope, aspect, X, Y) to VRI points."""
    arcpy.env.workspace = out_gdb
    arcpy.env.overwriteOutput = True

    # 1. Elevation
    print("• Extracting elevation")
    elev_pts = os.path.join(out_gdb, "vri_pts_elev")
    ExtractValuesToPoints(vri_points, path_dem, elev_pts)
    arcpy.AlterField_management(elev_pts, "RASTERVALU", "elev")

    # 2. Slope
    print("• Extracting slope")
    slope_ras = Slope(path_dem, "DEGREE")
    slope_pts = os.path.join(out_gdb, "vri_pts_slope")
    ExtractValuesToPoints(elev_pts, slope_ras, slope_pts)
    arcpy.AlterField_management(slope_pts, "RASTERVALU", "slope")

    # 3. Aspect
    print("• Extracting aspect")
    aspect_ras = Aspect(path_dem)
    aspect_pts = os.path.join(out_gdb, "vri_pts_aspect")
    ExtractValuesToPoints(slope_pts, aspect_ras, aspect_pts)
    arcpy.AlterField_management(aspect_pts, "RASTERVALU", "aspect")

    # 4. X and Y geometry
    print("• Calculating geometry (X, Y)")
    final_pts = os.path.join(out_gdb, "vri_pts_topo")
    arcpy.management.CopyFeatures(aspect_pts, final_pts)

    # Add and calculate X, Y
    arcpy.management.AddField(final_pts, "X", "DOUBLE")
    arcpy.management.AddField(final_pts, "Y", "DOUBLE")
    arcpy.management.CalculateGeometryAttributes(
        final_pts,
        [["X", "POINT_X"], ["Y", "POINT_Y"]],
        coordinate_system=arcpy.SpatialReference(4326)  # Or use same as input
    )

    print("✓ Done extracting topo info.")
    return final_pts


# ------------------------------------------------------------------ #
# 5. Main orchestrator
# ------------------------------------------------------------------ #
def main(cfg, year: int):
    """Write paths.processed_dir/VRI_<year>_processed.xlsx."""
    vcfg = cfg["vri_processing"]
    out_gdb = cfg.path("gdb")
    path_VRI = os.path.join(out_gdb, cfg.layer("vri", year))
    path_dem = os.path.join(out_gdb, cfg.layer("dem"))
    path_out = cfg.path("processed_dir", f"VRI_{year}_processed.xlsx", mkdir=True)

    # 1. Poly ➜ points
    pts_all, pts_empty = VRI_to_points(path_VRI, out_gdb)

    # 2. IDW fill for empties
    idw_fields = list(vcfg["idw_fields"])
    pts_empty_filled = IDW(pts_all, pts_empty, idw_fields, out_gdb, power=vcfg["idw_power"])

    # 3. Species for empties
    get_species(pts_empty_filled, vcfg["empty_polygon_species"])

    # 4. Match empties points to all_points
    update_from_idw(pts_all, pts_empty_filled)

    # # 5. Topography
    pts_all_filled = get_topo(pts_all, path_dem, out_gdb)

    # 6. Export to Excel
    table_to_excel(pts_all_filled, path_out)

    print("🎉 VRI processing complete.")