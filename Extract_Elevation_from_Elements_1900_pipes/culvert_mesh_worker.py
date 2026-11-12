# -*- coding: utf-8 -*-
"""Bend-aware MIKE 21 FM pipe/culvert bathymetry assignment.

INLET (pipe START):
  - Trace edge-connected quadrilateral mesh cells up to 15 m UPSTREAM,
    following bends; use a 3 m TOTAL-width corridor (1.5 m each side).
  - Z_Inflow = LOWEST bathymetry among selected quad elements.
  - If no usable quad path can be traced, search eligible inlet-side cells
    within 5 m using a Code-2-like endpoint method (triangles allowed).

OUTLET (pipe END):
  - Apply the SAME bend-aware quad tracing up to 15 m DOWNSTREAM,
    within a 3 m TOTAL-width corridor.
  - Z_Outflow = HIGHEST bathymetry STRICTLY LOWER than Z_Inflow among
    the downstream cells in that corridor.
  - If no lower quad candidate exists, use a Code-2-like 5 m outlet-side
    search. If a usable quad path was traced, this fallback accepts TRIANGLES
    only; otherwise it searches nearby elements regardless of type.
  - When NO valid Z_Outflow is found: preserve the newly calculated Z_Inflow
    and write Z_Outflow = 0 (requested sentinel).

Other safeguards:
  - Only TopoID == '1900s0' is processed.
  - Writes temporary calculated shapefile and optional QA geometries.
    ArcMap toolbox then writes ONLY Z_Inflow/Z_Outflow to input after backup.
  - Designed for invocation from ArcMap 10.8 / ArcGIS Pro via the companion .pyt.
  - Quad identification relies on true four-corner cell geometries. A mesh
    exported entirely as triangles will use the 5 m endpoint fallback.
  - Connected-cell tracing is an approximation; verify paths around junctions,
    very sharp bends and irregular mesh transitions before hydraulic use.
"""

from pathlib import Path
from collections import Counter
import argparse
import json
import sys
import math
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

# ------------------------- RUN CONFIGURATION ---------------------------------
# These values are supplied by the ArcMap/ArcGIS Pro toolbox as a JSON file.
# No project-specific path needs to be edited in this worker.
CULVERTS_SHP = None
SELECTED_PIPES_SHP = None
ELEMENTS_SHPS = []
BATH_FIELDS = []
SELECTED_OIDS = None
SOURCE_ID_FIELD = None
OUTPUT_SHP = None
QA_GPKG = None

TOPOID_FIELD = "TopoID"
TARGET_TOPOID = "1900s0"
BATH_FIELD = "Bathymetry"

SEARCH_LENGTH_M = 15.0
SEARCH_WIDTH_M = 3.0       # TOTAL width, not a 3 m radius
QUAD_SEED_RADIUS_M = 2.5
QUAD_MIN_TRACK_M = 2.5
QUAD_MAX_TURN_DEG = 75.0
QUAD_SHARED_EDGE_MIN_M = 0.01
FALLBACK_RADIUS_M = 5.0
WRITE_QA = True
# -----------------------------------------------------------------------------


