"""Read ESRI shapefile point layers (DBF attributes plus SHP geometry) with the standard library.

Only what the well pages need: every DBF field decoded to Python values, point coordinates
aligned to their records, and the .prj text so callers can tell geographic from projected layers.
Deleted DBF records are skipped but counted. Rows carry three private keys: ``_index`` (0-based
record number), ``_x`` and ``_y`` (point coordinates, or None for a null shape).
"""
from __future__ import annotations

import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path

POINT_SHAPES = {1: 'Point', 11: 'PointZ', 21: 'PointM'}
MAX_MEMBER_BYTES = 2 * 1024 * 1024 * 1024   # downloaded archives are untrusted input
MAX_RATIO = 300


@dataclass(frozen=True)
class Field:
    name: str
    type: str
    length: int
    decimals: int


@dataclass
class Layer:
    name: str
    fields: list
    rows: list
    deleted: int
    shape_type: int | None
    prj: str

    @property
    def geographic(self) -> bool:
        """True when the .prj describes latitude/longitude (GEOGCS) rather than a projection."""
        return self.prj.strip().upper().startswith('GEOGCS')

    @property
    def field_names(self) -> list:
        return [f.name for f in self.fields]


def _convert(raw: bytes, f: Field, encoding: str):
    text = raw.decode(encoding, 'replace').strip(' \x00')
    if not text:
        return None
    if f.type in ('N', 'F'):
        try:
            if f.decimals == 0 and '.' not in text and 'e' not in text.lower():
                return int(text)
            return float(text)
        except ValueError:
            return text
    if f.type == 'D':
        if len(text) == 8 and text.isdigit() and text[:4] != '0000' and '01' <= text[4:6] <= '12' and '01' <= text[6:] <= '31':
            return f'{text[:4]}-{text[4:6]}-{text[6:]}'
        return None
    if f.type == 'L':
        return True if text in 'TtYy' else False if text in 'FfNn' else None
    return text


def read_dbf(data: bytes, encoding: str = 'cp1252'):
    """Return (fields, rows, deleted_count). Field names are lower-cased."""
    if len(data) < 32:
        raise ValueError('Truncated DBF header')
    count = struct.unpack_from('<I', data, 4)[0]
    start, step = struct.unpack_from('<HH', data, 8)
    fields, pos = [], 32
    while pos < start:
        if data[pos:pos + 1] == b'\r':
            break
        descriptor = data[pos:pos + 32]
        if len(descriptor) != 32:
            raise ValueError('Truncated DBF field descriptor')
        name = descriptor[:11].split(b'\0')[0].decode('ascii', 'replace').strip().lower()
        fields.append(Field(name, chr(descriptor[11]), descriptor[16], descriptor[17]))
        pos += 32
    if 1 + sum(f.length for f in fields) != step:
        raise ValueError(f'DBF record length mismatch: header says {step}, field widths sum to {1 + sum(f.length for f in fields)}')
    if start + count * step > len(data):
        raise ValueError('Truncated DBF records')
    rows, deleted = [], 0
    for index in range(count):
        base = start + index * step
        if data[base:base + 1] == b'*':
            deleted += 1
            continue
        row, offset = {'_index': index}, base + 1
        for f in fields:
            row[f.name] = _convert(data[offset:offset + f.length], f, encoding)
            offset += f.length
        rows.append(row)
    return fields, rows, deleted


def read_shp(data: bytes):
    """Return (shape_type, points) where points[i] is (x, y) or None, in record order."""
    if len(data) < 100 or struct.unpack_from('>i', data, 0)[0] != 9994:
        raise ValueError('Not a shapefile (bad file code)')
    file_length = struct.unpack_from('>i', data, 24)[0] * 2
    shape_type = struct.unpack_from('<i', data, 32)[0]
    if shape_type not in POINT_SHAPES:
        raise ValueError(f'Shape type {shape_type} is not a point layer; this reader handles Point, PointZ and PointM')
    points, pos, end = [], 100, min(file_length, len(data))
    while pos + 8 <= end:
        _number, length = struct.unpack_from('>ii', data, pos)
        pos += 8
        content = data[pos:pos + length * 2]
        pos += length * 2
        if len(content) < 20 or struct.unpack_from('<i', content, 0)[0] == 0:
            points.append(None)
            continue
        points.append(struct.unpack_from('<dd', content, 4))
    return shape_type, points


