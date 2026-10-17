from itertools import permutations
import hashlib
import pytest
np=pytest.importorskip('numpy')
torch=pytest.importorskip('torch')
from packages.models.card_set import CardSet,encode_cards
from training.local_equity import fit


def test_card_encoder_preserves_order_and_suit_symmetry():
    own=['As','Kh'];board=['3c','7h','Td','Jc','Qs'];expected=encode_cards(own,board)
    for permutation in permutations('cdhs'):
        rename=dict(zip('cdhs',permutation))
        convert=lambda cards:[c[0]+rename[c[1]] for c in reversed(cards)]
        assert np.array_equal(encode_cards(convert(own),convert(board)),expected)
    with pytest.raises(ValueError):encode_cards(['As','As'],board)
    torch.manual_seed(1);model=CardSet(16);x=torch.tensor(expected)[None]
    assert torch.allclose(model(x),model(x[:,[1,0,6,4,3,2,5]]),atol=1e-7)


def test_card_set_overfit_and_exact_resume(tmp_path):
    torch.set_num_threads(2);torch.manual_seed(9)
    folder=tmp_path/'shards';folder.mkdir();rows=[]
    for i in range(12):
        rows.append({'x':torch.rand(55),'cards':torch.rand(7,18),'y':.1+.07*i,'family':hashlib.sha256(str(i).encode()).hexdigest(),'hand':str(i)})
    for split in ['train','validation']:torch.save(rows,folder/f'{split}-000.pt')
    manifest={'dataset_id':'fixture','files':{'train-000.pt':'fixture','validation-000.pt':'fixture'}}
    cfg={'epochs':6,'early_stopping_patience':20}
    full=tmp_path/'full';part=tmp_path/'part';resumed=tmp_path/'resumed'
    for p in (full,part,resumed):p.mkdir()
    a,c1,v1=fit(folder,manifest,'card_set',16,1.,11,cfg,full)
    fit(folder,manifest,'card_set',16,1.,11,{**cfg,'epochs':3},part)
    b,c2,v2=fit(folder,manifest,'card_set',16,1.,11,cfg,resumed,resume=part/'checkpoint.pt')
    assert c1==c2 and v1==v2
    assert all(torch.equal(v,b.state_dict()[k]) for k,v in a.state_dict().items())
    model=CardSet(32);optimizer=torch.optim.Adam(model.parameters(),lr=.01)
    x=torch.stack([r['cards'] for r in rows]);y=torch.tensor([r['y'] for r in rows])
    for _ in range(250):
        loss=((model(x)-y)**2).mean();optimizer.zero_grad();loss.backward();optimizer.step()
    assert float(loss.detach())<.001
