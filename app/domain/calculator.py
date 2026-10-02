from __future__ import annotations

import math

from app.domain.models import (
    AlertLevel,
    CornerType,
    DimensioningResult,
    DomainAlert,
    GutterType,
    HydraulicCapacity,
    HydraulicSection,
    SectionType,
    VerificationResult,
)

MANNING_STRICKLER_K = 60_000.0
MINIMUM_EAVES_SLOPE_PERCENT = 0.5
STANDARD_SEMICIRCULAR_DIAMETERS_M = (0.100, 0.125, 0.150, 0.200)


def _require_positive(name: str, value: float) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} deve ser um número finito e positivo")


def _require_non_negative(name: str, value: float) -> None:
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} deve ser um número finito e não negativo")


def calculate_contribution_area(
    *,
    horizontal_projection_m: float,
    roof_height_m: float,
    extension_m: float | None = None,
    extension_side_a_m: float | None = None,
    extension_side_b_m: float | None = None,
    vertical_half_areas_m2: tuple[float, ...] = (),
    opposed_vertical_areas_m2: tuple[float, float] | None = None,
    adjacent_vertical_areas_m2: tuple[float, float] | None = None,
) -> float:
    """Calcula a área da Figura 2, usando o maior lado para saída central."""
    _require_positive("projeção horizontal", horizontal_projection_m)
    _require_non_negative("altura da cobertura", roof_height_m)

    if extension_m is not None:
        _require_positive("extensão", extension_m)
        effective_extension = extension_m
    elif extension_side_a_m is not None and extension_side_b_m is not None:
        _require_positive("extensão do lado A", extension_side_a_m)
        _require_positive("extensão do lado B", extension_side_b_m)
        effective_extension = max(extension_side_a_m, extension_side_b_m)
    else:
        raise ValueError("informe a extensão ou os dois lados da saída central")

    area = (horizontal_projection_m + roof_height_m / 2) * effective_extension

    for vertical_area in vertical_half_areas_m2:
        _require_non_negative("área vertical", vertical_area)
        area += vertical_area / 2

    if opposed_vertical_areas_m2 is not None:
        area_a, area_b = opposed_vertical_areas_m2
        _require_non_negative("área vertical oposta A", area_a)
        _require_non_negative("área vertical oposta B", area_b)
        area += abs(area_a - area_b) / 2

    if adjacent_vertical_areas_m2 is not None:
        area_a, area_b = adjacent_vertical_areas_m2
        _require_non_negative("área vertical adjacente A", area_a)
        _require_non_negative("área vertical adjacente B", area_b)
        area += math.hypot(area_a, area_b) / 2

    return area


def calculate_project_flow(intensity_mm_h: float, contribution_area_m2: float) -> float:
    _require_positive("intensidade pluviométrica", intensity_mm_h)
    _require_positive("área de contribuição", contribution_area_m2)
    return intensity_mm_h * contribution_area_m2 / 60


def get_curve_factor(corner_type: CornerType | None, distance_m: float | None) -> float:
    if corner_type is None or distance_m is None or distance_m >= 4:
        return 1.0
    _require_non_negative("distância da mudança de direção", distance_m)
    if corner_type is CornerType.STRAIGHT:
        return 1.2 if distance_m < 2 else 1.1
    return 1.1 if distance_m < 2 else 1.05


def calculate_section(
    section_type: SectionType,
    *,
    bottom_width_m: float | None = None,
    water_depth_m: float | None = None,
    side_slope_z: float | None = None,
    diameter_m: float | None = None,
) -> HydraulicSection:
    if section_type is SectionType.SEMICIRCULAR:
        if diameter_m is None:
            raise ValueError("diâmetro é obrigatório para seção semicircular")
        _require_positive("diâmetro", diameter_m)
        wet_area = math.pi * diameter_m**2 / 8
        wet_perimeter = math.pi * diameter_m / 2
        return HydraulicSection(
            section_type=section_type,
            wet_area_m2=wet_area,
            wet_perimeter_m=wet_perimeter,
            hydraulic_radius_m=wet_area / wet_perimeter,
            water_depth_m=diameter_m / 2,
            diameter_m=diameter_m,
        )

    if bottom_width_m is None or water_depth_m is None:
        raise ValueError("largura de fundo e lâmina são obrigatórias")
    _require_positive("largura de fundo", bottom_width_m)
    _require_positive("lâmina", water_depth_m)

    if section_type is SectionType.RECTANGULAR:
        wet_area = bottom_width_m * water_depth_m
        wet_perimeter = bottom_width_m + 2 * water_depth_m
        return HydraulicSection(
            section_type=section_type,
            wet_area_m2=wet_area,
            wet_perimeter_m=wet_perimeter,
            hydraulic_radius_m=wet_area / wet_perimeter,
            water_depth_m=water_depth_m,
            bottom_width_m=bottom_width_m,
        )

    if side_slope_z is None:
        raise ValueError("talude z é obrigatório para seção trapezoidal")
    _require_non_negative("talude z", side_slope_z)
    wet_area = (bottom_width_m + side_slope_z * water_depth_m) * water_depth_m
    wet_perimeter = bottom_width_m + 2 * water_depth_m * math.sqrt(1 + side_slope_z**2)
    return HydraulicSection(
        section_type=section_type,
        wet_area_m2=wet_area,
        wet_perimeter_m=wet_perimeter,
        hydraulic_radius_m=wet_area / wet_perimeter,
        water_depth_m=water_depth_m,
        bottom_width_m=bottom_width_m,
        side_slope_z=side_slope_z,
    )


