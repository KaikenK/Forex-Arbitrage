"""Structural checks for main.tex, since no LaTeX is installed on the build machine.

Fails (exit 1) on: unbalanced braces; mismatched or unclosed \\begin/\\end; a \\cite key missing
from references.bib; any \\tbd macro (use or definition) or red text; leftover placeholder words;
a \\ref/\\eqref without a \\label; a missing \\includegraphics or \\input file.

    python -m research.paper.ieee.check_paper [path/to/main.tex]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_TEX = HERE / "main.tex"

_COMMENT = re.compile(r"(?<!\\)%.*")
_CITE = re.compile(r"\\(?:cite|citep|citet|nocite)\*?(?:\[[^\]]*\])*\{([^}]*)\}")
_REF = re.compile(r"\\(?:ref|eqref|autoref|cref|Cref|pageref)\{([^}]*)\}")
_LABEL = re.compile(r"\\label\{([^}]*)\}")
_GRAPHICS = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]*)\}")
_INPUT = re.compile(r"\\(?:input|include)\{([^}]*)\}")
_ENV = re.compile(r"\\(begin|end)\{([^}]*)\}")
_BIB_KEY = re.compile(r"^@\w+\s*\{\s*([^,\s]+)\s*,", re.MULTILINE)
_PLACEHOLDER = re.compile(r"\bTBD\b|\bTODO\b|\bFIXME\b|\bXXX\b|\[URL|\[TBD|\\tbd\b")


def strip_comments(text: str) -> str:
    return "\n".join(_COMMENT.sub("", line) for line in text.splitlines())


def brace_errors(text: str) -> list[str]:
    depth, line = 0, 1
    errors: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "\n":
            line += 1
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                errors.append(f"unmatched }} at line {line}")
                depth = 0
        i += 1
    if depth:
        errors.append(f"{depth} unclosed {{ at end of file")
    return errors


def environment_errors(text: str) -> list[str]:
    stack: list[tuple[str, int]] = []
    errors: list[str] = []
    for match in _ENV.finditer(text):
        kind, name = match.groups()
        line = text.count("\n", 0, match.start()) + 1
        if kind == "begin":
            stack.append((name, line))
        elif not stack or stack[-1][0] != name:
            top = stack[-1][0] if stack else "nothing open"
            errors.append(f"\\end{{{name}}} at line {line} but {top} is open")
            if stack and any(n == name for n, _ in stack):
                while stack and stack.pop()[0] != name:
                    pass
        else:
            stack.pop()
    errors += [f"\\begin{{{name}}} at line {line} is never closed" for name, line in stack]
    return errors


def bib_keys(bib_text: str) -> set[str]:
    return set(_BIB_KEY.findall(bib_text))


def split_keys(group: str) -> list[str]:
    return [k.strip() for k in group.split(",") if k.strip()]


def check_text(raw: str, *, bib: str, base: Path) -> list[str]:
    text = strip_comments(raw)
    errors = brace_errors(text) + environment_errors(text)

    known = bib_keys(bib)
    for group in _CITE.findall(text):
        errors += [f"\\cite key not in references.bib: {k}" for k in split_keys(group) if k not in known]

    labels = set(_LABEL.findall(text))
    for group in _REF.findall(text):
        errors += [f"\\ref without \\label: {k}" for k in split_keys(group) if k not in labels]

    for pattern in (r"\\tbd\b", r"\\textcolor\s*\{\s*red\s*\}", r"\\color\s*\{\s*red\s*\}"):
        found = len(re.findall(pattern, text))
        if found:
            errors.append(f"{found} occurrence(s) of {pattern}")
    for word in _PLACEHOLDER.findall(text):
        errors.append(f"placeholder text: {word}")

    for name in _GRAPHICS.findall(text):
        if not (base / name).is_file():
            errors.append(f"\\includegraphics file missing: {name}")
    for name in _INPUT.findall(text):
        if not ((base / name).is_file() or (base / f"{name}.tex").is_file()):
            errors.append(f"\\input file missing: {name}")
    return sorted(set(errors))


def check(tex_path: Path = DEFAULT_TEX, bib_path: Path | None = None) -> list[str]:
    bib_path = bib_path or tex_path.parent / "references.bib"
    return check_text(tex_path.read_text(encoding="utf-8"), bib=bib_path.read_text(encoding="utf-8"),
                      base=tex_path.parent)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    tex = Path(argv[0]) if argv else DEFAULT_TEX
    errors = check(tex)
    for e in errors:
        print(f"FAIL: {e}")
    print(f"check_paper: {'FAILED' if errors else 'ok'} ({len(errors)} problem(s)) for {tex.name}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
