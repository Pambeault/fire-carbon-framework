import arcpy
import os
import re

# === Sub-Utilities -- Not to be called from the Main
def safe_name(raw_name, workspace):
    """
    Return a workspace-legal dataset name.
    Works for feature classes *and* rasters.
    """
    # Use ValidateTableName for everything
    valid = arcpy.ValidateTableName(raw_name, workspace)

    # Extra clean-up in case ValidateTableName returns ''
    if not valid:
        valid = re.sub(r"[^A-Za-z0-9_]", "_", raw_name)
    if not valid[0].isalpha():
        valid = f"A_{valid}"
    return valid




# === Full utilities - can be called from the Main
def GIS_migration(path_gdb, path_aprx, map_name):
    """When trying to start a new ArcGIS pro Project, you can copy the layers/raters to a new project
    But when doing that, the location of those layers remain in the original project.
    The goal of this function is to copy these layers into the new project."""

    # Define project
    arcpy.env.overwriteOutput = True
    aprx = arcpy.mp.ArcGISProject(path_aprx)
    map_obj = aprx.listMaps()[0]  # Change index or name if needed

    # Get the desired map
    if map_name:
        maps = aprx.listMaps(map_name)
        if not maps:
            raise ValueError(f"Map '{map_name}' not found in project.")
        map_obj = maps[0]

    # Loop through layers and copy
    for lyr in map_obj.listLayers():
        if lyr.isFeatureLayer or lyr.isRasterLayer:
            # 1. Make a legal name for this workspace
            valid = safe_name(lyr.name, path_gdb)

            # 2. Build output path
            out_path = os.path.join(path_gdb, valid)

            # 3. Copy with error trapping
            try:
                if lyr.isFeatureLayer:
                    arcpy.CopyFeatures_management(lyr, out_path)
                else:
                    arcpy.CopyRaster_management(lyr, out_path)
                print(f"✓ Copied: {lyr.name}  →  {out_path}")
            except Exception as e:
                print(f"⚠ Failed on {lyr.name}: {e}")

def Get_table_from_layer(path_aprx, map_name, layer_name, path_out):
    """Export the attribute table of *layer_name* in *map_name* of *path_aprx*
        to an Excel file at *path_out*."""
    aprx = arcpy.mp.ArcGISProject(path_aprx)

    # ---- locate map ----
    maps = aprx.listMaps(map_name)
    if not maps:
        raise ValueError(f"Map '{map_name}' not found in project.")
    m = maps[0]

    # ---- locate layer ----
    layers = [lyr for lyr in m.listLayers() if lyr.name == layer_name]
    if not layers:
        raise ValueError(f"Layer '{layer_name}' not found in map '{map_name}'.")
    lyr = layers[0]

    if not lyr.supports("DATASOURCE"):
        raise RuntimeError(f"Layer '{layer_name}' has no datasource.")

    # ---- ensure output folder exists ----
    out_folder = os.path.dirname(path_out)
    os.makedirs(out_folder, exist_ok=True)

    # ---- use TableToExcel GP tool ----
    arcpy.env.overwriteOutput = True
    try:
        arcpy.conversion.TableToExcel(lyr, path_out, "NAME")
        print(f"✅ Table exported to: {path_out}")
    except Exception as exc:
        raise RuntimeError(f"TableToExcel failed: {exc}")