# ArchDDS Workflow

Use this procedure for every HTML preliminary strategy report. Keep the final artifact decision-oriented and source-labelled.

## 1. Intake And Inventory

Start from the project folder the user provides. List the files before writing. Identify:

- Project identity: city, district, parcel name, coordinates, land number, client, date.
- Legal/client source files: land listing, planning conditions, design task book, feasibility report, customer research, cost sheet, PPT/PDF/DOCX/CAD/images.
- DDS source files: `*完整投拓报告*.md`, `*_DDS报告*.json`, `*_DDS报告*.md`, `data_out/reports/parcel/*.json`, `data_out/reports/decision/*.json`.
- Existing output files: final/gold HTML, PDF export, previous backups.
- Case assets: local project images, ArchLib images, downloaded online references.

If the user says an existing HTML is final, do not keep editing it unless asked. Use it as a gold sample for structure and gap extraction.

## 2. Source Trust Tiers

Create a source matrix before synthesis.

| Tier | Sources | How to use |
| --- | --- | --- |
| L1 hard basis | Government listing, planning conditions, design task book, signed client feasibility/cost/customer research | Treat as invariant unless the user corrects it. |
| L2 high-confidence operating data | DDS local楼盘库, local GIS/AMap result, ArchLib real case images, client-provided photos/drawings | Use as main analysis evidence, with method/date. |
| L3 model/analysis output | DDS ABM, IRR, premium, CEO aggregator, benchmark extrapolation, online market snapshots, analyst estimates | Use as decision support. Label model status, confidence, and pending fields. |
| Derived | Calculations from L1/L2/L3 | Show formula or parent source. Do not hide derived assumptions. |

Never overwrite L1 with L3. When L1 and DDS differ, create a "口径冲突" row and explain which number is used in the final decision.

## 3. DDS Connection

Prefer existing formal DDS artifacts in the project folder:

1. Read `*完整投拓报告*.md` first. It usually contains the curated full decision view.
2. Read the newest `*_DDS报告*.json` for raw fields, generated time, coordinates, competitor samples, ABM, IRR, premium, and provenance.
3. If `data_out/reports/decision` or `data_out/reports/parcel` contains newer outputs for the same parcel, compare dates and coordinates before using.

If DDS outputs are missing, run local parcel reporting from the repo root when inputs are available:

```powershell
python scripts/report_parcel.py --city <城市> --district <区域> --address "<地址或地块名>" --expected-price <目标均价> --out-dir "<项目文件夹>"
python scripts/report_parcel.py --city <城市> --district <区域> --lng <经度> --lat <纬度> --expected-price <目标均价> --out-dir "<项目文件夹>"
```

If a decision report is needed and no CLI exists for the decision engine, use the local Python API pattern from `tests/test_e2e_real_parcel.py`: import `run_decision_engine` and `write_decision_outputs`, feed it the parcel report plus `client_goal`, then write outputs. Keep the one-off runner temporary or in the project folder only if the user wants to preserve it.

If DDS needs cloud access, `dds.ps1 -Query "<自然语言问题>"` is available but requires gcloud login/network. Ask for approval when sandbox/network policy blocks it.

Capture these DDS fields whenever present:

- Parcel basis: generated time, coordinates, land area, FAR, saleable area, units, parking, listing/start price, floor price.
- Market: sample count, competitor list, price bands, distance logic, fallback flags.
- ABM: persona names, shares, scores, WTP P50/P90, price sensitivity, absorption.
- Financial: land value bands, simulated IRR, IRR sensitivity ranking, cost口径 caveats.
- Premium: six-dimensional premium status, confidence, pending fields.
- CEO/decision: score, grade, confidence, headline, hard stops, action gates.
- Provenance: source labels and model/data limitations.

## 4. Client Document Extraction

Extract client docs into invariant tables before writing prose:

- Planning indicators: site area, FAR, building density, height, green ratio, saleable/计容/地下, parking, product constraints.
- Site constraints: slope, elevation, access, redlines, interfaces, utilities, school/metro/commerce/hospital proximity.
- Market and customer: benchmark competitors, survey sample size, customer personas, budget bands, purchase triggers, blockers.
- Product strategy: unit mix, area bands,赠送率,得房率,户型对标, total price targets.
- Risk basis: school, traffic, price ceiling, absorption, cost, earthwork, policy, phasing.

