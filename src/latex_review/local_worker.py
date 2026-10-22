"""A single locked local reading worker. Its owner pipe is never inherited."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import signal
import sys
from threading import Event, Thread

from .local_paths import state_directory
from .local_state import file_lock, read_json, write_json


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('recipe')
    parser.add_argument('--owner-fd', type=int, required=True)
    args = parser.parse_args(argv)
    os.set_inheritable(args.owner_fd, False)
    recipe = read_json(args.recipe)
    state = state_directory()
    import logging
    from logging.handlers import RotatingFileHandler
    logger = logging.getLogger('latex_review.worker')
    logger.setLevel(logging.INFO)
    log_path = Path(recipe['log'])
    log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handler = RotatingFileHandler(log_path, maxBytes=5 * 1024**2, backupCount=5)
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    logger.addHandler(handler)
    log_path.chmod(0o600)
    class LogStream:
        def write(self, value):
            if value.strip():
                logger.info('%s', value.rstrip())
        def flush(self):
            pass
    sys.stdout = sys.stderr = LogStream()
    logger.info('worker_started paper=%s kind=%s nonce=%s', recipe['paper_id'], recipe['kind'], recipe['nonce'])
    stop = Event()
    from .local_tasks import install, cancel
    from . import local_tasks
    local_tasks.OWNER_FD = args.owner_fd
    install()

    def request_stop(*_):
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    def watch_owner():
        try:
            while os.read(args.owner_fd, 1):
                pass
        finally:
            stop.set()
            cancel()
    Thread(target=watch_owner, name='local-owner', daemon=True).start()
    # Signal cancellation also runs off the main thread, which may be compiling.
    Thread(target=lambda: (stop.wait(), cancel()), name='local-cancel', daemon=True).start()
    try:
        with ExitStack() as stack:
            if recipe['kind'] == 'build-live':
                stack.enter_context(file_lock(state / 'slots' / f"{recipe['paper_id']}.live.job.lock"))
                from .live import build, fingerprint
                from .contract import ComparisonSource
                from .sources import ProjectSource
                from .report_rebuild import rebuild_lock
                candidate = Path(recipe['candidate'])
                candidate.mkdir(parents=True, exist_ok=True)
                from .cli import _config
                source = ProjectSource('new', Path(recipe['root']), recipe['entry'],
                                       ComparisonSource('directory', recipe['root']),
                                       configuration=_config(Path(recipe['config']), recipe.get('config_explicit', False)))
                with rebuild_lock(Path(recipe['output'])):
                    source_fingerprint = fingerprint(source.root, Path(recipe['output']), cache_directory=Path(recipe['cache']))
                    build(source, candidate)
                if stop.is_set():
                    return 4
                write_json(recipe['ready'], {'nonce': recipe['nonce'], 'built': True, 'source_fingerprint': source_fingerprint})
                return 0
            stack.enter_context(file_lock(state / 'slots' / f"{recipe['paper_id']}.{recipe['kind']}.lock", blocking=False))
            if stop.is_set():
                return 0
            if recipe['kind'] == 'live':
                from .live import run_worker
                return run_worker(recipe, stop)
            from .serve import create_server
            from .translation import TranslationConfig
            from .cli import _config
            config = _config(Path(recipe['config']), recipe.get('config_explicit', False))
            translation = TranslationConfig.from_mapping(config['translation']) if 'translation' in config else None
            server = create_server(Path(recipe['directory']), recipe.get('port', 0),
                                   translation_config=translation,
                                   cache_directory=Path(recipe['cache']),
                                   annotation_project_root=Path(recipe['root']))
            stack.callback(server.server_close)
            if stop.is_set():
                return 0
            server.local_nonce = recipe['nonce']
            thread = Thread(target=server.serve_forever, kwargs={'poll_interval': .1}, daemon=True)
            thread.start()
            write_json(recipe['ready'], {'nonce': recipe['nonce'], 'pid': os.getpid(),
                                        'port': server.server_port, 'directory': recipe['directory']})
            stop.wait()
            server.shutdown()
            thread.join()
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        write_json(recipe['ready'], {'nonce': recipe['nonce'], 'error': str(exc)})
        print(f'本地工作进程失败：{exc}', file=sys.stderr, flush=True)
        return 4
    finally:
        stop.set()
        cancel()
        os.close(args.owner_fd)


if __name__ == '__main__':
    raise SystemExit(main())
