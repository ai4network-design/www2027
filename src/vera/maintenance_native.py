"""Native IPC for the maintenance scenario (src/vera/ns3/ns3_multiservice_maint.cc).

Same protocol as multiservice_native.Native (that file is left unchanged: it is pinned by the calibration receipt);
the only difference is the --maintenance argument, a list of (path, start, end, mbps) capacity caps applied during
decision intervals [start, end). The build descriptor is runs/ns3_maintenance_build_20260930/native_maint.json.
"""
import json, os, subprocess
from pathlib import Path
from .multiservice_native import Native
from .zero_shot_evidence import save, text


def spec(maintenance):
    return ';'.join(f'{p},{s},{e},{int(round(mbps * 1e6))}' for p, s, e, mbps in maintenance)


def topology_spec(C):
    """--topology for src/vera/ns3/ns3_multiservice_topo.cc from a topology contract that carries ns3_low_mbps / ns3_high_mbps
    (per path); None for the frozen three-path contract (the binary's defaults reproduce it)."""
    if 'ns3_low_mbps' not in C:
        return None
    lst = lambda xs: ','.join(str(int(round(x * 1e6))) if isinstance(x, float) or isinstance(x, int) else str(x) for x in xs)
    return ';'.join([','.join(C['services']), lst(C['demand_mbps']), ','.join(str(d) for d in C['path_delay_ms']), lst(C['ns3_low_mbps']), lst(C['ns3_high_mbps']), ','.join(str(p) for p in C['initial_paths'])])


class MaintNative(Native):
    def __init__(self, build, out, seed, n, maintenance=(), topology=None):
        self.out = Path(out); self.out.mkdir(parents=True, exist_ok=False); self.tick = 0; self.n = n
        argv = [build['binary'], '--rundir=' + str(self.out.resolve()), '--seed=' + str(seed), '--decisions=' + str(n)]
        if maintenance:
            argv.append('--maintenance=' + spec(maintenance))
        if topology:
            argv.append('--topology=' + topology)
        save(self.out / 'launch.json', dict(argv=argv, build=build, maintenance=[list(x) for x in maintenance], topology=topology))
        self.log = (self.out / 'stdout.log').open('x')
        self.proc = subprocess.Popen(argv, stdout=self.log, stderr=subprocess.STDOUT,
                                     env={**os.environ, 'LD_LIBRARY_PATH': build['lib'] + ':' + os.environ.get('LD_LIBRARY_PATH', '')})
        self.state = self.read(0)

    def read(self, tick):
        """As Native.read but for N services (topology contract); multiservice_native.py stays unchanged (pinned by the calibration receipt)."""
        from . import multiservice_control as m
        path = self.out / f'obs_{tick}.json'; self.wait(path); s = json.loads(path.read_text())
        if s['tick'] != tick or s['physical_time'] != tick + 1 or len(s['services']) != m.N:
            raise ValueError('snapshot clock/schema')
        for r in s['services']:
            if r['errors'] or r['tx'] != r['rx'] + r['dropped'] + r['pending']:
                raise ValueError('packet identity accounting')
        return s
