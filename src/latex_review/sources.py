"""比较来源快照、LaTeX 依赖展开与逐字符来源信息。"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
import os
import re
import shutil
import stat
import subprocess
import tempfile
from typing import Iterator

from .contract import ComparisonSource
from .source_map import SourceMap, SourceSegment


@dataclass(frozen=True)
class SourceError(Exception):
    code: str
    side: str | None
    message: str
    path: str | None = None

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class SourceIssue:
    code: str
    side: str
    message: str
    file: str
    start: int
    end: int
    include_chain: tuple[str, ...]
    certainty: str = "unknown"


@dataclass(frozen=True)
class Dependency:
    kind: str
    file: str
    referenced_from: str
    include_instance: str
    argument: str | None = None
    source_start: int | None = None
    source_end: int | None = None


@dataclass(frozen=True)
class ProjectSource:
    side: str
    root: Path
    entry: str
    identity: ComparisonSource
    worktree_origin: Path | None = None

    def expand(self) -> ExpandedProject:
        return expand_project(self)


@dataclass(frozen=True)
class SourcePair:
    old: ProjectSource
    new: ProjectSource


@dataclass(frozen=True)
class ExpandedProject:
    source: ProjectSource
    text: str
    source_map: SourceMap
    dependencies: tuple[Dependency, ...]
    diagnostics: tuple[SourceIssue, ...]

    def origin_ranges(self, start: int, end: int):
        return self.source_map.map_range(start, end)


_COMMAND = re.compile(r"\\(input|include|bibliography|addbibresource|includegraphics|bibliographystyle|graphicspath)(?![A-Za-z@])")
_MACRO_DEFINITION = re.compile(r"\\(newcommand|renewcommand|providecommand|DeclareRobustCommand|newenvironment|renewenvironment|provideenvironment|def|gdef|edef|xdef)(?![A-Za-z@])")
_GRAPHICS = (".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg", ".webp")


def _git(where: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(("git", "-C", str(where), *args), capture_output=True)
    except FileNotFoundError as exc:
        raise SourceError("explicit_sources_required", None, "未找到 Git；请显式指定两个目录或两个独立文件") from exc
    if check and result.returncode:
        raise SourceError("git_failed", None, result.stderr.decode("utf-8", "replace").strip() or "Git 命令失败")
    return result


def _within(root: Path, path: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def _entry(root: Path, entry: str, side: str) -> str:
    candidate = root / entry
    if not _within(root, candidate):
        raise SourceError("entry_outside_root", side, f"{side} 入口不在项目根目录内", entry)
    if not candidate.is_file():
        raise SourceError("entry_missing", side, f"{side} 入口文件不存在：{entry}", entry)
    try:
        if not candidate.stat().st_mode & (stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH):
            raise OSError("缺少读取权限")
        candidate.read_bytes().decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise SourceError("entry_unreadable", side, f"{side} 入口不可读：{entry}：{exc}", entry) from exc
    return candidate.relative_to(root).as_posix()


def _revision(root: Path, revision: str, side: str) -> str:
    result = _git(root, "rev-parse", "--verify", f"{revision}^{{commit}}", check=False)
    if result.returncode:
        raise SourceError("invalid_revision", side, f"{side} Git 提交无效：{revision}")
    return result.stdout.decode().strip()


def _materialize_git(root: Path, sha: str, destination: Path) -> None:
    """逐个物化对象树，不依赖当前索引或工作区文件。"""
    listing = _git(root, "ls-tree", "-rz", "--full-tree", sha).stdout
    for record in listing.split(b"\0"):
        if not record:
            continue
        meta, name = record.split(b"\t", 1)
        mode, kind, oid = meta.decode().split()
        relative = Path(os.fsdecode(name))
        if relative.is_absolute() or ".." in relative.parts:
            raise SourceError("invalid_git_path", None, "Git 对象树含非法路径")
        target = destination / relative
        if kind == "commit":  # 外部子模块不自动初始化。
            continue
        if kind != "blob":
            raise SourceError("invalid_git_object", None, "Git 对象树含不支持的对象")
        target.parent.mkdir(parents=True, exist_ok=True)
        content = _git(root, "cat-file", "blob", oid).stdout
        if mode == "120000":
            link = os.fsdecode(content)
            if Path(link).is_absolute() or not _within(destination, target.parent / link):
                continue
            target.symlink_to(link)
        else:
            target.write_bytes(content)
            if mode == "100755":
                target.chmod(0o755)


def _materialize_worktree(root: Path, destination: Path) -> None:
    """索引用于找路径，内容一律取当前磁盘；补入未忽略的未跟踪文件。"""
    paths = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").stdout
    for name in set(paths.split(b"\0")) - {b""}:
        relative = Path(os.fsdecode(name))
        if relative.is_absolute() or ".." in relative.parts:
            continue
        original = root / relative
        if not original.is_file() and not original.is_symlink():
            continue  # 已删除的已跟踪文件保持缺失。
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if original.is_symlink():
            link = os.readlink(original)
            if Path(link).is_absolute() or not _within(root, original.parent / link):
                continue
            target.symlink_to(link)
        else:
            shutil.copy2(original, target)


@contextmanager
def resolve_sources(
    *, entry: str | None = None, old_dir: str | Path | None = None,
    new_dir: str | Path | None = None, old_file: str | Path | None = None,
    new_file: str | Path | None = None, old_revision: str | None = None,
    new_revision: str | None = None, cwd: str | Path | None = None,
) -> Iterator[SourcePair]:
    """解析互斥的来源模式；返回值仅在 with 作用域内有效。"""
    current = Path(cwd or Path.cwd()).resolve()
    directory_mode = old_dir is not None or new_dir is not None
    file_mode = old_file is not None or new_file is not None
    revision_mode = old_revision is not None or new_revision is not None
    if sum((directory_mode, file_mode, revision_mode)) > 1:
        raise SourceError("conflicting_modes", None, "目录、独立文件和 Git 提交参数互斥")
    if directory_mode:
        if old_dir is None or new_dir is None or not entry:
            raise SourceError("invalid_arguments", None, "目录模式需要 --old-dir、--new-dir 和 --entry")
        if Path(entry).is_absolute():
            raise SourceError("entry_outside_root", None, "目录模式入口必须为相对路径", entry)
        roots = (Path(old_dir).expanduser(), Path(new_dir).expanduser())
        roots = tuple(path if path.is_absolute() else current / path for path in roots)
        if any(not path.is_dir() for path in roots):
            raise SourceError("root_missing", None, "比较项目目录不存在")
        old_root, new_root = (path.resolve() for path in roots)
        yield SourcePair(
            ProjectSource("old", old_root, _entry(old_root, entry, "old"), ComparisonSource("directory", str(old_root))),
            ProjectSource("new", new_root, _entry(new_root, entry, "new"), ComparisonSource("directory", str(new_root))),
        )
        return
    if file_mode:
        if old_file is None or new_file is None or entry is not None:
            raise SourceError("invalid_arguments", None, "独立文件模式需要两个文件，且不能指定 --entry")
        paths = (Path(old_file).expanduser(), Path(new_file).expanduser())
        paths = tuple(path if path.is_absolute() else current / path for path in paths)
        roots = tuple(path.parent.resolve() for path in paths)
        old_entry = _entry(roots[0], paths[0].name, "old")
        new_entry = _entry(roots[1], paths[1].name, "new")
        yield SourcePair(
            ProjectSource("old", roots[0], old_entry, ComparisonSource("file", str(paths[0].resolve()))),
            ProjectSource("new", roots[1], new_entry, ComparisonSource("file", str(paths[1].resolve()))),
        )
        return
    if not entry:
        raise SourceError("invalid_arguments", None, "Git 模式需要论文入口")
    if revision_mode and (old_revision is None or new_revision is None):
        raise SourceError("invalid_arguments", None, "指定 Git 提交时须同时提供 --old 和 --new")
    entry_path = Path(entry).expanduser()
    entry_path = entry_path if entry_path.is_absolute() else current / entry_path
    probe = _git(entry_path.parent, "rev-parse", "--show-toplevel", check=False)
    if probe.returncode:
        raise SourceError("explicit_sources_required", None, "当前项目不在 Git 中；请显式指定两个目录或两个独立文件")
    root = Path(os.fsdecode(probe.stdout.strip())).resolve()
    if not _within(root, entry_path):
        raise SourceError("entry_outside_root", None, "入口不在 Git 项目根目录内", entry)
    relative = entry_path.relative_to(root).as_posix()
    if not revision_mode:
        head = _git(root, "rev-parse", "--verify", "HEAD^{commit}", check=False)
        if head.returncode:
            raise SourceError("explicit_sources_required", None, "Git 项目没有有效 HEAD；请显式指定两个目录或两个独立文件")
    with ExitStack() as stack:
        old_tmp = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="latex-review-old-"))).resolve()
        new_tmp = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="latex-review-new-"))).resolve()
        old_sha = _revision(root, old_revision or "HEAD", "old")
        _materialize_git(root, old_sha, old_tmp)
        if revision_mode:
            new_sha = _revision(root, new_revision, "new")
            _materialize_git(root, new_sha, new_tmp)
            new_identity = ComparisonSource("git", new_sha)
            new_origin = None
        else:
            _materialize_worktree(root, new_tmp)
            new_identity = ComparisonSource("worktree", "working-tree")
            new_origin = root
        old_entry = _entry(old_tmp, relative, "old")
        new_entry = _entry(new_tmp, relative, "new")
        yield SourcePair(
            ProjectSource("old", old_tmp, old_entry, ComparisonSource("git", old_sha)),
            ProjectSource("new", new_tmp, new_entry, new_identity, new_origin),
        )


def _skip_space_comments(text: str, position: int) -> int:
    """TeX 命令与参数之间可以有空白及整行注释。"""
    while position < len(text):
        if text[position].isspace():
            position += 1
        elif text[position] == "%":
            line_end = re.search(r"[\r\n]", text[position:])
            position = len(text) if line_end is None else position + line_end.start()
        else:
            break
    return position


def _group_end(text: str, position: int, opening: str, closing: str) -> int | None:
    if position >= len(text) or text[position] != opening:
        return None
    depth = 1
    cursor = position + 1
    while cursor < len(text):
        if text[cursor] == "\\" and cursor + 1 < len(text) and text[cursor + 1] in (opening, closing, "%"):
            cursor += 2
            continue
        if text[cursor] == "%":
            line_end = re.search(r"[\r\n]", text[cursor:])
            if line_end is None:
                return None
            cursor += line_end.start()
            continue
        if text[cursor] == opening:
            depth += 1
        elif text[cursor] == closing:
            depth -= 1
            if depth == 0:
                return cursor + 1
        cursor += 1
    return None


def _macro_definition_end(text: str, match: re.Match[str]) -> tuple[int, str] | None:
    cursor = match.end()
    if text[cursor:cursor + 1] == "*":
        cursor += 1
    cursor = _skip_space_comments(text, cursor)
    if match.group(1) in ("def", "gdef", "edef", "xdef"):
        name = re.match(r"\\[A-Za-z@]+", text[cursor:])
        if name is None:
            return None
        cursor += len(name.group())
        while cursor < len(text) and text[cursor] != "{":
            cursor += 1
    elif cursor < len(text) and text[cursor] == "{":
        cursor = _group_end(text, cursor, "{", "}")
        if cursor is None:
            return None
    else:
        name = re.match(r"\\[A-Za-z@]+", text[cursor:])
        if name is None:
            return None
        cursor += len(name.group())
    cursor = _skip_space_comments(text, cursor)
    for _ in range(2):
        if cursor < len(text) and text[cursor] == "[":
            cursor = _group_end(text, cursor, "[", "]")
            if cursor is None:
                return None
            cursor = _skip_space_comments(text, cursor)
    body_start = cursor
    end = _group_end(text, body_start, "{", "}")
    if end is not None and match.group(1).endswith("environment"):
        end = _group_end(text, _skip_space_comments(text, end), "{", "}")
    return None if end is None else (end, text[body_start + 1:end - 1])


def _commands(text: str):
    """仅识别静态字面依赖；注释及 verbatim 内的命令不当作依赖。"""
    index = 0
    while index < len(text):
        if text[index] == "%":
            line_end = re.search(r"[\r\n]", text[index:])
            if line_end is None:
                return
            index += line_end.start()
            continue
        if text.startswith("\\begin{verbatim}", index) or text.startswith("\\begin{lstlisting}", index):
            name = "verbatim" if text.startswith("\\begin{verbatim}", index) else "lstlisting"
            end = text.find(f"\\end{{{name}}}", index)
            index = len(text) if end < 0 else end + len(f"\\end{{{name}}}")
            continue
        if text.startswith("\\verb", index) and index + 5 < len(text) and not text[index + 5].isalpha():
            delimiter = text[index + 5]
            end = text.find(delimiter, index + 6)
            index = len(text) if end < 0 else end + 1
            continue
        if text[index] != "\\":
            index += 1
            continue
        definition = _MACRO_DEFINITION.match(text, index)
        if definition is not None:
            parsed = _macro_definition_end(text, definition)
            if parsed is not None:
                end, body = parsed
                yield "macro_definition", index, end, body
                index = end
                continue
            yield "unparsed_macro_definition", index, len(text), text[definition.end():]
            return
        match = _COMMAND.match(text, index)
        if match is None:
            index += 2  # 已转义的 %、反斜线等不再次扫描。
            continue
        name = match.group(1)
        cursor = match.end()
        if name == "includegraphics" and cursor < len(text) and text[cursor] == "*":
            cursor += 1
        cursor = _skip_space_comments(text, cursor)
        if name in ("includegraphics", "addbibresource") and cursor < len(text) and text[cursor] == "[":
            close = text.find("]", cursor + 1)
            if close < 0:
                index = match.end()
                continue
            cursor = close + 1
            cursor = _skip_space_comments(text, cursor)
        if cursor < len(text) and text[cursor] == "{":
            close = _group_end(text, cursor, "{", "}")
            if close is None:
                index = match.end()
                continue
            argument = text[cursor + 1:close - 1]
            end = close
        elif name == "input":
            bare = re.match(r"[^\s%{}]+", text[cursor:])
            if bare is None:
                index = match.end()
                continue
            argument = bare.group()
            end = cursor + len(argument)
        else:
            index = match.end()
            continue
        yield name, index, end, argument
        index = end


def expand_project(source: ProjectSource) -> ExpandedProject:
    """递归展开 input/include，收集资源，并维护每次包含的独立来源。"""
    output: list[str] = []
    spans: list[SourceSegment] = []
    files: dict[str, str] = {}
    dependencies: list[Dependency] = []
    issues: list[SourceIssue] = []
    graphics_paths: list[str] = []
    length = 0
    instance_number = 0

    def emit(file: str, content: str, start: int, end: int, instance: str, confidence: str = "exact") -> None:
        nonlocal length
        if start == end:
            return
        output.append(content[start:end])
        spans.append(SourceSegment(length, length + end - start, file, start, end, instance, confidence))
        length += end - start

    def issue(code: str, message: str, file: str, start: int, end: int, chain: tuple[str, ...], certainty: str = "unknown") -> None:
        issues.append(SourceIssue(code, source.side, message, file, start, end, chain, certainty))

    def candidate_file(current_file: str, argument: str, extensions: tuple[str, ...], *, paths: tuple[str, ...] = ()) -> tuple[str | None, str | None]:
        if not argument or argument.startswith("/") or "\\" in argument or "#" in argument:
            return None, "uncertain_dependency"
        option = Path(argument)
        if option.is_absolute():
            return None, "dependency_outside_root"
        bases = [Path(current_file).parent, Path(".")]
        bases.extend(Path(path) for path in paths)
        seen = set()
        in_root_candidate = False
        for base in bases:
            for suffix in (("",) if option.suffix else extensions):
                relative = base / f"{argument}{suffix}"
                if relative in seen:
                    continue
                seen.add(relative)
                target = source.root / relative
                if not _within(source.root, target):
                    if target.exists() or target.is_symlink():
                        return None, "dependency_outside_root"
                    continue
                in_root_candidate = True
                if target.is_file():
                    try:
                        if not target.stat().st_mode & (stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH):
                            return None, "unreadable_dependency"
                        if extensions == (".tex",):
                            target.read_bytes().decode("utf-8")
                        else:
                            target.read_bytes()
                    except (OSError, UnicodeError):
                        return None, "unreadable_dependency"
                    return target.resolve().relative_to(source.root).as_posix(), None
        if source.worktree_origin is not None:
            for base in bases:
                for suffix in (("",) if option.suffix else extensions):
                    relative = base / f"{argument}{suffix}"
                    result = _git(source.worktree_origin, "check-ignore", "-q", str(relative), check=False)
                    if result.returncode == 0 and (source.worktree_origin / relative).exists():
                        return None, "ignored_dependency"
        return None, "missing_dependency" if in_root_candidate else "dependency_outside_root"

    def visit(file: str, chain: tuple[str, ...]) -> None:
        nonlocal instance_number
        instance_number += 1
        instance = f"{source.side}:{instance_number}"
        path = source.root / file
        try:
            content = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise SourceError("source_changed", source.side, f"快照文件在展开期间不可读：{file}：{exc}", file) from exc
        files[file] = content
        cursor = 0
        for name, start, end, argument in _commands(content):
            if name == "unparsed_macro_definition":
                issue("uncertain_dependency", "宏定义无法静态解析；未展开其中可能存在的依赖", file, start, end, chain)
                continue
            if name == "macro_definition":
                if _COMMAND.search(argument):
                    issue("uncertain_dependency", "宏定义内的依赖命令未执行，实际依赖取决于宏调用", file, start, end, chain)
                continue
            if name == "graphicspath":
                graphics_paths.extend(re.findall(r"\{([^{}]+)\}", argument))
                continue
            if name in ("input", "include"):
                resolved, problem = candidate_file(file, argument, (".tex",))
                if problem:
                    issue(problem, f"无法确定或读取包含文件 {argument}；包含链：{' → '.join(chain)}", file, start, end, chain)
                    emit(file, content, cursor, start, instance)
                    emit(file, content, start, end, instance, "unknown")
                    cursor = end
                    continue  # 原命令仍保留在展开文本中。
                dependencies.append(Dependency("include", resolved, file, instance, argument, start, end))
                if resolved in chain:
                    issue("include_cycle", f"包含循环：{' → '.join((*chain, resolved))}", file, start, end, (*chain, resolved))
                    emit(file, content, cursor, start, instance)
                    emit(file, content, start, end, instance, "unknown")
                    cursor = end
                    continue
                emit(file, content, cursor, start, instance)
                visit(resolved, (*chain, resolved))
                cursor = end
                continue
            if name == "bibliography":
                arguments = [part.strip() for part in argument.split(",")]
                extensions = (".bib",)
            elif name == "addbibresource":
                arguments, extensions = [argument.strip()], (".bib",)
            elif name == "bibliographystyle":
                arguments, extensions = [argument.strip()], (".bst",)
            else:
                arguments, extensions = [argument.strip()], _GRAPHICS
            for item in arguments:
                resolved, problem = candidate_file(file, item, extensions, paths=tuple(graphics_paths) if name == "includegraphics" else ())
                if problem:
                    issue(problem, f"无法确定或读取资源 {item}；包含链：{' → '.join(chain)}", file, start, end, chain)
                else:
                    dependencies.append(Dependency("graphic" if name == "includegraphics" else "bibliography",
                                                   resolved, file, instance, item, start, end))
        emit(file, content, cursor, len(content), instance)

    visit(source.entry, (source.entry,))
    text = "".join(output)
    return ExpandedProject(source, text, SourceMap(text, files, tuple(spans)), tuple(dependencies), tuple(issues))