def configure(config):
    """Accept user-supplied input paths and analysis distances from toolbox."""
    global CULVERTS_SHP, SELECTED_PIPES_SHP, ELEMENTS_SHPS, OUTPUT_SHP, QA_GPKG
    global BATH_FIELDS, SELECTED_OIDS, SOURCE_ID_FIELD
    global TOPOID_FIELD, TARGET_TOPOID, BATH_FIELD
    global SEARCH_LENGTH_M, SEARCH_WIDTH_M, FALLBACK_RADIUS_M, WRITE_QA
    required = ("pipes", "elements", "output_shp", "qa_gpkg")
    for key in required:
        if not config.get(key):
            raise ValueError("Missing configuration setting: {}".format(key))
    CULVERTS_SHP = str(config["pipes"])
    SELECTED_PIPES_SHP = (str(config["selected_pipes"])
                          if config.get("selected_pipes") else None)
    ELEMENTS_SHPS = [str(p) for p in config["elements"]]
    BATH_FIELDS = [str(f) for f in config.get("bath_fields", [])]
    SELECTED_OIDS = config.get("selected_oids", None)
    SOURCE_ID_FIELD = config.get("source_id_field", None)
    if BATH_FIELDS and len(BATH_FIELDS) != len(ELEMENTS_SHPS):
        raise ValueError("Each mesh layer must have one elevation field")
    if SELECTED_OIDS is not None and not SOURCE_ID_FIELD:
        raise ValueError("Source ID field required to honor selected pipes")
    OUTPUT_SHP = str(config["output_shp"])
    QA_GPKG = str(config["qa_gpkg"])
    TOPOID_FIELD = str(config.get("topoid_field", "TopoID"))
    TARGET_TOPOID = str(config.get("target_topoid", "1900s0"))
    BATH_FIELD = str(config.get("bath_field", "Bathymetry"))
    SEARCH_LENGTH_M = float(config.get("search_length_m", 15.0))
    SEARCH_WIDTH_M = float(config.get("search_width_m", 3.0))
    FALLBACK_RADIUS_M = float(config.get("fallback_radius_m", 5.0))
    WRITE_QA = bool(config.get("write_qa", True))
    if min(SEARCH_LENGTH_M, SEARCH_WIDTH_M, FALLBACK_RADIUS_M) <= 0:
        raise ValueError("All three search distances must be positive")
    if not OUTPUT_SHP.lower().endswith(".shp"):
        raise ValueError("Output must be a .shp file")
    if Path(OUTPUT_SHP).exists():
        raise ValueError("Refusing to overwrite output shapefile: {}".format(OUTPUT_SHP))
    if WRITE_QA and Path(QA_GPKG).exists():
        raise ValueError("Refusing to overwrite QA GeoPackage: {}".format(QA_GPKG))
    qa_shp_dir = Path(OUTPUT_SHP).parent / (Path(OUTPUT_SHP).stem + "_QA_layers")
    if WRITE_QA and qa_shp_dir.exists():
        raise ValueError("Refusing to overwrite QA shapefile directory: {}".format(qa_shp_dir))
    if Path(CULVERTS_SHP).resolve() == Path(OUTPUT_SHP).resolve():
        raise ValueError("Output file must not be the input file")



def verify_crs(gdf, name):
    if gdf.crs is None or gdf.crs.is_geographic:
        raise ValueError("{} requires a projected CRS with metre units".format(name))
    for a in gdf.crs.axis_info[:2]:
        if a.unit_name.lower() not in ("metre", "meter"):
            raise ValueError("{} coordinate system is not in metres".format(name))


def unit(v):
    d = math.hypot(float(v[0]), float(v[1]))
    if d <= 1e-12:
        raise ValueError("Pipe contains a zero-length first or last segment")
    return np.array([float(v[0]) / d, float(v[1]) / d])


def quad_type(poly):
    """Classify by actual number of DISTINCT corner vertices; not cell size."""
    if poly is None or poly.is_empty or poly.geom_type != "Polygon":
        return "OTHER"
    if len(poly.interiors) > 0:
        return "OTHER"
    verts = list(poly.exterior.coords)[:-1]  # final coordinate closes ring
    distinct = len(set((round(x, 8), round(y, 8)) for x, y in verts))
    return "QUAD" if distinct == 4 else ("TRI" if distinct == 3 else "OTHER")


def hits(gdf, geom):
    if gdf.empty:
        return gdf
    positions = gdf.sindex.query(geom, predicate="intersects")
    return gdf.iloc[positions]


def upstream_direction(line):
    coords = np.asarray(line.coords, dtype=float)[:, :2]
    return -unit(coords[1] - coords[0])


def downstream_direction(line):
    coords = np.asarray(line.coords, dtype=float)[:, :2]
    return unit(coords[-1] - coords[-2])


def geom_center(poly):
    p = poly.representative_point()
    return np.array([p.x, p.y])


