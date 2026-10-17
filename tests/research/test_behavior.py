from copy import deepcopy
import pytest
np=pytest.importorskip('numpy')
pytest.importorskip('torch')
from packages.simulation.behavior_data import generate_behavior,POLICIES,probabilities
from packages.pokermind_core.engine import reconstruct,Engine
from packages.pokermind_core.contracts import Event
from packages.features.behavior import encode,candidates,target,ACTIONS
from packages.models.behavior import BehaviorModel
from training.behavior import collate


def test_generated_behavior_actions_match_legal_buckets_and_reconcile():
    for policy in POLICIES:
        for seed in range(20):
            hand=generate_behavior(81000+seed,policy);_,obs=reconstruct(hand)
            engine=Engine(hand)
            for o in obs:
                x,seq,mask=encode(o)
                assert x.shape==(45,) and seq.shape[1]==9 and np.isfinite(x).all()
                assert mask[target(o,hand.events[o['action_index']].model_dump())]
                probs=np.array(probabilities(o,policy))
                assert probs.sum()==pytest.approx(1) and np.all(probs[~mask]==0)
                for candidate in candidates(o).values():
                    copy=deepcopy(engine)
                    # Rebuild to this decision; candidates must be accepted by rules.
                    copy=Engine(hand)
                    for e in hand.events[:o['action_index']]:copy.apply(e)
                    copy.apply(Event(**candidate))


def test_features_ignore_hidden_cards_and_future_and_preserve_suit_symmetry():
    hand=generate_behavior(71234,'pressure');_,obs=reconstruct(hand);o=obs[0]
    x,seq,mask=encode(o);changed=deepcopy(o)
    other=next(s for s in changed['state']['seats'] if s['seat']!=changed['acting_player']);other['cards']=['As','Ah']
    changed['terminal_summary']={'anything':'future'}
    a,b,c=encode(changed)
    assert np.array_equal(x,a) and np.array_equal(seq,b) and np.array_equal(mask,c)
    changed=deepcopy(o);mapping=dict(zip('cdhs','shcd'))
    for s in changed['state']['seats']:s['cards']=[c[0]+mapping[c[1]] for c in s['cards']]
    changed['state']['board']=[c[0]+mapping[c[1]] for c in changed['state']['board']]
    assert np.array_equal(x,encode(changed)[0])


def test_model_masks_and_can_overfit_controlled_batch():
    import torch
    torch.manual_seed(2);torch.set_num_threads(2)
    hand=generate_behavior(71234,'pressure');_,obs=reconstruct(hand)
    rows=[]
    for o in obs:
        x,seq,mask=encode(o);rows.append({'x':torch.tensor(x),'seq':torch.tensor(seq),'mask':torch.tensor(mask),'y':target(o,hand.events[o['action_index']].model_dump())})
    x,seq,lens,mask,y,_=collate(rows);model=BehaviorModel();opt=torch.optim.Adam(model.parameters(),lr=.02)
    before=float(torch.nn.functional.cross_entropy(model(x,seq,lens,mask),y).detach())
    for _ in range(100):
        loss=torch.nn.functional.cross_entropy(model(x,seq,lens,mask),y);opt.zero_grad();loss.backward();opt.step()
    probs=model(x,seq,lens,mask).softmax(-1)
    assert float(loss.detach())<before*.1
    assert torch.all(probs[~mask]==0) and torch.allclose(probs.sum(-1),torch.ones(len(y)))


def test_resume_matches_uninterrupted_training(tmp_path):
    import torch
    from training.behavior import fit
    root=tmp_path/'shards';root.mkdir()
    rows=[]
    for seed in range(5):
        hand=generate_behavior(74100+seed,'responsive');_,obs=reconstruct(hand)
        for o in obs:
            x,seq,mask=encode(o);rows.append({'x':torch.tensor(x),'seq':torch.tensor(seq),'mask':torch.tensor(mask),'y':target(o,hand.events[o['action_index']].model_dump()),'policy':'responsive','street':o['state']['street']})
    for split in ['train','validation']:torch.save(rows,root/f'{split}-000.pt')
    manifest={'dataset_id':'test','files':{'train-000.pt':'test-only','validation-000.pt':'test-only'}}
    full=tmp_path/'full';full.mkdir();part=tmp_path/'part';part.mkdir();resumed=tmp_path/'resumed';resumed.mkdir()
    config={'epochs':4,'early_stopping_patience':20}
    a,t1,c1=fit(root,manifest,11,True,8,config,full)
    fit(root,manifest,11,True,8,{**config,'epochs':2},part)
    b,t2,c2=fit(root,manifest,11,True,8,config,resumed,resume=part/'checkpoint.pt')
    assert t1==t2 and c1==c2
    assert all(torch.equal(v,b.state_dict()[k]) for k,v in a.state_dict().items())


def test_support_counts_only_families_with_retained_test_examples(tmp_path):
    import torch
    from training.behavior import retained_test_families
    torch.save([{'family':'a'},{'family':'a'},{'family':'b'}],tmp_path/'test-001.pt')
    torch.save([{'family':'training-only'}],tmp_path/'train-001.pt')
    manifest={'files':{'test-001.pt':'fixture','train-001.pt':'fixture'},'independent_families':{'test':999}}
    assert retained_test_families(tmp_path,manifest)==2
