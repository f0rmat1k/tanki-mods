"""Minimal reader for Havok 2020 tagfiles (TAG0) as shipped in the game's .havok files."""
import struct

# subtype format (low 5 bits)
F_VOID, F_OPAQUE, F_BOOL, F_STRING, F_INT, F_FLOAT, F_POINTER, F_RECORD, F_ARRAY, F_TUPLE = range(10)


class HkType:
    def __init__(self, index):
        self.index = index
        self.name = None
        self.templates = []
        self.parent = None
        self.flags = 0
        self.sub_flags = None
        self.pointer = None
        self.version = None
        self.size = None
        self.align = None
        self.members = []

    def resolved(self, attr):
        t = self
        while t is not None:
            v = getattr(t, attr)
            if v is not None:
                return v
            t = t.parent
        return None

    @property
    def format(self):
        f = self.resolved('sub_flags')
        return 0 if f is None else f & 0x1F

    @property
    def byte_size(self):
        return self.resolved('size')

    def all_members(self):
        chain = []
        t = self
        while t is not None:
            chain.append(t)
            t = t.parent
        out = []
        for t in reversed(chain):
            out.extend(t.members)
        return out

    def template(self, name):
        for k, v in self.templates:
            if k == name:
                return v
        return None

    def __repr__(self):
        return '<%s #%d>' % (self.name, self.index)


class Item:
    def __init__(self, type_, offset, count, is_ptr, is_array):
        self.type = type_
        self.offset = offset
        self.count = count
        self.is_ptr = is_ptr
        self.is_array = is_array


def _varint(d, p):
    b = d[p]
    if b & 0x80 == 0:
        return b, p + 1
    if b & 0xC0 == 0x80:
        return ((b << 8) | d[p + 1]) & 0x3FFF, p + 2
    if b & 0xE0 == 0xC0:
        return ((b << 16) | (d[p + 1] << 8) | d[p + 2]) & 0x1FFFFF, p + 3
    if b & 0xF8 == 0xE0:
        return int.from_bytes(d[p:p + 4], 'big') & 0x07FFFFFF, p + 4
    if b & 0xF8 == 0xE8:
        return int.from_bytes(d[p:p + 5], 'big') & 0x07FFFFFFFF, p + 5
    if b & 0xF8 == 0xF8:
        return int.from_bytes(d[p + 1:p + 9], 'big'), p + 9
    raise ValueError('bad varint 0x%02x at %d' % (b, p))


class TagFile:
    def __init__(self, data):
        self.d = data
        self.sections = {}
        self._walk(0, len(data))
        self.data_off, self.data_size = self.sections['DATA']
        self._read_types()
        self._read_items()

    def _walk(self, p, end):
        while p < end:
            h = struct.unpack('>I', self.d[p:p + 4])[0]
            size, leaf = h & 0x3FFFFFFF, h >> 30
            tag = self.d[p + 4:p + 8].decode('latin1')
            if leaf == 0:
                self._walk(p + 8, p + size)
            else:
                self.sections[tag] = (p + 8, size - 8)
            p += size

    def _strings(self, tag):
        off, size = self.sections[tag]
        return [s.decode('utf-8', 'replace') for s in self.d[off:off + size].split(b'\0')]

    def _read_types(self):
        d = self.d
        tnames = self._strings('TST1')
        fnames = self._strings('FST1')
        off, size = self.sections['TNA1']
        p, end = off, off + size
        count, p = _varint(d, p)
        self.types = [None] + [HkType(i) for i in range(1, count)]
        for i in range(1, count):
            t = self.types[i]
            ni, p = _varint(d, p)
            t.name = tnames[ni]
            nt, p = _varint(d, p)
            for _ in range(nt):
                ki, p = _varint(d, p)
                v, p = _varint(d, p)
                key = tnames[ki]
                if key.startswith('t'):
                    v = self.types[v] if v else None
                t.templates.append((key, v))
        off, size = self.sections['TBDY']
        p, end = off, off + size
        while p < end:
            ti, p = _varint(d, p)
            if ti == 0:
                continue
            t = self.types[ti]
            pi, p = _varint(d, p)
            t.parent = self.types[pi] if pi else None
            t.flags, p = _varint(d, p)
            if t.flags & 0x01:
                t.sub_flags, p = _varint(d, p)
            if t.flags & 0x02:
                v, p = _varint(d, p)
                t.pointer = self.types[v] if v else None
            if t.flags & 0x04:
                t.version, p = _varint(d, p)
            if t.flags & 0x08:
                t.size, p = _varint(d, p)
                t.align, p = _varint(d, p)
            if t.flags & 0x10:
                _, p = _varint(d, p)
            if t.flags & 0x20:
                n, p = _varint(d, p)
                for _ in range(n & 0xFFFF):  # high bits carry extra flags
                    fi, p = _varint(d, p)
                    mf, p = _varint(d, p)
                    mo, p = _varint(d, p)
                    mt, p = _varint(d, p)
                    t.members.append((fnames[fi], mf, mo, self.types[mt]))
            if t.flags & 0x40:
                n, p = _varint(d, p)
                for _ in range(n):
                    _, p = _varint(d, p)
                    _, p = _varint(d, p)
            if t.flags & 0x80:
                _, p = _varint(d, p)

    def _read_items(self):
        off, size = self.sections['ITEM']
        self.items = []
        for i in range(size // 12):
            tf, o, c = struct.unpack_from('<III', self.d, off + i * 12)
            ti = tf & 0xFFFFFF
            self.items.append(Item(self.types[ti] if ti else None, o, c,
                                   bool(tf & 0x10000000), bool(tf & 0x20000000)))

    def dump_types(self):
        lines = []
        for t in self.types[1:]:
            lines.append('#%d %s parent=%s fmt=%s sub=%s ptr=%s size=%s tmpl=%s' % (
                t.index, t.name, t.parent and t.parent.name, t.format,
                None if t.sub_flags is None else hex(t.sub_flags),
                t.pointer and t.pointer.name, t.byte_size,
                [(k, v.name if isinstance(v, HkType) else v) for k, v in t.templates]))
            for m in t.members:
                lines.append('    %s off=%d flags=%d type=%s' % (m[0], m[2], m[1], m[3].name))
        return '\n'.join(lines)
