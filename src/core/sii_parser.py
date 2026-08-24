from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Dict, Union, Optional, Any, Tuple


AttrValue = Union[str, List[str]]


@dataclass
class SiiUnit:
    unit_type: str
    unit_name: str
    attrs: Dict[str, AttrValue] = field(default_factory=dict)

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        v = self.attrs.get(key)
        if v is None:
            return default
        if isinstance(v, list):
            return v[0] if v else default
        return v

    def get_list(self, key: str) -> List[str]:
        """
        兼容 3 种写法：
          1) category[]: "a"   category[]: "b"        -> attrs["category"] = ["a","b"]
          2) category[0]: "a"  category[1]: "b"      -> attrs["category[0]"], attrs["category[1]"]
          3) mods_info 特殊： attrs 只存 "info[0]", "info[1]"...
        """
        v = self.attrs.get(key)
        if isinstance(v, list):
            return v
        result: List[str] = []
        if isinstance(v, str):
            result.append(v)
        for k, val in sorted(self.attrs.items()):
            m = re.match(r"^" + re.escape(key) + r"\[\d*\]$", k)
            if m and isinstance(val, str):
                result.append(val)
        return result

    def get_indexed(self, key: str) -> List[str]:
        """按 info[0..N] 数字升序返回值（mods_info.sii 专用）。"""
        found: List[Tuple[int, str]] = []
        for k, v in self.attrs.items():
            m = re.match(r"^" + re.escape(key) + r"\[(\d+)\]$", k)
            if m and isinstance(v, str):
                try:
                    found.append((int(m.group(1)), v))
                except ValueError:
                    pass
        found.sort(key=lambda x: x[0])
        return [s for _, s in found]


class _SiiLexer:
    T_IDENT = "IDENT"
    T_STR = "STR"
    T_NUM = "NUM"
    T_COLON = ":"
    T_LBRACE = "{"
    T_RBRACE = "}"
    T_LBRACKET = "["
    T_RBRACKET = "]"
    T_COMMA = ","
    T_EOF = "EOF"

    def __init__(self, text: str):
        self._text = text
        self._tokens: List[tuple] = []
        self._tokenize()
        self._cursor = 0

    def _tokenize(self):
        t = self._text
        i = 0
        n = len(t)
        while i < n:
            c = t[i]
            if c.isspace():
                i += 1
                continue
            if c == "#":
                while i < n and t[i] != "\n":
                    i += 1
                continue
            if c == "/" and i + 1 < n and t[i+1] == "/":
                while i < n and t[i] != "\n":
                    i += 1
                continue
            if c == '"':
                j = i + 1
                buf = []
                while j < n:
                    ch = t[j]
                    if ch == "\\":
                        if j + 1 >= n:
                            buf.append("\\"); j += 1; break
                        next_ch = t[j+1]
                        if next_ch == "x" and j + 3 < n:
                            hx = t[j+2:j+4]
                            try:
                                byte_val = int(hx, 16)
                                buf.append(bytes([byte_val]).decode("latin-1"))
                                j += 4; continue
                            except ValueError:
                                pass
                        if next_ch == "n":
                            buf.append("\n"); j += 2; continue
                        if next_ch == "r":
                            buf.append("\r"); j += 2; continue
                        if next_ch == "t":
                            buf.append("\t"); j += 2; continue
                        if next_ch == "\\":
                            buf.append("\\"); j += 2; continue
                        if next_ch == '"':
                            buf.append('"'); j += 2; continue
                        buf.append(next_ch); j += 2; continue
                    if ch == '"':
                        break
                    buf.append(ch)
                    j += 1
                raw = "".join(buf)
                if any(ord(ch) >= 128 for ch in raw):
                    out_chars = []
                    k = 0
                    raw_n = len(raw)
                    while k < raw_n:
                        if ord(raw[k]) >= 128:
                            m = k
                            while m < raw_n and ord(raw[m]) >= 128:
                                m += 1
                            if m - k >= 2:
                                try:
                                    b = raw[k:m].encode('latin-1')
                                    out_chars.append(b.decode('utf-8', errors='replace'))
                                except Exception:
                                    out_chars.append(raw[k:m])
                            else:
                                out_chars.append(raw[k:m])
                            k = m
                        else:
                            out_chars.append(raw[k])
                            k += 1
                    final_str = "".join(out_chars)
                else:
                    final_str = raw
                self._tokens.append((self.T_STR, final_str))
                i = j + 1
                continue
            if c == ":": self._tokens.append((self.T_COLON, ":")); i += 1; continue
            if c == "{": self._tokens.append((self.T_LBRACE, "{")); i += 1; continue
            if c == "}": self._tokens.append((self.T_RBRACE, "}")); i += 1; continue
            if c == "[": self._tokens.append((self.T_LBRACKET, "[")); i += 1; continue
            if c == "]": self._tokens.append((self.T_RBRACKET, "]")); i += 1; continue
            if c == ",": self._tokens.append((self.T_COMMA, ",")); i += 1; continue
            if c.isdigit() or (c == "-" and i+1 < n and t[i+1].isdigit()):
                j = i + 1
                while j < n and (t[j].isdigit() or t[j] in ".eE+-"):
                    j += 1
                self._tokens.append((self.T_NUM, t[i:j]))
                i = j
                continue
            if c.isalpha() or c == "_" or c == ".":
                j = i + 1
                while j < n and (t[j].isalnum() or t[j] in "_.-"):
                    j += 1
                self._tokens.append((self.T_IDENT, t[i:j]))
                i = j
                continue
            i += 1
        self._tokens.append((self.T_EOF, None))

    def peek(self, offset: int = 0) -> tuple:
        return self._tokens[min(self._cursor + offset, len(self._tokens) - 1)]

    def consume(self, expected_type: Optional[str] = None) -> tuple:
        tok = self._tokens[self._cursor]
        if expected_type is not None and tok[0] != expected_type:
            raise SyntaxError(f"Expected {expected_type}, got {tok[0]}={tok[1]!r}")
        self._cursor += 1
        return tok


