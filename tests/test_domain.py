from __future__ import annotations

import math

import pytest

from app.domain.calculator import (
    calculate_capacity,
    calculate_contribution_area,
    calculate_project_flow,
    calculate_section,
    dimension_gutter,
    get_curve_factor,
    verify_gutter,
)
from app.domain.models import (
    AlertLevel,
    CornerType,
    GutterType,
    SectionType,
)


@pytest.mark.parametrize(
    ("diameter_mm", "slope_percent", "expected_l_min"),
    [
        (100, 0.5, 130),
        (100, 1.0, 183),
        (100, 2.0, 256),
        (125, 0.5, 236),
        (125, 1.0, 333),
        (125, 2.0, 466),
        (150, 0.5, 384),
        (150, 1.0, 541),
        (150, 2.0, 757),
        (200, 0.5, 829),
        (200, 1.0, 1167),
        (200, 2.0, 1634),
    ],
)
def test_semicircular_capacity_matches_table_3(
    diameter_mm: float,
    slope_percent: float,
    expected_l_min: float,
) -> None:
    section = calculate_section(
        SectionType.SEMICIRCULAR,
        diameter_m=diameter_mm / 1000,
    )

    capacity = calculate_capacity(section, roughness=0.011, slope_percent=slope_percent)

    assert capacity.flow_l_min == pytest.approx(expected_l_min, rel=0.01)


def test_prototype_case_aracaju() -> None:
    area = calculate_contribution_area(
        horizontal_projection_m=10,
        roof_height_m=2.5,
        extension_m=12,
    )
    flow = calculate_project_flow(122, area)
    section = calculate_section(
        SectionType.RECTANGULAR,
        bottom_width_m=0.18,
        water_depth_m=0.10,
    )
    capacity = calculate_capacity(section, roughness=0.011, slope_percent=1)

    assert area == pytest.approx(135.0)
    assert flow == pytest.approx(274.5)
    assert section.wet_area_m2 == pytest.approx(0.018)
    assert section.wet_perimeter_m == pytest.approx(0.38)
    assert section.hydraulic_radius_m == pytest.approx(0.047368, rel=1e-5)
    assert capacity.flow_l_min == pytest.approx(1285.4, rel=0.01)
    assert capacity.velocity_m_s == pytest.approx(1.19, rel=0.01)


def test_central_outlet_uses_larger_side() -> None:
    area = calculate_contribution_area(
        horizontal_projection_m=10,
        roof_height_m=2,
        extension_side_a_m=4,
        extension_side_b_m=7,
    )

    assert area == pytest.approx(77.0)


@pytest.mark.parametrize(
    ("corner_type", "distance_m", "expected"),
    [
        (CornerType.STRAIGHT, 1.5, 1.2),
        (CornerType.STRAIGHT, 3.0, 1.1),
        (CornerType.ROUNDED, 1.5, 1.1),
        (CornerType.ROUNDED, 3.0, 1.05),
        (CornerType.STRAIGHT, 4.0, 1.0),
    ],
)
def test_table_1_curve_factors(
    corner_type: CornerType,
    distance_m: float,
    expected: float,
) -> None:
    assert get_curve_factor(corner_type, distance_m) == expected


def test_opposed_vertical_surfaces_use_authorized_absolute_difference() -> None:
    area = calculate_contribution_area(
        horizontal_projection_m=5,
        roof_height_m=0,
        extension_m=8,
        opposed_vertical_areas_m2=(12, 20),
    )

    assert area == pytest.approx(44.0)


def test_low_slope_fails_eaves_but_warns_valley() -> None:
    section = calculate_section(
        SectionType.RECTANGULAR,
        bottom_width_m=0.18,
        water_depth_m=0.10,
    )

    eaves = verify_gutter(
        project_flow_l_min=100,
        section=section,
        roughness=0.011,
        slope_percent=0.4,
        gutter_type=GutterType.EAVES_OR_PARAPET,
    )
    valley = verify_gutter(
        project_flow_l_min=100,
        section=section,
        roughness=0.011,
        slope_percent=0.4,
        gutter_type=GutterType.VALLEY,
    )

    assert not eaves.meets_standard
    assert eaves.alerts[0].level is AlertLevel.ERROR
    assert valley.meets_standard
    assert valley.alerts[0].level is AlertLevel.WARNING


@pytest.mark.parametrize("section_type", [SectionType.RECTANGULAR, SectionType.TRAPEZOIDAL])
def test_dimensioned_depth_is_minimal_at_constructive_step(section_type: SectionType) -> None:
    result = dimension_gutter(
        project_flow_l_min=900,
        section_type=section_type,
        fixed_bottom_width_m=0.18,
        side_slope_z=0.5 if section_type is SectionType.TRAPEZOIDAL else None,
        roughness=0.011,
        slope_percent=1,
        constructive_step_m=0.01,
    )
    previous_depth = result.water_depth_m - 0.01
    previous_section = calculate_section(
        section_type,
        bottom_width_m=0.18,
        water_depth_m=previous_depth,
        side_slope_z=0.5 if section_type is SectionType.TRAPEZOIDAL else None,
    )
    previous_capacity = calculate_capacity(previous_section, 0.011, 1)

    assert result.meets_standard
    assert result.capacity_l_min >= 900
    assert previous_capacity.flow_l_min < 900
    assert math.isclose(result.water_depth_m / 0.01, round(result.water_depth_m / 0.01))


def test_semicircular_dimensioning_chooses_smallest_standard_diameter() -> None:
    result = dimension_gutter(
        project_flow_l_min=300,
        section_type=SectionType.SEMICIRCULAR,
        fixed_bottom_width_m=None,
        roughness=0.011,
        slope_percent=1,
    )

    assert result.meets_standard
    assert result.diameter_m == pytest.approx(0.125)