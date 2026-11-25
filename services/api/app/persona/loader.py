"""人格包加载：把 persona.yaml 读成结构化对象，并编译成 system prompt。"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Device:
    id: str
    name: str
    desc: str
    pattern: str
    examples: list[str] = field(default_factory=list)
    avoid: str = ""
    weight: float = 0.5


@dataclass
class Persona:
    raw: dict[str, Any]
    path: Path

    @property
    def id(self) -> str:
        return self.raw["id"]

    @property
    def display_name(self) -> str:
        return self.raw["display_name"]

    @property
    def version(self) -> str:
        return self.raw.get("version", "0.0.0")

    @property
    def license(self) -> str:
        return self.raw.get("license", "UNKNOWN")

    @property
    def voice(self) -> dict[str, Any]:
        return self.raw.get("voice", {})

    @property
    def devices(self) -> list[Device]:
        out = []
        for d in self.raw.get("devices", []):
            out.append(
                Device(
                    id=d["id"],
                    name=d.get("name", d["id"]),
                    desc=(d.get("desc") or "").strip(),
                    pattern=(d.get("pattern") or "").strip(),
                    examples=list(d.get("examples") or []),
                    avoid=(d.get("avoid") or "").strip(),
                    weight=float(d.get("weight", 0.5)),
                )
            )
        return out

    def device(self, device_id: str) -> Device | None:
        for d in self.devices:
            if d.id == device_id:
                return d
        return None

    @property
    def sentence_max(self) -> int:
        return int(self.voice.get("sentence_max", 3))

    @property
    def char_max(self) -> int:
        return int(self.voice.get("char_max", 90))


class PersonaError(RuntimeError):
    pass


def load_persona(path: Path) -> Persona:
    if not path.exists():
        raise PersonaError(f"人格包不存在：{path}")
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict) or "id" not in raw:
        raise PersonaError(f"人格包格式错误（缺 id）：{path}")
    return Persona(raw=raw, path=path)


@lru_cache(maxsize=8)
def get_persona(path_str: str) -> Persona:
    return load_persona(Path(path_str))


def available_personas(persona_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not persona_dir.exists():
        return out
    for child in sorted(persona_dir.iterdir()):
        f = child / "persona.yaml"
        if not f.exists():
            continue
        try:
            p = load_persona(f)
        except PersonaError:
            continue
        out.append(
            {
                "id": p.id,
                "display_name": p.display_name,
                "version": p.version,
                "license": p.license,
                "devices": len(p.devices),
            }
        )
    return out
