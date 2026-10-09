# -*- coding: utf-8 -*-
# ArcMap 10.8 / Python 2.7 compatible Python Toolbox
#
# Tool: Populate Pipe Z From Mesh
#
# Workflow:
#   - Processes only pipes where TopoID == "1900s0"
#   - Start vertex = inflow node
#   - End vertex   = outflow node
#   - Inflow candidate mesh elements must be BEHIND the start node
#   - Outflow candidate mesh elements must be IN FRONT OF the end node
#   - Inflow = LOWEST of snapped start element + valid behind-start candidates
#   - Outflow priority: use snapped end value if it is < inflow; otherwise search front and take lowest valid value
#   - Therefore Z_Inflow > Z_Outflow is enforced before update
#   - Otherwise leave the existing pipe values unchanged
#
# No external .py file is required. Add this .pyt directly in ArcMap Catalog.

import arcpy
import math
import traceback


class Toolbox(object):
    def __init__(self):
        self.label = "Pipe Culvert Mesh Tools"
        self.alias = "pipe_culvert_mesh_tools"
        self.tools = [PopulatePipeZFromMesh]


class PopulatePipeZFromMesh(object):
    def __init__(self):
        self.label = "Populate Pipe Z From Mesh"
        self.description = (
            "Updates selected inflow/outflow fields for TopoID 1900s0 pipes "
            "using a user-selected bathymetry/elevation field from mesh element polygons."
        )
        self.canRunInBackground = False

    def getParameterInfo(self):
        p0 = arcpy.Parameter(
            displayName="Pipe / Culvert",
            name="pipe_culvert",
            datatype="GPFeatureLayer",
            parameterType="Required",
            direction="Input"
        )
        p0.filter.list = ["Polyline"]

        p1 = arcpy.Parameter(
            displayName="Element File",
            name="element_file",
            datatype="GPFeatureLayer",
            parameterType="Required",
            direction="Input"
        )
        p1.filter.list = ["Polygon"]

        p2 = arcpy.Parameter(
            displayName="Bathymetry Column",
            name="bathymetry_column",
            datatype="Field",
            parameterType="Required",
            direction="Input"
        )
        p2.parameterDependencies = [p1.name]
        p2.filter.list = ["Short", "Long", "Float", "Double"]

        p3 = arcpy.Parameter(
            displayName="Inflow Column",
            name="inflow_column",
            datatype="Field",
            parameterType="Required",
            direction="Input"
        )
        p3.parameterDependencies = [p0.name]
        p3.filter.list = ["Short", "Long", "Float", "Double"]

        p4 = arcpy.Parameter(
            displayName="Outflow Column",
            name="outflow_column",
            datatype="Field",
            parameterType="Required",
            direction="Input"
        )
        p4.parameterDependencies = [p0.name]
        p4.filter.list = ["Short", "Long", "Float", "Double"]

        p5 = arcpy.Parameter(
            displayName="Search Distance",
            name="search_distance",
            datatype="GPLinearUnit",
            parameterType="Optional",
            direction="Input"
        )
        p5.value = "2.5 Meters"

        return [p0, p1, p2, p3, p4, p5]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        return

    def updateMessages(self, parameters):
        try:
            pipes = parameters[0].valueAsText
            elements = parameters[1].valueAsText

            if pipes:
                pipe_fields = [f.name for f in arcpy.ListFields(pipes)]
                pipe_lower = dict((f.lower(), f) for f in pipe_fields)

                if "topoid" not in pipe_lower:
                    parameters[0].setErrorMessage(
                        "Pipe / Culvert must contain a TopoID field."
                    )

            if elements and parameters[2].valueAsText:
                elem_fields = [f.name for f in arcpy.ListFields(elements)]
                if parameters[2].valueAsText not in elem_fields:
                    parameters[2].setErrorMessage(
                        "Selected bathymetry field does not exist in Element File."
                    )
        except:
            pass

    def execute(self, parameters, messages):
        pipes_fc = parameters[0].valueAsText
        elements_fc = parameters[1].valueAsText
        bathy_field = parameters[2].valueAsText
        inflow_field = parameters[3].valueAsText
        outflow_field = parameters[4].valueAsText
        search_distance = parameters[5].valueAsText or "2.5 Meters"

        arcpy.env.overwriteOutput = True

        pipe_fields = [f.name for f in arcpy.ListFields(pipes_fc)]
        elem_fields = [f.name for f in arcpy.ListFields(elements_fc)]

        pipe_lookup = dict((f.lower(), f) for f in pipe_fields)

        if "topoid" not in pipe_lookup:
            raise arcpy.ExecuteError("Pipe / Culvert is missing TopoID.")

        if bathy_field not in elem_fields:
            raise arcpy.ExecuteError(
                "Selected bathymetry field does not exist: {0}".format(bathy_field)
            )

        if inflow_field not in pipe_fields:
            raise arcpy.ExecuteError(
                "Selected inflow field does not exist: {0}".format(inflow_field)
            )

        if outflow_field not in pipe_fields:
            raise arcpy.ExecuteError(
                "Selected outflow field does not exist: {0}".format(outflow_field)
            )

        topoid_field = pipe_lookup["topoid"]

        pipe_sr = arcpy.Describe(pipes_fc).spatialReference
        elem_sr = arcpy.Describe(elements_fc).spatialReference

        if not pipe_sr or pipe_sr.name == "Unknown":
            raise arcpy.ExecuteError(
                "Pipe / Culvert has an unknown spatial reference."
            )

        if not elem_sr or elem_sr.name == "Unknown":
            raise arcpy.ExecuteError(
                "Element File has an unknown spatial reference."
            )

        if pipe_sr.factoryCode != elem_sr.factoryCode:
            arcpy.AddWarning(
                "Pipe and element coordinate systems differ. "
                "Matching projected coordinate systems are strongly recommended."
            )

        elem_layer = "pipez_elements_lyr"
        if arcpy.Exists(elem_layer):
            arcpy.Delete_management(elem_layer)
        arcpy.MakeFeatureLayer_management(elements_fc, elem_layer)

        oid_field = arcpy.Describe(elements_fc).OIDFieldName

        def _safe_float(value):
            if value is None:
                return None
            try:
                return float(value)
            except:
                return None

        def _direction_components(geom):
            p0 = geom.firstPoint
            p1 = geom.lastPoint

            dx = p1.X - p0.X
            dy = p1.Y - p0.Y
            length = math.sqrt(dx * dx + dy * dy)

            if length == 0:
                return None

            return (dx / length, dy / length)

        def _candidate_side_dot(label_point, node_point, ux, uy):
            vx = label_point.X - node_point.X
            vy = label_point.Y - node_point.Y
            return (vx * ux) + (vy * uy)

        def _select_distance_candidates(node_geom):
            """
            Select mesh polygons within the user search distance of a node.
            """
            arcpy.SelectLayerByAttribute_management(
                elem_layer, "CLEAR_SELECTION"
            )
            arcpy.SelectLayerByLocation_management(
                elem_layer,
                "WITHIN_A_DISTANCE",
                node_geom,
                search_distance,
                "NEW_SELECTION"
            )

        def _select_snapped_candidates(node_geom):
            """
            Select mesh polygon(s) directly intersected by the node.
            This represents the search-distance = 0 / snapped element value.
            """
            arcpy.SelectLayerByAttribute_management(
                elem_layer, "CLEAR_SELECTION"
            )
            arcpy.SelectLayerByLocation_management(
                elem_layer,
                "INTERSECT",
                node_geom,
                "",
                "NEW_SELECTION"
            )

        def _get_snapped_values(node_geom):
            """
            Return all valid selected bathymetry values for polygon(s)
            directly intersecting the node.
            """
            _select_snapped_candidates(node_geom)

            vals = []
            with arcpy.da.SearchCursor(
                    elem_layer, [oid_field, bathy_field]) as cur:
                for oid, z in cur:
                    zf = _safe_float(z)
                    if zf is not None:
                        vals.append((oid, zf))

            return vals

        def _get_directional_values(node_geom, node_point, ux, uy, side):
            """
            Return valid bathymetry candidates within search distance,
            restricted by digitized pipe direction.

            side = "behind" -> dot < 0
            side = "front"  -> dot > 0
            """
            _select_distance_candidates(node_geom)

            vals = []
            fields = [oid_field, "SHAPE@", bathy_field]

            with arcpy.da.SearchCursor(elem_layer, fields) as cur:
                for oid, poly, z in cur:
                    zf = _safe_float(z)
                    if zf is None or poly is None:
                        continue

                    lp = poly.labelPoint
                    dot = _candidate_side_dot(
                        lp, node_point, ux, uy
                    )

                    if side == "behind" and dot < 0:
                        vals.append((oid, zf))
                    elif side == "front" and dot > 0:
                        vals.append((oid, zf))

            return vals

        def _get_inflow_value(start_geom, ux, uy):
            """
            Inflow rule:
              1. Read the element directly snapped/intersected by the start node
                 (equivalent to search distance = 0).
              2. Read valid elements BEHIND the start node within search distance.
              3. Use the LOWEST bathymetry value from both groups.

            Example:
              snapped start = 320.18
              lower behind candidate within 2.5 m = 319.95
              -> Z_Inflow = 319.95
            """
            snapped = _get_snapped_values(start_geom)
            behind = _get_directional_values(
                start_geom,
                start_geom.firstPoint,
                ux,
                uy,
                "behind"
            )

            all_candidates = snapped + behind
            if not all_candidates:
                return None

            return min([z for oid, z in all_candidates])

        def _get_outflow_value(end_geom, ux, uy, chosen_inflow):
            """
            Outflow priority rule:

              STEP 1 - snapped endpoint value
                Read polygon(s) directly intersected by the actual end node.
                If the lowest snapped value is STRICTLY LOWER than the already
                selected inflow, use that snapped value immediately.

              STEP 2 - search-distance fallback
                Only when the snapped end value is not acceptable
                (snapped Z >= chosen inflow), search IN FRONT OF the end node
                within the user search distance.

                From those forward candidates, keep values STRICTLY LOWER
                than chosen inflow and take the LOWEST one.

              If no acceptable value exists, return None and leave the pipe
              unchanged.
            """
            snapped = _get_snapped_values(end_geom)

            if snapped:
                snapped_min = min([z for oid, z in snapped])

                # Priority: if the true endpoint value already satisfies
                # gravity, use it without replacing it with another nearby
                # lower polygon.
                if snapped_min < chosen_inflow:
                    return snapped_min

            # Snapped endpoint is missing, equal to inflow, or above inflow:
            # now use the directional search-distance fallback.
            front = _get_directional_values(
                end_geom,
                end_geom.firstPoint,
                ux,
                uy,
                "front"
            )

            if not front:
                return None

            lower_than_inflow = [
                z for oid, z in front
                if z < chosen_inflow
            ]

            if not lower_than_inflow:
                return None

            return min(lower_than_inflow)

        total = int(arcpy.GetCount_management(pipes_fc)[0])

        arcpy.AddMessage("----------------------------------------")
        arcpy.AddMessage("Pipe Z population started")
        arcpy.AddMessage("Input pipes: {0}".format(total))
        arcpy.AddMessage("Target TopoID: 1900s0")
        arcpy.AddMessage("Selected bathymetry field: {0}".format(bathy_field))
        arcpy.AddMessage("Search distance: {0}".format(search_distance))
        arcpy.AddMessage("----------------------------------------")

        target_count = 0
        updated_count = 0
        gravity_fail_count = 0
        no_candidate_count = 0
        zero_length_count = 0
        skipped_count = 0

        cursor_fields = [
            "OID@",
            "SHAPE@",
            topoid_field,
            inflow_field,
            outflow_field
        ]

        try:
            with arcpy.da.UpdateCursor(pipes_fc, cursor_fields) as cur:
                for row in cur:
                    oid = row[0]
                    geom = row[1]
                    topo_value = row[2]

                    topo_text = ""
                    if topo_value is not None:
                        topo_text = str(topo_value).strip()

                    if topo_text.lower() != "1900s0":
                        skipped_count += 1
                        continue

                    target_count += 1

                    # ArcMap 10.8 / ArcPy Python 2.7 geometry objects do not
                    # provide the Pro-style `isEmpty` property.  Validate the
                    # geometry using pointCount and endpoint availability.
                    if (geom is None or
                            geom.pointCount < 2 or
                            geom.firstPoint is None or
                            geom.lastPoint is None):
                        no_candidate_count += 1
                        arcpy.AddWarning(
                            "OID {0}: empty/invalid pipe geometry; unchanged."
                            .format(oid)
                        )
                        continue

                    direction = _direction_components(geom)
                    if direction is None:
                        zero_length_count += 1
                        arcpy.AddWarning(
                            "OID {0}: zero-length pipe; unchanged.".format(oid)
                        )
                        continue

                    ux, uy = direction

                    start_geom = arcpy.PointGeometry(
                        geom.firstPoint, geom.spatialReference
                    )
                    end_geom = arcpy.PointGeometry(
                        geom.lastPoint, geom.spatialReference
                    )

                    z_in = _get_inflow_value(start_geom, ux, uy)
                    z_out = _get_outflow_value(end_geom, ux, uy, z_in)

                    if z_in is None or z_out is None:
                        no_candidate_count += 1
                        arcpy.AddWarning(
                            "OID {0}: no acceptable snapped/directional candidate found "
                            "for one or both nodes; unchanged.".format(oid)
                        )
                        continue

                    if not (z_in > z_out):
                        gravity_fail_count += 1
                        arcpy.AddWarning(
                            "OID {0}: rejected because {1} <= {2}; unchanged."
                            .format(oid, z_in, z_out)
                        )
                        continue

                    row[3] = z_in
                    row[4] = z_out
                    cur.updateRow(row)
                    updated_count += 1

                    arcpy.AddMessage(
                        "OID {0}: {1}={2}, {3}={4}"
                        .format(
                            oid,
                            inflow_field,
                            z_in,
                            outflow_field,
                            z_out
                        )
                    )

        except Exception:
            arcpy.AddError(traceback.format_exc())
            raise
        finally:
            try:
                arcpy.SelectLayerByAttribute_management(
                    elem_layer, "CLEAR_SELECTION"
                )
            except:
                pass
            try:
                arcpy.Delete_management(elem_layer)
            except:
                pass

        arcpy.AddMessage("========================================")
        arcpy.AddMessage("Completed")
        arcpy.AddMessage("TopoID 1900s0 pipes: {0}".format(target_count))
        arcpy.AddMessage("Updated: {0}".format(updated_count))
        arcpy.AddMessage(
            "No directional candidate: {0}".format(no_candidate_count)
        )
        arcpy.AddMessage(
            "Failed Z_Inflow > Z_Outflow: {0}".format(gravity_fail_count)
        )
        arcpy.AddMessage(
            "Zero-length geometry: {0}".format(zero_length_count)
        )
        arcpy.AddMessage(
            "Other TopoID skipped: {0}".format(skipped_count)
        )
        arcpy.AddMessage("========================================")
