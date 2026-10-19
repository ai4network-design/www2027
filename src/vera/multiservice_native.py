"""Own-process native IPC with complete per-service ACK and exact edit ledger."""
import json
import os
from pathlib import Path
import subprocess
import time
from .zero_shot_evidence import save,text
from .multiservice_control import config,sha

class Native:
    def __init__(self,build,out,seed,n):
        self.out=Path(out);self.out.mkdir(parents=True,exist_ok=False);self.tick=0;self.n=n
        argv=[build['binary'],'--rundir='+str(self.out.resolve()),'--seed='+str(seed),'--decisions='+str(n)]
        save(self.out/'launch.json',dict(argv=argv,build=build))
        self.log=(self.out/'stdout.log').open('x')
        self.proc=subprocess.Popen(argv,stdout=self.log,stderr=subprocess.STDOUT,
            env={**os.environ,'LD_LIBRARY_PATH':build['lib']+':'+os.environ.get('LD_LIBRARY_PATH','')})
        self.state=self.read(0)
    def wait(self,path):
        start=time.perf_counter()
        while not path.exists():
            if self.proc.poll() is not None:raise RuntimeError('native exit before '+path.name)
            if time.perf_counter()-start>30:raise TimeoutError('IPC '+path.name)
            time.sleep(.001)
    def read(self,tick):
        path=self.out/f'obs_{tick}.json';self.wait(path);s=json.loads(path.read_text())
        if s['tick']!=tick or s['physical_time']!=tick+1 or len(s['services'])!=3:raise ValueError('snapshot clock/schema')
        for r in s['services']:
            if r['errors'] or r['tx']!=r['rx']+r['dropped']+r['pending']:raise ValueError('packet identity accounting')
        return s
    def apply(self,t):
        if self.tick>=self.n:raise ValueError('no extra decision')
        before=self.state;k=self.tick;tmp=self.out/f'act_{k}.tmp';dest=self.out/f'act_{k}.txt'
        text(tmp,' '.join(map(str,[k]+t['paths']+t['caps_bps']))+'\n');os.replace(tmp,dest)
        path=self.out/f'ack_{k}.json';self.wait(path);ack=json.loads(path.read_text());after=self.read(k+1)
        if config(ack)!=t or config(after)!=t or ack['tick']!=k:raise ValueError('installed target mismatch')
        route=sum(a!=b for a,b in zip(before['paths'],t['paths']))
        rate=sum(a!=b for a,b in zip(before['caps_bps'],t['caps_bps']))
        if route+rate>1 or any(ack[name]-before[name]!=value or after[name]!=ack[name]
                for name,value in [('edits',route+rate),('route_edits',route),('rate_edits',rate)]):raise ValueError('real edit counter mismatch')
        self.tick+=1;self.state=after;return ack
    def close(self):
        try:return self.proc.wait(timeout=30)
        finally:self.log.close()
    def abort(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait(timeout=5)
        self.log.close()
