from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator, model_validator

from app.domain.calculator import (
    calculate_capacity,
    calculate_contribution_area,
    calculate_project_flow,
    calculate_section,
    dimension_gutter,
    get_curve_factor,
    verify_gutter,
)
from app.domain.models import CornerType, GutterType, SectionType
from app.services.memorial import build_docx

router = APIRouter()
ENGINE_VERSION = "0.2.0"
TABLE5_PATH = Path(__file__).resolve().parent / "data" / "tabela5.json"
TABLE5 = json.loads(TABLE5_PATH.read_text(encoding="utf-8"))
STATIONS = TABLE5["postos"]
MATERIALS = {
    "plastico_fibrocimento_aco_naoferrosos": (0.011, "Plástico, fibrocimento, aço e metais não-ferrosos"),
    "ferro_concreto_alisado_alvenaria_revestida": (0.012, "Ferro fundido, concreto alisado e alvenaria revestida"),
    "ceramica_concreto_nao_alisado": (0.013, "Cerâmica e concreto não-alisado"),
    "alvenaria_tijolos_nao_revestida": (0.015, "Alvenaria de tijolos não-revestida"),
}


class ProjectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nome: str = Field(default="", max_length=120)
    cliente: str = Field(default="", max_length=120)
    responsavel: str = Field(default="", max_length=120)
    data: str = Field(default="", max_length=32)


class CalculationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    station_id: int | None = Field(default=None, ge=1)
    station_name: str = Field(default="", max_length=120)
    station_uf: str = Field(default="", max_length=2)
    return_period: Literal[1, 5, 25] = 5
    intensity: FiniteFloat | None = Field(default=None, gt=0, le=1000)
    rainfall_source: Literal["station", "manual", "simplified"] = "station"
    manual_justification: str = Field(default="", max_length=300)
    roof_width: FiniteFloat = Field(gt=0, le=500)
    roof_rise: FiniteFloat = Field(ge=0, le=200)
    roof_surface: Literal["horizontal", "inclined"] = "inclined"
    gutter_length: FiniteFloat = Field(gt=0, le=500)
    outlet_type: Literal["extremidade", "central"] = "extremidade"
    extension_side_a: FiniteFloat | None = Field(default=None, gt=0, le=500)
    extension_side_b: FiniteFloat | None = Field(default=None, gt=0, le=500)
    profile: Literal["rectangular", "semicircular", "trapezoidal"]
    bottom_width_mm: FiniteFloat = Field(gt=0, le=5000)
    top_width_mm: FiniteFloat = Field(gt=0, le=5000)
    useful_depth_mm: FiniteFloat = Field(gt=0, le=5000)
    freeboard_mm: FiniteFloat = Field(ge=0, le=1000)
    constructive_step_mm: FiniteFloat = Field(default=10, ge=1, le=500)
    slope_percent: FiniteFloat = Field(gt=0, le=100)
    roughness: FiniteFloat = Field(gt=0, le=0.1)
    curve_condition: Literal[
        "none",
        "straight-under-2m",
        "straight-2-to-4m",
        "rounded-under-2m",
        "rounded-2-to-4m",
    ] = "none"
    gutter_type: Literal["beiral_platibanda", "agua_furtada"] = "beiral_platibanda"
    project: ProjectInput = Field(default_factory=ProjectInput)

    @field_validator("manual_justification")
    @classmethod
    def require_manual_justification(cls, value: str, info):
        if info.data.get("rainfall_source") == "manual" and not value.strip():
            raise ValueError("informe a justificativa para a intensidade manual")
        return value

    @field_validator("roughness")
    @classmethod
    def validate_roughness(cls, value: float) -> float:
        if not any(math.isclose(value, allowed) for allowed, _ in MATERIALS.values()):
            raise ValueError("selecione um material da Tabela 2")
        return value

    @model_validator(mode="after")
    def validate_profile_dimensions(self):
        if self.profile == "trapezoidal" and self.top_width_mm < self.bottom_width_mm:
            raise ValueError("a largura na lâmina deve ser maior ou igual à largura da base")
        if self.rainfall_source == "station" and self.station_id is None:
            raise ValueError("selecione um posto pluviométrico")
        if self.outlet_type == "central" and (self.extension_side_a is None or self.extension_side_b is None):
            raise ValueError("saída central exige as extensões dos lados A e B")
        if self.rainfall_source == "manual" and self.intensity is None:
            raise ValueError("informe a intensidade manual")
        if self.rainfall_source == "simplified" and self.intensity not in (None, 150):
            raise ValueError("a intensidade simplificada é fixa em 150 mm/h")
        return self


