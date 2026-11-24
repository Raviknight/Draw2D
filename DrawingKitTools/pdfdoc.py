# SPDX-License-Identifier: LGPL-2.1-or-later
"""Put TechDraw's sheet PDFs together into the file DrawingKit hands out.

TechDraw (through Qt) draws each sheet into its own one-page PDF. This
module reads those files and writes one new PDF from them:

- every sheet a page, in order (one file for the whole drawing),
- the document properties filled in (title, drawing number, revision...),
- files attached inside it (the characteristics list), so the drawing
  and its data travel together.

It reads the plain PDFs Qt writes (classic cross-reference tables, also
with DrawingKit's own incremental update for the text layer on top); it
isn't a general PDF library.
"""

import datetime
import hashlib
import re
import zlib

_WS = b" \t\r\n\f\x00"
_DELIM = b"()<>[]{}/%"


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def _skip_ws(b, i):
    while i < len(b):
        if b[i:i + 1] in (b" ", b"\t", b"\r", b"\n", b"\f", b"\x00"):
            i += 1
        elif b[i:i + 1] == b"%":                    # comment to end of line
            while i < len(b) and b[i:i + 1] not in (b"\r", b"\n"):
                i += 1
        else:
            break
    return i


def _string_end(b, i):
    """b[i] == '(' : index after the matching ')'."""
    depth = 0
    while i < len(b):
        c = b[i:i + 1]
        if c == b"\\":
            i += 2
            continue
        if c == b"(":
            depth += 1
        elif c == b")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("unterminated string")


def value_end(b, i):
    """Index just after the PDF value starting at (or after blanks from) i."""
    i = _skip_ws(b, i)
    if b[i:i + 2] == b"<<":
        depth = 0
        while i < len(b):
            if b[i:i + 2] == b"<<":
                depth += 1
                i += 2
            elif b[i:i + 2] == b">>":
                depth -= 1
                i += 2
                if depth == 0:
                    return i
            elif b[i:i + 1] == b"(":
                i = _string_end(b, i)
            elif b[i:i + 1] == b"<":
                i = b.index(b">", i) + 1
            else:
                i += 1
        raise ValueError("unterminated dictionary")
    c = b[i:i + 1]
    if c == b"[":
        i += 1
        while True:
            i = _skip_ws(b, i)
            if b[i:i + 1] == b"]":
                return i + 1
            i = value_end(b, i)
    if c == b"(":
        return _string_end(b, i)
    if c == b"<":
        return b.index(b">", i) + 1
    if c == b"/":
        i += 1
        while i < len(b) and b[i:i + 1] not in _WS and b[i:i + 1] not in _DELIM:
            i += 1
        return i
    # a number, keyword (true/false/null) or a reference 'n g R'
    m = re.compile(rb"(\d+)\s+(\d+)\s+R(?![A-Za-z])").match(b, i)
    if m:
        return m.end()
    m = re.compile(rb"[-+]?[\d.]+|[A-Za-z]+").match(b, i)
    if not m:
        raise ValueError("no value at %d" % i)
    return m.end()


def dict_items(d):
    """[(key, value bytes)] of a dictionary's top level."""
    d = d.strip()
    if not d.startswith(b"<<"):
        raise ValueError("not a dictionary")
    out = []
    i = 2
    while True:
        i = _skip_ws(d, i)
        if d[i:i + 2] == b">>":
            return out
        if d[i:i + 1] != b"/":
            raise ValueError("bad key at %d" % i)
        k_end = value_end(d, i)
        key = d[i + 1:k_end]
        v_start = _skip_ws(d, k_end)
        v_end = value_end(d, v_start)
        out.append((key.decode("latin-1"), d[v_start:v_end]))
        i = v_end


def make_dict(items):
    return b"<< " + b" ".join(b"/" + k.encode("latin-1") + b" " + v for k, v in items) + b" >>"


def get(d, key):
    for k, v in dict_items(d):
        if k == key:
            return v
    return None


def set_key(d, key, value):
    """The dictionary with ``key`` set to ``value`` (bytes), or removed if None."""
    items = [(k, v) for k, v in dict_items(d) if k != key]
    if value is not None:
        items.append((key, value))
    return make_dict(items)


def ref(v):
    m = re.fullmatch(rb"\s*(\d+)\s+\d+\s+R\s*", v or b"")
    return int(m.group(1)) if m else None


