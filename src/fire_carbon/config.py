"""Load config.yaml and resolve paths.

Every script receives a ``Config`` object. Nothing else in the package hard-codes
a path, layer name or modelling assumption.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config.yaml"


class Config:
    def __init__(self, data: dict, config_path: Path):
        self.data = data
        self.config_path = Path(config_path)
        self.root = Path(data["paths"]["root"])

    # -- generic access -----------------------------------------------------
    def __getitem__(self, key):
        return self.data[key]

    def get(self, *keys, default=None):
        node = self.data
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    # -- paths ----------------------------------------------------------------
    def path(self, key: str, *parts, year: int | None = None, mkdir: bool = False) -> str:
        """Return paths.<key> resolved against paths.root, joined with *parts*.
        If *year* is given it is appended as a sub-folder."""
        p = Path(self.data["paths"][key])
        if not p.is_absolute():
            p = self.root / p
        if year is not None:
            p = p / str(year)
        p = p.joinpath(*[str(x) for x in parts])
        if mkdir:
            (p if not p.suffix else p.parent).mkdir(parents=True, exist_ok=True)
        return str(p)

    def supporting(self, key: str) -> str:
        """Return a file shipped with the repository (supporting.<key>)."""
        p = Path(self.data["supporting"][key])
        return str(p if p.is_absolute() else REPO_ROOT / p)

    def exe(self, key: str) -> str:
        return str(Path(self.data["executables"][key]))

    def treatment_table(self, year: int) -> str:
        return self.path("treatment_dir", f"Treatment_intersection_{year}.xlsx")

    # -- GIS ------------------------------------------------------------------
    @property
    def resolution(self) -> int:
        return self.data["gis"]["resolution_m"]

    @property
    def map_name(self) -> str:
        return self.data["gis"]["map"]

    def layer(self, key: str, year: int | None = None) -> str:
        name = self.data["gis"]["layers"][key]
        return name.format(year=year) if year is not None else name

    def spatial_reference(self):
        import arcpy
        return arcpy.SpatialReference(self.data["gis"]["crs_epsg"])

    def open_map(self):
        """Return the arcpy.mp Map object named gis.map in paths.aprx."""
        import arcpy
        aprx = arcpy.mp.ArcGISProject(self.path("aprx"))
        maps = aprx.listMaps(self.map_name)
        if not maps:
            raise ValueError(f"Map '{self.map_name}' not found in {self.path('aprx')}")
        return maps[0]


def load_config(path: str | os.PathLike | None = None) -> Config:
    path = Path(path) if path else DEFAULT_CONFIG
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return Config(data, path)