def edge_neighbors(local, i, center_cache):
    """Neighbor cells must share an actual polygon edge, NOT just a corner."""
    geom = local.geometry.iloc[i]
    near_ids = local.sindex.query(geom, predicate="intersects")
    for j in near_ids:
        j = int(j)
        if j == i:
            continue
        other = local.geometry.iloc[j]
        common = geom.boundary.intersection(other.boundary)
        if common.length > QUAD_SHARED_EDGE_MIN_M:
            yield j


def trace_quad_strip(all_quads, line, is_outlet=False):
    """Follow edge-connected quadrilateral cells along the trench.

    For inlet, go upstream from the line START; for outlet, go downstream
    from the line END. Returns (cells, path, corridor, travelled_m, mode)
    or None if no usable connected path was found.
    """
    endpoint = (Point(line.coords[-1][:2]) if is_outlet
                else Point(line.coords[0][:2]))
    origin = np.array([endpoint.x, endpoint.y])
    heading = (downstream_direction(line) if is_outlet
               else upstream_direction(line))

    local = hits(all_quads, endpoint.buffer(SEARCH_LENGTH_M + 5.0)).reset_index(drop=True)
    if local.empty:
        return None
    seeds = hits(local, endpoint.buffer(QUAD_SEED_RADIUS_M))
    if seeds.empty:
        return None

    centers = [geom_center(g) for g in local.geometry]
    seed_options = []
    for i in seeds.index:
        i = int(i)
        c = centers[i]
        projection = float(np.dot(c - origin, heading))
        if projection >= -0.5 or local.geometry.iloc[i].intersects(endpoint):
            dist = float(local.geometry.iloc[i].distance(endpoint))
            # Prioritize cells touching the endpoint and facing search direction.
            score = (dist, max(0.0, -projection), float(local.iloc[i][BATH_FIELD]))
            seed_options.append((score, i))
    if not seed_options:
        return None
    seed = min(seed_options)[1]

    coords = [tuple(origin)]
    first = centers[seed]
    travelled = float(np.linalg.norm(first - origin))
    if travelled > 0.03:
        coords.append(tuple(first))

    visited = {seed}
    current = seed
    min_alignment = math.cos(math.radians(QUAD_MAX_TURN_DEG))
    for _ in range(min(len(local), 3000)):
        if travelled >= SEARCH_LENGTH_M - 1e-6:
            break
        options = []
        a = centers[current]
        for j in edge_neighbors(local, current, centers):
            if j in visited:
                continue
            delta = centers[j] - a
            dist = float(np.linalg.norm(delta))
            if dist < 0.02:
                continue
            direction = delta / dist
            alignment = float(np.dot(direction, heading))
            if alignment < min_alignment:
                continue
            # Most aligned cell first, bathymetry used only to break ties.
            options.append((-alignment, float(local.iloc[j][BATH_FIELD]), dist, j, direction))
        if not options:
            break
        _, _, dist, next_i, direction = min(options, key=lambda t: t[:4])
        if travelled + dist >= SEARCH_LENGTH_M:
            portion = (SEARCH_LENGTH_M - travelled) / dist
            next_point = centers[current] + portion * (centers[next_i] - centers[current])
            if np.linalg.norm(np.asarray(coords[-1]) - next_point) > 0.02:
                coords.append(tuple(next_point))
            travelled = SEARCH_LENGTH_M
            break
        travelled += dist
        current = next_i
        visited.add(current)
        coords.append(tuple(centers[current]))
        heading = unit(0.55 * heading + 0.45 * direction)

    if len(coords) < 2 or travelled < QUAD_MIN_TRACK_M:
        return None
    path = LineString(coords)
    corridor = path.buffer(SEARCH_WIDTH_M / 2.0, cap_style=2, join_style=1)
    potential = hits(local, corridor)
    potential = potential[potential.geometry.intersection(corridor).area > 1e-9].copy()
    if potential.empty:
        return None

    # Exclude disconnected quad strips, even if physically near the path.
    eligible = set(int(i) for i in potential.index)
    connected = set()
    queue = [seed]
    while queue:
        k = queue.pop()
        if k in connected or k not in eligible:
            continue
        connected.add(k)
        for neighbor in edge_neighbors(local, k, centers):
            if neighbor in eligible and neighbor not in connected:
                queue.append(neighbor)
    if not connected:
        return None
    selected = local.iloc[sorted(connected)].copy()
    selected["MeshType"] = "QUAD"
    mode = "QUAD" if travelled >= SEARCH_LENGTH_M - 0.05 else "QUAD_SHORT"
    return selected, path, corridor, travelled, mode



