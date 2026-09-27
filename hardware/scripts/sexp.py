"""Minimal S-expression reader/writer for KiCad files."""
import re

_tok = re.compile(r'\s*(\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+)', re.S)


class Str(str):
    """A quoted string token (kept distinct from bare atoms)."""


def _unescape(s):
    return re.sub(r'\\(.)', lambda m: {'n': '\n', 't': '\t'}.get(m.group(1), m.group(1)), s)


def parse(text):
    stack = [[]]
    pos = 0
    while True:
        m = _tok.match(text, pos)
        if not m:
            break
        t = m.group(1)
        pos = m.end()
        if t == '(':
            stack.append([])
        elif t == ')':
            node = stack.pop()
            stack[-1].append(node)
        elif t.startswith('"'):
            stack[-1].append(Str(_unescape(t[1:-1])))
        else:
            stack[-1].append(t)
    return stack[0]


def q(s):
    s = str(s).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
    return '"' + s + '"'


def dump(node, indent=0):
    if isinstance(node, Str):
        return q(node)
    if not isinstance(node, list):
        return str(node)
    if not any(isinstance(c, list) for c in node):
        return '(' + ' '.join(dump(c) for c in node) + ')'
    pad = '\t' * (indent + 1)
    head, rest = [], []
    for c in node:
        (rest if (rest or isinstance(c, list)) else head).append(c)
    s = '(' + ' '.join(dump(c) for c in head)
    for c in rest:
        s += '\n' + pad + dump(c, indent + 1)
    return s + '\n' + '\t' * indent + ')'


def find(node, key):
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def first(node, key):
    r = find(node, key)
    return r[0] if r else None
