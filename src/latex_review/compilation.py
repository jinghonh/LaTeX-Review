"""显式论文编译：只在私有副本内运行，并保留有界产物。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import resource
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from .contract import Diagnostic
from .report import _write_managed
from .sources import ProjectSource


@dataclass(frozen=True)
class CompileStatus:
    side: str
    status: str
    engine: str
    exit_code: int | None
    pdf: str | None
    log: str | None
    synctex: str | None
    reason: str | None


_ENGINE_MARKERS = {"pdflatex": "pdfTeX", "xelatex": "XeTeX", "lualatex": "Lua"}
_MODES = {"pdflatex": "-pdf", "xelatex": "-xelatex", "lualatex": "-lualatex"}
_MAX_FILE = 16 * 1024 * 1024
_MAX_TREE = 128 * 1024 * 1024


class _ToolUnavailable(Exception):
    pass


def _safe_path(sources: ProjectSource | tuple[ProjectSource, ...], output_dir: Path) -> tuple[str, tuple[Path, ...]]:
    if isinstance(sources, ProjectSource):
        sources = (sources,)
    roots = [output_dir.resolve(), Path.cwd().resolve()]
    for source in sources:
        roots.extend((source.root.resolve(), source.root.parent.resolve()))
    forbidden = tuple(dict.fromkeys(roots))
    entries = []
    for raw in os.environ.get("PATH", os.defpath).split(os.pathsep):
        if not raw or not Path(raw).is_absolute():
            continue
        candidate = Path(raw)
        absolute = Path(os.path.abspath(candidate))
        resolved = candidate.resolve()
        if any(absolute.is_relative_to(root) or resolved.is_relative_to(root) for root in forbidden):
            continue
        entries.append(str(candidate))
    return os.pathsep.join(entries), forbidden


def _program(name: str, path: str, forbidden: tuple[Path, ...]) -> str | None:
    """仅解析路径，不在宿主运行版本探测。"""
    found = shutil.which(name, path=path)
    if found is None:
        return None
    candidate = Path(found).resolve()
    return str(candidate) if not any(candidate.is_relative_to(root) for root in forbidden) else None


def _copy_project(source: ProjectSource, destination: Path, excluded: Path) -> None:
    root = source.root.resolve()
    total = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        folder = Path(current)
        dirs[:] = [name for name in dirs if name != ".git" and not (folder / name).is_relative_to(excluded)]
        for name in dirs:
            if (folder / name).is_symlink():
                raise OSError(f"隔离副本拒绝目录符号链接：{(folder / name).relative_to(root)}")
        for name in files:
            original = folder / name
            if original.is_relative_to(excluded):
                continue
            if original.is_symlink() or not original.is_file():
                raise OSError(f"隔离副本拒绝非普通文件：{original.relative_to(root)}")
            size = original.stat().st_size
            total += size
            if size > _MAX_FILE or total > _MAX_TREE:
                raise OSError("隔离副本超过文件或总容量限制")
            target = destination / original.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target, follow_symlinks=False)
            target.chmod(0o644)


def _limits() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (90, 90))
    resource.setrlimit(resource.RLIMIT_FSIZE, (_MAX_FILE, _MAX_FILE))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    if hasattr(resource, "RLIMIT_AS"):
        try:
            resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
        except (OSError, ValueError):
            pass  # 部分 macOS 内核拒绝设置；父进程仍监测进程组的实际 RSS。
    if hasattr(resource, "RLIMIT_NPROC"):
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (1024, 1024))
        except (OSError, ValueError):
            pass  # 进程组数量仍受父进程监测。


def _group_usage(pgid: int) -> tuple[int, int]:
    process_listing = "/bin/ps" if Path("/bin/ps").is_file() else "/usr/bin/ps"
    if not Path(process_listing).is_file():
        return 0, 0
    try:
        result = subprocess.run((process_listing, "-axo", "pgid=,rss="), capture_output=True,
                                timeout=2, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return 0, 0
    members = [line.split() for line in result.stdout.splitlines()]
    rss = [int(parts[1]) for parts in members if len(parts) == 2 and parts[0].isdigit()
           and parts[1].isdigit() and int(parts[0]) == pgid]
    return len(rss), sum(rss) * 1024


def _tree_size(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def _run_bounded(command: list[str], cwd: Path, env: dict[str, str], timeout: float,
                 output_limit: int, *, sandbox_profile: Path | None = None) -> tuple[int | None, bytes, str | None]:
    if sandbox_profile is not None:
        command = ["/usr/bin/sandbox-exec", "-f", str(sandbox_profile), *command]
    output = cwd / "latex-review-command.log"
    try:
        with output.open("wb") as stream:
            proc = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                    stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
                                    preexec_fn=_limits)
            deadline = time.monotonic() + timeout
            reason = None
            while proc.poll() is None:
                if time.monotonic() >= deadline:
                    reason = "编译超时"
                elif output.stat().st_size > output_limit:
                    reason = "编译输出超过限制"
                elif _tree_size(cwd / "build") > _MAX_TREE:
                    reason = "编译产物超过容量限制"
                else:
                    process_count, memory = _group_usage(proc.pid)
                    if process_count > 24:
                        reason = "编译子进程超过限制"
                    elif memory > 1024**3:
                        reason = "编译进程内存超过限制"
                if reason:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                    break
                time.sleep(0.1)
        return proc.returncode, output.read_bytes()[:output_limit], reason
    except (OSError, subprocess.SubprocessError) as exc:
        return None, b"", f"无法启动受控编译：{exc}"


def _sandbox_profile(path: Path, project: Path, executable_paths: tuple[Path, ...]) -> None:
    """默认拒绝；仅允许 TeX 运行时只读、隔离目录读写和指定程序执行。"""
    def q(value: Path) -> str:
        return json.dumps(str(value.resolve()))
    reads = [Path("/System"), Path("/usr"), Path("/bin"), Path("/sbin"), Path("/Library/TeX"),
             Path("/Library/Fonts"), Path("/Library/Frameworks"), Path("/private/var/db"),
             Path("/private/var/select")]
    parents = [parent for item in (project, path, *reads, *executable_paths) for parent in reversed(item.parents)]
    read_rules = "\n".join(f"(allow file-read* (literal {q(item)}))" for item in dict.fromkeys(parents))
    read_rules += "\n" + "\n".join(f"(allow file-read* (subpath {q(item)}))" for item in reads if item.exists())
    exec_rules = "\n".join(f"(allow process-exec (literal {q(item)}))" for item in executable_paths if item.exists())
    content = ("(version 1)\n(deny default)\n(deny network*)\n"
               f"(allow file-read* file-write* (subpath {q(project)}))\n"
               f"(allow file-read* (literal {q(path)}))\n{read_rules}\n{exec_rules}\n"
               "(allow process-fork)\n(allow sysctl-read)\n")
    path.write_text(content, encoding="utf-8")


def _sandbox_ready(profile: Path, project: Path) -> bool:
    if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").is_file():
        return False
    try:
        good = subprocess.run(("/usr/bin/sandbox-exec", "-f", str(profile), "/bin/echo", "ok"),
                              cwd=project, capture_output=True, timeout=3)
        denied = subprocess.run(("/usr/bin/sandbox-exec", "-f", str(profile), "/bin/cat", "/etc/passwd"),
                                cwd=project, capture_output=True, timeout=3)
        probe = project.parent / f"latex-review-sandbox-{secrets.token_hex(12)}"
        write = subprocess.run(("/usr/bin/sandbox-exec", "-f", str(profile), "/bin/sh", "-c",
                                f"printf forbidden > {shlex.quote(str(probe))}"),
                               cwd=project, capture_output=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return False
    escaped = probe.exists()
    if escaped:
        probe.unlink(missing_ok=True)
    return good.returncode == 0 and good.stdout.strip() == b"ok" and denied.returncode != 0 \
        and write.returncode != 0 and not escaped


def compile_side(source: ProjectSource, output_dir: Path, *, engine: str = "pdflatex",
                 timeout: float = 90, output_limit: int = 2 * 1024 * 1024,
                 sandbox: bool = False, related_sources: tuple[ProjectSource, ...] = ()
                 ) -> tuple[CompileStatus, tuple[Diagnostic, ...]]:
    if engine not in _MODES or timeout <= 0 or not 1024 <= output_limit <= 16 * 1024 * 1024:
        raise ValueError("编译引擎、超时或输出上限无效")
    side = source.side
    safe_path, forbidden = _safe_path((source, *related_sources), output_dir)
    latexmk = _program("latexmk", safe_path, forbidden)
    engine_path = _program(engine, safe_path, forbidden)
    reason = None
    if not latexmk or not engine_path:
        reason = (f"缺少可用的 latexmk 或 {engine}；论文目录及当前目录中的工具不会执行，"
                  "请自行安装受信任的 TeX 工具并确认其位于 PATH")
    status = "unavailable" if reason else "failed"
    exit_code = None
    pdf_rel = log_rel = synctex_rel = None
    log = b""
    if reason is None:
        with tempfile.TemporaryDirectory(prefix=f"latex-review-compile-{side}-") as temporary:
            project = Path(temporary).resolve()
            try:
                _copy_project(source, project, output_dir.resolve())
                entry = project / source.entry
                if not entry.is_file():
                    raise OSError("隔离副本缺少入口文件")
                profile = project / "sandbox.sb"
                if sandbox:
                    allowed = [Path(latexmk), Path(engine_path), Path("/usr/bin/env"), Path("/usr/bin/perl"),
                               Path("/bin/sh"), Path("/private/var/select/sh"), Path("/bin/echo"), Path("/bin/cat")]
                    for tool in ("bibtex", "biber", "makeindex", "kpsewhich"):
                        if found := _program(tool, safe_path, forbidden):
                            allowed.append(Path(found))
                    _sandbox_profile(profile, project, tuple(allowed))
                    if not _sandbox_ready(profile, project):
                        raise RuntimeError("macOS 沙箱不可用或隔离自检未通过；拒绝高级渲染")
                out = project / "build"
                out.mkdir()
                env = {key: value for key, value in os.environ.items() if key in {"LANG", "LC_ALL"}}
                env.update({"HOME": str(project), "TEXMFOUTPUT": str(out), "openin_any": "p", "openout_any": "p",
                            "shell_escape": "f", "max_print_line": "1000", "TMPDIR": str(out), "PATH": safe_path})
                for program, marker in ((latexmk, "latexmk"), (engine_path, _ENGINE_MARKERS[engine])):
                    probe_code, probe_log, probe_reason = _run_bounded(
                        [program, "--version"], project, env, 4, 4096,
                        sandbox_profile=profile if sandbox else None)
                    if probe_code != 0 or marker.lower() not in probe_log.decode("utf-8", "replace").lower():
                        log = probe_log
                        raise _ToolUnavailable(f"无法在隔离环境中验证 {Path(program).name} 版本："
                                               f"{probe_reason or '版本输出不符或退出失败'}")
                # BibTeX 直接处理 build/main.aux，避免 latexmk 为它切换目录后影响 TeX 重跑。
                command = [latexmk, "-norc", "-nobibfudge", _MODES[engine], "-interaction=nonstopmode", "-halt-on-error",
                           "-file-line-error", "-latexoption=-no-shell-escape", "-latexoption=-synctex=1",
                           "-outdir=build", source.entry]
                exit_code, log, reason = _run_bounded(command, project, env, timeout, output_limit,
                                                      sandbox_profile=profile if sandbox else None)
                pdf_path = out / f"{entry.stem}.pdf"
                tex_log = out / f"{entry.stem}.log"
                synctex_path = out / f"{entry.stem}.synctex.gz"
                if tex_log.is_file() and not tex_log.is_symlink():
                    log = (log + b"\n--- TeX log ---\n" + tex_log.read_bytes()[:output_limit])[:output_limit]
                if pdf_path.is_file() and not pdf_path.is_symlink() and pdf_path.stat().st_size <= _MAX_FILE:
                    pdf_rel = f"compiled/{side}/document.pdf"
                    _write_managed(output_dir, Path(pdf_rel), source=pdf_path)
                if synctex_path.is_file() and not synctex_path.is_symlink() and synctex_path.stat().st_size <= _MAX_FILE:
                    synctex_rel = f"compiled/{side}/document.synctex.gz"
                    _write_managed(output_dir, Path(synctex_rel), source=synctex_path)
                if exit_code == 0 and pdf_rel is not None and reason is None:
                    status = "success"
                else:
                    status = "failed"
                    reason = reason or f"latexmk 退出状态 {exit_code}；详见编译日志"
            except _ToolUnavailable as exc:
                status, reason = "unavailable", str(exc)
            except RuntimeError as exc:
                status, reason = "sandbox_rejected", str(exc)
            except OSError as exc:
                status, reason = "failed", f"隔离副本或编译产物错误：{exc}"
    log_rel = f"compiled/{side}/compile.log"
    _write_managed(output_dir, Path(log_rel), content=log or (reason or "").encode("utf-8"))
    result = CompileStatus(side, status, engine, exit_code, pdf_rel, log_rel, synctex_rel, reason)
    _write_managed(output_dir, Path(f"compiled/{side}/status.json"),
                   content=(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode())
    diagnostics = () if status == "success" else (Diagnostic(
        "sandbox_unavailable" if status == "sandbox_rejected" else "compile_failed", "warning",
        f"{'修改前' if side == 'old' else '修改后'}侧编译{reason or '失败'}"),)
    return result, diagnostics


def skipped_side(output_dir: Path, engine: str) -> CompileStatus:
    status = CompileStatus("old", "skipped", engine, None, None, None, None, "用户选择仅编译修改后版本")
    _write_managed(output_dir, Path("compiled/old/status.json"),
                   content=(json.dumps(asdict(status), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode())
    return status