class Pdf:
    """The live objects of a PDF: {number: [head bytes, stream data or None]}."""

    def __init__(self, raw):
        self.raw = raw
        if b"/Encrypt" in raw:
            raise ValueError("encrypted PDF")
        self.offsets, self.trailer = self._xref()
        self.objects = {}
        for num, off in self.offsets.items():
            if off is not None:
                self.objects[num] = self._object(num, off)

    @classmethod
    def open(cls, path):
        with open(path, "rb") as f:
            return cls(f.read())

    def _xref(self):
        raw = self.raw
        starts = re.findall(rb"startxref\s+(\d+)", raw)
        if not starts:
            raise ValueError("no cross-reference table")
        pos = int(starts[-1])
        offsets, trailer, seen = {}, None, set()
        while pos is not None and pos not in seen:
            seen.add(pos)
            if raw[pos:pos + 4] != b"xref":
                raise ValueError("cross-reference streams aren't supported")
            t = raw.index(b"trailer", pos)
            toks = raw[pos + 4:t].split()
            k = 0
            while k + 1 < len(toks):
                start, count = int(toks[k]), int(toks[k + 1])
                k += 2
                for n in range(count):
                    off, _gen, kind = toks[k:k + 3]
                    k += 3
                    # the newest table is read first: it wins
                    offsets.setdefault(start + n, int(off) if kind == b"n" else None)
            ts = _skip_ws(raw, t + 7)
            tdict = raw[ts:value_end(raw, ts)]
            if trailer is None:
                trailer = tdict
            prev = get(tdict, "Prev")
            pos = int(prev) if prev else None
        return offsets, trailer

    def _length(self, head):
        v = get(head, "Length")
        r = ref(v)
        if r is None:
            return int(v)
        m = re.compile(rb"\s*%d\s+\d+\s+obj\s*(\d+)" % r).match(self.raw, self.offsets[r])
        return int(m.group(1))

    def _object(self, num, off):
        raw = self.raw
        m = re.compile(rb"\s*(\d+)\s+(\d+)\s+obj").match(raw, off)
        if not m or int(m.group(1)) != num:
            raise ValueError("object %d isn't where the table says" % num)
        s = _skip_ws(raw, m.end())
        e = value_end(raw, s)
        head = raw[s:e]
        k = _skip_ws(raw, e)
        if raw[k:k + 6] == b"stream":
            k += 6
            if raw[k:k + 2] == b"\r\n":
                k += 2
            elif raw[k:k + 1] in (b"\n", b"\r"):
                k += 1
            n = self._length(head)
            return [head, raw[k:k + n]]
        return [head, None]

    def head(self, num):
        return self.objects[num][0]

    def root(self):
        return ref(get(self.trailer, "Root"))

    def info(self):
        n = ref(get(self.trailer, "Info"))
        return self.head(n) if n in self.objects else None

    def pages(self):
        """[(page number, {inherited key: value})] in order."""
        out = []

        def walk(num, inherited):
            d = self.head(num)
            kind = get(d, "Type")
            if kind == b"/Pages":
                inh = dict(inherited)
                for k in ("Resources", "MediaBox", "CropBox", "Rotate"):
                    v = get(d, k)
                    if v is not None:
                        inh[k] = v
                kids = get(d, "Kids") or b"[]"
                for r in re.findall(rb"(\d+)\s+\d+\s+R", kids):
                    walk(int(r), inh)
            else:
                out.append((num, inherited))
        walk(ref(get(self.head(self.root()), "Pages")), {})
        return out


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def text_string(s):
    """A PDF text string: plain when it's ASCII, else UTF-16 with a BOM."""
    s = str(s)
    try:
        b = s.encode("ascii")
        return b"(" + b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"
    except UnicodeEncodeError:
        return b"<FEFF" + s.encode("utf-16-be").hex().upper().encode() + b">"


