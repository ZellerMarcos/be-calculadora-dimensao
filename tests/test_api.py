from io import BytesIO

from docx import Document
from fastapi.testclient import TestClient
import pytest

from app.main import app

client = TestClient(app)


def verification_input() -> dict:
    return {
        "station_id": 5,
        "station_name": "Aracaju",
        "station_uf": "SE",
        "return_period": 5,
        "intensity": 122,
        "rainfall_source": "station",
        "roof_width": 10,
        "roof_rise": 2.5,
        "roof_surface": "inclined",
        "gutter_length": 12,
        "outlet_type": "extremidade",
        "profile": "rectangular",
        "bottom_width_mm": 180,
        "top_width_mm": 180,
        "useful_depth_mm": 100,
        "freeboard_mm": 30,
        "slope_percent": 1,
        "roughness": 0.011,
        "curve_condition": "none",
        "gutter_type": "beiral_platibanda",
        "project": {"nome": "Residência", "cliente": "Cliente de teste"},
    }


def test_verification_endpoint_returns_domain_calculation() -> None:
    response = client.post("/api/v1/nbr10844/calhas/verificar", json=verification_input())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ATENDE"
    assert body["resultados"]["areaContribuicaoM2"] == 135
    assert body["resultados"]["vazaoProjetoLmin"] == 274.5
    assert body["resultados"]["areaMolhadaM2"] == pytest.approx(0.018)
    assert len(body["passos"]) >= 8


def test_central_outlet_uses_larger_side() -> None:
    payload = verification_input()
    payload.update({"outlet_type": "central", "extension_side_a": 4, "extension_side_b": 7})

    response = client.post("/api/v1/nbr10844/calhas/verificar", json=payload)

    assert response.status_code == 200
    assert response.json()["resultados"]["areaContribuicaoM2"] == 77


def test_horizontal_surface_uses_flat_area_formula() -> None:
    payload = verification_input()
    payload["roof_surface"] = "horizontal"

    response = client.post("/api/v1/nbr10844/calhas/verificar", json=payload)

    assert response.status_code == 200
    assert response.json()["resultados"]["areaContribuicaoM2"] == 120
    assert "a · b" in response.json()["passos"][1]["formulaTexto"]


def test_manual_rainfall_can_be_calculated_without_justification() -> None:
    payload = verification_input()
    payload.update({"rainfall_source": "manual", "manual_justification": ""})

    response = client.post("/api/v1/nbr10844/calhas/verificar", json=payload)

    assert response.status_code == 200
    manual_alert = next(
        alert for alert in response.json()["alertas"]
        if alert["codigo"] == "INTENSIDADE_MANUAL"
    )
    assert manual_alert["nivel"] == "aviso"
    assert manual_alert["mensagem"] == "Intensidade manual informada sem justificativa adicional."


def test_docx_memorial_recalculates_and_returns_word_document() -> None:
    response = client.post("/api/v1/nbr10844/memorial?formato=docx", json=verification_input())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert response.content.startswith(b"PK")
    document = Document(BytesIO(response.content))
    paragraphs = [paragraph.text for paragraph in document.paragraphs]
    headings = [
        paragraph.text
        for paragraph in document.paragraphs
        if paragraph.style.name == "Heading 1"
    ]
    assert headings == [
        "2. Dados de entrada e premissas",
        "3. Desenvolvimento dos cálculos",
        "4. Resultados e dimensionamento",
        "5. Verificações e conclusão",
    ]
    assert "Cliente de teste" not in "\n".join(paragraphs)
    assert "Residência" not in "\n".join(paragraphs)


def test_json_memorial_contains_inputs_premises_steps_and_conclusion() -> None:
    response = client.post("/api/v1/nbr10844/memorial?formato=json", json=verification_input())

    assert response.status_code == 200
    body = response.json()
    assert body["norma"] == "ABNT NBR 10844:1989"
    assert body["entradas"]["intensity"] == 122
    assert body["premissas"]
    assert body["passos"]
    assert "atende aos critérios" in body["conclusao"]


def test_table5_has_98_records_three_periods_and_unique_ids() -> None:
    response = client.get("/api/v1/nbr10844/tabela5")

    assert response.status_code == 200
    posts = response.json()["postos"]
    assert len(posts) == 98
    assert len({post["id"] for post in posts}) == 98
    assert all(all(key in post for key in ("i1", "i5", "i25")) for post in posts)


def test_table5_surfaces_suspected_decreasing_intensity_for_review() -> None:
    posts = client.get("/api/v1/nbr10844/tabela5").json()["postos"]
    suspected = [
        post["nome"]
        for post in posts
        if post["i25"] is not None and post["i5"] is not None and post["i25"] < post["i5"]
    ]

    assert suspected == ["São Carlos"]


def test_station_intensity_endpoint_reports_real_return_period() -> None:
    response = client.get("/api/v1/nbr10844/localidades/59/intensidade?periodoRetorno=5")

    assert response.status_code == 200
    assert response.json()["intensidadeMmH"] == 139
    assert response.json()["periodoRealAnos"] == 2
    assert response.json()["alerta"]


def test_station_search_filters_by_query_and_state() -> None:
    response = client.get("/api/v1/nbr10844/localidades?q=Rio&uf=RJ")

    assert response.status_code == 200
    assert all("Rio" in post["nome"] and post["uf"] == "RJ" for post in response.json())


def test_material_endpoint_returns_only_table2_roughness_values() -> None:
    response = client.get("/api/v1/nbr10844/materiais")

    assert response.status_code == 200
    assert [material["n"] for material in response.json()] == [0.011, 0.012, 0.013, 0.015]


def test_station_with_missing_intensity_requires_manual_value() -> None:
    payload = verification_input()
    payload.update({"station_id": 4, "return_period": 25, "station_name": "Alto Teresópolis", "station_uf": "RJ"})

    response = client.post("/api/v1/nbr10844/calhas/verificar", json=payload)

    assert response.status_code == 422


def test_rectangular_dimensioning_returns_constructive_minimum() -> None:
    payload = verification_input()
    payload["constructive_step_mm"] = 10

    response = client.post("/api/v1/nbr10844/calhas/dimensionar", json=payload)

    assert response.status_code == 200
    body = response.json()
    dimension = body["dimensionamento"]
    assert dimension["status"] == "ATENDE"
    assert dimension["capacidadeLmin"] >= body["resultados"]["vazaoProjetoLmin"]
    assert dimension["laminaMm"] % 10 == pytest.approx(0)
    assert len(body["passos"]) == 9


def test_semicircular_dimensioning_uses_smallest_table3_diameter() -> None:
    payload = verification_input()
    payload.update({"profile": "semicircular", "bottom_width_mm": 100, "top_width_mm": 100})

    response = client.post("/api/v1/nbr10844/calhas/dimensionar", json=payload)

    assert response.status_code == 200
    assert response.json()["dimensionamento"]["diametroMm"] == 125


def test_dimensioning_memorial_recalculates_on_server() -> None:
    response = client.post(
        "/api/v1/nbr10844/memorial?formato=json&tipo=dimensionar",
        json=verification_input(),
    )

    assert response.status_code == 200
    assert response.json()["dimensionamento"]["status"] == "ATENDE"