def endpoint_fallback(elements, line):
    """Code 2 type: min snapped inlet + upgradient-side candidates in 5 m.

    'Upgradient' here refers to position BEHIND digitised inlet, not actual
    topographic gradient. No flow-path inference is made for triangles.
    """
    inlet = Point(line.coords[0][:2])
    direction = upstream_direction(line)
    origin = np.array([inlet.x, inlet.y])
    candidates = hits(elements, inlet.buffer(FALLBACK_RADIUS_M)).copy()
    if candidates.empty:
        return None
    keep = []
    for _, rec in candidates.iterrows():
        # Allow cell directly touching start point (Code 2 snapped rule).
        touching = rec.geometry.intersects(inlet)
        behind = np.dot(geom_center(rec.geometry) - origin, direction) > 0.0
        keep.append(bool(touching or behind))
    selected = candidates.loc[np.asarray(keep, dtype=bool)].copy()
    if selected.empty:
        return None
    selected["MeshType"] = selected["MeshType"].astype(str)
    corridor = inlet.buffer(FALLBACK_RADIUS_M)
    return (float(selected[BATH_FIELD].min()), selected, None,
            corridor, None, "ENDPOINT")


def outlet_endpoint_candidates(elements, line, triangles_only=False):
    """Find forward-side cells near the outlet (Code-2-like 5 m fallback).

    Unlike the previous outlet method, there is no 'snapped first' rule:
    all eligible cells in the endpoint buffer compete for HIGHEST Z < Z_In.
    """
    outlet = Point(line.coords[-1][:2])
    direction = downstream_direction(line)
    origin = np.array([outlet.x, outlet.y])
    circle = outlet.buffer(FALLBACK_RADIUS_M)
    nearby = hits(elements, circle).copy()
    if triangles_only:
        nearby = nearby[nearby["MeshType"] == "TRI"].copy()
    if nearby.empty:
        return nearby, circle
    mask = []
    for _, cell in nearby.iterrows():
        touches = cell.geometry.intersects(outlet)
        ahead = np.dot(geom_center(cell.geometry) - origin, direction) > 0.0
        mask.append(bool(touches or ahead))
    return nearby.loc[np.asarray(mask, dtype=bool)].copy(), circle


def build_mesh_search_mask(pipes, eligible):
    """Areas that can influence either endpoint's 15 m trench search.

    Quad tracing already limits itself to a circle of search_length + 5 m
    at each pipe endpoint; fallback uses the smaller configurable radius.
    Unioning these small circles means GIS files are spatially filtered
    without removing any cell the existing algorithm could encounter.
    """
    radius = max(SEARCH_LENGTH_M + 5.0, FALLBACK_RADIUS_M, QUAD_SEED_RADIUS_M)
    circles = []
    for line in pipes.loc[eligible].geometry:
        if line is None or line.is_empty or line.geom_type != "LineString":
            continue
        if len(line.coords) < 2:
            continue
        for pt in (line.coords[0], line.coords[-1]):
            circles.append(Point(pt[:2]).buffer(radius, resolution=10))
    if not circles:
        return None
    return unary_union(circles)


