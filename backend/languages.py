"""Lightweight declaration index; not a semantic resolver or a call graph.

Python uses ast (invalid syntax has no index). Other languages use a masked
lexical view and conservative declaration patterns. Macros, overload resolution,
JS regex literals, nested template expressions and shell eval are not resolved.
"""
import ast
import re
from pathlib import Path

LANGUAGES = ("java", "python", "javascript", "typescript", "go", "shell")
EXTENSIONS = {".java": "java", ".py": "python", ".pyw": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript", ".go": "go", ".sh": "shell", ".bash": "shell"}
ROOT = Path(__file__).resolve().parent.parent


def detect_language(path: str) -> str | None:
    return EXTENSIONS.get(Path(path).suffix.lower())


def _mask(source: str, shell: bool = False) -> str:
    # Replace comments/quoted strings with spaces, preserving offsets and lines.
    result = list(source)
    i = 0
    while i < len(source):
        start = i
        if shell and source[i] == "#":
            end = source.find("\n", i)
            i = len(source) if end < 0 else end
        elif not shell and source.startswith("//", i):
            end = source.find("\n", i)
            i = len(source) if end < 0 else end
        elif not shell and source.startswith("/*", i):
            end = source.find("*/", i + 2)
            i = len(source) if end < 0 else end + 2
        elif shell and source.startswith("<<", i):
            delimiter = re.match(r"<<(-?)[ \t]*(['\"]?)([A-Za-z_]\w*)\2", source[i:])
            if delimiter is None:
                i += 2
                continue
            line_end = source.find("\n", i)
            i = len(source) if line_end < 0 else line_end + 1
            while i < len(source):
                next_end = source.find("\n", i)
                next_end = len(source) if next_end < 0 else next_end + 1
                line = source[i:next_end].rstrip("\r\n")
                if delimiter.group(1):
                    line = line.lstrip("\t")
                i = next_end
                if line == delimiter.group(3):
                    break
        elif not shell and source.startswith('"""', i):
            end = source.find('"""', i + 3)
            i = len(source) if end < 0 else end + 3
        elif source[i] in "\"'`":
            quote = source[i]
            i += 1
            while i < len(source):
                if source[i] == "\\" and not (shell and quote == "'"):
                    i += 2
                elif source[i] == quote:
                    i += 1
                    break
                else:
                    i += 1
        else:
            i += 1
            continue
        for j in range(start, min(i, len(source))):
            if source[j] not in "\n\r":
                result[j] = " "
    return "".join(result)


def _brace_end(masked: str, opening: int, fallback: int) -> int:
    depth = 0
    for offset in range(opening, len(masked)):
        if masked[offset] == "{":
            depth += 1
        elif masked[offset] == "}":
            depth -= 1
            if depth == 0:
                return masked.count("\n", 0, offset) + 1
    # Unfinished declarations remain bounded by actual source lines.
    return max(fallback, len(masked.splitlines()))


def analyze_file(path: str, content: str) -> dict:
    language = detect_language(path)
    symbols = []
    if language == "python":
        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append({"name": node.name, "kind": "class" if isinstance(node, ast.ClassDef) else "function", "line": node.lineno, "end_line": node.end_lineno or node.lineno})
        except (SyntaxError, ValueError, RecursionError):
            pass
    elif language:
        masked = _mask(content, language == "shell")
        patterns = []
        if language in ("javascript", "typescript"):
            patterns = [
                (r"^[ \t]*(?:(?:export|default|declare|abstract)\s+)*(?:class|interface|enum)\s+([\w$]+)[^{;]*\{", "class"),
                (r"^[ \t]*(?:(?:export|default|async)\s+)*function\s*\*?\s*([\w$]+)\s*\([^)]*\)[^{;]*\{", "function"),
                (r"^[ \t]*(?:export\s+)?(?:const|let|var)\s+([\w$]+)\s*(?::[^=\n]+)?=\s*(?:async\s*)?(?:\([^)]*\)|[\w$]+)\s*(?::[^=\n]+)?=>\s*\{", "function"),
            ]
        elif language == "java":
            patterns = [
                (r"^[ \t]*(?:(?:public|private|protected|abstract|final|static|sealed)\s+)*(?:class|interface|enum|record)\s+(\w+)[^{;]*\{", "class"),
                (r"^[ \t]*(?:(?:public|private|protected|static|final|synchronized|abstract|native|default)\s+)*(?:[\w<>?,.\[\]]+\s+)?(\w+)\s*\([^;{}]*\)\s*(?:throws\s+[\w., ]+)?\{", "function"),
            ]
        elif language == "go":
            patterns = [(r"^[ \t]*func\s+(?:\([^)]*\)\s*)?(\w+)\s*\([^)]*\)[^{]*\{", "function"), (r"^[ \t]*type\s+(\w+)\s+(?:struct|interface)\s*\{", "class")]
        elif language == "shell":
            patterns = [(r"^[ \t]*(?:function\s+)?([a-zA-Z_][\w-]*)\s*\(\s*\)\s*\{", "function"), (r"^[ \t]*function\s+([a-zA-Z_][\w-]*)\s*\{", "function")]
        seen = set()
        for pattern, kind in patterns:
            for match in re.finditer(pattern, masked, re.MULTILINE):
                name = match.group(1)
                line = masked.count("\n", 0, match.start(1)) + 1
                if name in {"if", "for", "while", "switch", "catch", "synchronized"} or (name, line) in seen:
                    continue
                seen.add((name, line))
                opening = masked.rfind("{", match.start(), match.end())
                symbols.append({"name": name, "kind": kind, "line": line, "end_line": _brace_end(masked, opening, line)})
    symbols.sort(key=lambda s: (s["line"], s["name"]))
    return {"path": path, "language": language, "size": len(content.encode("utf-8")), "symbols": symbols}


def suggest_entry(files: list[dict], language: str | None = None) -> str | None:
    choices = []
    for file in files:
        path = file.get("path", "")
        detected = detect_language(path)
        if detected is None or (language and language != detected):
            continue
        basename = Path(path).name.lower()
        score = 0
        if basename in {"main.py", "main.java", "main.go", "main.js", "main.ts", "main.sh", "app.py", "index.js", "index.ts"}:
            score += 100
        if any(s.get("name") in {"main", "Main"} for s in file.get("symbols", [])):
            score += 50
        if any(part in {"tests", "test", "node_modules", "vendor"} for part in Path(path).parts):
            score -= 100
        choices.append((-score, len(Path(path).parts), path))
    return min(choices)[2] if choices else None


def demo_path(language: str) -> Path:
    if language not in LANGUAGES:
        raise ValueError("不支持的演示语言")
    return ROOT / "examples" / language
