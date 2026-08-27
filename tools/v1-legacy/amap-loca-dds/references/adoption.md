# Adoption Decision

## Selected sources

| Source | Status | DDS use |
| --- | --- | --- |
| `AMap-Web/amap-skills` | Official, updated 2026-04, verified JSAPI examples | Base loading, security, controls, cleanup |
| `AMap-Web/amap-loca-types` | Official MIT type package | Exact Loca 2.0 layer and style signatures |
| AMap Loca 2.0 demo center | Official runnable examples | Tested layer construction and style values |
| `yangyanggu/vue-amap` / `@vuemap/vue-amap-loca` | MIT, maintained, complete Loca wrappers | Source destruction, visibility, events, teardown patterns |
| `AMap-Web/amap-lbs-skill` | Official MIT data Skill | Optional POI/geocoding/route inputs; not embedded rendering |
| `@amap-lbs/amap-gui` | Official Agent CLI | Standalone map GUI only; do not embed in DDS |
| `AMap-Web/layer-3dtiles` | Official MIT, last update 2022 | Future BIM/3D Tiles only; not a Loca replacement |

## Rejected approaches

- Do not migrate DDS to Vue solely to use `@vuemap/vue-amap-loca`; the current app is native ES modules.
- Do not iframe the official heatmap viewer; it cannot share DDS state, sections, or evidence interactions.
- Do not install the official Skill unchanged. It contains telemetry and OpenClaw-specific output rules that do not belong in DDS.
- Do not build an MCP for rendering. A server can provide data, but Loca rendering must run in the browser with the active AMap instance.

## Official references

- Loca introduction: https://lbs.amap.com/api/loca-v2/intro
- Loca API: https://a.amap.com/Loca/static/loca-v2/doc/html/index.html
- Loca demos: https://lbs.amap.com/api/loca-v2/demo-overview
- Official JSAPI Skill: https://github.com/AMap-Web/amap-skills
- Official Loca types: https://github.com/AMap-Web/amap-loca-types
- Official AMap MCP: https://lbs.amap.com/api/mcp-server/summary
- Official AMap GUI CLI: https://lbs.amap.com/api/cli/map-cli/summary
- Maintained Vue wrapper: https://github.com/yangyanggu/vue-amap