def _number(value: float, digits: int = 3) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def _curve_configuration(condition: str) -> tuple[CornerType | None, float | None]:
    return {
        "none": (None, None),
        "straight-under-2m": (CornerType.STRAIGHT, 1.0),
        "straight-2-to-4m": (CornerType.STRAIGHT, 3.0),
        "rounded-under-2m": (CornerType.ROUNDED, 1.0),
        "rounded-2-to-4m": (CornerType.ROUNDED, 3.0),
    }[condition]


def _resolve_rainfall(data: CalculationInput) -> tuple[float, dict | None, int | None]:
    if data.rainfall_source == "simplified":
        return 150.0, None, None
    if data.rainfall_source == "manual":
        if data.intensity is None:
            raise ValueError("informe a intensidade manual")
        return data.intensity, None, None

    station = next((item for item in STATIONS if item["id"] == data.station_id), None)
    if station is None:
        raise ValueError("o posto pluviométrico selecionado não existe na Tabela 5")
    intensity = station[f"i{data.return_period}"]
    if intensity is None:
        raise ValueError("não há intensidade para esse posto e período; informe um valor manual")
    actual_period = station.get("periodosReais", {}).get(str(data.return_period))
    return float(intensity), station, actual_period


def _calculate(data: CalculationInput) -> dict:
    intensity, station, actual_period = _resolve_rainfall(data)
    roof_height = data.roof_rise if data.roof_surface == "inclined" else 0.0
    area_arguments: dict[str, float] = {
        "horizontal_projection_m": data.roof_width,
        "roof_height_m": roof_height,
    }
    if data.outlet_type == "central":
        area_arguments["extension_side_a_m"] = data.extension_side_a
        area_arguments["extension_side_b_m"] = data.extension_side_b
    else:
        area_arguments["extension_m"] = data.gutter_length
    area = calculate_contribution_area(**area_arguments)
    base_flow = calculate_project_flow(intensity, area)
    corner, distance = _curve_configuration(data.curve_condition)
    curve_factor = get_curve_factor(corner, distance)
    project_flow = base_flow * curve_factor
    section_type = {
        "rectangular": SectionType.RECTANGULAR,
        "semicircular": SectionType.SEMICIRCULAR,
        "trapezoidal": SectionType.TRAPEZOIDAL,
    }[data.profile]
    depth_m = data.useful_depth_mm / 1000
    bottom_width_m = data.bottom_width_mm / 1000

    if section_type is SectionType.SEMICIRCULAR:
        section = calculate_section(section_type, diameter_m=bottom_width_m)
    elif section_type is SectionType.TRAPEZOIDAL:
        top_width_m = data.top_width_mm / 1000
        side_slope_z = (top_width_m - bottom_width_m) / (2 * depth_m)
        section = calculate_section(
            section_type,
            bottom_width_m=bottom_width_m,
            water_depth_m=depth_m,
            side_slope_z=side_slope_z,
        )
    else:
        section = calculate_section(
            section_type,
            bottom_width_m=bottom_width_m,
            water_depth_m=depth_m,
        )

    gutter_type = GutterType(data.gutter_type)
    verification = verify_gutter(
        project_flow_l_min=project_flow,
        section=section,
        roughness=data.roughness,
        slope_percent=data.slope_percent,
        gutter_type=gutter_type,
    )
    capacity = calculate_capacity(section, data.roughness, data.slope_percent)
    status = "ATENDE" if verification.meets_standard else "NAO_ATENDE"
    station_name = station["nome"] if station else data.station_name
    station_uf = station["uf"] if station else data.station_uf
    intensity_description = (
        f"{station_name}/{station_uf}: I = {_number(intensity, 1)} mm/h para T = {data.return_period} anos"
        if station
        else f"I = {_number(intensity, 1)} mm/h; valor manual informado pelo projetista"
        if data.rainfall_source == "manual"
        else f"I = {_number(intensity, 1)} mm/h; valor simplificado (item 5.1.4)"
    )
    if data.roof_surface == "horizontal":
        formula_area = "A = a · b"
        area_substitution = f"A = {_number(data.roof_width)} · "
    else:
        formula_area = "A = (a + h/2) · b"
        area_substitution = f"A = ({_number(data.roof_width)} + {_number(roof_height)}/2) · "
    if data.outlet_type == "central":
        formula_area += "; saída central usa o maior lado"
        area_substitution += (
            f"max({_number(data.extension_side_a or 0)}, {_number(data.extension_side_b or 0)})"
        )
    else:
        area_substitution += _number(data.gutter_length)
    if section_type is SectionType.RECTANGULAR:
        section_formula = "S = b · h; P = b + 2h"
        section_latex = r"S=b\cdot h;\quad P=b+2h"
        section_substitution = (
            f"S = {_number(bottom_width_m)} · {_number(section.water_depth_m)} = "
            f"{_number(section.wet_area_m2, 5)} m²; P = {_number(bottom_width_m)} + "
            f"2·{_number(section.water_depth_m)} = {_number(section.wet_perimeter_m, 5)} m"
        )
    elif section_type is SectionType.TRAPEZOIDAL:
        section_formula = "S = (b + z · h) · h; P = b + 2h · √(1 + z²)"
        section_latex = r"S=(b+z\cdot h)h;\quad P=b+2h\sqrt{1+z^2}"
        section_substitution = (
            f"z = {_number(section.side_slope_z or 0)}; S = {_number(section.wet_area_m2, 5)} m²; "
            f"P = {_number(section.wet_perimeter_m, 5)} m"
        )
    else:
        section_formula = "S = πD²/8; P = πD/2; lâmina = D/2"
        section_latex = r"S=\pi D^2/8;\quad P=\pi D/2;\quad h=D/2"
        section_substitution = (
            f"D = {_number(section.diameter_m or 0)} m; lâmina = "
            f"{_number(section.water_depth_m)} m; S = {_number(section.wet_area_m2, 5)} m²; "
            f"P = {_number(section.wet_perimeter_m, 5)} m"
        )
    area_formula_latex = (
        r"A=a\cdot " if data.roof_surface == "horizontal" else r"A=(a+h/2)\cdot "
    )
    if data.outlet_type == "central":
        area_formula_latex += r"\max(L_A,L_B)"
    else:
        area_formula_latex += "b"
    steps = [
        {
            "ordem": 1,
            "titulo": "Intensidade pluviométrica",
            "referenciaNorma": "5.1.2 a 5.1.4",
            "formulaLatex": r"I = I_{fonte}",
            "formulaTexto": "I conforme fonte selecionada; duração de chuva = 5 min",
            "substituicao": intensity_description,
            "resultado": intensity,
            "unidade": "mm/h",
        },
        {
            "ordem": 2,
            "titulo": "Área de contribuição por saída",
            "referenciaNorma": "5.1.5 / Fig. 1; 5.2 / Fig. 2(a) ou 2(b); 5.5.4",
            "formulaLatex": area_formula_latex,
            "formulaTexto": formula_area,
            "substituicao": area_substitution,
            "resultado": area,
            "unidade": "m²",
        },
        {
            "ordem": 3,
            "titulo": "Vazão de projeto sem correção",
            "referenciaNorma": "5.3.1",
            "formulaLatex": r"Q = I\cdot A/60",
            "formulaTexto": "Q = I · A / 60",
            "substituicao": f"Q = {_number(intensity, 1)} · {_number(area)} / 60",
            "resultado": base_flow,
            "unidade": "L/min",
        },
        {
            "ordem": 4,
            "titulo": "Correção por mudança de direção",
            "referenciaNorma": "5.5.6 / Tabela 1",
            "formulaLatex": r"Q_{proj} = Q\cdot C",
            "formulaTexto": "Qproj = Q · coeficiente da Tabela 1",
            "substituicao": f"Qproj = {_number(base_flow)} · {_number(curve_factor, 2)}",
            "resultado": project_flow,
            "unidade": "L/min",
        },
        {
            "ordem": 5,
            "titulo": "Geometria da seção molhada",
            "referenciaNorma": "5.5.7",
            "formulaLatex": section_latex + r";\quad R_H=S/P",
            "formulaTexto": section_formula + "; RH = S/P",
            "substituicao": f"{section_substitution}; RH = {_number(section.wet_area_m2, 5)} / {_number(section.wet_perimeter_m, 5)} = {_number(section.hydraulic_radius_m, 5)} m",
            "resultado": section.hydraulic_radius_m,
            "unidade": "m",
        },
        {
            "ordem": 6,
            "titulo": "Capacidade pelo método de Manning-Strickler",
            "referenciaNorma": "5.5.7",
            "formulaLatex": r"Q_{cap}=60000\cdot(S/n)\cdot R_H^{2/3}\cdot i^{1/2}",
            "formulaTexto": "Qcap = 60.000 · (S/n) · RH^(2/3) · i^(1/2)",
            "substituicao": (
                f"Qcap = 60.000 · ({_number(section.wet_area_m2, 5)}/{data.roughness}) · "
                f"{_number(section.hydraulic_radius_m, 5)}^(2/3) · "
                f"({data.slope_percent}/100)^(1/2)"
            ),
            "resultado": capacity.flow_l_min,
            "unidade": "L/min",
        },
        {
            "ordem": 7,
            "titulo": "Verificação da capacidade",
            "referenciaNorma": "5.5.7",
            "formulaLatex": r"\text{atende se }Q_{cap}\ge Q_{proj}",
            "formulaTexto": "Atende quando Qcap ≥ Qproj",
            "substituicao": f"{_number(capacity.flow_l_min)} ≥ {_number(project_flow)}",
            "resultado": status,
            "unidade": "",
        },
        {
            "ordem": 8,
            "titulo": "Taxa de atendimento e velocidade",
            "referenciaNorma": "Informação hidráulica; sem limite normativo de velocidade",
            "formulaLatex": r"\text{taxa}=Q_{cap}/Q_{proj};\quad v=Q_{cap}/(60000S)",
            "formulaTexto": "Taxa = Qcap/Qproj; velocidade = Qcap/(60.000 · S)",
            "substituicao": f"Taxa = {_number(verification.service_ratio)}; v = {_number(capacity.velocity_m_s)}",
            "resultado": verification.service_ratio,
            "unidade": "vezes; m/s",
        },
    ]
    alerts = [
        {
            "codigo": alert.code,
            "nivel": alert.level.value,
            "mensagem": alert.message,
            "referenciaNorma": alert.standard_reference,
        }
        for alert in verification.alerts
    ]
    if data.rainfall_source == "simplified" and area > 100:
        alerts.append({
            "codigo": "AREA_ACIMA_SIMPLIFICADO",
            "nivel": "aviso",
            "mensagem": "O valor simplificado de 150 mm/h aplica-se até 100 m², salvo casos especiais.",
            "referenciaNorma": "5.1.4",
        })
    if data.rainfall_source == "manual":
        alerts.append({
            "codigo": "INTENSIDADE_MANUAL",
            "nivel": "info",
            "mensagem": f"Justificativa registrada: {data.manual_justification}",
            "referenciaNorma": "Tabela 5, nota a",
        })
    if actual_period is not None and actual_period != data.return_period:
        alerts.append({
            "codigo": "PERIODO_REAL_DIVERGENTE",
            "nivel": "aviso",
            "mensagem": f"A Tabela 5 informa período real de {actual_period} anos para este valor.",
            "referenciaNorma": "Tabela 5, nota b",
        })
    if station and station["i25"] is not None and station["i5"] is not None and station["i25"] < station["i5"]:
        alerts.append({
            "codigo": "TABELA5_REVISAO_HUMANA",
            "nivel": "aviso",
            "mensagem": f"I(25) < I(5) em {station['nome']}/{station['uf']}; valor preservado conforme PDF para revisão humana.",
            "referenciaNorma": "Tabela 5",
        })

    input_summary = {
        **data.model_dump(exclude={"project", "station_name", "station_uf", "intensity"}),
        "station_name": station_name,
        "station_uf": station_uf,
        "intensity": intensity,
        "actual_return_period": actual_period,
        "project": data.project.model_dump(),
        "material": next(
            (label for roughness, label in MATERIALS.values() if math.isclose(roughness, data.roughness)),
            "Material informado pelo projetista",
        ),
        "total_height_mm": section.water_depth_m * 1000 + data.freeboard_mm,
    }
    premises = [
        {"origem": "da norma", "descricao": "Duração da precipitação adotada: 5 min (item 5.1.3)."},
        {"origem": "da norma", "descricao": f"Período de retorno selecionado: {data.return_period} ano(s) (item 5.1.2)."},
        {"origem": "da norma", "descricao": "Vento: incremento h/2 na projeção horizontal para cobertura inclinada (item 5.1.5 / Fig. 1)."},
        {"origem": "da norma", "descricao": "Bordo livre não possui valor numérico prescrito; foi informado pelo projetista."},
        {"origem": "adotado pelo projetista", "descricao": f"Bordo livre: {data.freeboard_mm} mm."},
        {"origem": "da norma", "descricao": "Para saída central, usa-se a maior área dos lados (item 5.5.4)."},
        {"origem": "da norma", "descricao": "Velocidade é informativa; a norma não fixa limite de aprovação."},
    ]
    if section_type is SectionType.SEMICIRCULAR:
        premises.append({"origem": "da norma", "descricao": "Para seção semicircular, a lâmina foi fixada em D/2 (Tabela 3)."})
    else:
        premises.append({"origem": "adotado pelo projetista", "descricao": f"Lâmina útil informada: {data.useful_depth_mm} mm."})
    if data.rainfall_source == "manual":
        premises.append({"origem": "adotado pelo projetista", "descricao": f"Intensidade manual: {data.manual_justification}."})

    memorial = {
        "projeto": data.project.model_dump(),
        "norma": "ABNT NBR 10844:1989",
        "versaoMotor": ENGINE_VERSION,
        "geradoEm": datetime.now(timezone.utc).isoformat(),
        "entradas": input_summary,
        "premissas": premises,
        "passos": steps,
        "resultados": {
            "status": status,
            "taxaAtendimento": verification.service_ratio,
            "intensidadeMmH": intensity,
            "areaContribuicaoM2": area,
            "coeficienteTabela1": curve_factor,
            "vazaoBaseLmin": base_flow,
            "vazaoProjetoLmin": project_flow,
            "areaMolhadaM2": section.wet_area_m2,
            "perimetroMolhadoM": section.wet_perimeter_m,
            "raioHidraulicoM": section.hydraulic_radius_m,
            "vazaoCapacidadeLmin": capacity.flow_l_min,
            "velocidadeMs": capacity.velocity_m_s,
            "alturaTotalMm": section.water_depth_m * 1000 + data.freeboard_mm,
        },
        "alertas": alerts,
        "conclusao": (
            "A seção atende aos critérios da NBR 10844 para as entradas e premissas registradas."
            if status == "ATENDE"
            else "A seção não atende aos critérios da NBR 10844 para as entradas e premissas registradas."
        ),
    }
    return {
        "status": status,
        "taxaAtendimento": verification.service_ratio,
        "resultados": memorial["resultados"],
        "alertas": alerts,
        "passos": steps,
        "metadados": {
            "norma": memorial["norma"],
            "versaoMotor": ENGINE_VERSION,
            "geradoEm": memorial["geradoEm"],
        },
        "memorial": memorial,
    }


