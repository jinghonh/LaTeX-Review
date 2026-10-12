"""显式上传静态报告；SSH 凭据与论文源码不进入发布包。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib

from .delivery import DeliveryError as UploadError, build_archive, prepare_publication, write_archive, manifest_id


def upload_config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")).expanduser()
    if not base.is_absolute():
        raise UploadError("XDG_CONFIG_HOME 必须是绝对路径")
    return base / "latex-review" / "upload.toml"


def load_upload_config(entry: Path) -> tuple[str, str, str]:
    path = upload_config_path()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UploadError(f"无法读取上传配置 {path}：{exc}") from exc
    host, base = data.get("host"), data.get("base_url")
    if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", host):
        raise UploadError("上传 host 必须是 SSH 配置别名")
    if (not isinstance(base, str) or not re.fullmatch(r"https://[A-Za-z0-9.-]+(?::[0-9]+)?", base)):
        raise UploadError("上传 base_url 必须是 HTTPS 站点地址")
    papers = data.get("papers", [])
    if not isinstance(papers, list):
        raise UploadError("上传 papers 必须是表数组")
    matches = [item for item in papers if isinstance(item, dict) and isinstance(item.get("entry"), str)
               and Path(item["entry"]).expanduser().resolve() == entry.resolve()]
    if (len(matches) != 1 or not isinstance(matches[0].get("slug"), str)
            or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", matches[0]["slug"])):
        raise UploadError("论文入口须在 upload.toml 中唯一注册，并指定有效 slug")
    return host, base, matches[0]["slug"]


def _ssh(host: str, command: str, **kwargs):
    return subprocess.run(['ssh', '-C', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
                           '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
                           host, command], capture_output=True, timeout=300, **kwargs)


def _missing_receipt(value: dict, prepared) -> set[str]:
    if (not isinstance(value, dict) or type(value.get('protocol')) is not int or value['protocol'] != 1
            or value.get('slug') != prepared.metadata['slug']
            or value.get('manifest_id') != manifest_id(prepared.manifest)
            or not isinstance(value.get('missing'), list)
            or any(not isinstance(item, str) for item in value['missing'])):
        raise UploadError('服务器资源查询回执无效')
    missing = set(value['missing'])
    known = {item['sha256'] for item in prepared.manifest['resources'].values()}
    if not missing <= known or len(missing) != len(value['missing']):
        raise UploadError('服务器请求了清单外资源或重复资源')
    return missing


def upload_report(directory: Path, entry: Path, translation_config: dict | None = None) -> str:
    host, base, slug = load_upload_config(entry)
    try:
        prepared = prepare_publication(directory, slug, translation_config)
        query = _ssh(host, 'resource-query-v1', input=json.dumps(prepared.manifest, ensure_ascii=False).encode())
        if query.returncode:
            message = query.stderr.decode('utf-8', errors='replace').strip()
            # This exact refusal is emitted by the legacy forced-command receiver.
            if query.returncode == 1 and message == '此账号仅允许发布报告':
                missing = None
            else:
                raise UploadError(f'SSH 资源查询失败：{message or query.returncode}')
        else:
            missing = _missing_receipt(json.loads(query.stdout), prepared)
        total_files = total_bytes = 0
        for attempt in range(3):
            with tempfile.TemporaryFile() as archive:
                stats = write_archive(prepared, archive, missing=missing)
                total_files += stats['resource_files_sent']
                total_bytes += stats['resource_bytes_sent']
                archive.seek(0)
                result = _ssh(host, 'publish' if missing is None else 'publish-incremental-v1', stdin=archive)
            if result.returncode == 2 and missing is not None:
                receipt = json.loads(result.stdout)
                if not isinstance(receipt, dict) or receipt.get('error') != 'missing_resources':
                    raise UploadError('服务器增量发布错误回执无效')
                missing = _missing_receipt(receipt, prepared)
                if not missing or attempt == 2:
                    raise UploadError('服务器资源在发布前持续缺失，请重试')
                continue
            if result.returncode:
                message = result.stderr.decode('utf-8', errors='replace').strip()
                raise UploadError(f'SSH 发布失败：{message or result.returncode}')
            receipt = json.loads(result.stdout)
            if not isinstance(receipt, dict) or receipt.get('release') != prepared.metadata['release'] or receipt.get('slug') != slug:
                raise UploadError('服务器发布回执与本次报告不一致')
            print(f'上传资源：{total_files} 个文件，{total_bytes} 字节；'
                  + ('完整上传兼容模式。' if missing is None else '增量上传。'), file=sys.stderr)
            break
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        if isinstance(exc, UploadError):
            raise
        raise UploadError(str(exc)) from exc
    return f'{base}/{slug}/latest/report.html'
