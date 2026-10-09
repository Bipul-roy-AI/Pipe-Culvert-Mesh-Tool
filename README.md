# Pipe-Culvert-Mesh-Tool

**A self-contained ArcMap 10.8 Python toolbox for assigning pipe/culvert inflow and outflow elevations from bathymetric mesh polygons with endpoint-first outflow logic and directional proximity search.**

![ArcMap 10.8](https://img.shields.io/badge/ArcMap-10.8-3276A8)
![Python 2.7](https://img.shields.io/badge/Python-2.7-3776AB)
![Version 5](https://img.shields.io/badge/Toolbox-v5-2E7D73)

> **Important:** The tool edits existing elevation attributes **in place**. Back up your pipe dataset and test on a copy before running it on production data. The current implementation is intended for **ArcGIS Desktop / ArcMap 10.8**, not ArcGIS Pro.

## Overview

**Pipe-Culvert-Mesh-Tool** contains the **Populate Pipe Z From Mesh** tool. It uses a polyline **Pipe / Culvert** layer and a polygon **Element File** containing bathymetry/elevation values to assign logically consistent invert elevations along the digitized pipe direction.

The tool processes **only features with `TopoID = 1900s0`**. It treats the **first pipe vertex** as the inflow/start node and the **last vertex** as the outflow/end node. The search radius defaults to **2.5 Meters**. All required analysis logic is contained in the `.pyt` file; no external companion Python script is necessary.

### Key features

- Uses mesh polygons that **intersect a pipe endpoint**, including boundary-contact cases.
- Searches eligible mesh elements **behind the start node** and, when needed, **in front of the end node**.
- Implements **snapped-endpoint priority**: a valid end-node elevation is preserved even if a lower value exists nearby.
- Enforces **inflow > outflow** before updating pipe elevation attributes.
- Leaves a pipe **unchanged** if no valid candidate pair satisfies the rule.
- Preserves the original **TopoID** values and reports updated/skipped feature counts in ArcMap messages.
- Uses a **user-selected numeric bathymetry field** and user-selected inflow/outflow output fields.

## Algorithm: how the elevations are chosen

### 1. Start node / inflow

1. Read the bathymetry of mesh polygon(s) intersecting the exact **start node**.
2. Search within the specified distance **behind** the start node.
3. Choose the **lowest** valid elevation among those snapped and behind-start candidates.

**Example from the supplied usage notes:**

| Candidate | Elevation |
| :-- | --: |
| Mesh snapped to start node | 320.18 |
| Eligible mesh behind the start node | 319.95 |
| **Chosen inflow** | **319.95** |

### 2. End node / outflow — priority rule

1. Read the value of the mesh polygon directly under/intersecting the **actual end node**. If the node touches several polygons, choose the **lowest snapped** value.
2. **If that value is below the chosen inflow**, use it directly as outflow, **even if a lower value is present nearby**.
3. Only if no snapped value exists, or the snapped value is **not below inflow**, search **in front of** the end node within the search distance.
4. Filter forward candidates to values **strictly less than inflow**, then choose the lowest such value.
5. If there is no valid candidate, **leave the pipe unchanged**.

| Condition | Action |
| :-- | :-- |
| Snapped end elevation exists and is below inflow | **Use snapped end elevation** |
| Snapped end missing or ≥ inflow | Search in front of end node |
| Forward candidates below inflow exist | Use the **lowest** valid forward candidate |
| No value below inflow | **No attribute update** |

The source classifies forward/behind mesh candidates using each polygon's **labelPoint** and the sign of the dot product against the start-to-end pipe direction. This is a geometric classification convention, and results should be checked for irregular mesh geometries.

### 3. Validation and update

Pipe attributes are updated **only if `chosen_inflow > chosen_outflow`**. Invalid pipe geometries, zero-length pipes, and insufficient candidate values are skipped with messages. The tool does not modify the `TopoID` field.

## Requirements

- **ArcMap 10.8** with its **ArcPy / Python 2.7** runtime.
- **Pipe / Culvert**: polyline layer with a `TopoID` field and numeric target inflow/outflow attributes.
- **Element File**: polygon mesh layer containing the selected numeric bathymetry/elevation attribute.
- **Known spatial references** for both datasets; matching **projected** coordinate systems are strongly recommended.
- A **backup** of the pipe data, because updates are written to existing fields.

## Toolbox parameters

| # | Parameter | Data / meaning |
| --: | :-- | :-- |
| 1 | **Pipe / Culvert** | Polyline feature layer containing `TopoID` |
| 2 | **Element File** | Polygon mesh feature layer |
| 3 | **Bathymetry Column** | Numeric elevation field from Element File |
| 4 | **Inflow Column** | Numeric field in Pipe / Culvert to be updated |
| 5 | **Outflow Column** | Numeric field in Pipe / Culvert to be updated |
| 6 | **Search Distance** | Optional radius; default **2.5 Meters** |

### Installation

1. Clone this repository, or select **Code → Download ZIP** and extract the archive.
2. Open **ArcMap 10.8** and navigate to **Catalog → Toolboxes**.
3. Choose **Add Toolbox** and select `Pipe_Culvert_Mesh_Tools.pyt`.
4. Open **Pipe Culvert Mesh Tools → Populate Pipe Z From Mesh**.
5. Choose the pipe polyline layer, mesh polygon layer, input bathymetry field, inflow field, outflow field, and search distance.
6. Run the tool on a **backup or small test subset** and inspect ArcMap messages and the resulting attributes.

The Python toolbox is **self-contained**; the XML files are ArcGIS metadata companions rather than executable dependencies.

## Recommended checks before and after running

- Verify that digitized pipe orientation matches **first vertex = inflow**, **last vertex = outflow**.
- Confirm that elevation values use compatible vertical units and reference datums.
- Use layers with known coordinate systems; investigate any ArcPy spatial reference warnings.
- Verify mesh polygon coverage at pipe endpoints, especially at polygon boundaries.
- Inspect all features that were skipped or updated and confirm that **inflow > outflow**.
- Preserve a backup of original attributes to make changes reversible.

## Repository contents

```text
Pipe-Culvert-Mesh-Tool/
├── Pipe_Culvert_Mesh_Tools.pyt
├── Pipe_Culvert_Mesh_Tools.pyt.xml
├── Pipe_Culvert_Mesh_Tools.PopulatePipeZFromMesh.pyt.xml
├── README.md
├── CONTRIBUTING.md
├── docs/
│   └── DISCUSSION.md
└── .gitignore
```

## Discussions and feedback

Questions about endpoint snapping, the inflow/outflow selection logic, mesh resolution, data validation and suggested improvements are welcome.

- **[Join the community technical discussion (Issue #1)](https://github.com/Bipul-roy-AI/Pipe-Culvert-Mesh-Tool/issues/1)**
- **[Read the discussion and validation guide](docs/DISCUSSION.md)**
- **[Contributing guidelines](CONTRIBUTING.md)**

GitHub's native **Discussions** feature is not enabled on this repository as of this documentation update. The linked Issue provides a temporary place for public questions and feedback.

## Limitations and project status

- **ArcMap 10.8 / Python 2.7-specific:** not tested in ArcGIS Pro.
- The **TopoID filter** is currently fixed to `1900s0`.
- The search direction is based on **digitized pipe orientation**, not independently inferred hydraulic flow.
- Candidate classification uses mesh polygon `labelPoint`; review cases with unusual polygon shapes.
- The code directly **updates existing attributes**; a built-in preview/export or undo log is **not included**.
- The uploaded source has **not been run end-to-end with real ArcMap datasets in this conversation**; independently validate it in your project environment.
- **No open-source licence was provided** with the original files; do not assume rights to reuse or redistribute them beyond applicable law.

---

*Developed for structured pipe–culvert elevation attribution and hydraulic modelling data preparation.*
