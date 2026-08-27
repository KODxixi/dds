# ArchDDS Demo And Case Selection

Use this gate before selecting demo images, benchmark cases, or visual references. The goal is to choose cases that prove the strategy, not images that merely look expensive.

## Output Contract

Before embedding final case images, produce a compact candidate board either in the working notes, a sidecar markdown file, or an appendix:

| Strategy | Selected case | Fit | Score | Why it fits | Backup | Rejected |
| --- | --- | --- | --- | --- | --- | --- |

Use this board when the user can review case choices. If the user asks for a full report without review, still keep the board internally and surface uncertain choices in the final provenance/caveat section.

## Longlist First

Build a longlist of 20-40 candidates when the library is available:

- ArchLib tagged projects and cover images.
- DDS `benchmark_engine.py match/frontier/trend/extrapolate` results.
- Client-provided reference images or named competitors.
- Online official/developer pages only when local images are missing or current verification matters.

Each candidate needs source path/URL, project name, image type, design topic, and source reliability.

## Hard Filters

Reject before scoring if any condition fails:

- No usable image or source provenance.
- Mismatched category with no explicit analogy, such as a resort hotel used as an urban residential proof without explaining the transferable tactic.
- Pure mood image with no built detail, planning implication, or product strategy relevance.
- Incompatible scale, climate, cost level, customer, or implementation condition.
- Overly old or generic case when newer/local/closer analogs exist.
- Rendering-only image unless the report clearly marks it as concept reference.

## Scoring Matrix

Score each candidate out of 100.

| Dimension | Weight | What good looks like |
| --- | ---: | --- |
| Strategy relevance | 30 | Directly supports one design strategy or risk response. |
| Site/product fit | 20 | Matches terrain, density, FAR, height, product type, or generation difference. |
| Customer/premium mechanism | 20 | Explains why the target buyer pays more, moves faster, or trusts the product. |
| Implementation feasibility | 15 | Transferable within likely cost, regulation, construction, and operation limits. |
| Image/provenance quality | 10 | Real image, clear view, credible source, usable resolution. |
| Freshness/positioning fit | 5 | Current enough and aligned with brand/market ambition. |

Fit labels:

- `强匹配`: 80-100, can be embedded as primary proof.
- `可用参考`: 65-79, usable with caveat or as backup.
- `替代参考`: 50-64, only use if exact cases are missing.
- `不采用`: below 50 or failed hard filters.

## Strategy Coverage

For a full report, target 6-10 final visual references:

- 1-2 site/terrain or arrival cases.
- 1-2 facade/material hierarchy cases.
- 1 community lobby/架空层/club case.
- 1 landscape/garden or activity system case.
- 1 unit/product-generation case.
- 1 demonstration/sales path case.
- Optional local competitor or direct market case.

Avoid repeating the same visual idea across multiple sections. Each image should carry a different decision point.

## User Dissatisfaction Protocol

When the user says the demo/case selection is unsatisfactory:

1. Do not defend the existing selection.
2. Name the likely mismatch: product grade, site condition, customer, design problem, image quality, or source credibility.
3. Rebuild the candidate board with stricter hard filters.
4. Offer replacement groups by strategy, not by style adjectives.
5. If the user can download images, give exact download targets: case name, view type, why needed, and preferred orientation.
6. Do not rewrite the whole report unless the case choice changes the strategy logic.

## Red Flags

Avoid these common failures:

- Choosing "豪宅感" images that do not explain how the project earns premium.
- Using hotel/villa images for high-rise or mid-rise residential without a clear transferable element.
- Selecting only facade images while the project risk is customer, school, cost, terrain, or absorption.
- Selecting a famous case that is impossible under the parcel's FAR, budget, or planning condition.
- Hiding low confidence. Mark weak matches as `替代参考` and provide backups.

## Case Card Minimum

Every embedded case card should include:

- Project/case name.
- Source path or URL.
- Fit label and score if available.
- Strategy supported.
- Transferable tactic in one sentence.
- Limitation or caveat when the fit is not strong.