def _members(path: Path, member: str | None):
    """Yield (name, bytes) for the .dbf/.shp/.prj of one layer inside a zip or a bare .shp/.dbf path."""
    if path.suffix.lower() == '.zip':
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if not n.endswith('/')]
            dbfs = sorted(n for n in names if n.lower().endswith('.dbf'))
            if member:
                dbfs = [n for n in dbfs if Path(n).stem.lower() == member.lower()]
            if not dbfs:
                raise FileNotFoundError(f'No .dbf layer{" named " + member if member else ""} in {path.name}')
            if len(dbfs) > 1 and not member:
                preferred = [n for n in dbfs if Path(n).stem.lower() == path.stem.lower()]
                dbfs = preferred or dbfs[:1]
            stem = dbfs[0][:-4]
            found = {}
            for ext in ('.dbf', '.shp', '.prj'):
                for n in names:
                    if n.lower() == (stem + ext).lower():
                        info = archive.getinfo(n)
                        if info.file_size > MAX_MEMBER_BYTES:
                            raise ValueError(f'{n} expands to {info.file_size:,} bytes, above the {MAX_MEMBER_BYTES:,} byte limit')
                        if info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
                            raise ValueError(f'{n} has a suspicious compression ratio')
                        found[ext] = archive.read(n)
            return Path(stem).name, found
    stem = path.with_suffix('')
    found = {}
    for ext in ('.dbf', '.shp', '.prj'):
        candidate = stem.with_suffix(ext)
        if candidate.exists():
            found[ext] = candidate.read_bytes()
    if '.dbf' not in found:
        raise FileNotFoundError(f'No .dbf beside {path}')
    return stem.name, found


def load_layer(path, member: str | None = None, encoding: str = 'cp1252') -> Layer:
    """Load a point layer from a .zip (first/named .dbf inside) or from a .shp/.dbf path."""
    name, found = _members(Path(path), member)
    fields, rows, deleted = read_dbf(found['.dbf'], encoding)
    shape_type, points = (None, None)
    if '.shp' in found:
        shape_type, points = read_shp(found['.shp'])
    for row in rows:
        point = points[row['_index']] if points is not None and row['_index'] < len(points) else None
        row['_x'], row['_y'] = (point if point else (None, None))
    return Layer(name, fields, rows, deleted, shape_type, found.get('.prj', b'').decode('ascii', 'replace'))


def write_layer(folder: Path, name: str, fields, rows, prj: str | None = None, encoding: str = 'cp1252'):
    """Write a minimal point shapefile. ``fields`` is a list of Field; rows are dicts with
    the field names plus optional ``_x``/``_y``. Used by tests and fixtures; not needed in production."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    header_len = 32 + 32 * len(fields) + 1
    record_len = 1 + sum(f.length for f in fields)
    out = bytearray(struct.pack('<BBBBIHH20x', 3, 24, 1, 1, len(rows), header_len, record_len))
    for f in fields:
        out += struct.pack('<11sc4xBB14x', f.name.encode('ascii'), f.type.encode('ascii'), f.length, f.decimals)
    out += b'\r'
    for row in rows:
        out += b'*' if row.get('_deleted') else b' '
        for f in fields:
            value = row.get(f.name)
            if value is None:
                text = ''
            elif f.type == 'D':
                text = str(value).replace('-', '')
            elif f.type in ('N', 'F'):
                text = (f'{value:.{f.decimals}f}' if f.decimals else str(int(value))).rjust(f.length)
            elif f.type == 'L':
                text = 'T' if value else 'F'
            else:
                text = str(value)
            out += text.encode(encoding, 'replace')[:f.length].ljust(f.length)
    out += b'\x1a'
    (folder / f'{name}.dbf').write_bytes(out)
    records = bytearray()
    xs = [r['_x'] for r in rows if r.get('_x') is not None]
    ys = [r['_y'] for r in rows if r.get('_y') is not None]
    for number, row in enumerate(rows, 1):
        if row.get('_x') is None:
            content = struct.pack('<i', 0)
        else:
            content = struct.pack('<idd', 1, row['_x'], row['_y'])
        records += struct.pack('>ii', number, len(content) // 2) + content
    bbox = (min(xs), min(ys), max(xs), max(ys)) if xs else (0.0, 0.0, 0.0, 0.0)
    shp_header = struct.pack('>i20xi', 9994, (100 + len(records)) // 2)
    shp_header += struct.pack('<ii4d4d', 1000, 1, *bbox, 0.0, 0.0, 0.0, 0.0)
    (folder / f'{name}.shp').write_bytes(shp_header + records)
    if prj is not None:
        (folder / f'{name}.prj').write_text(prj, encoding='ascii')
    return folder / f'{name}.shp'