def parse_sii(text: str) -> List[SiiUnit]:
    if not text:
        return []
    lex = _SiiLexer(text)
    units: List[SiiUnit] = []

    # ——— 判断：IDENT 后面是否跟了下一个属性的结构（":"  或  "[N]:"）———
    def _looks_like_next_attr_key() -> bool:
        if lex.peek()[0] != _SiiLexer.T_IDENT:
            return False
        p1 = lex.peek(1)
        if p1[0] == _SiiLexer.T_COLON:
            return True
        if p1[0] == _SiiLexer.T_LBRACKET:
            off = 2
            for _ in range(10):
                pn = lex.peek(off)
                if pn[0] == _SiiLexer.T_RBRACKET:
                    return lex.peek(off + 1)[0] == _SiiLexer.T_COLON
                if pn[0] not in (_SiiLexer.T_NUM, _SiiLexer.T_IDENT):
                    return False
                off += 1
        return False

    def _parse_block_body() -> Dict[str, AttrValue]:
        attrs: Dict[str, Any] = {}
        while True:
            t, v = lex.peek()
            if t == _SiiLexer.T_RBRACE or t == _SiiLexer.T_EOF:
                break
            if t == _SiiLexer.T_IDENT:
                ident = v
                lex.consume()
                nxt = lex.peek()
                # 内嵌 unit 判定：IDENT : IDENT/STR  {
                if nxt[0] == _SiiLexer.T_COLON:
                    ahead2 = lex.peek(2)
                    ahead3 = lex.peek(3)
                    if (ahead2[0] in (_SiiLexer.T_IDENT, _SiiLexer.T_STR)
                            and ahead3[0] == _SiiLexer.T_LBRACE):
                        lex._cursor -= 1
                        break
                # 解析 key / key[] / key[N]
                base_key = ident
                bracket_suffix = ""
                is_array_form = False
                if lex.peek()[0] == _SiiLexer.T_LBRACKET:
                    lex.consume()  # [
                    inner = []
                    while lex.peek()[0] != _SiiLexer.T_RBRACKET:
                        p = lex.consume()
                        inner.append(p[1])
                    lex.consume()  # ]
                    bracket_suffix = "[" + "".join(str(x) for x in inner) + "]"
                    is_array_form = True
                # info[0] 这种保留 "[N]" 到 key 里，便于独立存放
                if is_array_form and bracket_suffix != "[]":
                    final_key = base_key + bracket_suffix
                    list_dedup = False
                else:
                    final_key = base_key
                    list_dedup = True
                if lex.peek()[0] == _SiiLexer.T_COLON:
                    lex.consume()
                # 读值：关键！循环顶部先判断"下一个 token 看起来是不是下一个属性 key"，是就立刻 break
                vals: List[str] = []
                guard = 0
                while guard < 10000:
                    guard += 1
                    tt, tv = lex.peek()
                    # 先检查：若下一个 IDENT 是新属性 key（'foo:' 或 'foo[N]:'），立即停
                    if _looks_like_next_attr_key():
                        break
                    if tt == _SiiLexer.T_STR:
                        vals.append(tv); lex.consume()
                    elif tt == _SiiLexer.T_NUM:
                        vals.append(tv); lex.consume()
                    elif tt == _SiiLexer.T_IDENT:
                        low = tv.lower()
                        if low in ("true", "false"):
                            vals.append(low)
                        else:
                            vals.append(tv)
                        lex.consume()
                    else:
                        break
                    if lex.peek()[0] == _SiiLexer.T_COMMA:
                        lex.consume()
                if len(vals) == 0:
                    continue
                if not list_dedup:
                    attrs[final_key] = vals[0]
                elif isinstance(attrs.get(final_key), list):
                    attrs[final_key].extend(vals)
                else:
                    prev = attrs.get(final_key)
                    if prev is not None:
                        attrs[final_key] = ([prev] if isinstance(prev, str) else prev) + vals
                    else:
                        attrs[final_key] = vals if len(vals) > 1 else vals[0]
            elif t == _SiiLexer.T_COMMA:
                lex.consume()
            else:
                lex.consume()
        return attrs

    while True:
        tok = lex.peek()
        if tok[0] == _SiiLexer.T_EOF:
            break
        if tok[0] == _SiiLexer.T_IDENT:
            first_ident = tok[1]
            lex.consume()
            nxt = lex.peek()
            if nxt[0] == _SiiLexer.T_LBRACE:
                lex.consume()
                continue
            if nxt[0] == _SiiLexer.T_COLON:
                lex.consume()
                unit_name = ""
                nxt2 = lex.peek()
                if nxt2[0] in (_SiiLexer.T_IDENT, _SiiLexer.T_STR):
                    unit_name = nxt2[1]
                    lex.consume()
                if lex.peek()[0] == _SiiLexer.T_LBRACE:
                    lex.consume()
                    attrs = _parse_block_body()
                    if lex.peek()[0] == _SiiLexer.T_RBRACE:
                        lex.consume()
                    units.append(SiiUnit(unit_type=first_ident, unit_name=unit_name, attrs=attrs))
                    continue
            continue
        if tok[0] == _SiiLexer.T_RBRACE:
            lex.consume()
            continue
        lex.consume()
    return units


def parse_sii_file(path: str) -> List[SiiUnit]:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return parse_sii(f.read())
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("Unable to decode file: " + path)


def _append_info(dct: Dict[str, int], item: str) -> None:
    if "|" in item:
        name, ts = item.rsplit("|", 1)
        try:
            dct[name] = int(ts)
        except ValueError:
            dct[name] = 0
    else:
        dct[item] = 0


def parse_mods_info(path: str) -> Dict[str, int]:
    units = parse_sii_file(path)
    result: Dict[str, int] = {}
    for u in units:
        if u.unit_type != "mods_info":
            continue
        items = u.get_indexed("info")
        for s in items:
            if isinstance(s, str) and "|" in s:
                _append_info(result, s)
    return result