"""Actual approved hosted visual extraction on a signed synthetic fixture only.

Run in the protected release environment after exact-main CI/deployment. No
provider is mocked. Database access is read-only and constrained to the newly
created fixture and its authenticated owner. Existing cleanup receipts prove
physical removal even if visual extraction fails.
"""

import asyncio
import json
import os
import uuid

import ci_production_smoke as smoke
import pymupdf
from release_gate import trusted_ci, validate_database_url
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


def visual_pdf(marker, doc_name="Public visual gold"):
    # Raster content forces the actual hosted layout path. Two panels share one
    # image, so correct classification cannot rely on PDF native labels alone.
    with pymupdf.open() as source:
        page = source.new_page(width=1000, height=600)
        page.draw_rect((25, 40, 460, 560))
        page.draw_rect((540, 40, 975, 560))
        page.insert_text((60, 85), "Public counts", fontsize=24)
        page.draw_line((100, 450), (420, 450))
        page.draw_line((100, 160), (100, 450))
        for x, value, label in [(160, 42, "Alpha"), (300, 80, "Beta")]:
            page.draw_rect((x, 450 - value * 3, x + 65, 450), color=(0, 0, 0), fill=(0.2, 0.5, 0.8))
            page.insert_text((x + 15, 440 - value * 3), str(value), fontsize=20)
            page.insert_text((x, 480), label, fontsize=18)
        page.insert_text((110, 520), "Units: items", fontsize=18)
        page.insert_text((580, 85), "Public process", fontsize=24)
        for y, label in [(140, "Start"), (290, "Review"), (440, "Finish")]:
            page.draw_rect((640, y, 875, y + 55))
            page.insert_text((675, y + 35), label, fontsize=20)
        for y in [195, 345]:
            page.draw_line((755, y), (755, y + 95))
            page.draw_line((755, y + 95), (745, y + 80))
            page.draw_line((755, y + 95), (765, y + 80))
        pixels = page.get_pixmap().tobytes("png")
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=1000, height=600)
        page.insert_image(page.rect, stream=pixels)
        # An unrelated small native marker permits exact fixture identification;
        # the visual labels themselves exist only inside the raster image.
        page.insert_text((30, 590), marker, fontsize=8)
        return pdf.tobytes()


async def evidence_ids(document_id, owner):
    url = os.environ["DATABASE_URL"]
    validate_database_url(url)
    engine = create_async_engine(make_url(url).set(drivername="postgresql+asyncpg"), hide_parameters=True,
        connect_args={"command_timeout": 5, "timeout": 10})
    try:
        async with engine.connect() as db:
            await db.execute(text("SET TRANSACTION READ ONLY"))
            rows = await db.execute(text("""SELECT evidence_id FROM document_evidence
                WHERE document_kind='standalone' AND source_record_id=:document
                AND client_id='search_sphere' AND tenant_id=:owner AND subject_id=:owner"""),
                {"document": document_id, "owner": owner})
            return list(rows.scalars())
    finally:
        await engine.dispose()


def evaluate(objects):
    charts = [item for item in objects if item.get("kind") == "chart"]
    diagrams = [item for item in objects if item.get("kind") == "diagram"]
    numeric = []
    for chart in charts:
        assert chart.get("review_required") is True
        for series in chart.get("series", []):
            for point in series.get("points", []):
                assert point.get("verified") is False
                if point.get("readability") == "readable":
                    try:
                        numeric.append(float(point["value"]))
                    except (ValueError, TypeError):
                        pass
    labels, relations = set(), set()
    for diagram in diagrams:
        assert diagram.get("review_required") is True
        nodes = {node["node_id"]: node["label"].strip().lower() for node in diagram.get("nodes", [])}
        labels.update(nodes.values())
        for edge in diagram.get("edges", []):
            assert edge.get("visible") is False
            if edge.get("direction") == "directed":
                relations.add((nodes[edge["source"]], nodes[edge["target"]]))
    return {"chart_objects": len(charts), "diagram_objects": len(diagrams),
            "visible_label_matches": sum(value in numeric for value in (42, 80)), "gold_visible_labels": 2,
            "diagram_label_matches": len(labels & {"start", "review", "finish"}), "gold_diagram_labels": 3,
            "diagram_relation_matches": len(relations & {("start", "review"), ("review", "finish")}),
            "gold_diagram_relations": 2, "numerical_or_arrow_verification_claimed": False,
            "independent_visual_accuracy_accepted": False}


def main():
    trusted_ci(os.environ["EXPECTED_SHA"])
    smoke.wait_for_matching_backend()
    smoke.signing_key()
    auth = smoke.acquire_ci_auth()
    if not auth:
        raise AssertionError("Dedicated synthetic identity is required")
    token, _ = auth
    headers = {"Authorization": "Bearer " + token}
    status, _, identity = smoke.http(smoke.API + "/auth/me", headers=headers)
    owner = smoke.require_json(status, identity, "Visual fixture owner")["id"]
    smoke.CI_RUN_ID = uuid.uuid4().hex
    marker = "SS-VISUAL-" + smoke.CI_RUN_ID
    smoke.generate_synthetic_pdf = visual_pdf
    try:
        document_id, _ = smoke.ensure_synthetic_document(token, marker, user_id=owner,
            email=os.environ["PROD_SMOKE_USER_EMAIL"])
        identifiers = asyncio.run(evidence_ids(document_id, owner))
        objects = []
        for identifier in identifiers:
            status, _, payload = smoke.http(f"{smoke.API}/documents/{document_id}/evidence/{identifier}", headers=headers)
            objects.append(smoke.require_json(status, payload, "Authorized visual evidence")["data"])
        result = evaluate(objects)
        print(json.dumps({"live_hosted_visual_evaluation": result}), flush=True)
        if not result["chart_objects"] or not result["diagram_objects"]:
            raise AssertionError("Live visual classification incomplete; see partial capability evidence")
    finally:
        for document_id in list(smoke.SYNTHETIC_DOCUMENT_IDS):
            smoke.cleanup_synthetic_document(token, document_id)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001 -- redact credentials/provider bodies at the CLI boundary
        # Only error categories escape; provider bodies, credentials and data do not.
        print(json.dumps({"live_visual_status": "FAILED", "error_type": type(error).__name__}), flush=True)
        raise SystemExit(1) from None
