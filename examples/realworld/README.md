# Real-world terrain sample

`nagarkot_points.csv` - 1317 terrain points on a hillside below Nagarkot on the Kathmandu valley
rim, Nepal (about 27.713-27.717 N, 85.517-85.523 E), roughly 600 m x 400 m, elevations 1816-1934 m.

* Coordinates: UTM zone 45N, WGS84 (EPSG:32645). Create the project with CRS `UTM45N`.
* Elevations: SRTM 30 m (NASA, public domain) via the OpenTopoData API, whole metres.
* Columns: `PtNo,Easting,Northing,Elevation,Remark` - the Remark codes are illustrative.
* Terrain points only; no feature lines, boundary or alignment.

Regenerate or load with `python examples/make_realworld.py [--fetch] [--load http://127.0.0.1:8000]`.
