# DDS Loca Scene Contract

## Manifest

```json
{
  "schema": "dds-loca-scene/v1",
  "coordinateSystem": "gcj02",
  "camera": { "center": [117.1667788, 36.6821349], "zoom": 13.6, "pitch": 48, "rotation": -10 },
  "layers": {
    "site": { "type": "FeatureCollection", "features": [] },
    "competitors": { "type": "FeatureCollection", "features": [] },
    "resources": { "type": "FeatureCollection", "features": [] },
    "risks": { "type": "FeatureCollection", "features": [] },
    "links": { "type": "FeatureCollection", "features": [] },
    "pulseLines": { "type": "FeatureCollection", "features": [] },
    "lines": { "type": "FeatureCollection", "features": [] },
    "heat": { "type": "FeatureCollection", "features": [] }
  },
  "stats": {}
}
```

## Feature properties

Preserve these when available:

- `id`, `label`, `layer`, `source`
- `wgs84`, `gcj02`, `coordinateStatus`
- `distanceKm`, `unitPriceCny`, `weight`, `category`, `severity`
- `evidenceGrade`, `observedAt`, `sourceUrl`

## DDS mapping

| DDS source | GeoJSON | Loca layer | Rule |
| --- | --- | --- | --- |
| project location | Point | ScatterLayer | Exactly one site anchor |
| market competitors | Point | IconLayer + HeatMapLayer | Heat value is price or normalized score |
| nearest ranked competitors | LineString | LinkLayer | Site to competitor; cap at 12 |
| amenity group items | Point | IconLayer | Flatten `amenities.{group}.items` |
| evidence gaps / risks | Point | PointLayer | Aggregate at site if no spatial coordinate |
| parcel boundary | LineString/Polygon | LineLayer/PolygonLayer | Preserve real altitude if supplied |
| route or flow geometry | LineString | PulseLineLayer | Require real coordinates and direction |

## Coordinate handling

- Prefer an explicit `gcj02` point for AMap.
- When only WGS84 exists, conversion belongs in a tested gateway, not inside a style callback.
- Keep the original coordinate and CRS in feature properties.
- Reject points outside valid longitude/latitude ranges.
- Mark unknown coordinate systems as `unknown`; do not silently claim GCJ-02.

## Section visibility

| Report section | Default layers |
| --- | --- |
| decision | site, competitors (reduced) |
| site | site, resources, boundary |
| market | site, competitors, links, heat |
| customer | site, resources |
| finance | site, competitors, heat, risks |
| risk | site, risks |
| provenance | site only |

