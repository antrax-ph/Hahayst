# language: Python, file: api_server.py, target: Python 3.10+
import asyncio
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

import mlbb_solver as solver
from jobs import JobStore
from solver_pool import SessionPool

log = logging.getLogger('mlbb_solver.api')

CONFIG = {
    'pool_size': int(os.environ.get('SOLVER_POOL', '5')),
    'transport': os.environ.get('SOLVER_TRANSPORT', 'chromium'),
    'headless': os.environ.get('SOLVER_HEADLESS', '1') not in ('0', 'false',
                                                               'no'),
    'token': os.environ.get('SOLVER_TOKEN', ''),
    'db_path': os.environ.get('SOLVER_DB',
                              os.path.join(os.path.dirname(__file__),
                                           'solver_jobs.db')),
    'best_params': os.environ.get('SOLVER_BEST_PARAMS',
                                  os.path.join(os.path.dirname(__file__),
                                               'best_drag_params.json')),
    'drag_variant': os.environ.get('SOLVER_DRAG_VARIANT', 'best'),
    'worker_count': int(os.environ.get('SOLVER_WORKERS', '4')),
}

state = {
    'pool': None,
    'store': None,
    'workers': [],
    'stop': threading.Event(),
    'started_at': time.time(),
    'solved': 0,
    'failed': 0,
}


def require_token(authorization: str = Header(default=None)):
    if not CONFIG['token']:
        return
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='missing bearer token')
    if authorization[7:].strip() != CONFIG['token']:
        raise HTTPException(status_code=403, detail='bad token')


class SolveReq(BaseModel):
    account: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    proxy: str | None = None


class SubmitReq(BaseModel):
    accounts: list[SolveReq] = Field(..., min_length=1, max_length=10000)


def _parse_proxy(s):
    if not s:
        return None
    s = s.strip()
    if not s:
        return None
    if '://' not in s:
        return {'server': f'http://{s}'}
    return {'server': s}


def _do_sync_solve(account, password, proxy):
    pool = state['pool']
    sess = pool.acquire()
    try:
        payload = solver.gen_payload(account, password)
        if proxy:
            sess.proxy = _parse_proxy(proxy)
        raw = sess.solve_login(payload)
        result = solver.classify_login(None, raw)
        state['solved'] += 1
        return result
    except Exception as e:
        state['failed'] += 1
        log.exception('sync solve failed: %s', account)
        return {'status': 'error', 'error': 'exception',
                'message': str(e)[:300]}
    finally:
        pool.release(sess)


def _worker_loop():
    store = state['store']
    while not state['stop'].is_set():
        job = store.claim_next()
        if job is None:
            time.sleep(0.4)
            continue
        try:
            result = _do_sync_solve(job['account'], job['password'],
                                    job.get('proxy'))
            store.finish(job['id'], result=result)
        except Exception as e:
            log.exception('worker job failed: %s', job['id'])
            store.finish(job['id'], error=str(e)[:300])


@asynccontextmanager
async def lifespan(app):
    loaded = solver.load_best_params(CONFIG['best_params'])
    if loaded:
        log.info('loaded best_drag_params.json variant=%s rate=%s',
                 loaded.get('variant'), loaded.get('rate'))
    else:
        log.warning('no best_drag_params.json — using seed defaults')
    if CONFIG['drag_variant'] != 'best':
        solver.set_drag_variant(CONFIG['drag_variant'])

    state['store'] = JobStore(CONFIG['db_path'])
    orphans = state['store'].recover_orphans()
    if orphans:
        log.warning('recovered %d orphaned running jobs', orphans)

    log.info('warming pool: size=%d transport=%s headless=%s',
             CONFIG['pool_size'], CONFIG['transport'], CONFIG['headless'])
    state['pool'] = SessionPool(size=CONFIG['pool_size'],
                                 transport=CONFIG['transport'],
                                 headless=CONFIG['headless'])

    for _ in range(CONFIG['worker_count']):
        th = threading.Thread(target=_worker_loop, daemon=True)
        th.start()
        state['workers'].append(th)

    log.info('api ready: pool=%d workers=%d auth=%s',
             CONFIG['pool_size'], CONFIG['worker_count'],
             'on' if CONFIG['token'] else 'off')
    yield

    log.info('shutting down')
    state['stop'].set()
    if state['pool']:
        state['pool'].close()


app = FastAPI(title='MLBB solver', version='1.0', lifespan=lifespan)


@app.get('/healthz')
async def healthz():
    pool = state['pool']
    return {
        'ok': True,
        'pool_ready': pool.ready if pool else 0,
        'pool_size': CONFIG['pool_size'],
        'transport': CONFIG['transport'],
        'drag_variant': solver._DRAG_VARIANT,
        'workers': CONFIG['worker_count'],
        'auth': bool(CONFIG['token']),
        'solved': state['solved'],
        'failed': state['failed'],
        'uptime_s': int(time.time() - state['started_at']),
    }


@app.get('/metrics')
async def metrics(_=Depends(require_token)):
    pool = state['pool']
    return {'solved': state['solved'], 'failed': state['failed'],
            'pool_ready': pool.ready if pool else 0,
            'pool_size': CONFIG['pool_size'],
            'uptime_s': int(time.time() - state['started_at'])}


@app.post('/solve', dependencies=[Depends(require_token)])
async def solve(req: SolveReq):
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None, _do_sync_solve, req.account, req.password, req.proxy)
    result.pop('full_response', None)
    return result


@app.post('/submit', dependencies=[Depends(require_token)])
async def submit(req: SubmitReq):
    store = state['store']
    batch_id = os.urandom(12).hex()
    job_ids = []
    for idx, item in enumerate(req.accounts):
        jid = store.create(kind='batch', account=item.account,
                           password=item.password, proxy=item.proxy,
                           batch_id=batch_id, idx=idx)
        job_ids.append(jid)
    return {'batch_id': batch_id, 'job_ids': job_ids,
            'total': len(job_ids)}


@app.get('/job/{job_id}', dependencies=[Depends(require_token)])
async def job_status(job_id: str):
    store = state['store']
    row = store.get(job_id)
    if not row:
        raise HTTPException(404, 'job not found')
    resp = {'id': row['id'], 'status': row['status'],
            'account': row['account'],
            'created_at': row['created_at'],
            'updated_at': row['updated_at']}
    if row['result']:
        resp['result'] = json.loads(row['result'])
    if row['error']:
        resp['error'] = row['error']
    return resp


@app.post('/job/{job_id}/cancel', dependencies=[Depends(require_token)])
async def job_cancel(job_id: str):
    store = state['store']
    if not store.cancel(job_id):
        raise HTTPException(409, 'not cancellable')
    return {'id': job_id, 'cancelled': True}


@app.get('/batch/{batch_id}', dependencies=[Depends(require_token)])
async def batch_status(batch_id: str):
    return state['store'].batch_status(batch_id)


@app.get('/batch/{batch_id}/results', dependencies=[Depends(require_token)])
async def batch_results(batch_id: str):
    store = state['store']
    with store._conn() as c:
        rows = c.execute(
            "SELECT id, account, status, result, error FROM jobs "
            "WHERE batch_id=? ORDER BY idx", (batch_id,)).fetchall()
    out = []
    for r in rows:
        item = {'id': r['id'], 'account': r['account'],
                'status': r['status']}
        if r['result']:
            item['result'] = json.loads(r['result'])
        if r['error']:
            item['error'] = r['error']
        out.append(item)
    return {'batch_id': batch_id, 'results': out}