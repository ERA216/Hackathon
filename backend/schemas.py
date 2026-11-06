"""Строгие схемы ответов модели. Всё, что не проходит схему, отклоняется."""
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class FailureType(str, Enum):
    belt_break = "belt_break"
    overheating = "overheating"
    electrical = "electrical"
    sensor = "sensor"
    lubrication = "lubrication"
    bearing = "bearing"
    material = "material"
    other = "other"


FAILURE_LABELS = {
    "belt_break": "Разрыв/износ ремня",
    "overheating": "Перегрев",
    "electrical": "Электрика",
    "sensor": "Датчик",
    "lubrication": "Смазка",
    "bearing": "Подшипник",
    "material": "Сырьё/материал",
    "other": "Прочее",
}


class Part(BaseModel):
    name: str
    qty: int = Field(default=1, ge=1, le=100)


class Incident(BaseModel):
    equipment_id: Optional[str] = Field(None, description="id из справочника или null, если станок не назван")
    failure_type: FailureType
    downtime_min: Optional[int] = Field(None, ge=0, le=24 * 60)
    downtime_quote: Optional[str] = Field(None, description="Дословный фрагмент текста про длительность простоя")
    parts: list[Part] = Field(default_factory=list)
    cause: Optional[str] = None
    action: Optional[str] = None
    confidence: float = Field(ge=0, le=1)


class ExtractionResult(BaseModel):
    incidents: list[Incident] = Field(default_factory=list)
    needs_clarification: list[str] = Field(default_factory=list)


class MeasureChoice(BaseModel):
    measure_id: str
    why: str


class AdvisorChoice(BaseModel):
    measures: list[MeasureChoice] = Field(default_factory=list, max_length=3)


class Explanation(BaseModel):
    text: str


def kzt(v) -> str:
    """224000 -> '224 000' (неразрывный пробел не используем, чтобы текст было легко копировать)."""
    return f"{float(v):,.0f}".replace(",", " ")


def num(v) -> str:
    """1.6 -> '1,6' для русского текста."""
    return f"{float(v):g}".replace(".", ",")