def read_elements(pipe_crs, search_mask=None):
    """Read required columns and ONLY cells near eligible pipe endpoints.

    Directly reads selected bathymetry field from source shapefiles, avoiding
    expensive ArcMap exports. CRS-aware spatial masking uses OGR/pyogrio;
    if the spatial-mask driver is unavailable, a bbox filter is safe but less
    selective (the full geometry is still kept and never clipped).
    """
    parts = []
    for i, path in enumerate(ELEMENTS_SHPS):
        field = BATH_FIELDS[i] if BATH_FIELDS else BATH_FIELD
        # A single row is enough to discover CRS; do not load huge datasets
        # merely to determine their coordinate system.
        header = gpd.read_file(path, rows=1)
        if field not in header.columns:
            raise ValueError("Missing {} field: {}".format(field, path))
        if header.crs is None:
            raise ValueError("Missing coordinate system in {}".format(path))
        if search_mask is not None:
            mask_in_source = gpd.GeoSeries([search_mask], crs=pipe_crs).to_crs(
                header.crs).iloc[0]
            try:
                gdf = gpd.read_file(path, columns=[field], mask=mask_in_source)
            except (TypeError, NotImplementedError) as exc:
                print("Spatial mask unsupported on mesh {} ({}); using bounding box.".format(
                    i + 1, exc))
                gdf = gpd.read_file(path, columns=[field], bbox=mask_in_source.bounds)
        else:
            gdf = gpd.read_file(path, columns=[field])
        if gdf.crs is None:
            raise ValueError("Missing coordinate system in {}".format(path))
        if gdf.crs != pipe_crs:
            gdf = gdf.to_crs(pipe_crs)
        gdf = gdf[[field, "geometry"]].copy()
        gdf.rename(columns={field: BATH_FIELD}, inplace=True)
        gdf[BATH_FIELD] = pd.to_numeric(gdf[BATH_FIELD], errors="coerce")
        gdf = gdf[gdf[BATH_FIELD].notna() & np.isfinite(gdf[BATH_FIELD])]
        gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
        gdf = gdf[gdf.geometry.geom_type == "Polygon"]
        if not gdf.empty:
            parts.append(gdf)
        print("Mesh {}/{}: loaded {} relevant elements from {} (field: {}).".format(
            i + 1, len(ELEMENTS_SHPS), len(gdf), path, field), flush=True)
    if not parts:
        print("No mesh elements within search range; no pipe Z values will be changed.", flush=True)
        return gpd.GeoDataFrame(
            {BATH_FIELD: pd.Series(dtype=float), "MeshType": pd.Series(dtype=str)},
            geometry=gpd.GeoSeries([], crs=pipe_crs), crs=pipe_crs)
    result = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=pipe_crs)
    result["MeshType"] = result.geometry.apply(quad_type)
    return result


def write_qa(qa_rows, crs):
    if not WRITE_QA:
        return
    path = Path(QA_GPKG)
    shp_folder = path.parent / (path.stem + "_QA_layers")
    shp_folder.mkdir(parents=True, exist_ok=False)
    for layer in ("inlet_paths", "inlet_corridors", "inlet_mesh_cells",
                  "outlet_paths", "outlet_corridors", "outlet_mesh_cells"):
        records = qa_rows.get(layer, [])
        if not records:
            continue
        data = gpd.GeoDataFrame(records, geometry="geometry", crs=crs)
        data.to_file(str(path), layer=layer, driver="GPKG")
        # ArcMap 10.8 users can directly add these shapefile QA layers.
        data.to_file(str(shp_folder / (layer + ".shp")), driver="ESRI Shapefile")
    if path.exists():
        print("Saved QA GeoPackage: {}".format(path))
    print("Saved ArcMap QA shapefiles: {}".format(shp_folder))



def _record_match_key(row, original_fields):
    """Stable export-to-export identity from geometry + source attributes.

    ArcGIS changes ObjectIDs when exporting selected vs full records.
    Use exact shapefile geometry and source attribute values, not exported
    row positions or new FIDs. Raise if indistinguishable duplicate records
    would make a selection ambiguous.
    """
    geom = row.geometry
    shape_key = geom.wkb_hex if geom is not None else "<NULL_GEOMETRY>"
    attributes = []
    for field in original_fields:
        val = row[field]
        if pd.isna(val):
            attributes.append((field, "<NULL>"))
        else:
            attributes.append((field, str(val)))
    return (shape_key, tuple(attributes))