For PDFs/DOCX, use existing local parsers or direct extraction. For scanned material, OCR only the needed pages. Record the source file and page/section if possible.

## 5. ArchLib And Case Images

Use ArchLib as the case-image source of truth when available.

Default data paths:

- ArchLib search data: `D:/ArchLib/_检索系统/data`
- Environment override: `ARCHLIB_DATA_DIR`
- DDS sync source: `D:/ArchLib/_检索系统/data/dds_benchmark.json`

Useful commands:

```powershell
python scripts/sync_archlib_benchmark.py --archlib D:/ArchLib --dry-run
python scripts/benchmark_engine.py frontier
python scripts/benchmark_engine.py trend
python scripts/benchmark_engine.py match --city <城市> --product-type <产品类型> --top 8 --mode aspire --recent
python scripts/benchmark_engine.py extrapolate --city <城市> --product-type <产品类型> --current "facade=6,demo_zone=7,community=6"
```

Use `scripts/archlib_index.py` or import `search_archlib()` when you need project/image paths by tags. Search by product and strategy terms such as:

- `低密住宅`, `第四代住宅`, `山地住宅`, `坡地`, `台地花园`, `下沉庭院`
- `示范区`, `会所`, `社区盒子`, `架空层`, `归家大堂`
- `立面`, `大挑檐`, `石材`, `金属`, `玻璃`, `东方`, `现代`

Select cases to support design arguments, not to decorate. Each strategy module should ideally map:

`DDS/客研发现 -> 设计策略 -> 技术动作 -> ArchLib/benchmark案例 -> 可复制点`.

Before embedding final images, run the selection gate in `case-selection.md`:

1. Build a longlist from ArchLib, DDS benchmarks, user-provided images, and necessary online sources.
2. Apply hard filters for product type, design problem, source reliability, and usable image quality.
3. Score candidates against site/product/customer/implementation fit.
4. Keep a candidate board with selected cases, backups, and rejected cases.
5. If the user has complained that demo selection is weak, show the candidate board first and do not silently lock the final cases.

If exact cases are missing, use analog cases and label them "替代参考". If no image can be used, leave a marked image slot and tell the user which images to download.

## 6. Online Data

Use online data only when it materially improves current market, policy, traffic, school, competitor, or case verification. Prioritize official/government, developer official pages, recognized market platforms, and dated news. Always cite the access date and never let online snippets override L1 documents.

## 7. Synthesis Logic

Write from decision pressure to design answer:

1. What is fixed by the site and planning condition?
2. What does the market punish or reward?
3. Which customer has the highest probability and willingness to pay?
4. Which product/area/price band can win without breaking absorption?
5. Which design moves create visible premium or risk control?
6. What land/price/cost boundaries decide go/no-go?
7. What must be verified before land decision or schematic design?

Use tables for decisions and evidence; use short paragraphs for interpretation.

## 8. HTML Production

If improving an existing HTML, patch the existing file and preserve its naming. If starting fresh, copy `assets/archdds-template.html` into the project folder and replace placeholders.

HTML defaults:

- Standalone file; CSS and JS inline.
- Images embedded as base64 only when the user needs a single portable file. Otherwise use local relative/absolute image paths and keep a clear asset folder.
- Use `data-layout` and `data-role` annotations on major sections for later audits.
- Include source/provenance section at the end.

## 9. QA And Reporting

Before final response:

- Compare all DDS headline numbers against the formal DDS markdown/json.
- Check that every key table has source tags.
- Open/browser-test the HTML if possible; verify navigation, play control, progress bar, responsive width, and images.
- Check tables have scroll wrappers and no cramped card grids at mobile width.
- Ensure no placeholder base64, broken image, raw prompt, or temporary note remains.
- Verify demo/case cards are not generic mood imagery: each case must name the exact strategy it supports and why it was selected over backups.
- State exact output path, evidence used, and unresolved blockers.
