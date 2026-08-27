from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_project_report as builder
from report_delivery_evidence import _validate_source


project = Path(r"D:\Arch_Projects\HYP_天津_武清区六街 L-9 地块_Gary").resolve()
canonical, inbox, work = builder._validate_project_dir(project)
records = builder._apply_evidence_families(builder._raw_file_records(inbox), builder._load_normalization_overrides(work))
manifest = builder._build_manifest(canonical, work / "project_manifest.json", records, "2026-08-26")
seed = json.loads((work / "report_seed.json").read_text(encoding="utf-8"))
frozen_seed = builder._freeze_report_seed_assets(seed, project=canonical, work=work)
request = builder._build_research_request(manifest, owner_id="local", research="offline", report_seed=frozen_seed)
package = builder._build_evidence_package(manifest, request, frozen_seed, project_panorama=json.loads((work / "project_panorama.json").read_text(encoding="utf-8")))
for source in package.get("source_registry", []):
    if source.get("source_id") == "SRC-BRIEF-STRATEGY":
        print(json.dumps(source, ensure_ascii=False, indent=2))
    _, errors = _validate_source(source, project=project, project_id=manifest["project_id"], phase="freeze", fetcher=None)
    if errors:
        print(source.get("source_id"), errors)