def match_selected_pipes(all_pipes, selected_file):
    """Boolean mask onto full pipe dataset for an ArcMap TOC selection.

    Input copies were created by ArcPy from the same source feature class,
    preserving geometry and attributes. Never infer selected rows from
    shapefile record positions (ArcGIS renumbers them on export).
    """
    selected = gpd.read_file(selected_file)
    if selected.empty:
        raise ValueError("A selection was supplied, but it contains no features")
    fields = [name for name in all_pipes.columns if name != "geometry"]
    absent = [name for name in fields if name not in selected.columns]
    if absent:
        raise ValueError("Selected export has different fields: {}".format(absent))
    full_keys = [_record_match_key(row, fields)
                 for _, row in all_pipes.iterrows()]
    selected_keys = [_record_match_key(row, fields)
                     for _, row in selected.iterrows()]
    full_counts = Counter(full_keys)
    selected_counts = Counter(selected_keys)
    for key, count in selected_counts.items():
        available = full_counts.get(key, 0)
        if count > available:
            raise ValueError(
                "A selected feature does not match the full source export. "
                "Use the same pipe layer and try again.")
        if 0 < count < available:
            raise ValueError(
                "Ambiguous selected features: two or more pipes have "
                "identical geometries AND attributes, but only some are "
                "selected. Add a unique pipe ID field and rerun.")
    mask = np.asarray([key in selected_counts for key in full_keys], dtype=bool)
    if int(mask.sum()) != len(selected):
        raise ValueError("Selected-to-full match count differs from ArcMap selection")
    print("Matched {} selected pipes in the complete dataset".format(len(selected)))
    return mask