def pdf_date(when=None):
    when = when or datetime.datetime.now().astimezone()
    off = when.utcoffset() or datetime.timedelta(0)
    mins = int(off.total_seconds() // 60)
    sign = "+" if mins >= 0 else "-"
    return "D:%s%s%02d'%02d'" % (when.strftime("%Y%m%d%H%M%S"), sign, abs(mins) // 60, abs(mins) % 60)


def _renumber(b, table):
    """Point a dictionary's references at the new object numbers."""
    def sub(m):
        n = table.get(int(m.group(1)))
        return b"%d 0 R" % n if n else b"null"
    # strings are left alone: references only stand outside them
    out, i = bytearray(), 0
    for m in re.finditer(rb"\((?:\\.|[^\\()]|\((?:\\.|[^\\()])*\))*\)", b, re.S):
        out += re.sub(rb"(?<![\d.])(\d+)\s+\d+\s+R(?![A-Za-z])", sub, b[i:m.start()])
        out += m.group(0)
        i = m.end()
    out += re.sub(rb"(?<![\d.])(\d+)\s+\d+\s+R(?![A-Za-z])", sub, b[i:])
    return bytes(out)


class Writer:
    def __init__(self):
        self.objects = []                      # [(head, stream data or None)]

    def reserve(self):
        self.objects.append(None)
        return len(self.objects)

    def put(self, num, head, data=None):
        self.objects[num - 1] = (head, data)

    def add(self, head, data=None):
        n = self.reserve()
        self.put(n, head, data)
        return n

    def stream(self, head_items, data, compress=True):
        items = [(k, v) for k, v in head_items if k not in ("Length", "Filter")]
        if compress:
            data = zlib.compress(data, 9)
            items.append(("Filter", b"/FlateDecode"))
        items.append(("Length", b"%d" % len(data)))
        return self.add(make_dict(items), data)

    def bytes(self, root, info=None):
        out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, (head, data) in enumerate(self.objects):
            offsets.append(len(out))
            out += b"%d 0 obj\n" % (i + 1) + head
            if data is not None:
                out += b"\nstream\n" + data + b"\nendstream"
            out += b"\nendobj\n"
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(self.objects) + 1)
        for off in offsets:
            out += b"%010d 00000 n \n" % off
        doc_id = hashlib.md5(bytes(out)).hexdigest().upper().encode()
        out += b"trailer\n<< /Size %d /Root %d 0 R" % (len(self.objects) + 1, root)
        if info:
            out += b" /Info %d 0 R" % info
        out += b" /ID [<%s> <%s>] >>\nstartxref\n%d\n%%%%EOF\n" % (doc_id, doc_id, xref)
        return bytes(out)


INFO_KEYS = ("Title", "Author", "Subject", "Keywords", "Creator")

# Qt sometimes writes a colour space and then SCN / scn with no colour
# ("/CSp CS SCN"). Setting the space already makes the colour its first
# (black), so the bare operator adds nothing, but some readers (Windows'
# own PDF engine) stop drawing the page at it.
_BARE_COLOUR = re.compile(rb"(\b(?:CS|cs)\s+)(?:SCN|scn)(?:\s|$)")


def clean_content(data):
    """Page drawing commands without the bare SCN / scn operators."""
    return _BARE_COLOUR.sub(rb"\1", data)


def clean_stream(head, data):
    """A page content or form stream with clean_content applied; anything
    else (fonts, images) as it was."""
    if data is None or get(head, "Filter") not in (None, b"/FlateDecode"):
        return head, data
    sub = get(head, "Subtype")
    if sub not in (None, b"/Form") or get(head, "Length1") is not None \
            or get(head, "Type") not in (None, b"/XObject"):
        return head, data
    flate = get(head, "Filter") == b"/FlateDecode"
    try:
        plain = zlib.decompress(data) if flate else data
    except zlib.error:
        return head, data
    fixed = clean_content(plain)
    if fixed == plain:
        return head, data
    data = zlib.compress(fixed, 9) if flate else fixed
    return set_key(head, "Length", b"%d" % len(data)), data


def assemble(sources, info=None, attachments=(), custom=None):
    """One PDF (bytes) with every page of ``sources`` (paths or bytes) in
    order. ``info``: {'Title': ..., 'Author': ..., 'Subject': ...,
    'Keywords': ..., 'Creator': ...}; ``custom``: extra properties
    ({'DrawingNumber': 'B-100', ...}); ``attachments``: [(file name, data
    bytes, MIME type, description)] carried inside the PDF."""
    w = Writer()
    pages_no = w.reserve()
    kids = []
    producer = None
    for src in sources:
        pdf = Pdf(src) if isinstance(src, (bytes, bytearray)) else Pdf.open(src)
        old_info = pdf.info()
        if old_info is not None and producer is None:
            producer = get(old_info, "Producer")
        skip = {pdf.root()}
        n = ref(get(pdf.trailer, "Info"))
        if n is not None:
            skip.add(n)
        page_list = pdf.pages()

        def tree(num):                          # the Pages nodes: rebuilt
            d = pdf.head(num)
            if get(d, "Type") == b"/Pages":
                skip.add(num)
                for r in re.findall(rb"(\d+)\s+\d+\s+R", get(d, "Kids") or b"[]"):
                    tree(int(r))
        tree(ref(get(pdf.head(pdf.root()), "Pages")))
        table = {}
        for num in sorted(pdf.objects):
            if num not in skip:
                table[num] = w.reserve()
        for num in sorted(table):
            head, data = pdf.objects[num]
            head, data = clean_stream(head, data)
            w.put(table[num], _renumber(head, table), data)
        for num, inherited in page_list:
            d = w.objects[table[num] - 1][0]
            for k, v in inherited.items():
                if get(d, k) is None:
                    d = set_key(d, k, _renumber(v, table))
            d = set_key(d, "Parent", b"%d 0 R" % pages_no)
            w.put(table[num], d)
            kids.append(table[num])
    w.put(pages_no, make_dict([("Type", b"/Pages"), ("Kids", b"[" + b" ".join(
        b"%d 0 R" % k for k in kids) + b"]"), ("Count", b"%d" % len(kids))]))
    cat = [("Type", b"/Catalog"), ("Pages", b"%d 0 R" % pages_no)]
    specs = []
    now = text_string(pdf_date())
    for name, data, mime, desc in attachments:
        ef = w.stream([("Type", b"/EmbeddedFile"),
                       ("Subtype", b"/" + mime.replace("/", "#2F").encode()),
                       ("Params", make_dict([("Size", b"%d" % len(data)), ("ModDate", now),
                                             ("CheckSum", b"<" + hashlib.md5(data).hexdigest()
                                              .upper().encode() + b">")]))], data)
        items = [("Type", b"/Filespec"), ("F", text_string(name)), ("UF", text_string(name)),
                 ("EF", make_dict([("F", b"%d 0 R" % ef), ("UF", b"%d 0 R" % ef)])),
                 ("AFRelationship", b"/Data")]
        if desc:
            items.append(("Desc", text_string(desc)))
        specs.append((name, w.add(make_dict(items))))
    if specs:
        names = b"[" + b" ".join(text_string(n) + b" %d 0 R" % s
                                 for n, s in sorted(specs)) + b"]"
        cat.append(("Names", make_dict([("EmbeddedFiles", make_dict([("Names", names)]))])))
        cat.append(("AF", b"[" + b" ".join(b"%d 0 R" % s for _n, s in specs) + b"]"))
    root = w.add(make_dict(cat))
    items = []
    for k in INFO_KEYS:
        v = (info or {}).get(k)
        if v:
            items.append((k, text_string(v)))
    for k, v in (custom or {}).items():
        if v and re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", k) and k not in INFO_KEYS:
            items.append((k, text_string(v)))
    if producer:
        items.append(("Producer", producer))
    items += [("CreationDate", now), ("ModDate", now)]
    info_no = w.add(make_dict(items))
    return w.bytes(root, info_no)


def write(path, sources, **kw):
    """assemble() into ``path`` (written whole, then swapped in)."""
    import os
    data = assemble(sources, **kw)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------------------
# Reading back (tests, and anyone checking a file)
# ---------------------------------------------------------------------------
def summary(src):
    """{'pages': n, 'info': {key: text}, 'attachments': {name: bytes}}."""
    pdf = Pdf(src) if isinstance(src, (bytes, bytearray)) else Pdf.open(src)
    info = {}
    d = pdf.info()
    if d is not None:
        for k, v in dict_items(d):
            info[k] = _text(v)
    att = {}
    names = get(pdf.head(pdf.root()), "Names")
    if names:
        ef = get(names, "EmbeddedFiles")
        arr = get(pdf.head(ref(ef)), "Names") if ref(ef) else get(ef, "Names") if ef else None
        for m in re.finditer(rb"(\((?:\\.|[^\\)])*\)|<[0-9A-Fa-f]*>)\s*(\d+)\s+\d+\s+R", arr or b""):
            spec = pdf.head(int(m.group(2)))
            f = ref(get(get(spec, "EF"), "F"))
            head, data = pdf.objects[f]
            if get(head, "Filter") == b"/FlateDecode":
                data = zlib.decompress(data)
            att[_text(m.group(1))] = data
    return {"pages": len(pdf.pages()), "info": info, "attachments": att}


def _text(v):
    v = v.strip()
    if v.startswith(b"<FEFF") or v.startswith(b"<feff"):
        return bytes.fromhex(v[5:-1].decode()).decode("utf-16-be")
    if v.startswith(b"("):
        s = v[1:-1]
        s = re.sub(rb"\\([\\()])", rb"\1", s)
        if s.startswith(b"\xfe\xff"):
            return s[2:].decode("utf-16-be", errors="replace")
        return s.decode("latin-1")
    return v.decode("latin-1")
