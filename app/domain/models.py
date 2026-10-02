from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SectionType(StrEnum):
    RECTANGULAR = "retangular"
    TRAPEZOIDAL = "trapezoidal"
    SEMICIRCULAR = "semicircular"


class GutterType(StrEnum):
    EAVES_OR_PARAPET = "beiral_platibanda"
    VALLEY = "agua_furtada"


class CornerType(StrEnum):
    STRAIGHT = "reto"
    ROUNDED = "arredondado"


class AlertLevel(StrEnum):
    ERROR = "erro"
    WARNING = "aviso"
    INFO = "info"


@dataclass(frozen=True, slots=True)
class HydraulicSection:
    section_type: SectionType
    wet_area_m2: float
    wet_perimeter_m: float
    hydraulic_radius_m: float
    water_depth_m: float
    bottom_width_m: float | None = None
    side_slope_z: float | None = None
    diameter_m: float | None = None


@dataclass(frozen=True, slots=True)
class HydraulicCapacity:
    flow_l_min: float
    velocity_m_s: float


@dataclass(frozen=True, slots=True)
class DomainAlert:
    code: str
    level: AlertLevel
    message: str
    standard_reference: str


@dataclass(frozen=True, slots=True)
class VerificationResult:
    meets_standard: bool
    service_ratio: float
    capacity_l_min: float
    velocity_m_s: float
    alerts: tuple[DomainAlert, ...]


@dataclass(frozen=True, slots=True)
class DimensioningResult:
    meets_standard: bool
    section_type: SectionType
    capacity_l_min: float
    water_depth_m: float
    bottom_width_m: float | None = None
    side_slope_z: float | None = None
    diameter_m: float | None = None
    message: str | None = None