def main():
    pipes = gpd.read_file(CULVERTS_SHP)
    if SELECTED_OIDS is not None:
        if SOURCE_ID_FIELD not in pipes.columns:
            raise ValueError("Source ID field not present in temporary pipes")
        selection_mask = pipes[SOURCE_ID_FIELD].isin(SELECTED_OIDS).to_numpy()
    elif SELECTED_PIPES_SHP:
        selection_mask = match_selected_pipes(pipes, SELECTED_PIPES_SHP)
    else:
        selection_mask = np.ones(len(pipes), dtype=bool)
    verify_crs(pipes, "Pipes")
    if TOPOID_FIELD not in pipes.columns:
        raise ValueError("Missing field: {}".format(TOPOID_FIELD))
    for field, default in (("Z_Inflow", np.nan), ("Z_Outflow", np.nan),
                           ("InMode", ""), ("InCells", 0), ("InLen_m", np.nan),
                           ("OutMode", ""), ("OutCells", 0),
                           ("OutLen_m", np.nan), ("OutDist_m", np.nan),
                           ("Status", "")):
        if field not in pipes.columns:
            pipes[field] = default
    pipes["Z_Inflow"] = pd.to_numeric(pipes["Z_Inflow"], errors="coerce").astype(float)
    pipes["Z_Outflow"] = pd.to_numeric(pipes["Z_Outflow"], errors="coerce").astype(float)
    topoid_mask = (pipes[TOPOID_FIELD].astype(str).str.strip() == TARGET_TOPOID).to_numpy()
    mask = topoid_mask & selection_mask
    print("Complete input: {} pipes | selection eligible: {} | matching TopoID={}: {} "
          "| to process: {}".format(len(pipes), int(selection_mask.sum()),
                                   TARGET_TOPOID, int(topoid_mask.sum()), int(mask.sum())))
    if mask.any():
        spatial_mask = build_mesh_search_mask(pipes, mask)
        if spatial_mask is None:
            # No valid lines to process. Do not load mesh data.
            elements = gpd.GeoDataFrame(
                {BATH_FIELD: pd.Series(dtype=float), "MeshType": pd.Series(dtype=str)},
                geometry=gpd.GeoSeries([], crs=pipes.crs), crs=pipes.crs)
        else:
            print("Loading {} mesh layers, spatially restricted to active pipe endpoints...".format(
                len(ELEMENTS_SHPS)), flush=True)
            elements = read_elements(pipes.crs, search_mask=spatial_mask)
    else:
        print("No selected/eligible pipes with TopoID={}; skipping all mesh reads".format(
            TARGET_TOPOID))
        elements = gpd.GeoDataFrame(
            {BATH_FIELD: pd.Series(dtype=float), "MeshType": pd.Series(dtype=str)},
            geometry=gpd.GeoSeries([], crs=pipes.crs), crs=pipes.crs)
    quads = elements[elements["MeshType"] == "QUAD"].copy().reset_index(drop=True)
    print("Mesh totals near target pipes: {} quads, {} triangles, {} others".format(
        len(quads), sum(elements.MeshType == "TRI"), sum(elements.MeshType == "OTHER")))

    qa = {layer: [] for layer in (
        "inlet_paths", "inlet_corridors", "inlet_mesh_cells",
        "outlet_paths", "outlet_corridors", "outlet_mesh_cells")}
    inlet_updated = 0
    outlet_updated = 0
    no_outlet = 0

    for index, row in pipes.loc[mask].iterrows():
        pipe = row.geometry
        if (pipe is None or pipe.is_empty or pipe.geom_type != "LineString"
                or len(pipe.coords) < 2):
            pipes.at[index, "Status"] = "BAD_LINE"
            continue
        try:
            # --- INLET: minimum elevation along connected upstream quad ribbon ---
            inlet_track = trace_quad_strip(quads, pipe, is_outlet=False)
            if inlet_track is not None:
                in_cells, in_path, in_corridor, in_len, in_mode = inlet_track
                z_in = float(in_cells[BATH_FIELD].min())
            else:
                fb = endpoint_fallback(elements, pipe)
                if fb is None:
                    pipes.at[index, "Status"] = "NO_INLET"
                    print("Pipe {}: no inlet mesh candidates; values unchanged".format(index))
                    continue
                z_in, in_cells, in_path, in_corridor, in_len, in_mode = fb

            # Reset outlet to the requested sentinel immediately after a valid
            # inlet is calculated. Even an unexpected outlet-search error cannot
            # leave a stale outlet value paired with the newly assigned inlet.
            pipes.at[index, "Z_Inflow"] = round(z_in, 4)
            pipes.at[index, "Z_Outflow"] = 0.0
            pipes.at[index, "OutMode"] = "NO_VALID"
            pipes.at[index, "OutCells"] = 0
            pipes.at[index, "OutLen_m"] = np.nan
            pipes.at[index, "OutDist_m"] = np.nan
            pipes.at[index, "InMode"] = in_mode
            pipes.at[index, "InCells"] = len(in_cells)
            pipes.at[index, "InLen_m"] = (round(in_len, 2)
                                           if in_len is not None else np.nan)
            inlet_updated += 1

            for _, cell in in_cells.iterrows():
                qa["inlet_mesh_cells"].append({
                    "pipe_idx": int(index), "bathy": float(cell[BATH_FIELD]),
                    "type": cell["MeshType"],
                    "is_min": bool(abs(float(cell[BATH_FIELD]) - z_in) < 1e-7),
                    "mode": in_mode, "geometry": cell.geometry})
            qa["inlet_corridors"].append({
                "pipe_idx": int(index), "mode": in_mode, "geometry": in_corridor})
            if in_path is not None:
                qa["inlet_paths"].append({
                    "pipe_idx": int(index), "len_m": float(in_len),
                    "geometry": in_path})

            # --- OUTLET: same tracing, but MAXIMUM of elevations BELOW Z_In ---
            outlet_track = trace_quad_strip(quads, pipe, is_outlet=True)
            out_chosen = None
            out_cells = None
            out_len = None
            out_mode = None
            outlet_pt = Point(pipe.coords[-1][:2])

            if outlet_track is not None:
                quad_cells, out_path, out_corridor, out_len, trace_mode = outlet_track
                qa["outlet_paths"].append({
                    "pipe_idx": int(index), "len_m": float(out_len),
                    "geometry": out_path})
                qa["outlet_corridors"].append({
                    "pipe_idx": int(index), "mode": trace_mode,
                    "geometry": out_corridor})
                valid_quad = quad_cells[
                    (quad_cells[BATH_FIELD] < z_in) &
                    (quad_cells[BATH_FIELD].round(4) < round(z_in, 4))
                ]
                if not valid_quad.empty:
                    out_chosen = valid_quad.loc[[valid_quad[BATH_FIELD].idxmax()]]
                    out_cells = quad_cells
                    out_mode = trace_mode

            if out_chosen is None:
                # Triangles only if a quad ribbon exists but all its values
                # fail the Z_Out < Z_In test. Else use all nearby cells.
                only_triangles = outlet_track is not None
                nearby, fallback_area = outlet_endpoint_candidates(
                    elements, pipe, triangles_only=only_triangles)
                qa["outlet_corridors"].append({
                    "pipe_idx": int(index),
                    "mode": "TRI5M" if only_triangles else "ENDPT5M",
                    "geometry": fallback_area})
                valid_nearby = nearby[
                    (nearby[BATH_FIELD] < z_in) &
                    (nearby[BATH_FIELD].round(4) < round(z_in, 4))
                ]
                if not valid_nearby.empty:
                    out_chosen = valid_nearby.loc[[valid_nearby[BATH_FIELD].idxmax()]]
                    out_cells = nearby
                    out_mode = "TRI5M" if only_triangles else "ENDPT5M"
                    if outlet_track is None:
                        out_len = None

            if out_chosen is None:
                # This is intentional: retain calculated Z_Inflow even if no
                # outlet satisfies the strict below-inlet condition.
                pipes.at[index, "Z_Outflow"] = 0.0
                pipes.at[index, "OutMode"] = "NO_VALID"
                pipes.at[index, "OutCells"] = 0
                pipes.at[index, "OutLen_m"] = (round(out_len, 2)
                                               if out_len is not None else np.nan)
                pipes.at[index, "OutDist_m"] = np.nan
                pipes.at[index, "Status"] = "IN_ONLY"
                no_outlet += 1
                print("Pipe {}: Z_In={:.4f}; no qualifying downstream cell; "
                      "Z_Out=0".format(index, z_in))
                continue

            z_out = float(out_chosen[BATH_FIELD].iloc[0])
            out_cell = out_chosen.geometry.iloc[0]
            pipes.at[index, "Z_Outflow"] = round(z_out, 4)
            pipes.at[index, "OutMode"] = out_mode
            pipes.at[index, "OutCells"] = len(out_cells)
            pipes.at[index, "OutLen_m"] = (round(out_len, 2)
                                           if out_len is not None else np.nan)
            pipes.at[index, "OutDist_m"] = round(float(out_cell.distance(outlet_pt)), 2)
            pipes.at[index, "Status"] = "UPDATED"
            outlet_updated += 1

            for _, cell in out_cells.iterrows():
                val = float(cell[BATH_FIELD])
                qa["outlet_mesh_cells"].append({
                    "pipe_idx": int(index), "bathy": val,
                    "type": cell["MeshType"], "lower": bool(val < z_in),
                    "is_max": bool(abs(val - z_out) < 1e-7),
                    "mode": out_mode, "geometry": cell.geometry})

            print("Pipe {}: Z_In={:.4f} ({}, {} cells); "
                  "Z_Out={:.4f} ({}, {} cells, max < Z_In)".format(
                      index, z_in, in_mode, len(in_cells),
                      z_out, out_mode, len(out_cells)))
        except (ValueError, TypeError) as ex:
            pipes.at[index, "Status"] = "ERROR"
            print("Pipe {}: {}".format(index, ex))
            continue

    Path(OUTPUT_SHP).parent.mkdir(parents=True, exist_ok=True)
    pipes.to_file(OUTPUT_SHP)
    print("Selected: {}; inlet updated: {}; outlet updated: {}; "
          "no outlet (Z_Outflow=0): {}".format(
              int(mask.sum()), inlet_updated, outlet_updated, no_outlet))
    print("Saved new shapefile: {}".format(OUTPUT_SHP))
    write_qa(qa, pipes.crs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="External culvert bathymetry engine")
    parser.add_argument("--config", required=True, help="UTF-8 JSON configuration")
    args = parser.parse_args()
    if sys.version_info[:2] < (3, 9):
        parser.error("Python 3.9+ required (not ArcMap Python 2.7)")
    try:
        with open(args.config, "r", encoding="utf-8") as stream:
            config = json.load(stream)
        configure(config)
        main()
    except Exception as exc:
        print("ERROR: {}: {}".format(type(exc).__name__, exc), flush=True)
        raise