@router.get("/materiais")
def get_materials():
    return [
        {"id": material_id, "n": roughness, "nome": label}
        for material_id, (roughness, label) in MATERIALS.items()
    ]


@router.get("/localidades")
def search_stations(
    q: str = Query(default="", max_length=120),
    uf: str | None = Query(default=None, min_length=2, max_length=2),
):
    normalized_query = q.casefold().strip()
    normalized_uf = uf.upper() if uf else None
    return [
        {"id": station["id"], "nome": station["nome"], "uf": station["uf"]}
        for station in STATIONS
        if (not normalized_query or normalized_query in station["nome"].casefold())
        and (not normalized_uf or station["uf"] == normalized_uf)
    ]


@router.get("/localidades/{station_id}/intensidade")
def get_station_intensity(
    station_id: int,
    periodo_retorno: Literal[1, 5, 25] = Query(alias="periodoRetorno"),
):
    station = next((item for item in STATIONS if item["id"] == station_id), None)
    if station is None:
        raise HTTPException(
            status_code=404,
            detail={"codigo": "POSTO_NAO_ENCONTRADO", "mensagem": "Posto não encontrado na Tabela 5."},
        )
    intensity = station[f"i{periodo_retorno}"]
    if intensity is None:
        raise HTTPException(
            status_code=422,
            detail={"codigo": "DADO_INDISPONIVEL", "mensagem": "Não há intensidade para esse posto e período; informe um valor manual."},
        )
    actual_period = station.get("periodosReais", {}).get(str(periodo_retorno))
    return {
        "localidade": {"id": station["id"], "nome": station["nome"], "uf": station["uf"]},
        "periodoRetornoAnos": periodo_retorno,
        "intensidadeMmH": intensity,
        "periodoRealAnos": actual_period,
        "alerta": (
            f"O valor corresponde ao período real de {actual_period} anos, não a {periodo_retorno} anos."
            if actual_period is not None and actual_period != periodo_retorno
            else None
        ),
    }


