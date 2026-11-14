"""Decoder for BigWorld packed XML (magic 0x62A14E45) used in game client files."""
import base64
import struct
import xml.etree.ElementTree as ET

PACKED_MAGIC = 0x62A14E45


class Reader:
    def __init__(self, data):
        self.d = data
        self.p = 0

    def read(self, n):
        b = self.d[self.p:self.p + n]
        self.p += n
        return b

    def u16(self):
        return struct.unpack('<H', self.read(2))[0]

    def u32(self):
        return struct.unpack('<I', self.read(4))[0]


def _desc(r):
    v = r.u32()
    return v & 0x0FFFFFFF, v >> 28


def _read_element(r, elem, names):
    count = r.u16()
    self_end, self_type = _desc(r)
    children = []
    for _ in range(count):
        name_idx = r.u16()
        children.append((name_idx, _desc(r)))
    offset = _read_data(r, elem, 0, self_end, self_type, names)
    for name_idx, (end, typ) in children:
        node = ET.SubElement(elem, names[name_idx])
        offset = _read_data(r, node, offset, end, typ, names)


def _read_data(r, elem, offset, end, typ, names):
    length = end - offset
    if typ == 0:
        _read_element(r, elem, names)
    elif typ == 1:
        elem.text = r.read(length).decode('utf-8', 'replace')
    elif typ == 2:
        raw = r.read(length)
        fmt = {0: None, 1: '<b', 2: '<h', 4: '<i', 8: '<q'}.get(length)
        elem.text = '0' if fmt is None else str(struct.unpack(fmt, raw)[0])
    elif typ == 3:
        n = length // 4
        vals = struct.unpack('<%df' % n, r.read(length))
        if n == 12:
            for i in range(4):
                row = ET.SubElement(elem, 'row%d' % i)
                row.text = ' '.join('%g' % x for x in vals[i * 3:i * 3 + 3])
        else:
            elem.text = ' '.join('%g' % x for x in vals)
    elif typ == 4:
        raw = r.read(length)
        elem.text = 'true' if raw and raw[0] else 'false'
    elif typ == 5:
        elem.text = base64.b64encode(r.read(length)).decode('ascii')
    else:
        elem.text = r.read(length).hex()
    return end


def parse(data):
    """Return an ElementTree root for packed or plain XML bytes."""
    if len(data) >= 4 and struct.unpack('<I', data[:4])[0] == PACKED_MAGIC:
        r = Reader(data)
        r.p = 5
        names = []
        while True:
            e = r.d.index(b'\x00', r.p)
            s = r.d[r.p:e].decode('utf-8', 'replace')
            r.p = e + 1
            if not s:
                break
            names.append(s)
        root = ET.Element('root')
        _read_element(r, root, names)
        return root
    return ET.fromstring(data)


def text(elem, path, default=None):
    node = elem.find(path)
    if node is None or node.text is None:
        return default
    return node.text.strip()
