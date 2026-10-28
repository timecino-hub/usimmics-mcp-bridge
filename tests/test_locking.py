import multiprocessing
import time
from pathlib import Path
from usimmics_bridge.locking import file_lock


def contender(path, queue):
    from filelock import Timeout
    try:
        with file_lock(path,timeout=0.2): queue.put('acquired')
    except Timeout: queue.put('blocked')


def test_process_lock(tmp_path):
    ctx=multiprocessing.get_context('spawn');queue=ctx.Queue();path=tmp_path/'circuit.sch'
    with file_lock(path):
        with file_lock(path): pass  # Calls can safely nest on one thread.
        proc=ctx.Process(target=contender,args=(str(path),queue));proc.start()
        assert queue.get(timeout=10)=='blocked'
        proc.join(10);assert proc.exitcode==0
    with file_lock(path,timeout=0.2): pass