@router.get("/tabela5")
def get_table5():
    return TABLE5


@router.post("/calhas/verificar")
def verify_gutter_endpoint(data: CalculationInput):
    try:
        return _calculate(data)
    except ValueError as error:
        raise HTTPException(status_code=422, detail={"codigo": "REGRA_DOMINIO", "mensagem": str(error)}) from error


@router.post("/calhas/dimensionar")
def dimension_gutter_endpoint(data: CalculationInput):
    try:
        baseline = _calculate(data)
        section_type = {
            "rectangular": SectionType.RECTANGULAR,
            "semicircular": SectionType.SEMICIRCULAR,
            "trapezoidal": SectionType.TRAPEZOIDAL,
        }[data.profile]
        bottom_width_m = data.bottom_width_mm / 1000
        depth_m = data.useful_depth_mm / 1000
        side_slope_z = (
            (data.top_width_mm / 1000 - bottom_width_m) / (2 * depth_m)
            if section_type is SectionType.TRAPEZOIDAL
            else None
        )
        dimension = dimension_gutter(
            project_flow_l_min=baseline["resultados"]["vazaoProjetoLmin"],
            section_type=section_type,
            roughness=data.roughness,
            slope_percent=data.slope_percent,
            fixed_bottom_width_m=None if section_type is SectionType.SEMICIRCULAR else bottom_width_m,
            side_slope_z=side_slope_z,
            constructive_step_m=data.constructive_step_mm / 1000,
        )
        updated = data.model_copy(update={
            "bottom_width_mm": dimension.diameter_m * 1000 if dimension.diameter_m is not None else data.bottom_width_mm,
            "useful_depth_mm": dimension.water_depth_m * 1000,
            "top_width_mm": (
                data.bottom_width_mm + 2 * (side_slope_z or 0) * dimension.water_depth_m * 1000
                if section_type is SectionType.TRAPEZOIDAL
                else data.top_width_mm
            ),
        })
        result = _calculate(updated)
    except ValueError as error:
        raise HTTPException(status_code=422, detail={"codigo": "REGRA_DOMINIO", "mensagem": str(error)}) from error

    dimension_summary = {
        "status": "ATENDE" if dimension.meets_standard and result["status"] == "ATENDE" else "NAO_ATENDE",
        "secao": data.profile,
        "larguraFundoMm": dimension.bottom_width_m * 1000 if dimension.bottom_width_m is not None else None,
        "taludeZ": dimension.side_slope_z,
        "diametroMm": dimension.diameter_m * 1000 if dimension.diameter_m is not None else None,
        "laminaMm": dimension.water_depth_m * 1000,
        "passoConstrutivoMm": data.constructive_step_mm,
        "capacidadeLmin": dimension.capacity_l_min,
        "mensagem": dimension.message,
    }
    result["status"] = dimension_summary["status"]
    result["dimensionamento"] = dimension_summary
    result["resultados"]["status"] = dimension_summary["status"]
    result["memorial"]["resultados"]["status"] = dimension_summary["status"]
    result["memorial"]["dimensionamento"] = dimension_summary
    result["memorial"]["passos"].append({
        "ordem": len(result["passos"]) + 1,
        "titulo": "Dimensionamento mínimo",
        "referenciaNorma": "Procedimento numérico de dimensionamento; Tabela 3 para calha semicircular",
        "formulaLatex": r"h=\min\{h:Q_{cap}(h)\ge Q_{proj}\}",
        "formulaTexto": "Menor dimensão construtiva cuja capacidade atende à vazão de projeto",
        "substituicao": dimension_summary["mensagem"] or f"Dimensão adotada: {dimension_summary['laminaMm']} mm",
        "resultado": dimension_summary["laminaMm"],
        "unidade": "mm de lâmina",
    })
    result["passos"] = result["memorial"]["passos"]
    if dimension.message:
        result["alertas"].append({
            "codigo": "DIMENSIONAMENTO_NAO_ATENDE",
            "nivel": "erro",
            "mensagem": dimension.message,
            "referenciaNorma": "Tabela 3",
        })
        result["memorial"]["alertas"] = result["alertas"]
        result["memorial"]["conclusao"] = "A calha não atende aos critérios da NBR 10844 com as dimensões máximas disponíveis na Tabela 3."
    else:
        result["memorial"]["conclusao"] = "A dimensão mínima calculada atende aos critérios da NBR 10844 para as entradas e premissas registradas."
    return result


@router.post("/memorial")
def memorial_endpoint(
    data: CalculationInput,
    formato: Literal["json", "docx"] = Query(default="json"),
    tipo: Literal["verificar", "dimensionar"] = Query(default="verificar"),
):
    try:
        result = (
            dimension_gutter_endpoint(data)
            if tipo == "dimensionar"
            else _calculate(data)
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail={"codigo": "REGRA_DOMINIO", "mensagem": str(error)}) from error
    if formato == "json":
        return result["memorial"]
    document = build_docx(result["memorial"])
    filename = "memorial-calculo-nbr10844.docx"
    return Response(
        content=document,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )