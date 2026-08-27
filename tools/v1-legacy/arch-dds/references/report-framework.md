# ArchDDS Report Framework

The report should read like a front-end strategy deck converted into a rigorous single-page report: fast executive judgment, dense evidence, designable conclusions, and traceable sources.

## Gold-Sample Standard From E15

The Changlingshan E15 final report establishes the expected flow:

`Read-me summary -> district/site -> constraints -> market -> competitors -> customers -> product/unit mix -> design strategy -> DDS benchmark -> DDS investment -> premium -> logic -> cases -> risks -> provenance`.

Future reports should preserve this integrated structure while tightening the data pipeline:

- DDS formal report is not optional; include investment decision, ABM, IRR sensitivity, premium, CEO, and provenance when available.
- Design strategy must be tied to customer/market/DDS evidence, not only visual mood.
- Case references must pass the selection matrix in `case-selection.md` and explain the transferable tactic.
- Layout must support dense tables without crowding.

## Section Blueprint

### 1. Hero / Read-Me

Purpose: make the project instantly identifiable.

Include project name, city/district, report type, date/version, 3-5 core tags, and one-line decision thesis. Use one strong site/case image if available.

### 2. Executive Judgment

Use a compact card/table set:

- Overall verdict: proceed / conditional proceed / pause / abandon.
- Land-price or cost boundary.
- Product position.
- Customer target.
- Biggest opportunity.
- Biggest risk.
- Immediate verification actions.

### 3. District And Access

Explain why the location can or cannot support premium:

- City/district hierarchy, employment anchors, schools, metro/road access, commerce, natural resources.
- Commute and arrival sequence.
- Externalities: noise, slope, interfaces, incomplete roads, surrounding underdevelopment.

### 4. Site Constraints

Convert physical/legal facts into design tasks:

- Parcel area, FAR, height, density, green ratio, setbacks, parking.
- Topography, earthwork, retaining walls, elevation difference, water/drainage, interface roads.
- What is a hard constraint vs. design opportunity.

### 5. Market And Competition

Separate market facts from judgment:

- Price band and supply context.
- Direct competitors within distance/price/product band.
- Competitive gap: where current projects are weak and the subject parcel can win.
- Threats: discounting, school, brand, location, product substitution.

Use competitor tables with project, price, distance, product, source, and implication.

### 6. Customer Research And DDS ABM

Merge client customer research with DDS simulation:

- Client-researched personas and survey sample size.
- DDS ABM personas, share, WTP P50/P90, score, confidence.
- Consistency/conflict table: where client and DDS agree, where DDS finds new risk.
- Demand translation: total price, unit area, room count, storage, commute, school, community.

### 7. Product Positioning And Unit Mix

Show the product answer:

- Product slogan/position, not marketing copy.
- Area bands, unit mix, total price envelope,赠送率/得房率 if available.
- Unit-by-unit competitor comparison.
- Phasing and absorption assumptions if DDS provides them.

### 8. Design Strategy

Use 6-10 strategy modules. Each module should contain:

- Strategy name.
- Evidence trigger: DDS/customer/market/site finding.
- Design action: planning, facade, landscape, lobby, club,架空层, unit plan, technology, arrival.
- Value mechanism: premium, absorption, risk control, or cost tradeoff.
- Case reference: ArchLib/benchmark image and transferable tactic.
- Implementation caution.

Do not pick the demo/case first and then write a strategy around it. Write the strategy from site, customer, and DDS evidence, then choose the case that best proves or clarifies that strategy.

Recommended modules:

1. Site and arrival re-ordering.
2. Terrain/台地/抬板 strategy.
3. Product generation difference: fourth-generation / high赠送 / family storage.
4. Facade recognizability and material hierarchy.
5. Community lobby,架空层, and club as daily-life infrastructure.
6. Landscape system: garden, mountain view, children's/elderly activity, seasonal planting.
7. Technology/green comfort: thermal, smart, noise, energy.
8. Demonstration area and sales path.

### 9. DDS Investment Decision

This section is required when DDS data exists.

Include:

- Land value sensitivity: conservative / balanced / aggressive and go/no-go rules.
- Simulated IRR and calibration warning if the model口径 is incomplete.
- IRR sensitivity ranking with interpretation.
- CEO aggregate score/grade/confidence.
- Key action gates before bid/design.
- Worst-case fallback strategy.

Do not present over-optimistic DDS values as final truth. If DDS says the absolute IRR is unstable but sensitivity ranking is useful, state exactly that.

### 10. Premium And Asset Curve

Use DDS premium engine where available:

- Six dimensions: location, design, policy generation, brand operation, scarcity, supply-demand migration.
- Premium, confidence, evidence, status.
- Asset curve or holding narrative only as scenario analysis.
- Pending dimensions clearly labelled.

### 11. Case Gallery

Group references by design question, not by random image order:

- Site/terrain.
- Facade/material.
- Club/community.
- Landscape/garden.
- Unit/living scenario.
- Demonstration/sales path.

Each case card needs project/case name, image, source, key tactic, why it matters for this parcel, and a concise fit label such as `强匹配`, `替代参考`, or `仅氛围不采用`. If the selected case is not a strong match, include a backup or a download request rather than pretending it is exact.

### 12. Risk And Action List

End with operational decisions:

- Risk matrix: probability, impact, severity, mitigation, source.
- Immediate actions: owner, question, evidence needed, deadline if known.
- Hard-stop conditions.

### 13. Provenance

List all major source files and methods:

- Client/legal files.
- DDS reports/scripts and generated time.
- ArchLib paths or benchmark source.
- Online sources with access date.
- Derived calculations.

State the trust tier definitions and remind that unlabelled data is not a decision basis.

## Writing Rules

- Use "because/therefore" logic. Avoid pure description.
- Keep labels precise: `起拍口径`, `可售楼面价`, `土地口径粗算`, `模型输出`, `待校准`.
- Show contradiction instead of smoothing it away.
- Prefer tables for numbers and action gates.
- Avoid marketing slogans unless the section explicitly needs positioning language.
