# Contributing and Community Feedback

Thank you for helping improve **Pipe-Culvert-Mesh-Tool**.

## Where to start

- **General algorithm questions and experience sharing:** [community discussion thread](https://github.com/Bipul-roy-AI/Pipe-Culvert-Mesh-Tool/issues/1).
- **Detailed algorithm rationale and suggested improvements:** [discussion guide](docs/DISCUSSION.md).
- **Reproducible bugs:** open a new GitHub Issue with ArcMap version, inputs and expected/actual behavior.
- **Proposed source-code changes:** create a feature branch and submit a Pull Request describing the impact on the toolbox's elevation-selection rules.

## Required information for bugs

1. ArcMap version (toolbox targets **10.8 / Python 2.7**).
2. Pipe and mesh dataset type, geometry and spatial reference.
3. Selected bathymetry, inflow and outflow field names and units.
4. Search radius and a small representative case.
5. ArcPy error messages, if any.
6. Before/after values and expected output.

Please remove sensitive or proprietary GIS data from shared examples.

## Engineering guidelines

- Preserve **ArcMap 10.8 / Python 2.7** compatibility unless proposing a separate migration.
- Preserve **TopoID = 1900s0** filtering and avoid unexpected edits to other pipes.
- Maintain **start = inflow / end = outflow** interpretation.
- Do not change the **snapped endpoint priority** without explicit discussion: valid lower snapped end values take precedence over nearby forward candidates.
- Never update pipe elevations unless the chosen inflow is strictly higher than outflow.
- Describe how a change affects mesh edge cases, empty/null attribute values, unit assumptions and spatial reference consistency.
- Test on a copy of a dataset; the toolbox edits rows in place.

## Licensing

A licence has not been supplied with the uploaded source. Do not assume that publication on GitHub grants permission to reuse or redistribute it under an open-source licence. Coordinate substantial contributions with the repository owner.
