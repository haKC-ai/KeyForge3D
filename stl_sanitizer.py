"""
stl_sanitizer.py

Strips creation artifacts from STL files output by KeyForge3D so no
forensic trace of the generating software, timestamp, or authoring tool
survives in the output mesh file.

STL binary format: 80-byte header (often contains slicer/tool info) +
4-byte triangle count + triangle data. Nothing else to strip.

STL ASCII format: `solid <name>` opener can carry the tool name;
comment lines (`#`) may appear in non-compliant variants. We normalize
to anonymous binary STL (no header info, no name) regardless of input.

Usage (standalone):
    python stl_sanitizer.py key_model.stl           # in-place
    python stl_sanitizer.py key_model.stl clean.stl  # explicit output
"""
import struct
import sys
import os
import re

_BINARY_HEADER_SIZE = 80
_BINARY_TRIANGLE_HEADER = 4  # uint32 triangle count
_BINARY_TRIANGLE_SIZE = 50   # 12*3 floats (normal+v1+v2+v3) + 2-byte attr


def _is_binary_stl(data: bytes) -> bool:
    """Heuristic: ASCII STL starts with 'solid'; binary may too but size check resolves."""
    if len(data) < _BINARY_HEADER_SIZE + _BINARY_TRIANGLE_HEADER:
        return False
    if data[:5].lower() == b"solid":
        # Could still be binary — verify size matches declared triangle count
        try:
            n_triangles = struct.unpack_from("<I", data, _BINARY_HEADER_SIZE)[0]
            expected = _BINARY_HEADER_SIZE + _BINARY_TRIANGLE_HEADER + n_triangles * _BINARY_TRIANGLE_SIZE
            return abs(len(data) - expected) < 16  # small slack for alignment
        except Exception:
            return False
    return True


def _ascii_to_binary(data: bytes) -> bytes:
    """Parse ASCII STL and re-serialize as binary with zeroed header."""
    text = data.decode("utf-8", errors="ignore")
    # Extract all vertex triplets from facets
    triangles = []
    normal_pat = re.compile(r"facet normal\s+([\-\d.eE+]+)\s+([\-\d.eE+]+)\s+([\-\d.eE+]+)")
    vertex_pat = re.compile(r"vertex\s+([\-\d.eE+]+)\s+([\-\d.eE+]+)\s+([\-\d.eE+]+)")

    normals = normal_pat.findall(text)
    vertices = vertex_pat.findall(text)

    if len(vertices) % 3 != 0:
        raise ValueError("Malformed ASCII STL: vertex count not divisible by 3")

    n_tri = len(vertices) // 3
    out = bytearray(_BINARY_HEADER_SIZE)  # zeroed header
    out += struct.pack("<I", n_tri)

    for i in range(n_tri):
        nx, ny, nz = (float(v) for v in normals[i]) if i < len(normals) else (0.0, 0.0, 0.0)
        v0 = [float(c) for c in vertices[i * 3]]
        v1 = [float(c) for c in vertices[i * 3 + 1]]
        v2 = [float(c) for c in vertices[i * 3 + 2]]
        out += struct.pack("<fff", nx, ny, nz)
        out += struct.pack("<fff", *v0)
        out += struct.pack("<fff", *v1)
        out += struct.pack("<fff", *v2)
        out += b"\x00\x00"  # attribute byte count — zero

    return bytes(out)


def _strip_binary_header(data: bytes) -> bytes:
    """Zero the 80-byte header of a binary STL, leave geometry intact."""
    return b"\x00" * _BINARY_HEADER_SIZE + data[_BINARY_HEADER_SIZE:]


def sanitize(input_path: str, output_path: str | None = None) -> str:
    """
    Sanitize an STL file by stripping all header/creation metadata.
    Converts ASCII STL to binary (eliminates 'solid <name>' authoring trace).
    Returns path to the sanitized file.
    """
    if output_path is None:
        output_path = input_path

    with open(input_path, "rb") as f:
        data = f.read()

    if _is_binary_stl(data):
        clean = _strip_binary_header(data)
    else:
        clean = _ascii_to_binary(data)

    with open(output_path, "wb") as f:
        f.write(clean)

    return output_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <input.stl> [output.stl]")
        sys.exit(1)
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else None
    result = sanitize(src, dst)
    print(f"[+] Sanitized → {result}")
