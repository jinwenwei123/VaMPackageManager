"""Resumable, opt-in summary generation; completed labels remain in the catalog."""
import threading
import uuid

from .content_tags import needed, outputs
from .util import Cancelled, atomic_json, read_json, stamp


class SummaryQueue:
    def __init__(self, catalog, ai, jobs):
        self.c, self.ai, self.jobs = catalog, ai, jobs
        self.file = catalog.data / 'summary-queue.json'
        self.lock = threading.RLock()
        self.stopping = False
        self.state = read_json(self.file, {})
        if self.state and 'run_id' not in self.state:
            # Pending queues from earlier versions were fill-only. Preserve that
            # meaning, and retain their completed count when migrating them.
            self.state.update(mode='missing',run_id=uuid.uuid4().hex,baseline_updated=self.state.get('updated',0))
            self._save()
        if self.state.get('status') in ('running', 'pausing'):
            self.state.update(status='paused', error='', message='上次运行已中断，本轮已保存的结果会自动跳过；点击继续生成。')
            self._save()

    def _save(self):
        self.state['modified'] = stamp()
        atomic_json(self.file, self.state)

    def public(self):
        # Completion visibility belongs to this game's durable queue, not the browser port.
        with self.lock:
            return {k: v for k, v in self.state.items() if k not in ('keys','baseline_updated')}

    def dismiss(self, run_id):
        with self.lock:
            if (not isinstance(run_id, str) or not run_id or run_id != self.state.get('run_id')
                    or self.state.get('status') != 'done'):
                raise ValueError('生成任务已变化或尚未完成，请刷新后再关闭完成提示。')
            if not self.state.get('notice_dismissed'):
                previous = self.state
                self.state = {**previous, 'notice_dismissed': True}
                try: self._save()
                except Exception:
                    self.state = previous
                    raise
            return self.public()

    def start(self, scope, keys=None, origin='ui', mode='all', target='summary'):
        outputs(target)
        if scope not in ('all', 'favorites', 'selected'):
            raise ValueError('请选择全部资源、收藏或已选资源。')
        if mode not in ('all','missing'):
            raise ValueError('请选择全部生成 / 重新生成或仅补充未生成。')
        if scope == 'selected' and (not isinstance(keys, list) or any(not isinstance(k, str) for k in keys)):
            raise ValueError('所选资源列表无效')
        with self.lock:
            if self.state.get('status') in ('running', 'pausing', 'paused', 'error'):
                raise ValueError('已有生成队列，请先继续或结束这一轮。')
            pkgs = self.c.packages()
            candidates = (list(dict.fromkeys(k.casefold() for k in keys)) if scope == 'selected' else
                          sorted(k for k, p in pkgs.items() if scope != 'favorites' or p['favorite']))
            todo = [k for k in candidates if k in pkgs and not pkgs[k]['error']
                    and needed(pkgs[k],target,mode,None)]
            previous = self.state
            self.state = {'scope': scope, 'mode':mode,'target':target,'run_id':uuid.uuid4().hex,'baseline_updated':0,
                          'keys': todo, 'total': len(todo), 'current': 0,
                          'updated': 0, 'skipped': len(candidates) - len(todo), 'status': 'running',
                          'created': stamp(), 'error': '', 'message': ('准备生成所选内容；简介、标签分别保存，未选择的内容保持不变。' if mode=='all'
                                else '分别跳过已完成的简介或标签，只补充所选的未生成内容。')}
            return self._launch(previous, origin)

    def _launch(self, previous, origin):
        # The worker takes our lock before reading state. Failed job admission cannot replace a queue.
        try:
            self.state.setdefault('mode','missing')
            self.state.setdefault('target','summary')
            self.state.setdefault('run_id',uuid.uuid4().hex)
            self.state.setdefault('baseline_updated',self.state.get('updated',0))
            self._save()
            title = {'all': '全部资源', 'favorites': '收藏', 'selected': '所选资源'}[self.state['scope']] + ' · AI 简介与标签'
            return self.jobs.start(title, self._run, origin=origin)
        except Exception:
            self.state = previous
            self._save()
            raise

    def resume(self, origin='ui'):
        with self.lock:
            if self.state.get('status') not in ('paused', 'error'):
                raise ValueError('当前没有暂停的生成队列。')
            previous = dict(self.state)
            self.state.update(status='running', error='', message='继续本轮生成，自动跳过本轮已保存的结果。')
            return self._launch(previous, origin)

    def pause(self):
        with self.lock:
            if self.state.get('status') == 'running':
                self.state.update(status='pausing', message='将在当前批次保存后暂停，不再发起下一批请求。')
                self._save()
            return self.public()

    def finish(self):
        with self.lock:
            if self.state.get('status') in ('running', 'pausing'):
                raise ValueError('请先暂停，等待当前批次保存后再结束。')
            self.state.update(status='cancelled', message='本轮已结束，已保存的内容保留。')
            self._save()
            return self.public()

    def suspend(self):
        """App exit cancels inference but keeps the unfinished queue resumable."""
        with self.lock:
            self.stopping = True
            if self.state.get('status') in ('running', 'pausing'):
                self.state.update(status='paused', error='', message='包管家已退出，已保存的内容保留；下次打开可继续生成。')
                self._save()

    def _update_completed(self):
        q=self.state
        q['updated']=q.get('baseline_updated',0)+len(self.c.summary_run_keys(q.get('run_id'),q.get('target','summary')) & set(q.get('keys',[])))

    def _run(self, job):
        try:
            while True:
                job.check()
                with self.lock:
                    q = self.state
                    job.update(q['current'], q['total'], f"已处理 {q['current']} / {q['total']} · 已生成 {q['updated']} · 跳过 {q['skipped']}")
                    if q['current'] >= q['total']:
                        q.update(status='done', message='本轮所选内容已完成；下次可选择全部重新生成或仅补充未生成。')
                        self._save()
                        return {'summary_queue': self.public()}
                    if q['status'] in ('pausing', 'paused'):
                        q.update(status='paused', message='已暂停，当前批次结果已保存。')
                        self._save()
                        return {'summary_queue': self.public()}
                    chunk = q['keys'][q['current']:q['current'] + 10]
                    scope = q['scope']
                    run_id=q['run_id'];mode=q['mode'];target=q.get('target','summary')
                # In all mode only this run's saved results are skipped. The run
                # marker is committed with the label before the JSON checkpoint.
                pkgs = self.c.packages()
                todo = [pkgs[k] for k in chunk if k in pkgs and not pkgs[k]['error']
                        and (scope != 'favorites' or pkgs[k]['favorite'])
                        and needed(pkgs[k],target,mode,run_id)]
                if todo:
                    if target=='summary':self.ai.generate_batch(todo, job,run_id=run_id)
                    else:self.ai.generate_batch(todo,job,run_id=run_id,target=target,mode=mode)
                completed=self.c.summary_run_keys(run_id,target)
                with self.lock:
                    q['current'] += len(chunk)
                    self._update_completed()
                    q['skipped'] += len(chunk) - len(completed & set(chunk))
                    self._save()
        except Cancelled:
            with self.lock:
                self._update_completed()
                if self.stopping:
                    self.state.update(status='paused', error='', message='包管家已退出，已保存的内容保留；下次打开可继续生成。')
                else:
                    self.state.update(status='cancelled', message='本轮已结束，已保存的内容保留。')
                self._save()
            raise
        except Exception as e:
            with self.lock:
                self._update_completed()
                if self.stopping:
                    self.state.update(status='paused', error='', message='包管家已退出，已保存的内容保留；下次打开可继续生成。')
                else:
                    self.state.update(status='error', error=str(e)[:1000], message='生成中断，已保存的内容保留；处理提示后可继续。')
                self._save()
            raise
