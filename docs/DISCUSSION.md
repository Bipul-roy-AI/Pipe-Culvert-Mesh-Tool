# Project Discussion & Feedback Guide

This project provides an ArcMap 10.8 Python toolbox to populate pipe/culvert invert elevations from an element mesh. This guide explains how to discuss the algorithm, share reproducible results, and propose improvements.

**Discussion thread:** [Community discussion — pipe–culvert mesh elevation workflow and validation](https://github.com/Bipul-roy-AI/Pipe-Culvert-Mesh-Tool/issues/1)

> The repository's GitHub **Discussions** feature is currently disabled. The linked issue acts as the initial public conversation thread. The owner can enable Discussions under **Settings → General → Features → Discussions**; after that, users can open native discussions in the Discussions tab.

## Questions worth discussing

### 1. Pipe orientation and topology

The toolbox treats the **first line vertex** as the start/inflow node and the **last line vertex** as the end/outflow node. Is this digitization convention always consistent in your data? How should reversed pipes be identified or handled?

### 2. Inflow selection

The inflow is the **lowest valid mesh value** among polygons intersecting the start node and eligible polygons **behind** that node within the specified search distance.

Example from the supplied usage notes:

- Snapped value: 320.18
- Behind-node candidate: 319.95
- Selected inflow: 319.95

Would logging the contributing polygon ID help with review and quality assurance?

### 3. Outflow priority and endpoint snapping

The end node has a specific priority rule:

1. Collect polygons intersecting the exact end node; when multiple values exist, use the lowest snapped value.
2. If the snapped value is **lower than inflow**, select it directly. **Do not replace it with a lower nearby value.**
3. Only if the snapped value is missing or not lower than inflow, inspect valid mesh elements **in front of** the node.
4. Select the **lowest forward value below inflow**, otherwise leave the pipe unchanged.

Is this endpoint priority appropriate for your specific culvert and drainage modelling workflow?

### 4. Directional candidate selection

The implementation projects the polygon's **labelPoint** relative to the pipe's start-to-end direction and uses the sign of the dot product to classify candidates as behind the start or in front of the end. This is a modelling choice, not an independently verified hydraulic property. Feedback on elongated, irregular, or boundary-adjacent polygons is valuable.

### 5. Reproducibility and numerical checks

The default search radius is **2.5 Meters** and the current target filter is **TopoID = 1900s0**. Field values are taken from the selected numeric bathymetry attribute. Please compare results at different mesh resolutions and with matching projected coordinate systems.

### 6. Feature proposals

Potential follow-ups include:

- A **dry-run / preview** mode that computes proposed changes without editing pipe attributes.
- An audit **CSV** with old/new elevations, reasons for skipped features, and contributing mesh element IDs.
- Configurable **TopoID** filtering and flow-direction checks.
- A small synthetic test dataset for regression and boundary-condition tests.

These are **suggestions**, not features implemented in the uploaded toolbox.

## How to report results

Share:

- ArcMap / ArcGIS Desktop version, layer types and coordinate system
- Names and units of the selected bathymetry, inflow and outflow fields
- Search distance and mesh resolution
- Whether the pipe was digitized in the intended flow direction
- Example start/end vertices, snapped polygons, nearby candidate values and observed before/after output
- Relevant ArcPy messages/errors and expected result

**Do not upload confidential drainage infrastructure, sensitive GIS data, or proprietary model datasets without permission.**

## Safety and maintenance

The tool uses an ArcPy UpdateCursor to edit pipe attributes **in place**. **Make a backup first** and validate outcomes on a small test selection. This source has been reviewed but **not executed end-to-end in ArcMap in this conversation**.

For implementation changes, open a focused Issue or Pull Request referencing the discussion thread. No open-source licence has been supplied; contributions and reuse should respect the repository owner's rights.
