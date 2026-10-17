import hashlib
import json

import pytest
torch=pytest.importorskip('torch')
pytest.importorskip('numpy')
from fastapi.testclient import TestClient
from packages.models.river import RiverNet
from packages.pokermind_core.contracts import canonical
from packages.strategy.holdem_nfsp import DIM,FEATURE_VERSION
from packages.strategy.nfsp import PolicyNet,VERSION
from services.api.app import create_app
from services.api.store import Store
from services.api.backup import restore
from services.api.nfsp_policy import action_probabilities

HEADERS={'X-PokerMind-Local':'1'}


def install_fixture(root):
    folder=root/'models/holdem-nfsp/fixture';folder.mkdir(parents=True)
    def ref(path):return {'path':str(path.relative_to(root)),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    members=[]
    for seed in (1,2,3):
        path=folder/f'river-{seed}.pt';torch.save(RiverNet(True,8).state_dict(),path)
        members.append({**ref(path),'seed':seed,'width':8})
    path=folder/'river.json';path.write_text(canonical({'version':'river-coach-bundle-v1','features_version':'river-structure-v1',
        'members':members,'training_dataset_id':'fixture','member_test_mae_mean':.03}))
    river=ref(path)
    net=PolicyNet(DIM,7,16)
    with torch.no_grad():
        for p in net.parameters():p.zero_()
        net.net[-1].bias.fill_(-50);net.net[-1].bias[1:3]=50
    path=folder/'average-policy.pt';torch.save({'version':VERSION,'features_version':FEATURE_VERSION,'domain':'holdem',
        'inputs':DIM,'actions':7,'width':16,'stack_bb':50,'river_pointer':river,'average':[net.state_dict(),net.state_dict()]},path)
    selected=ref(path)
    report={'version':VERSION,'status':'research-only','selected':selected,'quality_gates':{'kuhn':False},
            'promotion':{'eligible':False,'reason':'Test fixture, no quality evidence'},'runs':[],
            'selection':'fixture','cloud_enabled':False,'protocol':{'river_pointer':river}}
    path=folder/'report.json';path.write_text(canonical(report));(root/'holdem-nfsp.json').write_text(canonical(ref(path)))
    (folder/'checkpoint.pt').write_bytes(b'training-only-not-for-serving')
    return selected


def test_selfplay_game_policy_revision_boundaries_and_complete_hand(tmp_path,monkeypatch):
    selected=install_fixture(tmp_path);monkeypatch.setattr('services.api.holdem.secrets.randbelow',lambda _:18)
    with TestClient(create_app(tmp_path)) as client:
        assert client.get('/api/research/holdem-nfsp').json()['runtime_available']
        assert client.post('/api/holdem',json={'opponent':'selfplay','cards':['As','Ah']},headers=HEADERS).status_code==422
        match=client.post('/api/holdem',json={'opponent':'selfplay'},headers=HEADERS).json()
        game='/api/holdem/'+match['game_id'];streets=set()
        for _ in range(30):
            if match['completed']:break
            streets.add(match['state']['street'])
            assert match['state']['seats'][1]['cards']==[]
            revision={'revision':match['revision']}
            response=client.post(game+'/policy',json=revision,headers=HEADERS)
            assert response.status_code==200,response.text
            policy=response.json()['policy']
            assert sum(p['probability'] for p in policy)==pytest.approx(1)
            assert {p['label'] for p in policy}=={c['label'] for c in match['choices']}
            assert client.post(game+'/policy',json={**revision,'board':[]},headers=HEADERS).status_code==422
            if match['revision']==0:
                assert client.post(game+'/action',json={'kind':'raise','to':41,'revision':0},headers=HEADERS).status_code==422
                assert client.post(game+'/policy',json={'revision':9},headers=HEADERS).status_code==422
            result=client.post(game+'/bot-action',json=revision,headers=HEADERS)
            assert result.status_code==200,result.text
            match=result.json()
            assert client.post(game+'/bot-action',json=revision,headers=HEADERS).status_code==422
        assert match['completed'] and streets=={'preflop','flop','turn','river'}
        assert sum(s['stack'] for s in match['state']['seats'])==2000
        assert match['artifact_hash']==selected['sha256']
        assert client.get(game).json()==match
        assert client.post('/api/holdem/unknown/policy',json={'revision':0},headers=HEADERS).status_code==404


def test_pinned_policy_survives_pointer_change_and_backup_omits_training_buffers(tmp_path):
    root=tmp_path/'app';install_fixture(root)
    with TestClient(create_app(root)) as client:
        game=client.post('/api/holdem',json={'opponent':'selfplay'},headers=HEADERS).json()
        path='/api/holdem/'+game['game_id']+'/policy';body={'revision':0}
        original=client.post(path,json=body,headers=HEADERS).json()
        (root/'holdem-nfsp.json').write_text('{}')
        assert client.post(path,json=body,headers=HEADERS).json()==original
    Store(root).backup(tmp_path/'backup')
    assert not (tmp_path/'backup/models/holdem-nfsp/fixture/checkpoint.pt').exists()
    restore(tmp_path/'backup',tmp_path/'restored')
    with TestClient(create_app(tmp_path/'restored')) as client:
        assert client.post(path,json=body,headers=HEADERS).json()==original


def test_corrupt_cached_policy_or_frozen_feature_abstains(tmp_path):
    selected=install_fixture(tmp_path)
    with TestClient(create_app(tmp_path)) as client:
        match=client.post('/api/holdem',json={'opponent':'selfplay'},headers=HEADERS).json()
        path='/api/holdem/'+match['game_id']+'/policy'
        assert client.post(path,json={'revision':0},headers=HEADERS).status_code==200
        weights=tmp_path/selected['path'];weights.write_bytes(b'corrupt')
        assert client.post(path,json={'revision':0},headers=HEADERS).status_code==422
