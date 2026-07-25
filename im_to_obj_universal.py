#!/usr/bin/env python3
"""Universal Trainz/Auran Indexed Mesh (.im / JIRF-IDXM) to Wavefront OBJ.

Goals:
- Read common legacy and modern IM variants (INFO 100+, MATL 100+, GEOM
  100/101/102/103/104/200/201 and compatible extensions).
- Batch-convert files/folders without stopping on the first bad file.
- Preserve static geometry, UV0, normals, materials and attachment points.
- Reconstruct rigid parent-bone transforms from embedded SKEL/INFL data when
  possible. Skinned animation itself cannot be represented by OBJ.

No third-party Python packages are required.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import shutil
import struct
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

TOOL_VERSION = "2.0.0"
KNOWN_TOP_LEVEL = {b"INFO", b"CHNK", b"ATTR", b"ATCH", b"INFL", b"SKEL"}
MAX_VERTICES = 20_000_000
MAX_INDICES = 100_000_000
MAX_STRINGS = 1_000_000
MAX_UV_SETS = 32


class IMError(RuntimeError):
    """Raised for malformed or unsupported Indexed Mesh data."""


class Reader:
    def __init__(self, data: bytes, start: int = 0, end: int | None = None, label: str = ""):
        self.data = data
        self.pos = start
        self.start = start
        self.end = len(data) if end is None else end
        self.label = label

    def clone(self) -> "Reader":
        return Reader(self.data, self.pos, self.end, self.label)

    def remaining(self) -> int:
        return self.end - self.pos

    def tell(self) -> int:
        return self.pos

    def seek(self, pos: int) -> None:
        if pos < self.start or pos > self.end:
            raise IMError(f"Invalid seek to 0x{pos:X} in {self.label or 'reader'}")
        self.pos = pos

    def read(self, size: int) -> bytes:
        if size < 0 or self.pos + size > self.end:
            raise IMError(
                f"Unexpected end of {self.label or 'data'} at 0x{self.pos:X}: "
                f"requested {size} bytes, {self.remaining()} remain"
            )
        out = self.data[self.pos : self.pos + size]
        self.pos += size
        return out

    def peek(self, size: int) -> bytes:
        if size < 0 or self.pos + size > self.end:
            return b""
        return self.data[self.pos : self.pos + size]

    def u8(self) -> int:
        return self.read(1)[0]

    def u16(self) -> int:
        return struct.unpack("<H", self.read(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.read(4))[0]

    def f32(self) -> float:
        return struct.unpack("<f", self.read(4))[0]

    def floats(self, count: int) -> tuple[float, ...]:
        return struct.unpack("<" + "f" * count, self.read(count * 4))

    def subreader(self, size: int, label: str = "") -> "Reader":
        start = self.pos
        end = start + size
        if end > self.end:
            raise IMError(
                f"Subchunk {label!r} at 0x{start:X} extends past parent "
                f"({size} bytes requested, {self.remaining()} remain)"
            )
        self.pos = end
        return Reader(self.data, start, end, label)

    def jet_string(self) -> str:
        if self.remaining() < 4:
            raise IMError(f"Missing JET string length at 0x{self.pos:X}")
        raw_length = self.u32()
        wide = bool(raw_length & 0x40000000)
        length = raw_length & 0x3FFFFFFF
        if length > MAX_STRINGS or length > self.remaining():
            raise IMError(
                f"Invalid JET string length {length} at 0x{self.pos - 4:X} "
                f"({self.remaining()} bytes remain)"
            )
        raw = self.read(length)
        if wide:
            # Some exporters include 4-byte alignment in the length. Ensure an
            # odd trailing byte does not break UTF-16 decoding.
            raw = raw[: len(raw) - (len(raw) % 2)]
            return raw.rstrip(b"\x00").decode("utf-16le", errors="replace")
        return raw.rstrip(b"\x00").decode("utf-8", errors="replace")


@dataclass
class TextureSlot:
    slot_type: int
    source: str
    amount: float
    primary_image: str | None = None
    alpha_image: str | None = None
    primary_path: Path | None = None
    alpha_path: Path | None = None


@dataclass
class Material:
    name: str
    version: int
    two_sided: bool = False
    opacity: float = 1.0
    ambient: tuple[float, float, float] = (1.0, 1.0, 1.0)
    diffuse: tuple[float, float, float] = (1.0, 1.0, 1.0)
    specular: tuple[float, float, float] = (0.0, 0.0, 0.0)
    emissive: tuple[float, float, float] = (0.0, 0.0, 0.0)
    shininess: float = 0.0
    textures: list[TextureSlot] = field(default_factory=list)
    properties: dict[str, str] = field(default_factory=dict)


@dataclass
class Geometry:
    version: int
    primitive_flags: int
    area: float
    vertices: list[tuple[float, float, float]]
    uv_sets: list[list[tuple[float, float]]]
    indices: list[int]
    normals: list[tuple[float, float, float]]
    face_normals: list[tuple[float, float, float]]
    tangents: list[tuple[float, float, float]]
    vertex_colors: list[tuple[float, float, float, float]]
    parent_bone: str
    max_influences: int
    index_size: int = 2
    trailing_bytes: int = 0

    @property
    def primitive_kind(self) -> str:
        # JET's common primitive constants are points=1, lines=2, triangles=4.
        if self.primitive_flags == 4 or (self.primitive_flags & 4):
            return "triangles"
        if self.primitive_flags == 2 or (self.primitive_flags & 2):
            return "lines"
        if self.primitive_flags == 1 or (self.primitive_flags & 1):
            return "points"
        return "unknown"

    @property
    def is_triangles(self) -> bool:
        return self.primitive_kind == "triangles"

    @property
    def is_lines(self) -> bool:
        return self.primitive_kind == "lines"

    @property
    def is_points(self) -> bool:
        return self.primitive_kind == "points"


@dataclass
class MeshChunk:
    index: int
    version: int
    material: Material
    geometry: Geometry


@dataclass
class Attachment:
    name: str
    orientation: tuple[float, ...]
    position: tuple[float, float, float]


@dataclass
class Bone:
    name: str
    parent: str
    position: tuple[float, float, float]
    rotation: tuple[float, ...]  # row-major 3x3
    source: str


@dataclass
class IndexedMesh:
    source: Path
    info_version: int
    position: tuple[float, float, float]
    rotation: tuple[float, float, float, float]
    bounds_min: tuple[float, float, float] | None
    bounds_max: tuple[float, float, float] | None
    chunks: list[MeshChunk]
    attachments: list[Attachment]
    bones: dict[str, Bone]
    warnings: list[str]
    unknown_chunks: list[str]
    resource_mode: str


# --------------------------- matrix helpers ---------------------------

Mat4 = tuple[
    float, float, float, float,
    float, float, float, float,
    float, float, float, float,
    float, float, float, float,
]


def mat4_identity() -> Mat4:
    return (
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    )


def mat4_mul(a: Mat4, b: Mat4) -> Mat4:
    out = [0.0] * 16
    for row in range(4):
        for col in range(4):
            out[row * 4 + col] = sum(a[row * 4 + k] * b[k * 4 + col] for k in range(4))
    return tuple(out)  # type: ignore[return-value]


def mat4_from_rt(rotation: Sequence[float], position: Sequence[float]) -> Mat4:
    return (
        rotation[0], rotation[1], rotation[2], position[0],
        rotation[3], rotation[4], rotation[5], position[1],
        rotation[6], rotation[7], rotation[8], position[2],
        0.0, 0.0, 0.0, 1.0,
    )


def mat4_transform_point(m: Mat4, p: Sequence[float]) -> tuple[float, float, float]:
    x, y, z = p
    return (
        m[0] * x + m[1] * y + m[2] * z + m[3],
        m[4] * x + m[5] * y + m[6] * z + m[7],
        m[8] * x + m[9] * y + m[10] * z + m[11],
    )


def mat4_transform_vector(m: Mat4, p: Sequence[float]) -> tuple[float, float, float]:
    x, y, z = p
    return (
        m[0] * x + m[1] * y + m[2] * z,
        m[4] * x + m[5] * y + m[6] * z,
        m[8] * x + m[9] * y + m[10] * z,
    )


def quat_xyzw_to_mat3(q: Sequence[float]) -> tuple[float, ...]:
    x, y, z, w = q
    length = math.sqrt(x * x + y * y + z * z + w * w)
    if length <= 1e-20:
        return (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    x, y, z, w = x / length, y / length, z / length, w / length
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return (
        1.0 - 2.0 * (yy + zz), 2.0 * (xy - wz), 2.0 * (xz + wy),
        2.0 * (xy + wz), 1.0 - 2.0 * (xx + zz), 2.0 * (yz - wx),
        2.0 * (xz - wy), 2.0 * (yz + wx), 1.0 - 2.0 * (xx + yy),
    )


def transpose3(m: Sequence[float]) -> tuple[float, ...]:
    return (m[0], m[3], m[6], m[1], m[4], m[7], m[2], m[5], m[8])


def normalize3(v: Sequence[float]) -> tuple[float, float, float]:
    x, y, z = v
    length = math.sqrt(x * x + y * y + z * z)
    if length <= 1e-20 or not math.isfinite(length):
        return (0.0, 0.0, 1.0)
    return (x / length, y / length, z / length)


# --------------------------- parser ---------------------------

class IMParser:
    def __init__(self, source: Path, *, salvage: bool = True):
        self.source = source
        self.data = source.read_bytes()
        self.salvage = salvage
        self.warnings: list[str] = []
        self.unknown_chunks: list[str] = []

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    @staticmethod
    def _is_padding(data: bytes) -> bool:
        return not data or all(byte == 0 for byte in data)

    def _read_chunk(self, reader: Reader) -> tuple[bytes, Reader, int]:
        offset = reader.tell()
        chunk_id = reader.read(4)
        size = reader.u32()
        payload = reader.subreader(size, chunk_id.decode("ascii", errors="replace"))
        return chunk_id, payload, offset

    def _find_resync(self, reader: Reader) -> int | None:
        # Search for a known chunk tag followed by a plausible payload length.
        start = reader.tell() + 1
        limit = min(reader.end - 8, start + 4 * 1024 * 1024)
        data = reader.data
        for pos in range(start, max(start, limit + 1)):
            tag = data[pos : pos + 4]
            if tag not in KNOWN_TOP_LEVEL:
                continue
            size = struct.unpack_from("<I", data, pos + 4)[0]
            if pos + 8 + size <= reader.end:
                return pos
        return None

    def parse(self) -> IndexedMesh:
        if len(self.data) < 4:
            raise IMError("File is too small to be an Indexed Mesh")

        mode: str
        if self.data.startswith(b"JIRF"):
            if len(self.data) < 12:
                raise IMError("Truncated JIRF header")
            declared_size = struct.unpack_from("<I", self.data, 4)[0]
            resource_end = 8 + declared_size
            if resource_end > len(self.data):
                if not self.salvage:
                    raise IMError(
                        f"Declared JIRF size {resource_end} exceeds actual file size {len(self.data)}"
                    )
                self.warn(
                    f"Declared JIRF size {resource_end} exceeds actual file size {len(self.data)}; "
                    "using available bytes"
                )
                resource_end = len(self.data)
            r = Reader(self.data, 8, resource_end, "IDXM resource")
            if r.read(4) != b"IDXM":
                raise IMError("The JIRF resource is not an IDXM indexed mesh")
            mode = "JIRF/IDXM"
            if resource_end < len(self.data) and not self._is_padding(self.data[resource_end:]):
                self.warn(f"Ignoring {len(self.data) - resource_end} bytes after declared JIRF resource")
        elif self.data.startswith(b"IDXM"):
            # Some third-party tools store a raw IDXM resource without JIRF.
            r = Reader(self.data, 4, len(self.data), "raw IDXM resource")
            mode = "raw IDXM"
            self.warn("File uses raw IDXM without the normal JIRF wrapper")
        else:
            raise IMError("Not a Trainz/Auran Indexed Mesh: missing JIRF/IDXM header")

        info_version = 0
        position = (0.0, 0.0, 0.0)
        rotation = (1.0, 0.0, 0.0, 0.0)
        bounds_min = None
        bounds_max = None
        expected_chunks: int | None = None
        chunks: list[MeshChunk] = []
        attachments: list[Attachment] = []
        bones: dict[str, Bone] = {}

        while r.remaining() > 0:
            if r.remaining() < 8:
                tail = r.read(r.remaining())
                if not self._is_padding(tail):
                    self.warn(f"Ignoring {len(tail)} trailing nonzero bytes")
                break
            if self._is_padding(r.peek(min(r.remaining(), 64))):
                break

            try:
                chunk_id, payload, offset = self._read_chunk(r)
            except IMError as exc:
                if not self.salvage:
                    raise
                resync = self._find_resync(r)
                if resync is None:
                    self.warn(f"Stopped parsing after damaged top-level chunk: {exc}")
                    break
                self.warn(f"Recovered from damaged top-level chunk at 0x{r.tell():X}; resuming at 0x{resync:X}")
                r.seek(resync)
                continue

            try:
                if chunk_id == b"INFO":
                    (
                        info_version,
                        position,
                        rotation,
                        expected_chunks,
                        bounds_min,
                        bounds_max,
                    ) = self._parse_info(payload)
                elif chunk_id in {b"CHNK", b"ATTR"}:
                    try:
                        chunk = self._parse_mesh_chunk(payload, len(chunks))
                    except (IMError, struct.error, ValueError) as exc:
                        if not self.salvage:
                            raise
                        self.warn(f"Skipped damaged mesh chunk at 0x{offset:X}: {exc}")
                    else:
                        chunks.append(chunk)
                elif chunk_id == b"ATCH":
                    attachments.extend(self._parse_attachments(payload))
                elif chunk_id == b"SKEL":
                    parsed = self._parse_skeleton(payload)
                    bones.update(parsed)
                elif chunk_id == b"INFL":
                    parsed = self._parse_influences(payload)
                    # Embedded SKEL is usually more direct for rigid parents.
                    for name, bone in parsed.items():
                        bones.setdefault(name, bone)
                else:
                    label = chunk_id.decode("ascii", errors="replace")
                    self.unknown_chunks.append(f"{label}@0x{offset:X} ({payload.remaining()} bytes)")
            except (IMError, struct.error, ValueError) as exc:
                if not self.salvage:
                    raise
                label = chunk_id.decode("ascii", errors="replace")
                self.warn(f"Could not parse {label} at 0x{offset:X}: {exc}")

        if not chunks:
            raise IMError("No readable CHNK/ATTR geometry sections found")
        if expected_chunks is not None and expected_chunks != len(chunks):
            self.warn(f"INFO declares {expected_chunks} chunks, but {len(chunks)} were readable")

        return IndexedMesh(
            source=self.source,
            info_version=info_version,
            position=position,
            rotation=rotation,
            bounds_min=bounds_min,
            bounds_max=bounds_max,
            chunks=chunks,
            attachments=attachments,
            bones=bones,
            warnings=self.warnings,
            unknown_chunks=self.unknown_chunks,
            resource_mode=mode,
        )

    def _parse_info(self, r: Reader):
        version = r.u32()
        position = r.floats(3)
        rotation = r.floats(4)
        count = r.u32()
        if version >= 102:
            if r.remaining() >= 8:
                r.u32()  # max influences per vertex
                r.u32()  # max influences per chunk
            else:
                self.warn(f"INFO {version} omits influence limits")
        bounds_min = bounds_max = None
        if version >= 104:
            if r.remaining() >= 24:
                bounds_min = r.floats(3)
                bounds_max = r.floats(3)
            else:
                self.warn(f"INFO {version} omits its advertised bounding box")
        if r.remaining() and not self._is_padding(r.read(r.remaining())):
            self.warn(f"INFO {version} has trailing data")
        return version, position, rotation, count, bounds_min, bounds_max

    def _parse_mesh_chunk(self, r: Reader, fallback_index: int) -> MeshChunk:
        if r.remaining() < 4:
            raise IMError("Mesh chunk has no version")
        chunk_version = r.u32()
        if r.remaining() >= 4 and r.peek(4) not in {b"MATL", b"GEOM", b"NINF"}:
            chunk_index = r.u32()
        else:
            chunk_index = fallback_index
            self.warn(f"CHNK {fallback_index} omits explicit chunk index")

        material: Material | None = None
        geometry: Geometry | None = None
        while r.remaining() >= 8:
            chunk_id, payload, _ = self._read_chunk(r)
            if chunk_id == b"MATL":
                try:
                    material = self._parse_material(payload, chunk_index)
                except (IMError, struct.error, ValueError) as exc:
                    if not self.salvage:
                        raise
                    self.warn(f"CHNK {chunk_index}: damaged MATL replaced with default ({exc})")
            elif chunk_id == b"GEOM":
                geometry = self._parse_geometry(payload, chunk_index)
            else:
                # NINF and future per-mesh blocks do not affect OBJ geometry.
                continue

        if material is None:
            material = Material(name=f"material_{fallback_index:03d}", version=0)
        if geometry is None:
            raise IMError(f"CHNK {chunk_index} contains no readable GEOM section")
        return MeshChunk(chunk_index, chunk_version, material, geometry)

    def _parse_material_layout(self, r: Reader, chunk_index: int, version: int, with_name: bool) -> Material:
        name = f"material_{chunk_index:03d}"
        properties: dict[str, str] = {}
        if with_name:
            name = r.jet_string() or name
            if version >= 102:
                property_count = r.u32()
                if property_count > 100_000:
                    raise IMError(f"Implausible material property count {property_count}")
                for _ in range(property_count):
                    properties[r.jet_string()] = r.jet_string()

        two_sided_raw = r.u32()
        if two_sided_raw not in (0, 1):
            raise IMError(f"Invalid two-sided flag {two_sided_raw}")
        opacity = r.f32() if version >= 102 else 1.0
        ambient = r.floats(3)
        diffuse = r.floats(3)
        specular = r.floats(3)
        emissive = r.floats(3)
        shininess = r.f32()
        texture_count = r.u32()
        if texture_count > 4096:
            raise IMError(f"Implausible texture count {texture_count}")
        textures: list[TextureSlot] = []
        for _ in range(texture_count):
            textures.append(TextureSlot(r.u32(), r.jet_string(), r.f32()))

        trailing = r.remaining()
        if trailing:
            tail = r.read(trailing)
            if not self._is_padding(tail):
                self.warn(f"MATL {version} in chunk {chunk_index} has {trailing} trailing bytes")
        return Material(
            name=name,
            version=version,
            two_sided=bool(two_sided_raw),
            opacity=opacity,
            ambient=ambient,
            diffuse=diffuse,
            specular=specular,
            emissive=emissive,
            shininess=shininess,
            textures=textures,
            properties=properties,
        )

    def _parse_material(self, r: Reader, chunk_index: int) -> Material:
        version = r.u32()
        payload_start = r.tell()
        attempts: list[tuple[bool, Exception]] = []

        # Standard exporters use material name/properties from v102 onward.
        candidates = [True] if version >= 102 else [False, True]
        for with_name in candidates:
            trial = Reader(r.data, payload_start, r.end, r.label)
            try:
                return self._parse_material_layout(trial, chunk_index, version, with_name)
            except (IMError, struct.error, ValueError) as exc:
                attempts.append((with_name, exc))

        details = "; ".join(f"with_name={flag}: {exc}" for flag, exc in attempts)
        raise IMError(f"Unsupported MATL {version} layout ({details})")

    def _parse_geometry(self, r: Reader, chunk_index: int) -> Geometry:
        version = r.u32()
        primitive_flags = r.u32() if version >= 101 else 4
        use_tangents = bool(r.u32()) if version >= 201 else False
        area = r.f32()
        vertex_count = r.u32()
        primitive_count = r.u32()  # present in known v100 files too

        if version >= 101:
            index_count = r.u32()
            face_normal_count = r.u32()
        else:
            # GEOM 100 writes primitive count but no explicit index count and no
            # face-normal list in the known legacy exporter layout.
            stride = 2 if primitive_flags == 2 else (1 if primitive_flags == 1 else 3)
            index_count = primitive_count * stride
            face_normal_count = 0

        texcoord_set_count = r.u32() if version in (103, 104) else 1
        max_influences = r.u32() if version >= 102 else 0
        parent_bone = r.jet_string() if version >= 200 else ""

        if vertex_count > MAX_VERTICES or index_count > MAX_INDICES:
            raise IMError(
                f"Implausible geometry counts in CHNK {chunk_index}: "
                f"{vertex_count} vertices, {index_count} indices"
            )
        if texcoord_set_count < 1 or texcoord_set_count > MAX_UV_SETS:
            raise IMError(f"Unsupported UV-set count {texcoord_set_count}")

        base_start = r.tell()
        failures: list[str] = []
        index_sizes = [2]
        if vertex_count > 65535:
            index_sizes = [4, 2]
        else:
            # 32-bit is a nonstandard fallback for third-party writers.
            index_sizes = [2, 4]

        for index_size in index_sizes:
            trial = Reader(r.data, base_start, r.end, r.label)
            try:
                geometry = self._parse_geometry_body(
                    trial,
                    version=version,
                    primitive_flags=primitive_flags,
                    use_tangents=use_tangents,
                    area=area,
                    vertex_count=vertex_count,
                    index_count=index_count,
                    face_normal_count=face_normal_count,
                    texcoord_set_count=texcoord_set_count,
                    max_influences=max_influences,
                    parent_bone=parent_bone,
                    index_size=index_size,
                )
                if index_size == 4:
                    self.warn(f"CHNK {chunk_index}: used nonstandard 32-bit indices")
                return geometry
            except (IMError, struct.error, ValueError) as exc:
                failures.append(f"{index_size * 8}-bit indices: {exc}")

        raise IMError(f"Could not decode GEOM {version} ({'; '.join(failures)})")

    def _parse_geometry_body(
        self,
        r: Reader,
        *,
        version: int,
        primitive_flags: int,
        use_tangents: bool,
        area: float,
        vertex_count: int,
        index_count: int,
        face_normal_count: int,
        texcoord_set_count: int,
        max_influences: int,
        parent_bone: str,
        index_size: int,
    ) -> Geometry:
        min_bytes = (
            vertex_count * (12 + texcoord_set_count * 8)
            + index_count * index_size
            + vertex_count * 12
            + face_normal_count * 12
            + (vertex_count * 12 if version >= 201 and use_tangents else 0)
        )
        if min_bytes > r.remaining():
            raise IMError(f"GEOM needs at least {min_bytes} bytes, only {r.remaining()} remain")

        vertices: list[tuple[float, float, float]] = []
        uv_sets: list[list[tuple[float, float]]] = [[] for _ in range(texcoord_set_count)]
        for _ in range(vertex_count):
            vertices.append(r.floats(3))
            for uv_set in uv_sets:
                uv_set.append(r.floats(2))

        if index_size == 2:
            indices = [r.u16() for _ in range(index_count)]
        else:
            indices = [r.u32() for _ in range(index_count)]
        if indices and max(indices) >= vertex_count:
            raise IMError(f"Index {max(indices)} exceeds vertex count {vertex_count}")

        normals = [r.floats(3) for _ in range(vertex_count)]
        face_normals = [r.floats(3) for _ in range(face_normal_count)]

        vertex_colors: list[tuple[float, float, float, float]] = []
        if version == 104:
            color_bytes = vertex_count * 4
            # Vertex colors are optional in real-world v104 files. Read them
            # only when the remaining payload is large enough to contain a full set.
            if r.remaining() >= color_bytes and color_bytes > 0:
                raw = r.read(color_bytes)
                vertex_colors = [
                    (raw[i] / 255.0, raw[i + 1] / 255.0, raw[i + 2] / 255.0, raw[i + 3] / 255.0)
                    for i in range(0, len(raw), 4)
                ]

        tangents: list[tuple[float, float, float]] = []
        if version >= 201 and use_tangents:
            tangents = [r.floats(3) for _ in range(vertex_count)]

        trailing = r.remaining()
        if trailing:
            tail = r.read(trailing)
            if not self._is_padding(tail):
                # Unknown extensions are tolerated because OBJ does not need them.
                pass

        return Geometry(
            version=version,
            primitive_flags=primitive_flags,
            area=area,
            vertices=vertices,
            uv_sets=uv_sets,
            indices=indices,
            normals=normals,
            face_normals=face_normals,
            tangents=tangents,
            vertex_colors=vertex_colors,
            parent_bone=parent_bone,
            max_influences=max_influences,
            index_size=index_size,
            trailing_bytes=trailing,
        )

    def _parse_attachments(self, r: Reader) -> list[Attachment]:
        version = r.u32()
        count = r.u32()
        if count > 1_000_000:
            raise IMError(f"Implausible attachment count {count}")
        out: list[Attachment] = []
        for _ in range(count):
            out.append(Attachment(r.jet_string(), r.floats(9), r.floats(3)))
        if version != 100:
            self.warn(f"ATCH version {version} parsed using the v100-compatible layout")
        return out

    def _parse_skeleton(self, r: Reader) -> dict[str, Bone]:
        version = r.u32()
        bones: dict[str, Bone] = {}
        if version != 100:
            self.warn(f"SKEL version {version} parsed using the v100-compatible layout")

        while r.remaining() >= 8:
            chunk_id, payload, _ = self._read_chunk(r)
            if chunk_id == b"BONE":
                self._parse_bone_recursive(payload, "", bones)
        return bones

    def _parse_bone_recursive(self, r: Reader, parent: str, bones: dict[str, Bone]) -> None:
        version = r.u32()
        name = r.jet_string()
        position = r.floats(3)
        quat = r.floats(4)  # exporter stores x, y, z, w for BONE
        child_count = r.u32()
        bones[name] = Bone(name, parent, position, quat_xyzw_to_mat3(quat), f"SKEL/{version}")

        parsed_children = 0
        while r.remaining() >= 8 and parsed_children < child_count:
            chunk_id, payload, _ = self._read_chunk(r)
            if chunk_id != b"BONE":
                self.warn(f"BONE {name!r} contains unexpected child {chunk_id!r}")
                continue
            self._parse_bone_recursive(payload, name, bones)
            parsed_children += 1
        if parsed_children != child_count:
            self.warn(f"BONE {name!r} declares {child_count} children, parsed {parsed_children}")

    def _parse_influences(self, r: Reader) -> dict[str, Bone]:
        version = r.u32()
        count = r.u32()
        if count > 1_000_000:
            raise IMError(f"Implausible bone count {count}")
        bones: dict[str, Bone] = {}
        for _ in range(count):
            name = r.jet_string()
            parent = r.jet_string()
            position = r.floats(3)
            file_orientation = r.floats(9)
            # The known exporter writes the Blender rotation matrix transposed.
            rotation = transpose3(file_orientation)
            influence_chunk_count = r.u32()
            if influence_chunk_count > 1_000_000:
                raise IMError(f"Bone {name!r} has implausible influence chunk count")
            for _ in range(influence_chunk_count):
                r.u32()  # chunk index
                vertex_influence_count = r.u32()
                if vertex_influence_count > MAX_VERTICES:
                    raise IMError(f"Bone {name!r} has implausible vertex influence count")
                # index(u32), weight(f32), position(float3)
                r.read(vertex_influence_count * 20)
            bones[name] = Bone(name, parent, position, rotation, f"INFL/{version}")
        if version != 100:
            self.warn(f"INFL version {version} parsed using the v100-compatible layout")
        return bones


# --------------------------- texture/material helpers ---------------------------

_INVALID_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


def safe_name(value: str, fallback: str) -> str:
    value = value.strip().replace("\\", "_").replace("/", "_")
    value = _INVALID_NAME.sub("_", value).strip("._")
    return value or fallback


_FILE_INDEX_CACHE: dict[tuple[str, ...], dict[str, list[Path]]] = {}


def build_file_index(roots: Sequence[Path]) -> dict[str, list[Path]]:
    normalized_roots: list[Path] = []
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            continue
        if resolved.is_dir() and resolved not in normalized_roots:
            normalized_roots.append(resolved)
    cache_key = tuple(str(root).casefold() for root in normalized_roots)
    cached = _FILE_INDEX_CACHE.get(cache_key)
    if cached is not None:
        return cached

    index: dict[str, list[Path]] = {}
    seen: set[Path] = set()
    for root in normalized_roots:
        if root in seen:
            continue
        seen.add(root)
        try:
            for path in root.rglob("*"):
                if path.is_file():
                    index.setdefault(path.name.casefold(), []).append(path)
        except (OSError, PermissionError):
            continue
    _FILE_INDEX_CACHE[cache_key] = index
    return index


def best_index_match(index: dict[str, list[Path]], name: str, near: Path) -> Path | None:
    candidates = index.get(Path(name.replace("\\", "/")).name.casefold(), [])
    if not candidates:
        return None
    # Prefer a file closest to the mesh directory.
    def score(path: Path) -> tuple[int, int]:
        try:
            common = len(Path(*Path(path).resolve().parts[:]).parts)
            rel = len(path.resolve().relative_to(near.resolve()).parts)
            return (0, rel)
        except (ValueError, OSError):
            return (1, len(path.parts))
    return min(candidates, key=score)


def parse_texture_txt(path: Path) -> tuple[str | None, str | None]:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None, None
    primary = alpha = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().casefold()
        value = value.strip().strip('"')
        if key == "primary":
            primary = value
        elif key == "alpha":
            alpha = value
    return primary, alpha


def resolve_textures(mesh: IndexedMesh, asset_root: Path | None) -> None:
    # Do not recursively index arbitrary ancestors (for example C:\\ or /),
    # which becomes extremely slow during large batch jobs. The mesh folder is
    # always searched; the nearest ancestor containing config.txt is treated as
    # the asset root, or the caller can supply --asset-root explicitly.
    roots: list[Path] = [mesh.source.parent]
    if asset_root is not None:
        roots.insert(0, asset_root)
    else:
        for parent in list(mesh.source.parents)[1:4]:
            if (parent / "config.txt").is_file():
                roots.insert(0, parent)
                break
    index = build_file_index(roots)
    image_exts = (".tga", ".png", ".jpg", ".jpeg", ".bmp", ".dds", ".webp", ".tif", ".tiff")

    for chunk in mesh.chunks:
        for texture in chunk.material.textures:
            source_name = Path(texture.source.replace("\\", "/")).name
            stem = source_name
            if stem.casefold().endswith(".texture"):
                stem = stem[:-8]
            else:
                stem = Path(stem).stem

            txt_names = [source_name + ".txt", stem + ".texture.txt"]
            primary = alpha = None
            for candidate in txt_names:
                match = best_index_match(index, candidate, mesh.source.parent)
                if match:
                    primary, alpha = parse_texture_txt(match)
                    break

            if primary:
                match = best_index_match(index, primary, mesh.source.parent)
                texture.primary_path = match
                texture.primary_image = match.name if match else Path(primary).name
            else:
                for ext in image_exts:
                    match = best_index_match(index, stem + ext, mesh.source.parent)
                    if match:
                        texture.primary_path = match
                        texture.primary_image = match.name
                        break

            if alpha:
                match = best_index_match(index, alpha, mesh.source.parent)
                texture.alpha_path = match
                texture.alpha_image = match.name if match else Path(alpha).name


def choose_texture(material: Material, slot_type: int) -> TextureSlot | None:
    return next((slot for slot in material.textures if slot.slot_type == slot_type), None)


def unique_material_names(chunks: Sequence[MeshChunk]) -> list[str]:
    used: dict[str, int] = {}
    result: list[str] = []
    for i, chunk in enumerate(chunks):
        base = safe_name(chunk.material.name, f"material_{i:03d}")
        count = used.get(base, 0)
        used[base] = count + 1
        result.append(base if count == 0 else f"{base}_{count + 1}")
    return result


# --------------------------- export helpers ---------------------------

def build_bone_matrices(mesh: IndexedMesh) -> dict[str, Mat4]:
    aliases: dict[str, str] = {}
    for name in mesh.bones:
        aliases[name] = name
        aliases[name.casefold()] = name
        if name.startswith("b.r."):
            aliases[name[4:]] = name
            aliases[name[4:].casefold()] = name

    cache: dict[str, Mat4] = {}
    visiting: set[str] = set()

    def resolve_name(name: str) -> str | None:
        return aliases.get(name) or aliases.get(name.casefold())

    def build(name: str) -> Mat4:
        canonical = resolve_name(name)
        if canonical is None:
            return mat4_identity()
        if canonical in cache:
            return cache[canonical]
        if canonical in visiting:
            mesh.warnings.append(f"Bone hierarchy cycle involving {canonical!r}")
            return mat4_identity()
        visiting.add(canonical)
        bone = mesh.bones[canonical]
        local = mat4_from_rt(bone.rotation, bone.position)
        parent_name = resolve_name(bone.parent) if bone.parent else None
        world = mat4_mul(build(parent_name), local) if parent_name else local
        visiting.remove(canonical)
        cache[canonical] = world
        return world

    for name in mesh.bones:
        build(name)

    # Add aliases for direct lookup by GEOM parent strings.
    out = dict(cache)
    for alias, canonical in aliases.items():
        if canonical in cache:
            out[alias] = cache[canonical]
    return out


def transform_vec(vec: Sequence[float], y_up: bool, scale: float) -> tuple[float, float, float]:
    x, y, z = vec
    if y_up:
        x, y, z = x, z, -y
    return x * scale, y * scale, z * scale


def transform_normal(vec: Sequence[float], y_up: bool) -> tuple[float, float, float]:
    x, y, z = normalize3(vec)
    if y_up:
        x, y, z = x, z, -y
    return normalize3((x, y, z))


def copy_texture(path: Path | None, output_dir: Path, texture_dir_name: str) -> str | None:
    if path is None or not path.is_file():
        return None
    destination_dir = output_dir / texture_dir_name
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / path.name
    if destination.resolve() != path.resolve():
        try:
            shutil.copy2(path, destination)
        except OSError:
            return None
    return f"{texture_dir_name}/{destination.name}".replace("\\", "/")


def texture_reference(
    slot: TextureSlot | None,
    *,
    alpha: bool,
    output_dir: Path,
    copy_textures: bool,
    texture_dir_name: str,
) -> str | None:
    if slot is None:
        return None
    path = slot.alpha_path if alpha else slot.primary_path
    name = slot.alpha_image if alpha else slot.primary_image
    if copy_textures:
        copied = copy_texture(path, output_dir, texture_dir_name)
        if copied:
            return copied
    return Path(name).name if name else None


def write_mtl(
    path: Path,
    chunks: Sequence[MeshChunk],
    material_names: Sequence[str],
    *,
    copy_textures: bool,
    texture_dir_name: str,
) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(f"# Generated by im_to_obj_universal.py {TOOL_VERSION}\n")
        for chunk, output_name in zip(chunks, material_names):
            m = chunk.material
            f.write(f"\nnewmtl {output_name}\n")
            f.write("Ka {:.8g} {:.8g} {:.8g}\n".format(*m.ambient))
            f.write("Kd {:.8g} {:.8g} {:.8g}\n".format(*m.diffuse))
            f.write("Ks {:.8g} {:.8g} {:.8g}\n".format(*m.specular))
            f.write("Ke {:.8g} {:.8g} {:.8g}\n".format(*m.emissive))
            f.write(f"Ns {max(0.0, m.shininess):.8g}\n")
            f.write(f"d {min(1.0, max(0.0, m.opacity)):.8g}\n")
            f.write("illum 2\n")
            f.write(f"# Trainz material: {m.name}\n")
            f.write(f"# MATL version: {m.version}; two_sided={int(m.two_sided)}\n")

            diffuse = choose_texture(m, 1)
            opacity_slot = choose_texture(m, 6)
            bump = choose_texture(m, 8)
            reflect = choose_texture(m, 9)

            tex = texture_reference(
                diffuse,
                alpha=False,
                output_dir=path.parent,
                copy_textures=copy_textures,
                texture_dir_name=texture_dir_name,
            )
            if diffuse:
                f.write(f"# Trainz diffuse source: {diffuse.source}\n")
            if tex:
                f.write(f"map_Kd {tex}\n")

            alpha_tex = texture_reference(
                opacity_slot,
                alpha=True,
                output_dir=path.parent,
                copy_textures=copy_textures,
                texture_dir_name=texture_dir_name,
            ) or texture_reference(
                opacity_slot,
                alpha=False,
                output_dir=path.parent,
                copy_textures=copy_textures,
                texture_dir_name=texture_dir_name,
            )
            if not alpha_tex and diffuse and diffuse.alpha_image:
                alpha_tex = texture_reference(
                    diffuse,
                    alpha=True,
                    output_dir=path.parent,
                    copy_textures=copy_textures,
                    texture_dir_name=texture_dir_name,
                )
            if alpha_tex:
                f.write(f"map_d {alpha_tex}\n")

            bump_tex = texture_reference(
                bump,
                alpha=False,
                output_dir=path.parent,
                copy_textures=copy_textures,
                texture_dir_name=texture_dir_name,
            )
            if bump_tex:
                f.write(f"map_Bump {bump_tex}\n")

            reflect_tex = texture_reference(
                reflect,
                alpha=False,
                output_dir=path.parent,
                copy_textures=copy_textures,
                texture_dir_name=texture_dir_name,
            )
            if reflect_tex:
                f.write(f"refl -type sphere {reflect_tex}\n")


def write_obj(
    path: Path,
    mesh: IndexedMesh,
    *,
    flip_v: bool,
    reverse_winding: bool,
    y_up: bool,
    scale: float,
    uv_set: int,
    apply_bones: bool,
    include_vertex_colors: bool,
    copy_textures: bool,
    texture_dir_name: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    material_names = unique_material_names(mesh.chunks)
    mtl_path = path.with_suffix(".mtl")
    write_mtl(
        mtl_path,
        mesh.chunks,
        material_names,
        copy_textures=copy_textures,
        texture_dir_name=texture_dir_name,
    )

    bone_matrices = build_bone_matrices(mesh) if apply_bones and mesh.bones else {}
    missing_parents: set[str] = set()

    vertex_offset = 1
    uv_offset = 1
    normal_offset = 1
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(f"# Trainz/Auran IDXM converted by universal converter {TOOL_VERSION}\n")
        f.write(f"# Source: {mesh.source.name}\n")
        f.write(f"# Resource: {mesh.resource_mode}; Chunks: {len(mesh.chunks)}\n")
        f.write(f"mtllib {mtl_path.name}\n\n")

        for ordinal, (chunk, material_name) in enumerate(zip(mesh.chunks, material_names)):
            g = chunk.geometry
            group_name = safe_name(
                f"chunk_{chunk.index:03d}_{chunk.material.name}",
                f"chunk_{ordinal:03d}",
            )
            f.write(f"o {group_name}\n")
            f.write(f"g {group_name}\n")
            f.write(f"usemtl {material_name}\n")
            f.write(
                f"# CHNK={chunk.version} GEOM={g.version} flags={g.primitive_flags} "
                f"index_bits={g.index_size * 8} parent={g.parent_bone!r}\n"
            )

            parent_matrix: Mat4 | None = None
            if g.parent_bone and apply_bones:
                parent_matrix = bone_matrices.get(g.parent_bone) or bone_matrices.get(g.parent_bone.casefold())
                if parent_matrix is None:
                    missing_parents.add(g.parent_bone)

            for index, vertex in enumerate(g.vertices):
                point = mat4_transform_point(parent_matrix, vertex) if parent_matrix else vertex
                x, y, z = transform_vec(point, y_up, scale)
                if include_vertex_colors and index < len(g.vertex_colors):
                    red, green, blue, _alpha = g.vertex_colors[index]
                    f.write(f"v {x:.9g} {y:.9g} {z:.9g} {red:.7g} {green:.7g} {blue:.7g}\n")
                else:
                    f.write(f"v {x:.9g} {y:.9g} {z:.9g}\n")

            selected_uv = g.uv_sets[uv_set] if 0 <= uv_set < len(g.uv_sets) else None
            if selected_uv is None:
                if uv_set >= len(g.uv_sets):
                    mesh.warnings.append(
                        f"Chunk {chunk.index} has {len(g.uv_sets)} UV sets; requested UV set {uv_set}, using zero UVs"
                    )
                selected_uv = [(0.0, 0.0)] * len(g.vertices)
            for u, v in selected_uv:
                if flip_v:
                    v = 1.0 - v
                f.write(f"vt {u:.9g} {v:.9g}\n")

            for normal in g.normals:
                transformed = mat4_transform_vector(parent_matrix, normal) if parent_matrix else normal
                x, y, z = transform_normal(transformed, y_up)
                f.write(f"vn {x:.9g} {y:.9g} {z:.9g}\n")

            if g.is_triangles:
                usable = len(g.indices) - (len(g.indices) % 3)
                if usable != len(g.indices):
                    mesh.warnings.append(f"Chunk {chunk.index}: ignored {len(g.indices) - usable} trailing triangle indices")
                for i in range(0, usable, 3):
                    tri = list(g.indices[i : i + 3])
                    if reverse_winding:
                        tri[1], tri[2] = tri[2], tri[1]
                    refs = []
                    for idx in tri:
                        refs.append(
                            f"{vertex_offset + idx}/{uv_offset + idx}/{normal_offset + idx}"
                        )
                    f.write("f " + " ".join(refs) + "\n")
            elif g.is_lines:
                usable = len(g.indices) - (len(g.indices) % 2)
                for i in range(0, usable, 2):
                    f.write(f"l {vertex_offset + g.indices[i]} {vertex_offset + g.indices[i + 1]}\n")
            elif g.is_points:
                for idx in g.indices:
                    f.write(f"p {vertex_offset + idx}\n")
            else:
                raise IMError(f"Chunk {chunk.index}: unsupported primitive flags {g.primitive_flags}")

            f.write("\n")
            vertex_offset += len(g.vertices)
            uv_offset += len(g.vertices)
            normal_offset += len(g.vertices)

    for parent in sorted(missing_parents):
        mesh.warnings.append(f"Could not find rigid parent bone {parent!r}; chunk remained in local coordinates")

    write_attachments_csv(path.with_name(path.stem + "_attachments.csv"), mesh, y_up, scale)
    write_bones_csv(path.with_name(path.stem + "_bones.csv"), mesh, bone_matrices, y_up, scale)
    write_report(path.with_name(path.stem + "_report.txt"), mesh, material_names)


def write_attachments_csv(path: Path, mesh: IndexedMesh, y_up: bool, scale: float) -> None:
    if not mesh.attachments:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "x", "y", "z", "m00", "m01", "m02", "m10", "m11", "m12", "m20", "m21", "m22"])
        for att in mesh.attachments:
            writer.writerow([att.name, *transform_vec(att.position, y_up, scale), *att.orientation])


def write_bones_csv(path: Path, mesh: IndexedMesh, matrices: dict[str, Mat4], y_up: bool, scale: float) -> None:
    if not mesh.bones:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "parent", "source", "world_x", "world_y", "world_z"])
        for name, bone in mesh.bones.items():
            matrix = matrices.get(name)
            position = (matrix[3], matrix[7], matrix[11]) if matrix else bone.position
            writer.writerow([name, bone.parent, bone.source, *transform_vec(position, y_up, scale)])


def write_report(path: Path, mesh: IndexedMesh, material_names: Sequence[str]) -> None:
    total_vertices = sum(len(c.geometry.vertices) for c in mesh.chunks)
    total_triangles = sum(len(c.geometry.indices) // 3 for c in mesh.chunks if c.geometry.is_triangles)
    total_lines = sum(len(c.geometry.indices) // 2 for c in mesh.chunks if c.geometry.is_lines)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(f"Converter: {TOOL_VERSION}\n")
        f.write(f"Source: {mesh.source}\n")
        f.write(f"Resource mode: {mesh.resource_mode}\n")
        f.write(f"INFO version: {mesh.info_version}\n")
        f.write(f"Chunks: {len(mesh.chunks)}\n")
        f.write(f"Vertices: {total_vertices}\n")
        f.write(f"Triangles: {total_triangles}\n")
        f.write(f"Lines: {total_lines}\n")
        f.write(f"Attachments: {len(mesh.attachments)}\n")
        f.write(f"Bones: {len(mesh.bones)}\n")
        if mesh.bounds_min is not None:
            f.write(f"Bounds min: {mesh.bounds_min}\nBounds max: {mesh.bounds_max}\n")

        if mesh.warnings:
            f.write("\nWarnings:\n")
            for warning in mesh.warnings:
                f.write(f"- {warning}\n")
        if mesh.unknown_chunks:
            f.write("\nSkipped unknown chunks:\n")
            for chunk in mesh.unknown_chunks:
                f.write(f"- {chunk}\n")

        f.write("\nChunks/materials:\n")
        for chunk, output_name in zip(mesh.chunks, material_names):
            g = chunk.geometry
            f.write(
                f"[{chunk.index}] {output_name} | original={chunk.material.name!r} | "
                f"CHNK={chunk.version} MATL={chunk.material.version} GEOM={g.version} | "
                f"primitive={g.primitive_kind}/{g.primitive_flags} | vertices={len(g.vertices)} | "
                f"indices={len(g.indices)} ({g.index_size * 8}-bit) | uv_sets={len(g.uv_sets)} | "
                f"colors={len(g.vertex_colors)} | tangents={len(g.tangents)} | "
                f"parent={g.parent_bone!r} | trailing={g.trailing_bytes}\n"
            )
            for tex in chunk.material.textures:
                f.write(
                    f"    texture slot={tex.slot_type} amount={tex.amount:g} source={tex.source!r} "
                    f"primary={str(tex.primary_path) if tex.primary_path else tex.primary_image!r} "
                    f"alpha={str(tex.alpha_path) if tex.alpha_path else tex.alpha_image!r}\n"
                )


# --------------------------- inputs / CLI ---------------------------

def files_in_directory(path: Path) -> list[Path]:
    out: list[Path] = []
    try:
        for candidate in path.rglob("*"):
            if candidate.is_file() and candidate.suffix.casefold() == ".im":
                out.append(candidate)
    except (OSError, PermissionError):
        pass
    return sorted(out, key=lambda p: str(p).casefold())


def im_references_from_config(config: Path) -> list[Path]:
    try:
        text = config.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []
    refs = re.findall(r'["\']([^"\']+\.im)["\']|\b([^\s{}]+\.im)\b', text, flags=re.IGNORECASE)
    out: list[Path] = []
    for quoted, bare in refs:
        raw = quoted or bare
        candidate = config.parent / Path(raw.replace("\\", "/"))
        if candidate.is_file():
            out.append(candidate)
    return out


def find_inputs(values: Iterable[str]) -> list[Path]:
    out: list[Path] = []
    seen: set[Path] = set()
    for raw in values:
        path = Path(raw.strip('"')).expanduser()
        if path.is_dir():
            candidates = files_in_directory(path)
        elif path.name.casefold() == "config.txt":
            candidates = im_references_from_config(path)
            if not candidates:
                candidates = files_in_directory(path.parent)
        else:
            candidates = [path]
        for candidate in candidates:
            if candidate.suffix.casefold() != ".im":
                continue
            try:
                key = candidate.resolve()
            except OSError:
                key = candidate.absolute()
            if key not in seen:
                seen.add(key)
                out.append(candidate)
    return out


def common_input_root(inputs: Sequence[Path]) -> Path | None:
    if not inputs:
        return None
    try:
        common = Path(*Path(inputs[0].resolve()).parts)
        common_text = str(inputs[0].resolve().parent)
        for item in inputs[1:]:
            import os
            common_text = os.path.commonpath([common_text, str(item.resolve().parent)])
        return Path(common_text)
    except (OSError, ValueError):
        return None


def output_path_for(source: Path, args: argparse.Namespace, input_root: Path | None) -> Path:
    if not args.output_dir:
        return source.with_suffix(".obj")
    root = Path(args.output_dir).expanduser()
    if args.flat_output or input_root is None:
        return root / f"{source.stem}.obj"
    try:
        relative_parent = source.resolve().parent.relative_to(input_root.resolve())
    except (ValueError, OSError):
        relative_parent = Path()
    return root / relative_parent / f"{source.stem}.obj"


def convert_one(source: Path, output: Path, args: argparse.Namespace) -> tuple[Path, IndexedMesh]:
    if not source.is_file():
        raise IMError(f"Input file does not exist: {source}")
    if source.suffix.casefold() != ".im":
        raise IMError(f"Not an .im file: {source}")
    if args.skip_existing and output.exists():
        raise FileExistsError(f"SKIP_EXISTS:{output}")

    mesh = IMParser(source, salvage=not args.strict).parse()
    asset_root = Path(args.asset_root).expanduser() if args.asset_root else None
    resolve_textures(mesh, asset_root)
    write_obj(
        output,
        mesh,
        flip_v=not args.no_flip_v,
        reverse_winding=args.reverse_winding,
        y_up=args.y_up,
        scale=args.scale,
        uv_set=args.uv_set,
        apply_bones=not args.no_apply_bones,
        include_vertex_colors=args.vertex_colors,
        copy_textures=args.copy_textures,
        texture_dir_name=args.texture_dir,
    )
    return output, mesh


def make_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Batch-convert Trainz/Auran Indexed Mesh (.im / JIRF-IDXM) files to Wavefront OBJ."
    )
    parser.add_argument("inputs", nargs="+", help=".im files, config.txt files or folders; folders are searched recursively")
    parser.add_argument("-o", "--output-dir", help="Output folder. Folder structure is preserved by default")
    parser.add_argument("--flat-output", action="store_true", help="Do not preserve subfolders inside --output-dir")
    parser.add_argument("--asset-root", help="Optional Trainz asset root for texture lookup")
    parser.add_argument("--no-flip-v", action="store_true", help="Do not convert Trainz V coordinates to OBJ convention")
    parser.add_argument("--reverse-winding", action="store_true", help="Reverse triangle winding")
    parser.add_argument("--y-up", action="store_true", help="Convert Z-up to Y-up using (x, z, -y)")
    parser.add_argument("--scale", type=float, default=1.0, help="Uniform scale, default 1.0")
    parser.add_argument("--uv-set", type=int, default=0, help="UV set exported to OBJ, default 0")
    parser.add_argument("--no-apply-bones", action="store_true", help="Do not reconstruct rigid parent-bone transforms")
    parser.add_argument("--vertex-colors", action="store_true", help="Write optional OBJ vertex RGB values for GEOM 104")
    parser.add_argument("--copy-textures", action="store_true", help="Copy resolved source images beside exported OBJ files")
    parser.add_argument("--texture-dir", default="textures", help="Texture subfolder used with --copy-textures")
    parser.add_argument("--strict", action="store_true", help="Abort a file on the first malformed/unknown layout instead of salvaging readable chunks")
    parser.add_argument("--skip-existing", action="store_true", help="Do not overwrite existing OBJ files")
    parser.add_argument("--debug", action="store_true", help="Include Python tracebacks in the batch report")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = make_arg_parser().parse_args(argv)
    if not math.isfinite(args.scale) or args.scale == 0.0:
        print("ERROR: --scale must be a finite non-zero number", file=sys.stderr)
        return 2
    if args.uv_set < 0:
        print("ERROR: --uv-set cannot be negative", file=sys.stderr)
        return 2

    inputs = find_inputs(args.inputs)
    if not inputs:
        print("ERROR: no .im files found", file=sys.stderr)
        return 2

    input_root = common_input_root(inputs)
    if args.output_dir:
        batch_report_dir = Path(args.output_dir).expanduser()
    else:
        batch_report_dir = input_root or inputs[0].parent
    batch_report_dir.mkdir(parents=True, exist_ok=True)
    batch_report = batch_report_dir / "_im_to_obj_batch_report.txt"

    successes: list[str] = []
    failures: list[str] = []
    skipped: list[str] = []

    print(f"Trainz IM -> OBJ universal converter {TOOL_VERSION}")
    print(f"Found {len(inputs)} .im file(s)\n")

    for number, source in enumerate(inputs, start=1):
        output = output_path_for(source, args, input_root)
        try:
            output, mesh = convert_one(source, output, args)
            vertices = sum(len(c.geometry.vertices) for c in mesh.chunks)
            triangles = sum(len(c.geometry.indices) // 3 for c in mesh.chunks if c.geometry.is_triangles)
            warning_suffix = f", warnings={len(mesh.warnings)}" if mesh.warnings else ""
            line = (
                f"OK [{number}/{len(inputs)}] {source} -> {output} "
                f"(chunks={len(mesh.chunks)}, vertices={vertices}, triangles={triangles}{warning_suffix})"
            )
            successes.append(line)
            print(line)
        except FileExistsError as exc:
            if str(exc).startswith("SKIP_EXISTS:"):
                line = f"SKIP [{number}/{len(inputs)}] {source}: output already exists"
                skipped.append(line)
                print(line)
            else:
                failures.append(f"FAIL {source}: {exc}")
                print(f"ERROR: {source}: {exc}", file=sys.stderr)
        except (IMError, OSError, struct.error, ValueError, OverflowError) as exc:
            line = f"FAIL [{number}/{len(inputs)}] {source}: {exc}"
            if args.debug:
                line += "\n" + traceback.format_exc()
            failures.append(line)
            print(f"ERROR: {source}: {exc}", file=sys.stderr)

    with batch_report.open("w", encoding="utf-8", newline="\n") as f:
        f.write(f"Trainz IM -> OBJ universal converter {TOOL_VERSION}\n")
        f.write(f"Input files: {len(inputs)}\nSuccess: {len(successes)}\nSkipped: {len(skipped)}\nFailed: {len(failures)}\n\n")
        if successes:
            f.write("SUCCESS\n-------\n" + "\n".join(successes) + "\n\n")
        if skipped:
            f.write("SKIPPED\n-------\n" + "\n".join(skipped) + "\n\n")
        if failures:
            f.write("FAILED\n------\n" + "\n\n".join(failures) + "\n")

    print(f"\nSummary: {len(successes)} OK, {len(skipped)} skipped, {len(failures)} failed")
    print(f"Batch report: {batch_report}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
