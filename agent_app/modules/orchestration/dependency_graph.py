from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Iterable

from .constants import DEFAULT_DEPENDENCY_EDGES, MODULE_SPECS


class DependencyGraphError(ValueError):
    """Raised when dependency graph resolution is invalid."""


@dataclass(frozen=True)
class DependencyEdge:
    source: str
    target: str
    critical: bool = True
    ttl_seconds: int | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "source": self.source,
            "target": self.target,
            "critical": self.critical,
        }
        if self.ttl_seconds is not None:
            payload["ttl_seconds"] = self.ttl_seconds
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "DependencyEdge":
        return cls(
            source=str(payload["source"]),
            target=str(payload["target"]),
            critical=bool(payload.get("critical", True)),
            ttl_seconds=payload.get("ttl_seconds"),  # type: ignore[arg-type]
        )


class DependencyGraph:
    def __init__(self, edges: Iterable[DependencyEdge]) -> None:
        self.edges = tuple(edges)
        self._by_source: dict[str, list[DependencyEdge]] = defaultdict(list)
        self._critical_lookup: dict[tuple[str, str], bool] = {}
        for edge in self.edges:
            if not edge.source or not edge.target:
                raise DependencyGraphError("dependency edge source and target are required")
            self._by_source[edge.source].append(edge)
            self._critical_lookup[(edge.source, edge.target)] = edge.critical

    @classmethod
    def default(cls) -> "DependencyGraph":
        return cls(DependencyEdge.from_dict(edge) for edge in DEFAULT_DEPENDENCY_EDGES)

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "DependencyGraph":
        edges = payload.get("edges")
        if not isinstance(edges, list):
            raise DependencyGraphError("graph_payload.edges must be a list")
        return cls(DependencyEdge.from_dict(edge) for edge in edges)

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": "1.0",
            "nodes": sorted({edge.source for edge in self.edges} | {edge.target for edge in self.edges}),
            "module_nodes": sorted(MODULE_SPECS),
            "edges": [edge.to_dict() for edge in self.edges],
        }

    def direct_downstream(self, sources: Iterable[str]) -> tuple[str, ...]:
        ordered: list[str] = []
        seen: set[str] = set()
        for source in sources:
            for edge in self._by_source.get(source, ()):
                if edge.target not in seen:
                    seen.add(edge.target)
                    ordered.append(edge.target)
        return tuple(ordered)

    def transitive_downstream(self, sources: Iterable[str]) -> tuple[str, ...]:
        queue: deque[str] = deque(source for source in sources if source)
        seen_nodes: set[str] = set(queue)
        ordered_targets: list[str] = []
        seen_targets: set[str] = set()

        while queue:
            source = queue.popleft()
            for edge in self._by_source.get(source, ()):
                target = edge.target
                if target not in seen_targets:
                    seen_targets.add(target)
                    ordered_targets.append(target)
                if target not in seen_nodes:
                    seen_nodes.add(target)
                    queue.append(target)

        return tuple(ordered_targets)

    def module_targets(self, sources: Iterable[str], transitive: bool = True) -> tuple[str, ...]:
        targets = self.transitive_downstream(sources) if transitive else self.direct_downstream(sources)
        return tuple(target for target in targets if target in MODULE_SPECS)

    def is_critical_edge(self, source: str, target: str) -> bool:
        return self._critical_lookup.get((source, target), False)

    def critical_targets_from(self, source: str, transitive: bool = True) -> tuple[str, ...]:
        if not transitive:
            return tuple(edge.target for edge in self._by_source.get(source, ()) if edge.critical)

        critical: list[str] = []
        queue: deque[str] = deque([source])
        seen: set[str] = {source}
        while queue:
            current = queue.popleft()
            for edge in self._by_source.get(current, ()):
                if edge.critical and edge.target not in critical:
                    critical.append(edge.target)
                if edge.target not in seen:
                    seen.add(edge.target)
                    queue.append(edge.target)
        return tuple(target for target in critical if target in MODULE_SPECS)

