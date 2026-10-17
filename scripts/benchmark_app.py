"""Repeated local API measurements against a generated corpus; no server/network."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse,json,platform,time
from statistics import median
import numpy as np
from fastapi.testclient import TestClient
from services.api.app import create_app
from training.limits import protocol


def run(root):
    config=protocol()['benchmark'];results={}
    with TestClient(create_app(root),headers={'X-PokerMind-Local':'1'}) as client:
        for name,url in [('hand_page','/api/hands?source=synthetic'),('import_page','/api/import-pages'),('session_page','/api/session-pages?source=synthetic'),('overview','/api/overview')]:
            timings=[]
            for n in range(config['warmups']+config['repetitions']):
                started=time.perf_counter();response=client.get(url);elapsed=(time.perf_counter()-started)*1000
                if response.status_code!=200:raise RuntimeError(response.text)
                if n>=config['warmups']:timings.append(elapsed)
            p95=float(np.quantile(timings,.95));target=config['targets_ms'][name+'_p95']
            results[name]={'milliseconds':timings,'median_ms':median(timings),'p95_ms':p95,'target_ms':target,'passed':p95<=target,'response_bytes':len(response.content)}
        count=client.get('/api/hands?limit=1').json()['total']
    report={'protocol':'local-research-v2','hardware':platform.platform(),'unique_hands':count,'execution':'in-process FastAPI on one Mac, warmed filesystem','results':results,'passed':all(r['passed'] for r in results.values()),'cost_usd':0}
    output=Path('benchmarks/results/local-app-latency.json');output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True);a=p.parse_args();run(a.data)
