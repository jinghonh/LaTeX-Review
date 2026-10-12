"""Bounded HTTP tasks with progress independent of the annotation storage lock."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Lock
import time
import uuid

from .annotations import AnnotationConflict


class AnnotationJobs:
    def __init__(self):
        self.lock = Lock()
        self.jobs = {}
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='annotation')

    def submit(self, run):
        with self.lock:
            now = time.time()
            self.jobs = {key: job for key, job in self.jobs.items()
                         if job['state'] in ('queued', 'running') or now - job['updated_at'] < 900}
            if len(self.jobs) >= 64:
                completed = next((key for key, job in self.jobs.items() if job['state'] not in ('queued', 'running')), None)
                if completed is None:
                    raise ValueError('批注任务队列已满，请等待现有任务完成')
                del self.jobs[completed]
            task_id = uuid.uuid4().hex
            self.jobs[task_id] = {'task_id': task_id, 'state': 'queued', 'phase': 'queued',
                                  'completed': 0, 'total': 0, 'updated_at': now}
        self.executor.submit(self._run, task_id, run)
        return {'task_id': task_id, 'status_url': f'/api/annotations/jobs/{task_id}'}

    def _run(self, task_id, run):
        def progress(phase, completed=0, total=0):
            with self.lock:
                self.jobs[task_id].update(state='running', phase=phase, completed=completed,
                                          total=total, updated_at=time.time())
        progress('read')
        try:
            result = run(progress)
            with self.lock:
                self.jobs[task_id].update(state='complete', phase='complete', result=result, updated_at=time.time())
        except Exception as exc:
            with self.lock:
                self.jobs[task_id].update(state='failed', error='conflict' if isinstance(exc, AnnotationConflict) else 'invalid_request',
                                          message=str(exc), updated_at=time.time())

    def snapshot(self, task_id):
        with self.lock:
            if task_id not in self.jobs:
                raise KeyError('批注任务不存在或已过期')
            return deepcopy(self.jobs[task_id])

    def active(self):
        with self.lock:
            return {'jobs': [{k: v for k, v in job.items() if k not in ('result', 'message')}
                             for job in self.jobs.values() if job['state'] in ('queued', 'running')]}

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)
