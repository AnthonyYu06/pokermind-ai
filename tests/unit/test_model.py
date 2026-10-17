import pytest
torch=pytest.importorskip('torch',reason='Optional research dependencies are not installed')
from packages.models.equity import EquityMLP
from training.equity import load_model
import hashlib

def test_tiny_batch_overfit_and_artifact_parity(tmp_path):
    torch.set_num_threads(2);torch.manual_seed(99)
    model=EquityMLP();opt=torch.optim.Adam(model.parameters(),lr=.01)
    x=torch.rand(8,55);y=torch.linspace(.1,.9,8)
    initial=float(((model(x)-y)**2).mean().detach())
    for _ in range(150):
        opt.zero_grad();loss=((model(x)-y)**2).mean();loss.backward();opt.step()
    assert float(loss.detach())<min(.001,initial/20)
    assert all(torch.isfinite(p.grad).all() for p in model.parameters())
    path=tmp_path/'weights.pt';torch.save(model.state_dict(),path)
    checksum=hashlib.sha256(path.read_bytes()).hexdigest();loaded=load_model(path,checksum)
    assert torch.equal(model(x),loaded(x))
    with pytest.raises(ValueError):load_model(path,'corrupt')
