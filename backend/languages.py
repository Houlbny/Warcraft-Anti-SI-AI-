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


def demo_quests(language: str) -> list[dict]:
    folder = demo_path(language)
    filename = {"python": "main.py", "java": "Main.java", "javascript": "main.js", "typescript": "main.ts", "go": "main.go", "shell": "main.sh"}[language]
    source = (folder / filename).read_text(encoding="utf-8")
    marker = next(i for i, line in enumerate(source.splitlines(), 1) if "BUG" in line)
    function_line = next(i for i, line in enumerate(source.splitlines(), 1) if "remaining" in line and "BUG" not in line)
    definitions = [
        ("explore", "追踪库存入口", "找到计算剩余库存的函数。", "填写函数名。", function_line, "remaining", "remaining=12", ["从输出语句向上追踪调用。", "函数名称表示剩余数量：remaining。"]),
        ("predict", "先预测再运行", "初始库存 10，订单数量 2。当前代码含有一处运算符错误。", "输入当前程序完整标准输出。", marker, "remaining=12", "remaining=12", ["先按源码的加号计算，而不是按业务含义计算。", "10 + 2 = 12；输出带 remaining= 前缀。"]),
        ("repair", "修复扣减库存", "销售订单应从库存扣除。修改 remaining 中 BUG 标记的表达式。", "保持输入为 10 和 2，让程序输出 remaining=8。", marker, None, "remaining=8", ["只需修改一个运算符。", "将加法改为减法，再实际运行确认。"]),
        ("feature", "避免负库存", "在扣减修复后增加边界处理：订单数量大于库存时返回 0。将示例订单从 2 改为 12。", "保留通用扣减函数，输出 remaining=0。", function_line, None, "remaining=0", ["先算 stock - ordered，再将负值限制为 0。", "可用条件分支或该语言的最大值函数；示例调用的第二个参数要改为 12。"]),
    ]
    quests = []
    for kind, title, brief, objective, line, answer, output, hints in definitions:
        quest = {"id": f"{language}-{kind}", "type": kind, "title": title, "brief": brief, "objective": objective, "source_file": filename, "line": line, "expected_output": output, "status": "available", "hints_used": 0, "origin": "demo", "verification": "探索/预测使用明确答案；修复/扩展必须实际运行，exit 0 且标准输出匹配目标。", "hints": hints}
        if answer is not None:
            quest["answer"] = answer
        quests.append(quest)
    return quests
