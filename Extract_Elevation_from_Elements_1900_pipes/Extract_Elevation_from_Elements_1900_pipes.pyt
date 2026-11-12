# -*- coding: utf-8 -*-
"""ArcMap 10.8 / ArcGIS Pro toolbox: in-place culvert Z with dated backup.

ArcMap Python 2.7 launches a separate Python 3 engine for the mesh analysis.
Only Z_Inflow/Z_Outflow are updated on the ORIGINAL pipe shapefile.
"""
import arcpy
import os
import sys
import json
import codecs
import shutil
import tempfile
import subprocess
import traceback
import datetime


class Toolbox(object):
    def __init__(self):
        self.label = "Extract Elevation from Elements (1900 pipes)"
        self.alias = "extract1900"
        self.tools = [ExtractElevation]


class ExtractElevation(object):
    def __init__(self):
        self.label = "Extract Elevation from Elements (1900 pipes)"
        self.description = (
            "Update Z_Inflow and Z_Outflow directly in the chosen pipe shapefile, "
            "after creating a dated backup in the same folder. Only TopoID = "
            "1900s0 is processed; ArcMap selected features are respected. "
            "Mesh elevation fields can differ between mesh layers. "
            "Uses an automatically installed separate Python 3 environment."
        )
        self.canRunInBackground = False

    # Up to ten individually linked mesh/elevation-field pairs.
    # The linked field dropdown works in ArcMap 10.8 and ArcGIS Pro.
    MAX_MESH_LAYERS = 10

    @staticmethod
    def _pair_indices():
        return [(1 + 2 * n, 2 + 2 * n) for n in range(10)]

    def getParameterInfo(self):
        pipe = arcpy.Parameter(
            displayName="Pipes / culverts (selected pipes are respected)",
            name="pipes", datatype="GPFeatureLayer",
            parameterType="Required", direction="Input")
        pipe.filter.list = ["Polyline"]

        # Every mesh gets its OWN linked field control. ArcMap's GPValueTable
        # does not consistently populate a different field dropdown for each
        # row, so separate, explicitly dependent fields are more reliable.
        mesh_parameters = []
        for i in range(self.MAX_MESH_LAYERS):
            layer = arcpy.Parameter(
                displayName=("Mesh 1 - element polygons" if i == 0 else
                             "Mesh {} - element polygons (optional)".format(i + 1)),
                name="mesh_{:02d}".format(i + 1),
                datatype="GPFeatureLayer",
                parameterType="Required" if i == 0 else "Optional",
                direction="Input")
            layer.filter.list = ["Polygon"]
            field = arcpy.Parameter(
                displayName="Mesh {} - bathymetry / elevation field".format(i + 1),
                name="mesh_field_{:02d}".format(i + 1),
                datatype="Field",
                parameterType="Required" if i == 0 else "Optional",
                direction="Input")
            field.parameterDependencies = [layer.name]
            field.filter.list = ["Short", "Long", "Float", "Double"]
            if i > 0:
                layer.category = "Additional mesh layers (optional)"
                field.category = "Additional mesh layers (optional)"
            mesh_parameters.extend([layer, field])

        distance = arcpy.Parameter(
            displayName="Search distance along trench (m)",
            name="search_distance", datatype="GPDouble",
            parameterType="Required", direction="Input")
        distance.value = 15.0

        width = arcpy.Parameter(
            displayName="Trench search width (m)",
            name="search_width", datatype="GPDouble",
            parameterType="Required", direction="Input")
        width.value = 3.0

        fallback = arcpy.Parameter(
            displayName="Nearby search radius if trench not found (m)",
            name="nearby_radius", datatype="GPDouble",
            parameterType="Required", direction="Input")
        fallback.value = 5.0

        python = arcpy.Parameter(
            displayName="Calculation setup (automatic)",
            name="python_location", datatype="DEFile",
            parameterType="Optional", direction="Input")
        python.category = "Advanced settings (normally leave blank)"
        # External calculation environment belongs on each user's LOCAL disk;
        # the toolbox itself may live on a network mapped drive/UNC share.
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        default = (os.path.join(local_app_data, "SPEKTER",
                                "ExtractElevation1900", "venv", "Scripts", "python.exe")
                   if local_app_data else "")
        if default and os.path.isfile(default):
            python.value = default
        else:
            legacy = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  ".venv", "Scripts", "python.exe")
            if os.path.isfile(legacy):
                python.value = legacy

        qa = arcpy.Parameter(
            displayName="Save map layers to check the elevations",
            name="quality_layers", datatype="GPBoolean",
            parameterType="Required", direction="Input")
        qa.value = True

        output = arcpy.Parameter(
            displayName="Updated pipe layer", name="updated_pipes",
            datatype="DEFeatureClass", parameterType="Derived",
            direction="Output")
        output.parameterDependencies = [pipe.name]
        return [pipe] + mesh_parameters + [distance, width, fallback, python, qa, output]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        """Automatically suggest Bathymetry separately for each chosen layer."""
        preferred_names = ("Bathymetry", "Bathy", "Elevation", "BedLevel",
                           "Bed_Level", "Z")
        numeric_types = ("Double", "Single", "Float", "Integer", "SmallInteger")
        for layer_idx, field_idx in self._pair_indices():
            mesh = parameters[layer_idx].valueAsText
            field_param = parameters[field_idx]
            if not mesh:
                if field_param.valueAsText:
                    field_param.value = None
                continue
            try:
                names = [f.name for f in arcpy.ListFields(mesh)
                         if f.type in numeric_types]
                selected = field_param.valueAsText
                if selected and selected.lower() not in [n.lower() for n in names]:
                    field_param.value = None
                    selected = None
                if not selected and not field_param.altered:
                    for preferred in preferred_names:
                        candidates = [n for n in names if n.lower() == preferred.lower()]
                        if candidates:
                            field_param.value = candidates[0]
                            break
            except Exception:
                # Native ArcMap/Pro Field dependency still populates choices.
                pass

    def updateMessages(self, parameters):
        for idx in (21, 22, 23):
            raw = parameters[idx].valueAsText
            if raw:
                try:
                    if float(raw) <= 0.0:
                        parameters[idx].setErrorMessage("Enter a number greater than zero.")
                except Exception:
                    parameters[idx].setErrorMessage("Enter a valid number.")
        numeric_types = ("Double", "Single", "Float", "Integer", "SmallInteger")
        seen = set()
        for mesh_idx, field_idx in self._pair_indices():
            mesh = parameters[mesh_idx].valueAsText
            field_name = parameters[field_idx].valueAsText
            if field_name and not mesh:
                parameters[field_idx].setErrorMessage("Choose the corresponding mesh layer first.")
            if mesh and not field_name:
                parameters[field_idx].setErrorMessage("Choose an elevation field for this mesh layer.")
                continue
            if mesh and field_name:
                try:
                    desc = arcpy.Describe(mesh)
                    path = os.path.normcase(os.path.normpath(desc.catalogPath))
                    if path in seen:
                        parameters[mesh_idx].setErrorMessage(
                            "This mesh layer has already been added. Choose it once only.")
                    seen.add(path)
                    match = [f for f in arcpy.ListFields(mesh)
                             if f.name.lower() == field_name.lower()]
                    if not match or match[0].type not in numeric_types:
                        parameters[field_idx].setErrorMessage(
                            "Choose a numeric bathymetry/elevation field from this mesh.")
                except Exception:
                    pass
        pipe = parameters[0].valueAsText
        if pipe:
            try:
                source = arcpy.Describe(pipe).catalogPath
                if not source.lower().endswith(".shp"):
                    parameters[0].setErrorMessage(
                        "For safe in-place editing, choose a pipe shapefile (.shp).")
            except Exception:
                pass
        return

    @staticmethod
    def _selected_oids(layer):
        """Respect an active TOC selection; no selection means full shapefile."""
        desc = arcpy.Describe(layer)
        fidset = getattr(desc, "FIDSet", None)
        if isinstance(fidset, (tuple, list, set)):
            selected = bool(fidset)
        else:
            selected = bool(str(fidset).strip(" ;")) if fidset is not None else False
        if not selected:
            return None
        # Source OIDs, not OIDs renumbered when CopyFeatures is called.
        return [int(row[0]) for row in arcpy.da.SearchCursor(layer, ["OID@"]) ]

    def _mesh_rows(self, parameters):
        """Return selected mesh layers with their own field dropdown choices."""
        rows = []
        for layer_idx, field_idx in self._pair_indices():
            layer = parameters[layer_idx].valueAsText
            field = parameters[field_idx].valueAsText
            if layer:
                if not field:
                    raise ValueError("Choose an elevation field for mesh layer {}".format(
                        (layer_idx + 1) // 2))
                rows.append((layer, field))
            elif field:
                raise ValueError("Choose a polygon layer for field {}".format(field))
        if not rows:
            raise ValueError("Choose at least one mesh polygon layer")
        seen = set()
        for layer, field in rows:
            path = os.path.normcase(os.path.normpath(arcpy.Describe(layer).catalogPath))
            if path in seen:
                raise ValueError("Duplicate mesh layer: {}".format(layer))
            seen.add(path)
        return rows

    @staticmethod
    def _backup_files(source):
        """Back up real shapefile components, never ArcMap lock/temp files.

        ArcMap creates files such as Pipe.shp.<machine>.<pid>.sr.lock while
        the layer is open. Older versions of this toolbox accidentally copied
        every file beginning with 'Pipe.', including these locks; Windows
        rightly denies reading some lock files. Only the known shapefile
        components are eligible for copying.
        """
        parent = os.path.dirname(source)
        stem = os.path.splitext(os.path.basename(source))[0]
        date = datetime.datetime.now().strftime("%Y-%m-%d")
        required = {".shp", ".shx", ".dbf"}
        metadata = {".prj", ".cpg", ".xml", ".shp.xml", ".qpj"}
        # Index files are optional and ArcGIS can recreate them.
        optional_indices = {".sbn", ".sbx", ".qix", ".aih", ".ain",
                            ".ixs", ".mxs", ".idm", ".ind", ".atx"}

        sidecars = []
        for name in os.listdir(parent):
            if not name.lower().startswith((stem + ".").lower()):
                continue
            suffix = name[len(stem):].lower()
            # Covers Pipe.Field.atx (per-field attribute indexes), too.
            is_index = (suffix in optional_indices or suffix.endswith(".atx"))
            if suffix not in required and suffix not in metadata and not is_index:
                continue
            if os.path.isfile(os.path.join(parent, name)):
                sidecars.append((name, suffix, is_index))
        available = {suffix for name, suffix, is_index in sidecars}
        if not required.issubset(available):
            raise RuntimeError("Input shapefile is missing required components: {}".format(
                ", ".join(sorted(required - available))))

        counter = 1
        while True:
            suffix = "_" + date if counter == 1 else "_" + date + "_" + str(counter)
            backed = stem + suffix
            if not any(os.path.exists(os.path.join(parent, backed + name[len(stem):]))
                       for name, _, _ in sidecars):
                break
            counter += 1

        copied = []
        skipped_indices = []
        # Essential components first: fail and clean up if any cannot be read.
        sidecars.sort(key=lambda entry: (entry[2], entry[0].lower()))
        try:
            for name, suffix, is_index in sidecars:
                destination = os.path.join(parent, backed + name[len(stem):])
                try:
                    shutil.copy2(os.path.join(parent, name), destination)
                    copied.append(destination)
                except (IOError, OSError) as exc:
                    if not is_index:
                        raise
                    # An ArcMap/ArcGIS index may be locked; it can be rebuilt.
                    skipped_indices.append("{} ({})".format(name, exc))
                    if os.path.exists(destination):
                        try:
                            os.remove(destination)
                        except Exception:
                            pass
        except Exception:
            for filename in copied:
                try:
                    os.remove(filename)
                except Exception:
                    pass
            raise
        if skipped_indices:
            arcpy.AddWarning(("Optional shapefile index files were not backed up: {}. "
                              "The core shapefile backup is intact.")
                             .format("; ".join(skipped_indices)))
        return os.path.join(parent, backed + ".shp")

    @staticmethod
    def _stamp_source_oids(source, copy):
        """Add temporary source OIDs; refuse to guess if CopyFeatures reordered."""
        existing = {f.name.upper() for f in arcpy.ListFields(copy)}
        id_field = "CM_SRC_ID"
        if id_field.upper() in existing:
            for n in range(1, 100):
                proposal = "CMRID{}".format(n)
                if proposal.upper() not in existing:
                    id_field = proposal
                    break
            else:
                raise ValueError("Cannot create temporary pipe identifier")
        arcpy.AddField_management(copy, id_field, "LONG")
        count = 0
        with arcpy.da.SearchCursor(source, ["OID@", "SHAPE@WKB"]) as original:
            with arcpy.da.UpdateCursor(copy, [id_field, "SHAPE@WKB"]) as temporary:
                for source_row in original:
                    temp_row = next(temporary, None)
                    if temp_row is None:
                        raise RuntimeError("Pipe copy contains fewer features than source")
                    if bytes(source_row[1]) != bytes(temp_row[1]):
                        raise RuntimeError(
                            "Pipe order/geometry changed during temporary export. "
                            "Original file was not edited.")
                    temp_row[0] = int(source_row[0])
                    temporary.updateRow(temp_row)
                    count += 1
                if next(temporary, None) is not None:
                    raise RuntimeError("Pipe copy contains extra features")
        return id_field, count

    @staticmethod
    def _validate_fields(source, meshtables):
        pipe_fields = {f.name.lower(): f for f in arcpy.ListFields(source)}
        if "topoid" not in pipe_fields:
            raise ValueError("Pipe file has no TopoID field")
        for name in ("Z_Inflow", "Z_Outflow"):
            old = pipe_fields.get(name.lower())
            if old and old.type not in ("Double", "Single", "Float"):
                raise ValueError("Pipe field {} must be a floating-point field (Double/Float), "
                                 "not integer/text; otherwise decimal elevations may be lost.".format(name))
        for path, field in meshtables:
            fields = {f.name.lower(): f for f in arcpy.ListFields(path)}
            f = fields.get(field.lower())
            if f is None:
                raise ValueError("Field '{}' not found in mesh '{}'".format(field, path))
            if f.type not in ("Double", "Single", "Float", "Integer", "SmallInteger"):
                raise ValueError("Mesh elevation field '{}' must be numeric".format(field))
            if arcpy.Describe(path).shapeType.lower() != "polygon":
                raise ValueError("Mesh layer must contain polygons: {}".format(path))
        return pipe_fields

    def execute(self, parameters, messages):
        temporary_dir = None
        backup = None
        try:
            toolbox_folder = os.path.dirname(os.path.abspath(__file__))
            worker = os.path.join(toolbox_folder, "culvert_mesh_worker.py")
            if not os.path.isfile(worker):
                raise RuntimeError("Missing culvert_mesh_worker.py beside the toolbox")
            # Prefer a per-user local environment to avoid Windows venv/ensurepip
            # errors when E: maps to a UNC network share.
            local_app_data = os.environ.get("LOCALAPPDATA", "")
            local_python = (os.path.join(local_app_data, "SPEKTER",
                                         "ExtractElevation1900", "venv", "Scripts", "python.exe")
                            if local_app_data else "")
            legacy_python = os.path.join(toolbox_folder, ".venv", "Scripts", "python.exe")
            python_exe = parameters[24].valueAsText
            if not python_exe:
                if local_python and os.path.isfile(local_python):
                    python_exe = local_python
                else:
                    python_exe = legacy_python
            if not os.path.isfile(python_exe):
                raise RuntimeError(
                    "Python 3 calculation environment was not found. "
                    "Run 'setup_environment.bat' from the toolbox folder to "
                    "install it in your LOCAL Windows user folder. "
                    "ArcMap's Python 2.7 is not suitable.")
            arcpy.AddMessage("Python environment for calculations: {}".format(python_exe))
            pipe_layer = parameters[0].valueAsText
            source = arcpy.Describe(pipe_layer).catalogPath
            if not source.lower().endswith(".shp") or not os.path.isfile(source):
                raise ValueError("The pipe input must be an existing .shp shapefile")
            mesh_rows = self._mesh_rows(parameters)
            self._validate_fields(source, mesh_rows)
            chosen = self._selected_oids(pipe_layer)
            if chosen is not None:
                arcpy.AddMessage("Pipe selection detected: {} selected features; only "
                                 "TopoID=1900s0 among these will be processed.".format(len(chosen)))
            else:
                arcpy.AddMessage("No pipe selection: processing all TopoID=1900s0 pipes.")

            # Back up immediately after checking inputs. In particular, do
            # not spend several minutes exporting mesh layers before finding
            # out that the source folder cannot be backed up.
            backup = self._backup_files(source)
            arcpy.AddMessage("BACKUP CREATED: {}".format(backup))

            # Nothing is ever modified until external computation succeeds.
            temporary_dir = tempfile.mkdtemp(prefix="extract1900_")
            pipes_all = os.path.join(temporary_dir, "pipe_input.shp")
            arcpy.CopyFeatures_management(source, pipes_all)
            source_id, n = self._stamp_source_oids(source, pipes_all)
            arcpy.AddMessage("Prepared {} source pipes safely.".format(n))
            mesh_copies = []
            mesh_fields = []
            direct_count = 0
            for i, (layer, bath_name) in enumerate(mesh_rows):
                catalog = arcpy.Describe(layer).catalogPath
                if catalog.lower().endswith(".shp") and os.path.isfile(catalog):
                    # Fast path: use original mesh shapefile in GeoPandas.
                    # No full mesh export. Worker applies endpoint search-area
                    # spatial filtering before loading elements into memory.
                    mesh_copies.append(catalog)
                    mesh_fields.append(bath_name)
                    direct_count += 1
                    arcpy.AddMessage("Mesh {}: direct spatial read, elevation field '{}'".format(
                        i + 1, bath_name))
                else:
                    # Compatible fallback for feature classes/geodatabases.
                    # Keep field mapping so long field names are handled safely.
                    mesh_copy = os.path.join(temporary_dir,
                                             "mesh_{:03d}.shp".format(i + 1))
                    f_map = arcpy.FieldMap()
                    f_map.addInputField(catalog, bath_name)
                    out_field = f_map.outputField
                    out_field.name = "CM_BATH"
                    out_field.aliasName = "CM_BATH"
                    f_map.outputField = out_field
                    f_maps = arcpy.FieldMappings()
                    f_maps.addFieldMap(f_map)
                    arcpy.FeatureClassToFeatureClass_conversion(
                        catalog, temporary_dir, os.path.basename(mesh_copy),
                        field_mapping=f_maps)
                    mesh_copies.append(mesh_copy)
                    mesh_fields.append("CM_BATH")
                    arcpy.AddMessage("Mesh {}: exported feature class, elevation field '{}'".format(
                        i + 1, bath_name))
            arcpy.AddMessage("Mesh preparation: {} / {} layers loaded directly without "
                             "full ArcMap export.".format(direct_count, len(mesh_rows)))

            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            stem = os.path.splitext(os.path.basename(source))[0]
            qa_base = os.path.join(os.path.dirname(source), stem + "_ElevationQA_" + stamp)
            qa_gpkg = qa_base + ".gpkg"
            if os.path.exists(qa_gpkg) or os.path.isdir(qa_base + "_QA_layers"):
                qa_base += "_" + str(os.getpid())
                qa_gpkg = qa_base + ".gpkg"
            output_shp = os.path.join(temporary_dir, "calculated.shp")
            cfg = {
                "pipes": pipes_all,
                "selected_pipes": None,
                "selected_oids": chosen,
                "source_id_field": source_id,
                "elements": mesh_copies,
                "bath_fields": mesh_fields,
                "output_shp": output_shp,
                "qa_gpkg": qa_gpkg,
                "topoid_field": "TopoID",
                "target_topoid": "1900s0",
                "search_length_m": float(parameters[21].valueAsText),
                "search_width_m": float(parameters[22].valueAsText),
                "fallback_radius_m": float(parameters[23].valueAsText),
                "write_qa": parameters[25].valueAsText.lower() in ("true", "1")
            }
            config_file = os.path.join(temporary_dir, "settings.json")
            with codecs.open(config_file, "w", encoding="utf-8") as stream:
                stream.write(json.dumps(cfg, indent=2, ensure_ascii=False))
            arcpy.AddMessage("Calculating elevations (separate Python 3)...")
            command = [python_exe, "-u", worker, "--config", config_file]
            environment = os.environ.copy()
            environment["PYTHONIOENCODING"] = "utf-8"
            startupinfo = None
            if os.name == "nt" and hasattr(subprocess, "STARTUPINFO"):
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            proc = subprocess.Popen(command, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT,
                                    cwd=toolbox_folder, env=environment,
                                    startupinfo=startupinfo)
            for raw in iter(proc.stdout.readline, b""):
                msg = raw.decode("utf-8", "replace").rstrip()
                if msg:
                    arcpy.AddMessage(msg)
            if proc.wait() != 0:
                raise RuntimeError("Elevation calculation failed. Original file is unchanged; "
                                   "see details above.")
            if not os.path.isfile(output_shp):
                raise RuntimeError("Calculation finished without producing results")
            # Compute in a temporary file; only now touch the original's DBF.
            import math
            results = {}
            with arcpy.da.SearchCursor(output_shp,
                        [source_id, "Z_Inflow", "Z_Outflow", "Status"]) as records:
                for oid, zin, zout, status in records:
                    if status not in ("UPDATED", "IN_ONLY"):
                        continue
                    if zin is None or (math.isnan(float(zin)) or math.isinf(float(zin))):
                        raise RuntimeError("Invalid Z_Inflow for original FID {}".format(oid))
                    if zout is None or (math.isnan(float(zout)) or math.isinf(float(zout))):
                        raise RuntimeError("Invalid Z_Outflow for original FID {}".format(oid))
                    if status == "IN_ONLY" and float(zout) != 0.0:
                        raise RuntimeError("Missing-outlet result must be zero")
                    if status == "UPDATED" and not float(zout) < float(zin):
                        raise RuntimeError("Outlet is not lower than inlet for FID {}".format(oid))
                    results[int(oid)] = (float(zin), float(zout))
            if chosen is not None and not set(results).issubset(set(chosen)):
                raise RuntimeError("Calculation attempted to update a pipe outside the selection")
            if not results:
                arcpy.AddWarning("No valid inlet values found; original shapefile was not edited.")
            else:
                # A displayed ArcMap layer often has a shared schema lock, but
                # attribute UpdateCursor still works. Do not require an
                # exclusive schema lock unless fields must first be created.
                fields = {f.name.lower(): f.name for f in arcpy.ListFields(source)}
                missing_fields = [name for name in ("Z_Inflow", "Z_Outflow")
                                  if name.lower() not in fields]
                if missing_fields and not arcpy.TestSchemaLock(source):
                    raise RuntimeError("Cannot create missing Z elevation fields "
                                       "while ArcMap holds a schema lock. "
                                       "Close the attribute table/stop editing first.")
                for name in ("Z_Inflow", "Z_Outflow"):
                    if name.lower() not in fields:
                        arcpy.AddField_management(source, name, "DOUBLE")
                changes = 0
                with arcpy.da.UpdateCursor(source, ["OID@", "Z_Inflow", "Z_Outflow"]) as cursor:
                    for row in cursor:
                        values = results.get(int(row[0]))
                        if values is None:
                            continue
                        row[1], row[2] = values
                        cursor.updateRow(row)
                        changes += 1
                if changes != len(results):
                    raise RuntimeError("Edited {} features, but expected {}. "
                                       "Check/restore the backup.".format(changes, len(results)))
                arcpy.AddMessage("SUCCESS: Updated {} pipes IN PLACE: {}".format(changes, source))
            parameters[26].value = source
            arcpy.AddMessage("Original backup: {}".format(backup))
            if cfg["write_qa"]:
                arcpy.AddMessage("QA maps beside source: {}_QA_layers".format(qa_base))
            arcpy.AddWarning("Z_Outflow = 0 means NO VALID OUTLET, not an actual invert level.")
        except Exception as exc:
            arcpy.AddError("Extract Elevation from Elements failed: {}".format(exc))
            if backup:
                arcpy.AddError("Your dated backup is available here: {}".format(backup))
            arcpy.AddError(traceback.format_exc())
            raise arcpy.ExecuteError
        finally:
            if temporary_dir and os.path.isdir(temporary_dir):
                try:
                    shutil.rmtree(temporary_dir)
                except Exception:
                    arcpy.AddWarning("Temporary processing folder could not be removed: " + temporary_dir)
