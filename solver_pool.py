# language: Python, file: solver_pool.py, target: Python 3.10+
import logging
import queue
import threading

from mlbb_solver import SolverSession

log = logging.getLogger('mlbb_solver.pool')


class SessionPool:
    def __init__(self, size=5, transport='chromium', headless=True):
        self.size = size
        self.transport = transport
        self.headless = headless
        self._q = queue.Queue(maxsize=size)
        self._lock = threading.Lock()
        self._closed = False
        self._warm()

    def _make(self):
        s = SolverSession(transport=self.transport, headless=self.headless)
        s.start()
        return s

    def _warm(self):
        for _ in range(self.size):
            try:
                self._q.put(self._make(), block=False)
            except Exception as e:
                log.error('pool warm failed: %s', str(e)[:120])

    def acquire(self, timeout=120.0):
        if self._closed:
            raise RuntimeError('pool closed')
        try:
            sess = self._q.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError('no session available')
        if not sess._started:
            log.warning('dead session from pool — relaunching')
            try:
                sess.close()
            except Exception:
                pass
            sess = self._make()
        return sess

    def release(self, sess):
        if self._closed:
            try:
                sess.close()
            except Exception:
                pass
            return
        if not sess._started:
            try:
                sess.close()
            except Exception:
                pass
            try:
                sess = self._make()
            except Exception as e:
                log.error('pool release relaunch failed: %s', str(e)[:120])
                return
        try:
            self._q.put(sess, block=False)
        except queue.Full:
            try:
                sess.close()
            except Exception:
                pass

    def close(self):
        self._closed = True
        while True:
            try:
                sess = self._q.get_nowait()
            except queue.Empty:
                break
            try:
                sess.close()
            except Exception:
                pass

    @property
    def ready(self):
        return self._q.qsize()