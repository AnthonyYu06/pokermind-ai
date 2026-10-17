"""Optional device operation checks, separate from CPU quality experiments."""
import copy
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import torch
from packages.features.equity import FEATURE_DIM
from packages.models.equity import EquityMLP
from packages.models.card_set import CardSet,encode_cards
from packages.models.behavior import BehaviorModel

torch.manual_seed(11);torch.set_num_threads(2)
cases=[('equity_mlp',EquityMLP(),[torch.randn(8,FEATURE_DIM)]),
       ('card_set',CardSet(16),[torch.tensor(encode_cards(['As','Kh'],['3c','7h','Td','Jc','Qs'])).repeat(8,1,1)]),
       ('behavior_gru',BehaviorModel(),[torch.randn(8,45),torch.randn(8,4,9),torch.tensor([4]*8),torch.ones(8,7,dtype=torch.bool)])]
results={'scope':'Forward parity and finite optimizer steps only; quality experiments remain CPU','cpu':True,'devices':{}}
for device,available in [('mps',torch.backends.mps.is_available()),('cuda',torch.cuda.is_available())]:
    checks=[]
    if available:
        for name,cpu,inputs in cases:
            try:
                model=copy.deepcopy(cpu).to(device);values=[x.to(device) for x in inputs]
                with torch.no_grad():
                    expected=cpu(*inputs);actual=model(*values).cpu();difference=float((expected-actual).abs().max())
                assert difference<1e-4,(name,difference)
                optimizer=torch.optim.Adam(model.parameters(),lr=.001);losses=[]
                for _ in range(4):
                    optimizer.zero_grad();prediction=model(*values);loss=prediction.square().mean();loss.backward()
                    assert torch.isfinite(loss) and all(p.grad is None or torch.isfinite(p.grad).all() for p in model.parameters())
                    optimizer.step();losses.append(float(loss.detach().cpu()))
                checks.append({'model':name,'status':'passed','max_abs_difference':difference,'step_losses':losses})
            except (AssertionError,RuntimeError,NotImplementedError) as e:checks.append({'model':name,'status':'unsupported-or-failed','reason':str(e)[:600]})
    results['devices'][device]={'available':available,'checks':checks}
(ROOT/'benchmarks/results/device-compatibility.json').write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps(results,indent=2))
