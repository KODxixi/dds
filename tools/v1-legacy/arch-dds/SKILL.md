---
name: arch-dds
description: Produce sourced HTML preliminary strategy reports for real-estate and architecture investment/design projects by combining DDS local data, client due-diligence files, ArchLib case images, benchmark logic, design strategy, and browser-verifiable single-page output. Use when asked for 前期研判, 投拓报告, 设计策略, 案例参考, DDS分析, ArchLib案例, or an HTML/PDF-ready report for a new project.
---

# ArchDDS

ArchDDS turns a project folder into a sourced HTML report for early-stage land, product, and design strategy decisions. The default output is one standalone `.html` file in the project folder, suitable for browser reading and PDF export.

## Required References

Load these files when doing real report work:

- `references/workflow.md` for the end-to-end operating procedure and DDS/ArchLib data sequence.
- `references/report-framework.md` before writing or restructuring the report logic.
- `references/case-selection.md` before selecting demo/case references or asking the user to download images.
- `references/html-spec.md` before implementing or QA-ing the HTML.
- `assets/archdds-template.html` when starting a new HTML report instead of improving an existing one.

## Non-Negotiables

- Treat any user-provided final sample as the gold standard. Extract its structure, density, tone, and deficiencies before generalizing.
- Never use an unlabelled number as a decision basis. Every key number needs a source tag such as `[挂牌]`, `[可研]`, `[客研]`, `[DDS]`, `[DDS-ABM]`, `[DDS-IRR]`, `[ArchLib]`, `[线上-日期]`, or `[推导]`.
- Use the latest formal DDS markdown/json in the project folder as the DDS source of truth. Do not let temporary calculations override it.
- L1 client/legal documents override DDS model output. DDS contradictions must be shown as a conflict, not silently averaged.
- If a field is missing, write `待接入` or a clear caveat. Do not invent market data, IRR values, case provenance, or image labels.
- HTML reports must include section navigation, reading progress, and a play/PPT-style scroll control. All play controls must share the same state.
- Case images must be real project/case images from ArchLib or clearly marked user-provided/online sources. If an exact case is unavailable, choose analog cases and label them as replacements.
- Demo/case selection must pass the candidate-board gate in `references/case-selection.md`. Do not choose references only because they look premium or atmospheric.

## Workflow Summary

1. Inventory the project folder and identify the city, parcel, coordinates, task book, planning files, feasibility report, customer research, drawings, images, and existing DDS outputs.
2. Build a source matrix using the trust tiers in `references/workflow.md`; make missing or conflicting data visible early.
3. Connect DDS before writing: read existing `*完整投拓报告*.md`, `*_DDS报告*.json`, and `data_out/reports/**` outputs; if absent, run local DDS scripts or report the blocker.
4. Extract client hard constraints: land indicators, FAR, area, height, redlines, program, cost assumptions, customer research, risk notes, and required product line.
5. Pull ArchLib/DDS benchmark evidence: use ArchLib case images, `benchmark_engine.py`, and linked benchmark dimensions to support each design strategy.
6. Build a demo/case candidate board with scores, strategy mapping, backup options, and rejected cases before embedding final images.
7. Write the report as a decision argument, not a brochure: site facts -> market pressure -> customer demand -> product answer -> design strategies -> investment decision -> risks/actions.
8. Build or patch the standalone HTML using the layout rules in `references/html-spec.md`.
9. QA with data, layout, and browser checks. Report exact output path and unresolved caveats.

## E15 Gold-Sample Lessons

The Jinan Changlingshan E15 final HTML is the baseline for future reports. Preserve its density, sourced decision tone, and integrated "研判 + 策略 + 案例 + DDS投拓" flow, while enforcing these fixes as repeatable rules:

- DDS cannot be reduced to a short benchmark paragraph. Include the formal DDS investment decision, ABM customers, IRR sensitivity, premium engine, CEO summary, and source provenance.
- Demo selection is a strategy decision, not decoration. For E15-like work, avoid generic luxury/demo imagery that does not match the parcel's terrain, customer, product generation, or risk logic.
- Temporary or manually estimated figures must be reconciled against the formal DDS report before final output.
- The play button needs one state controller for nav and floating controls, plus keyboard/browser verification.
- Dense tables and cards need scroll wrappers, min-widths, grid breakpoints, and enough spacing so Chinese text does not collide.

## Done Criteria

A report is complete only when:

- The HTML opens locally, navigation targets real sections, and the play/PPT scroll control works.
- DDS data and client data are both represented with source tags and trust tiers.
- Case/demo images appear in the design strategy or case section with provenance, strategy fit, and at least one backup or rejected-case note when selection is uncertain.
- The report contains a visible risk/action list and a source/provenance section.
- Any missing DDS, ArchLib, online, or client data is explicitly listed rather than hidden.
