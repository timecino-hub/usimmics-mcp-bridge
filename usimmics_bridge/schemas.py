"""Validated configuration and JSON tool inputs, shared with Python/CLI callers."""
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    AfterValidator, AliasChoices, BaseModel, ConfigDict, Field, StrictStr,
    TypeAdapter, field_validator, model_validator,
)

from .component_specs import (
    COMPONENT_SPECS, COORDINATE_LIMIT, HB_COUNT_MAX, HB_COUNT_MIN,
    HB_ORDER_MAX, HB_ORDER_MIN, validate_property,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class BridgeConfig(StrictModel):
    installation_root: StrictStr
    project_roots: Annotated[list[StrictStr], Field(strict=True, min_length=1)]

    @field_validator('installation_root')
    @classmethod
    def installation_is_absolute(cls, value):
        return cls.absolute_path(value)

    @field_validator('project_roots')
    @classmethod
    def roots_are_absolute(cls, values):
        for value in values:
            cls.absolute_path(value)
        return values

    @staticmethod
    def absolute_path(value):
        if not value.strip() or '\x00' in value or not Path(value).is_absolute():
            raise ValueError('Configuration paths must be nonempty absolute paths for this operating system')
        return value


Identifier = Annotated[StrictStr, Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]*$')]
Coordinate = Annotated[int, Field(strict=True, ge=-COORDINATE_LIMIT, le=COORDINATE_LIMIT)]
Position = Annotated[list[Coordinate], Field(strict=True, min_length=2, max_length=2)]
PropertyValue = Annotated[StrictStr, AfterValidator(validate_property)]
BuilderKind = Literal[tuple(kind for kind in COMPONENT_SPECS if kind != '.HB')]
ParameterName = Literal[tuple(sorted({name for spec in COMPONENT_SPECS.values() for name in spec.parameter_names if name is not None}))]


class ComponentInput(StrictModel):
    type: BuilderKind
    name: Identifier
    position: Position
    orientation: Annotated[int, Field(strict=True, ge=0, le=3)] = 0
    params: dict[ParameterName, PropertyValue] = Field(default_factory=dict, description='Semantic parameter names returned by inspect_schematic; each name must be supported by this component type')
    properties: list[PropertyValue] | None = Field(default=None, description='Explicit native property fields, preserving their order and exact count')
    model: Literal['BAT54'] | None = None
    label_offset: Position | None = None

    @model_validator(mode='after')
    def supported_properties(self):
        spec = COMPONENT_SPECS[self.type]
        for name in self.params:
            if name not in spec.parameter_names:
                raise ValueError(f'Unknown parameter for {self.type}: {name}')
        if self.properties is not None:
            spec.validate_properties(self.type, self.properties)
        if self.model is not None and self.type != 'Diode':
            raise ValueError('Only Diode supports model=BAT54')
        if self.model is not None and self.properties is not None:
            raise ValueError('Specify either model or properties, not both')
        if self.type == 'Diode' and self.model is None and self.properties is None:
            raise ValueError('Diode needs model=BAT54 or explicit properties')
        return self


class Endpoint(StrictModel):
    component: Identifier
    pin: Annotated[int, Field(strict=True, ge=0, description='Zero-based native pin index; GND has pin 0, two-terminal devices have pins 0 and 1')]


class Connection(StrictModel):
    source: Endpoint = Field(alias='from')
    to: Endpoint
    via: list[Position] = Field(default_factory=list)
    label: Annotated[StrictStr, Field(pattern=r'^(?:[A-Za-z_][A-Za-z0-9_]*)?$')] = ''


class HarmonicSettings(StrictModel):
    order: Annotated[int, Field(strict=True, ge=HB_ORDER_MIN, le=HB_ORDER_MAX)] = 3
    count: Annotated[int, Field(strict=True, ge=HB_COUNT_MIN, le=HB_COUNT_MAX)] = 4


class ParameterEdit(StrictModel):
    component: Identifier
    parameter: Identifier | Annotated[int, Field(strict=True, ge=0, le=31)] = Field(
        validation_alias=AliasChoices('parameter', 'index'), description='Semantic parameter name or zero-based native field index')
    value: Annotated[PropertyValue, Field(min_length=1)]


ComponentList = Annotated[list[ComponentInput], Field(strict=True, min_length=1, max_length=100)]
ConnectionList = Annotated[list[Connection], Field(strict=True, min_length=1, max_length=300)]
EditList = Annotated[list[ParameterEdit], Field(strict=True, min_length=1)]
RunTimeout = Annotated[int, Field(strict=True, ge=1, le=300)]
SweepTimeout = Annotated[int, Field(strict=True, ge=1, le=30)]
ResultPointLimit = Annotated[int, Field(strict=True, ge=1, le=100000)]
SweepValues = Annotated[list[Annotated[PropertyValue, Field(min_length=1)]], Field(strict=True, min_length=1, max_length=20)]
DatasetPaths = Annotated[list[StrictStr], Field(strict=True, min_length=1, max_length=20)]
Frequency = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]
Impedance = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]

_components = TypeAdapter(ComponentList)
_connections = TypeAdapter(ConnectionList)
_edits = TypeAdapter(EditList)


def normalize_build(components, connections, harmonics):
    """Keep the public dict-based Python API while using the MCP contract everywhere."""
    return (
        [item.model_dump(by_alias=True, exclude_none=True) for item in _components.validate_python(components)],
        [item.model_dump(by_alias=True, exclude_none=True) for item in _connections.validate_python(connections)],
        HarmonicSettings.model_validate({} if harmonics is None else harmonics).model_dump(),
    )


def normalize_edits(edits):
    return [item.model_dump() for item in _edits.validate_python(edits)]


class ReadSchematicResult(BaseModel):
    path: str
    sha256: str
    content: str
    state: Literal['saved_on_disk']


class ProjectResult(BaseModel):
    project_path: str


class SaveResult(BaseModel):
    model_config = ConfigDict(extra='allow')
    path: str
    sha256: str
    backup: str | None
    saved: Literal[True]
    warnings: list[str]
    gui_reload_required: Literal[True]
