from __future__ import annotations

from typing import Iterator, Literal, TypeAlias

from .models import Ambience, CutMapping, CutSourceMapping, Entity, EntityRegistry, Entity_Child, Unknown


TargetInteractionType: TypeAlias = Literal["sfx", "ambience"]
RegistryTarget: TypeAlias = Entity | Entity_Child | Ambience
RegistryLeaf: TypeAlias = RegistryTarget | Unknown


def iter_all_nodes(registry: EntityRegistry) -> Iterator[RegistryTarget]:
    for entity in registry.entities:
        yield entity
        yield from entity.children
    yield from registry.ambience


def get_sfx_targets(registry: EntityRegistry) -> dict[str, Entity | Entity_Child]:
    targets: dict[str, Entity | Entity_Child] = {}
    for entity in registry.entities:
        if not entity.children:
            targets[entity.id] = entity
        for child in entity.children:
            targets[child.id] = child
    return targets


def get_ambience_targets(registry: EntityRegistry) -> dict[str, Ambience]:
    return {ambience.id: ambience for ambience in registry.ambience}


def get_all_valid_targets(registry: EntityRegistry) -> dict[str, RegistryTarget]:
    return {
        **get_sfx_targets(registry),
        **get_ambience_targets(registry),
    }


def derive_interaction_type(
    primary_source_id: str,
    registry: EntityRegistry,
) -> TargetInteractionType | None:
    if primary_source_id in get_sfx_targets(registry):
        return "sfx"
    if primary_source_id in get_ambience_targets(registry):
        return "ambience"
    if primary_source_id.startswith("UNKNOWN_"):
        return None
    return None


def iter_leaf_nodes(registry: EntityRegistry) -> Iterator[RegistryTarget]:
    yield from get_sfx_targets(registry).values()
    yield from get_ambience_targets(registry).values()


def build_leaf_index(registry: EntityRegistry) -> dict[str, RegistryLeaf]:
    return {
        **get_all_valid_targets(registry),
        **{unknown.id: unknown for unknown in registry.unknowns},
    }


def get_cut_hints(cut_mapping: CutMapping, cut_id: str) -> CutSourceMapping | None:
    for mapping in cut_mapping.mappings:
        if mapping.cut_id == cut_id:
            return mapping
    return None
