---
name: amap-loca-dds
description: Integrate and verify AMap Loca 2.0 data visualization in DDS real-estate decision reports using official AMap examples, types, and lifecycle patterns. Use for ViewControl camera animation, ScatterLayer site pulses, LinkLayer comparison links, PulseLineLayer routes, altitude-aware LineLayer, IconLayer POIs, meter-based HeatMapLayer aggregation, blurred PointLayer uncertainty, or compiling DDS report JSON into GeoJSON scene data.
---

# DDS AMap Loca

Use the official AMap JS API 2.x and Loca 2.0 runtime inside DDS. Reuse official examples and types; add only the DDS data contract and application adapter.

## Source Order

1. Read [references/adoption.md](references/adoption.md) before choosing a package.
2. Use the cloned official `AMap-Web/amap-skills` references for map loading, security, controls, and cleanup.
3. Use `AMap-Web/amap-loca-types/index.d.ts` as the API signature source.
4. Use official Loca demo source and the MIT `@vuemap/vue-amap-loca` implementation for tested layer lifecycle patterns.
5. Do not copy the official Skill's telemetry calls or OpenClaw-only path rules into DDS.

## Workflow

1. Inspect the live `/api/v2/projects/{id}/report-data` payload. Count valid points, grouped POIs, lines, coordinate systems, and non-spatial evidence.
2. Read [references/dds-scene-contract.md](references/dds-scene-contract.md).
3. Compile payload data into an auditable scene manifest:

```powershell
python scripts/compile_dds_loca.py report-data.json --output scene.json
```

4. Feed the manifest or equivalent descriptors into `web/decision-field/js/scene/LocaDataScene.js`.
5. Keep state normalization in `EvidenceLayers`, engine lifecycle in `CesiumSceneAdapter`, and report-section behavior in `ReportApp`.
6. Verify the real report route at desktop and mobile sizes.

## Layer Semantics

- `ScatterLayer`: selected parcel anchor; use one restrained pulse.
- `IconLayer`: competitors and categorized POIs; use zoom gates and collision-aware HTML labels separately.
- `LinkLayer`: ranked parcel-to-comparable relationships; cap the count.
- `PulseLineLayer`: real routes, flows, or explicitly modeled directional links only.
- `LineLayer`: boundaries, transit corridors, and route geometry with real altitude data.
- `HeatMapLayer`: density or magnitude distributions; prefer `unit: "meter"` at site scale.
- `PointLayer.blurWidth`: uncertainty, influence, or confidence falloff.
- `ViewControl`: section and presentation camera transitions; stop after user interaction.

## Guardrails

- Loca 2.0 consumes standard GeoJSON and requires AMap JS API 2.x.
- Render GCJ-02 in AMap. Preserve WGS84 in feature properties for provenance.
- Do not convert unknown or BD-09 coordinates silently.
- Do not fabricate line geometry to demonstrate animation.
- Aggregate coincident non-spatial risks instead of stacking markers.
- Keep the map dominant and reveal analytical layers by report section.
- Pause animation for reduced motion, low-quality profiles, hidden tabs, and teardown.
- Destroy Loca sources, layers, listeners, and container before destroying AMap.

## Verification Gate

- `globalThis.Loca.Container` initializes against the active AMap instance.
- Scene statistics match the payload after invalid coordinates are excluded.
- Layer visibility follows the active DDS report section.
- User drag or rotation cancels ViewControl motion.
- Desktop and mobile screenshots have nonblank map pixels and no UI overlap.
- Console has no application errors; external tile failures are reported separately.

