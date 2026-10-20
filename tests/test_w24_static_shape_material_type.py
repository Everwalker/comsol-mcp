from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_material_types_use_comsol_64_case_sensitive_value_and_strict_readback():
    source_path = ROOT / "tools/java/W24StaticShapeFixture.java"
    resource_path = ROOT / "comsol_mcp/data/science_recipes/java/W24StaticShapeFixture.java"
    source = source_path.read_bytes()
    resource = resource_path.read_bytes()
    assert source == resource

    text = source.decode("utf-8")
    assert text.count('glue.materialType("nonSolid");') == 1
    assert text.count('gas.materialType("nonSolid");') == 1
    assert 'glue.materialType("NON_SOLID")' not in text
    assert 'gas.materialType("NON_SOLID")' not in text
    assert '"nonSolid".equals(glue.materialType())' in text
    assert '"nonSolid".equals(gas.materialType())' in text

    origins = json.loads((ROOT / "comsol_mcp/data/science_recipes/SOURCE_ORIGINS.json").read_text())
    rows = [row for row in origins["files"] if row["origin"] ==
            "tools/java/W24StaticShapeFixture.java" and row["resource"] ==
            "comsol_mcp/data/science_recipes/java/W24StaticShapeFixture.java"]
    assert len(rows) == 1
    assert hashlib.sha256(source).hexdigest() == rows[0]["sha256"]


def test_material_type_repair_is_tied_to_same_version_official_documentation():
    provenance_path = ROOT / "tests/fixtures/w24_r18_material_type_provenance.json"
    provenance = json.loads(provenance_path.read_text())
    assert provenance["comsol_version"] == "6.4.0.293"
    docs = {(row["document_id"], row["chunk_id"]): row for row in provenance["official_documentation"]}
    assert docs[("doc4111", "chunk16779")]["source_sha256"] == (
        "80ae61bc99d91b2e91aaeff7c91406bae0a768c7fbadef5b8efe95db33c262ab")
    assert docs[("doc7624", "chunk22923")]["source_sha256"] == (
        "2809a9300c7bed64c9289b9815f5ba072ec7555b48bdbc75c24e092d22196fdf")
    assert "exact case-sensitive value nonSolid" in provenance["claim"]
    assert "returns String" in provenance["claim"]
    assert "no COMSOL model was executed" in provenance["scope"]
