"""Read simple IEC declarations from the registered root Global.var."""

from pathlib import Path
import re

from .project import objects

IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
DECLARATION = re.compile(rf"^[ \t]*({IDENTIFIER})[ \t]*:[ \t]*(.*)$", re.MULTILINE)
BARE_TYPE = re.compile(rf"({IDENTIFIER})[ \t]*;[ \t]*")
GLOBAL_START = r"^[ \t]*VAR(?:_GLOBAL)?(?:[ \t]+(?:RETAIN|NON_RETAIN))?[ \t]*\n"
GLOBAL_BLOCK = re.compile(GLOBAL_START + r"(.*?)^[ \t]*END_VAR[ \t]*;?[ \t]*$", re.IGNORECASE | re.MULTILINE | re.DOTALL)
IGNORED = re.compile(r"""(?:\(\*[\s\S]*?\*\)|//[^\n]*|'(?:\$[^\n]|[^'$\n])*'|"(?:\$[^\n]|[^"$\n])*")""")
GLOBAL_LIMIT = 1024 * 1024


def global_declarations(source):
    manifest = Path("Logical/Package.pkg")
    package, _ = source.xml(manifest, "Package")
    result = {"state": "unverified", "path": None, "declarations": []}
    if package is None:
        return result
    paths = [item.text for item in objects(package, "File") if (item.text or "").strip().casefold() == "global.var"]
    if len(paths) != 1:
        return result
    path = source.member(manifest, paths[0])
    if path is None:
        return result
    result["path"] = path.as_posix()
    raw = source.read_bytes(path, GLOBAL_LIMIT, label="IEC declaration file")
    if raw is None:
        return result
    try:
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = raw.decode("utf-16")
        else:
            text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return result
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    masked = IGNORED.sub(lambda match: re.sub(r"[^\n]", " ", match[0]), text)
    if any(marker in masked for marker in ("\0", "'", '"', "(*", "*)")):
        return result
    blocks = list(GLOBAL_BLOCK.finditer(masked))
    starters = re.findall(GLOBAL_START, masked, re.MULTILINE | re.IGNORECASE)
    if not blocks or len(blocks) != len(starters):
        return result
    for block in blocks:
        line = masked.count("\n", 0, block.start(1)) + 1
        for number, content in enumerate(block[1].splitlines(), line):
            declaration = DECLARATION.fullmatch(content)
            if declaration is not None:
                typename = BARE_TYPE.fullmatch(declaration[2])
                result["declarations"].append({"name": declaration[1], "type": typename[1] if typename else None,
                                               "line": number})
    result["state"] = "read"
    return result
