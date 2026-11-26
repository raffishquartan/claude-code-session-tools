"""Split a Bash command line into simple commands, for hooks that must know which words the
shell would actually execute as commands.

This is a deliberately small lexer, not a full shell grammar. It understands what decides
whether a word is a command or data: single, double and ANSI-C (``$'...'``) quoting,
backslash escapes and line continuations, ``#`` comments, the separators ``;`` ``&`` ``|``
``&&`` ``||`` and newlines, ``( ... )`` groups, redirections (whose targets are not
arguments), heredoc bodies (data, except for the command substitutions an unquoted
delimiter lets the shell expand), and the expansions ``$(...)``, backticks, ``<(...)``,
``>(...)``, ``${...}`` and ``$((...))``. Every command substitution body is parsed
recursively and its commands are returned alongside the outer ones.

Each word keeps its quote-removed value, with every expansion replaced by
:data:`DYNAMIC`, so callers can tell a literal path from one the shell will compute.
Malformed input (an unterminated quote, substitution or backtick) raises
:class:`ShellParseError`; callers are expected to fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass

DYNAMIC = "\x00"
"""Stands in, inside :attr:`Word.value`, for any text the shell computes at run time."""

_WORD_BREAK = frozenset(" \t\n;&|()<>")
_GLOB_CHARS = frozenset("*?[")
_PARAM_SPECIAL = frozenset("@*#?$!-0123456789")
_ANSI_SIMPLE = {
    "a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n", "r": "\r",
    "t": "\t", "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?",
}


class ShellParseError(ValueError):
    """The command text is not well-formed enough to split safely."""


@dataclass(frozen=True, slots=True)
class Word:
    """One shell word.

    ``value`` is the word after quote removal, with expansions replaced by
    :data:`DYNAMIC`. ``raw`` is its source text. ``glob_mask[i]`` is True when
    ``value[i]`` is an unquoted glob metacharacter. ``tilde`` and ``brace`` flag an
    unquoted leading ``~`` and an unquoted ``{`` (brace expansion candidates).
    """

    value: str
    raw: str
    glob_mask: tuple[bool, ...]
    tilde: bool
    brace: bool

    @property
    def dynamic(self) -> bool:
        return DYNAMIC in self.value

    @property
    def has_glob(self) -> bool:
        return any(self.glob_mask)


@dataclass(frozen=True, slots=True)
class SimpleCommand:
    """A command's words, the ``<<<`` here-strings fed to its stdin, whether stdin is
    redirected from a file or process (``<``, ``<&``, ``<>``), whether it has a
    heredoc, whether it is the name of a function definition (``f() { ...; }``), and
    whether it evaluates arithmetic (``((...))``, ``$((...))``), where bash also expands
    variables whose values may hold command substitutions."""

    words: tuple[Word, ...]
    herestrings: tuple[Word, ...] = ()
    stdin_redirected: bool = False
    has_heredoc: bool = False
    defines_function: bool = False
    arithmetic: bool = False


def split_commands(text: str) -> list[SimpleCommand]:
    """Return every simple command in *text*, including those inside substitutions."""
    parser = _Parser(text)
    try:
        parser.parse_list(stop=None)
    except RecursionError as exc:
        raise ShellParseError("nesting too deep") from exc
    return parser.commands


class _WordBuilder:
    def __init__(self) -> None:
        self.chars: list[str] = []
        self.mask: list[bool] = []
        self.tilde = False
        self.brace = False

    def add(self, text: str, *, glob: bool = False) -> None:
        for ch in text:
            self.chars.append(ch)
            self.mask.append(glob)

    @property
    def empty(self) -> bool:
        return not self.chars

    def build(self, raw: str) -> Word:
        value = "".join(self.chars)
        brace = self.brace and value != "{}"
        return Word(value, raw, tuple(self.mask), self.tilde, brace)


@dataclass
class _PendingHeredoc:
    delimiter: str
    strip_tabs: bool
    expands: bool


class _Parser:
    def __init__(self, text: str) -> None:
        self.s = text
        self.i = 0
        self.commands: list[SimpleCommand] = []
        self._pending: list[_PendingHeredoc] = []
        self._stdin_redirected = False
        self._has_heredoc = False
        self._defines_function = False
        self._arithmetic_seen = False

    # ---- command lists ----

    def parse_list(self, stop: str | None) -> None:
        """Parse commands until *stop* (``)``) at depth zero, or end of text."""
        words: list[Word] = []
        here: list[Word] = []
        self._stdin_redirected = False
        self._has_heredoc = False
        s = self.s
        while self.i < len(s):
            c = s[self.i]
            if c in " \t":
                self.i += 1
            elif c == "\\" and s.startswith("\\\n", self.i):
                self.i += 2
            elif c == "\n":
                self._flush(words, here)
                self.i += 1
                self._read_heredoc_bodies()
            elif c == "#":
                while self.i < len(s) and s[self.i] != "\n":
                    self.i += 1
            elif c in "<>" or (c == "&" and s.startswith("&>", self.i)):
                self._redirection(words, here)
            elif c in ";&|":
                self._flush(words, here)
                while self.i < len(s) and s[self.i] in ";&|":
                    self.i += 1
            elif c == "(" and not words and s.startswith("((", self.i):
                self.i += 2
                self._arithmetic()
            elif c == "(":
                if words and s[self.i + 1:].lstrip(" \t").startswith(")"):
                    self._defines_function = True
                self._flush(words, here)
                self.i += 1
                self.parse_list(stop=")")
            elif c == ")":
                self._flush(words, here)
                self.i += 1
                if stop == ")":
                    return
            else:
                start = self.i
                builder = self._read_word()
                if self.i < len(s) and s[self.i] in "<>" and s[start:self.i].isdigit():
                    continue  # an fd number such as the 2 in 2>/dev/null
                words.append(builder.build(s[start:self.i]))
        self._flush(words, here)
        if stop is not None:
            raise ShellParseError("unterminated '('")

    def _flush(self, words: list[Word], here: list[Word]) -> None:
        if words or here or self._arithmetic_seen:
            self.commands.append(SimpleCommand(
                tuple(words), tuple(here), self._stdin_redirected, self._has_heredoc,
                self._defines_function, self._arithmetic_seen,
            ))
            words.clear()
            here.clear()
        self._arithmetic_seen = False
        self._stdin_redirected = False
        self._has_heredoc = False
        self._defines_function = False

    def _redirection(self, words: list[Word], here: list[Word]) -> None:
        s = self.s
        if s.startswith(("<(", ">("), self.i):
            self.i += 2
            self.parse_list(stop=")")
            builder = _WordBuilder()
            builder.add(DYNAMIC)
            words.append(builder.build("<(...)"))
            return
        for op in ("<<<", "<<-", "<<", "&>>", "&>", ">>", ">&", "<&", ">|", "<>", ">", "<"):
            if s.startswith(op, self.i):
                self.i += len(op)
                break
        if op in ("<", "<&", "<>"):
            self._stdin_redirected = True
        while self.i < len(s) and s[self.i] in " \t":
            self.i += 1
        if self.i >= len(s) or s[self.i] in _WORD_BREAK:
            return
        start = self.i
        target = self._read_word()
        if op == "<<<":
            here.append(target.build(s[start:self.i]))
        elif op in ("<<", "<<-"):
            # bash removes quotes from the delimiter word but never expands it.
            raw = s[start:self.i]
            self._has_heredoc = True
            self._pending.append(_PendingHeredoc(
                _quote_removed(raw), op == "<<-", expands=not any(q in raw for q in "'\"\\")
            ))

    def _read_heredoc_bodies(self) -> None:
        s = self.s
        for doc in self._pending:
            body_start = self.i
            body_end = -1
            while self.i < len(s):
                nl = s.find("\n", self.i)
                line_end = len(s) if nl == -1 else nl
                line = s[self.i:line_end]
                if doc.strip_tabs:
                    line = line.lstrip("\t")
                if line == doc.delimiter:
                    body_end = self.i
                    self.i = line_end + 1 if nl != -1 else line_end
                    break
                self.i = line_end + 1 if nl != -1 else line_end
            if body_end == -1:
                # bash would treat everything after this point as body; a heredoc that
                # swallows the rest of the command is how later lines get hidden.
                raise ShellParseError(f"heredoc {doc.delimiter!r} is never terminated")
            if doc.expands:
                self._scan_expansions(s[body_start:body_end])
        self._pending = []

    def _scan_expansions(self, body: str) -> None:
        """Parse the command substitutions inside an unquoted-delimiter heredoc body."""
        sub = _Parser(body)
        builder = _WordBuilder()
        while sub.i < len(body):
            c = body[sub.i]
            if c == "\\":
                sub.i += 2
            elif c == "$":
                sub._dollar(builder)
            elif c == "`":
                sub._backtick(builder)
            else:
                sub.i += 1
        self.commands.extend(sub.commands)

    # ---- words ----

    def _read_word(self) -> _WordBuilder:
        s = self.s
        b = _WordBuilder()
        at_start = True
        while self.i < len(s) and s[self.i] not in _WORD_BREAK:
            c = s[self.i]
            if c == "\\":
                if self.i + 1 >= len(s):
                    self.i += 1
                elif s[self.i + 1] == "\n":
                    self.i += 2
                else:
                    b.add(s[self.i + 1])
                    self.i += 2
            elif c == "'":
                end = s.find("'", self.i + 1)
                if end == -1:
                    raise ShellParseError("unterminated single quote")
                b.add(s[self.i + 1:end])
                self.i = end + 1
            elif c == '"':
                self.i += 1
                self._double_quoted(b)
            elif c == "$" and s.startswith("$'", self.i):
                self._ansi_c(b)
            elif c == "$" and s.startswith('$"', self.i):
                self.i += 2
                self._double_quoted(b)
            elif c == "$":
                self._dollar(b)
            elif c == "`":
                self._backtick(b)
            else:
                if c == "~" and at_start:
                    b.tilde = True
                if c == "{":
                    b.brace = True
                b.add(c, glob=c in _GLOB_CHARS)
                self.i += 1
            at_start = False
        return b

    def _double_quoted(self, b: _WordBuilder) -> None:
        s = self.s
        while self.i < len(s):
            c = s[self.i]
            if c == '"':
                self.i += 1
                return
            if c == "\\" and self.i + 1 < len(s) and s[self.i + 1] in '$`"\\\n':
                if s[self.i + 1] != "\n":
                    b.add(s[self.i + 1])
                self.i += 2
            elif c == "$":
                self._dollar(b)
            elif c == "`":
                self._backtick(b)
            else:
                b.add(c)
                self.i += 1
        raise ShellParseError("unterminated double quote")

    def _ansi_c(self, b: _WordBuilder) -> None:
        s = self.s
        self.i += 2
        out: list[str] = []
        while self.i < len(s):
            c = s[self.i]
            if c == "'":
                self.i += 1
                b.add("".join(out))
                return
            if c != "\\" or self.i + 1 >= len(s):
                out.append(c)
                self.i += 1
                continue
            e = s[self.i + 1]
            self.i += 2
            if e in _ANSI_SIMPLE:
                out.append(_ANSI_SIMPLE[e])
            elif e in "01234567":
                digits = e + _take(s, self.i, "01234567", 2)
                self.i += len(digits) - 1
                out.append(chr(int(digits, 8) & 0xFF))
            elif e in "xuU":
                width = {"x": 2, "u": 4, "U": 8}[e]
                digits = _take(s, self.i, "0123456789abcdefABCDEF", width)
                self.i += len(digits)
                out.append(_code_point(int(digits, 16)) if digits else "\\" + e)
            elif e == "c" and self.i < len(s):
                out.append(chr(ord(s[self.i]) & 0x1F))
                self.i += 1
            else:
                out.append("\\" + e)
        raise ShellParseError("unterminated $'...' quote")

    def _dollar(self, b: _WordBuilder) -> None:
        s = self.s
        nxt = s[self.i + 1] if self.i + 1 < len(s) else ""
        if nxt == "(" and s.startswith("$((", self.i):
            self.i += 3
            self._arithmetic()
            b.add(DYNAMIC)
        elif nxt == "(":
            self.i += 2
            self.parse_list(stop=")")
            b.add(DYNAMIC)
        elif nxt == "{":
            self.i += 2
            self._braced_parameter()
            b.add(DYNAMIC)
        elif nxt.isalpha() or nxt == "_":
            self.i += 2
            while self.i < len(s) and (s[self.i].isalnum() or s[self.i] == "_"):
                self.i += 1
            b.add(DYNAMIC)
        elif nxt and nxt in _PARAM_SPECIAL:
            self.i += 2
            b.add(DYNAMIC)
        else:
            b.add("$")
            self.i += 1

    def _arithmetic(self) -> None:
        """Skip an arithmetic body after ``((`` / ``$((`` up to its ``))``; ``<<`` in it
        is a shift, not a heredoc. Substitutions inside it are parsed."""
        s = self.s
        depth = 0
        start = self.i
        self._arithmetic_seen = True
        while self.i < len(s):
            c = s[self.i]
            if c == "(":
                depth += 1
            elif c == ")":
                if depth == 0:
                    if not s.startswith("))", self.i):
                        raise ShellParseError("malformed arithmetic")
                    self._scan_expansions(s[start:self.i])
                    self.i += 2
                    return
                depth -= 1
            self.i += 1
        raise ShellParseError("unterminated arithmetic")

    def _braced_parameter(self) -> None:
        """Skip a ``${...}`` body, parsing any substitutions inside it."""
        s = self.s
        scratch = _WordBuilder()
        while self.i < len(s):
            c = s[self.i]
            if c == "}":
                self.i += 1
                return
            if c == "\\":
                self.i += 2
            elif c == "'":
                end = s.find("'", self.i + 1)
                if end == -1:
                    raise ShellParseError("unterminated single quote")
                self.i = end + 1
            elif c == '"':
                self.i += 1
                self._double_quoted(scratch)
            elif c == "$":
                self._dollar(scratch)
            elif c == "`":
                self._backtick(scratch)
            else:
                self.i += 1
        raise ShellParseError("unterminated ${")

    def _backtick(self, b: _WordBuilder) -> None:
        s = self.s
        j = self.i + 1
        inner: list[str] = []
        while j < len(s):
            c = s[j]
            if c == "\\" and j + 1 < len(s) and s[j + 1] in "`\\$":
                inner.append(s[j + 1])
                j += 2
                continue
            if c == "`":
                sub = _Parser("".join(inner))
                sub.parse_list(stop=None)
                self.commands.extend(sub.commands)
                self.i = j + 1
                b.add(DYNAMIC)
                return
            inner.append(c)
            j += 1
        raise ShellParseError("unterminated backtick")


def _code_point(value: int) -> str:
    """The character for an ANSI-C escape, or DYNAMIC when Python cannot represent it
    as a path character (beyond U+10FFFF, or a lone surrogate)."""
    if value > 0x10FFFF or 0xD800 <= value <= 0xDFFF:
        return DYNAMIC
    return chr(value)


def _quote_removed(raw: str) -> str:
    """*raw* with quotes and backslash escapes removed and nothing expanded."""
    out: list[str] = []
    i = 0
    while i < len(raw):
        c = raw[i]
        if c == "\\" and i + 1 < len(raw):
            out.append(raw[i + 1])
            i += 2
            continue
        if c not in "'\"":
            out.append(c)
        i += 1
    return "".join(out)


def _take(s: str, start: int, allowed: str, limit: int) -> str:
    end = start
    while end < len(s) and end - start < limit and s[end] in allowed:
        end += 1
    return s[start:end]
