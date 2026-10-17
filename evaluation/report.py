"""Summarize available local evidence without starting training or cloud jobs."""
import json
from pathlib import Path
from services.api.artifacts import report


def main():
    root=Path(__file__).resolve().parents[1]/'.data'
    output={}
    if (root/'research.json').exists():
        r=report(root,'research');output['equity']={k:r[k] for k in ['status','domain','mean_test_mae','std_test_mae','promotion']}
    for game in ['kuhn','leduc']:
        if (root/f'strategy-{game}.json').exists():
            r=report(root,'strategy-'+game);output[game]={k:r[k] for k in ['status','mean_exploitability','std_exploitability','promotion']}
    if (root/'behavior.json').exists():
        r=report(root,'behavior');output['behavior']={'status':r['status'],'domain':r['domain'],'decisions':r['dataset']['decisions'],'promotion':r['promotion']}
    if not output:raise SystemExit('No local research report. Run make research-tiny or make research-strategy.')
    print(json.dumps(output,indent=2))


if __name__=='__main__':main()