def calculate_capacity(
    section: HydraulicSection,
    roughness: float,
    slope_percent: float,
) -> HydraulicCapacity:
    _require_positive("rugosidade", roughness)
    _require_positive("declividade", slope_percent)
    slope_m_m = slope_percent / 100
    velocity = (
        section.hydraulic_radius_m ** (2 / 3)
        * math.sqrt(slope_m_m)
        / roughness
    )
    return HydraulicCapacity(
        flow_l_min=MANNING_STRICKLER_K * section.wet_area_m2 * velocity,
        velocity_m_s=velocity,
    )


def verify_gutter(
    *,
    project_flow_l_min: float,
    section: HydraulicSection,
    roughness: float,
    slope_percent: float,
    gutter_type: GutterType,
) -> VerificationResult:
    _require_positive("vazão de projeto", project_flow_l_min)
    capacity = calculate_capacity(section, roughness, slope_percent)
    alerts: list[DomainAlert] = []

    slope_violation = slope_percent < MINIMUM_EAVES_SLOPE_PERCENT
    if slope_violation:
        is_error = gutter_type is GutterType.EAVES_OR_PARAPET
        alerts.append(
            DomainAlert(
                code="DECLIVIDADE_MINIMA",
                level=AlertLevel.ERROR if is_error else AlertLevel.WARNING,
                message=(
                    "Calhas de beiral e platibanda exigem declividade mínima de 0,5%."
                    if is_error
                    else "A declividade da calha de água-furtada segue o projeto da cobertura."
                ),
                standard_reference="5.5.2" if is_error else "5.5.3",
            )
        )

    hydraulically_sufficient = capacity.flow_l_min >= project_flow_l_min
    has_error = any(alert.level is AlertLevel.ERROR for alert in alerts)
    return VerificationResult(
        meets_standard=hydraulically_sufficient and not has_error,
        service_ratio=capacity.flow_l_min / project_flow_l_min,
        capacity_l_min=capacity.flow_l_min,
        velocity_m_s=capacity.velocity_m_s,
        alerts=tuple(alerts),
    )


def dimension_gutter(
    *,
    project_flow_l_min: float,
    section_type: SectionType,
    roughness: float,
    slope_percent: float,
    fixed_bottom_width_m: float | None,
    side_slope_z: float | None = None,
    constructive_step_m: float = 0.01,
) -> DimensioningResult:
    _require_positive("vazão de projeto", project_flow_l_min)
    _require_positive("passo construtivo", constructive_step_m)

    if section_type is SectionType.SEMICIRCULAR:
        for diameter_m in STANDARD_SEMICIRCULAR_DIAMETERS_M:
            section = calculate_section(section_type, diameter_m=diameter_m)
            capacity = calculate_capacity(section, roughness, slope_percent)
            if capacity.flow_l_min >= project_flow_l_min:
                return DimensioningResult(
                    meets_standard=True,
                    section_type=section_type,
                    capacity_l_min=capacity.flow_l_min,
                    water_depth_m=diameter_m / 2,
                    diameter_m=diameter_m,
                )
        return DimensioningResult(
            meets_standard=False,
            section_type=section_type,
            capacity_l_min=calculate_capacity(
                calculate_section(section_type, diameter_m=STANDARD_SEMICIRCULAR_DIAMETERS_M[-1]),
                roughness,
                slope_percent,
            ).flow_l_min,
            water_depth_m=STANDARD_SEMICIRCULAR_DIAMETERS_M[-1] / 2,
            diameter_m=STANDARD_SEMICIRCULAR_DIAMETERS_M[-1],
            message="Nenhum diâmetro da Tabela 3 atende à vazão de projeto.",
        )

    if fixed_bottom_width_m is None:
        raise ValueError("largura de fundo fixa é obrigatória")
    _require_positive("largura de fundo", fixed_bottom_width_m)

    low = 0.0001
    high = constructive_step_m
    for _ in range(32):
        section = calculate_section(
            section_type,
            bottom_width_m=fixed_bottom_width_m,
            water_depth_m=high,
            side_slope_z=side_slope_z,
        )
        if calculate_capacity(section, roughness, slope_percent).flow_l_min >= project_flow_l_min:
            break
        high *= 2
    else:
        raise ValueError("não foi possível limitar a dimensão da calha")

    for _ in range(80):
        middle = (low + high) / 2
        section = calculate_section(
            section_type,
            bottom_width_m=fixed_bottom_width_m,
            water_depth_m=middle,
            side_slope_z=side_slope_z,
        )
        if calculate_capacity(section, roughness, slope_percent).flow_l_min >= project_flow_l_min:
            high = middle
        else:
            low = middle

    steps = math.ceil((high - 1e-12) / constructive_step_m)
    depth = steps * constructive_step_m
    section = calculate_section(
        section_type,
        bottom_width_m=fixed_bottom_width_m,
        water_depth_m=depth,
        side_slope_z=side_slope_z,
    )
    capacity = calculate_capacity(section, roughness, slope_percent)
    return DimensioningResult(
        meets_standard=True,
        section_type=section_type,
        capacity_l_min=capacity.flow_l_min,
        water_depth_m=depth,
        bottom_width_m=fixed_bottom_width_m,
        side_slope_z=side_slope_z,
    )