import json
import pytest
pytest.importorskip('pyspiel')
pytest.importorskip('torch')
from fastapi.testclient import TestClient
from pathlib import Path
from packages.strategy.games import game_tree
from packages.strategy.tabular import TabularCFR
from training.strategy import immutable_json
from services.api.app import create_app
from services.api.store import Store
from services.api.small_games import SmallGames


def prepare(root,game):
    tree=game_tree(game);p=TabularCFR(tree).train(10)
    artifact,sha=immutable_json(root/'policy.json',{'game':game,'keys':tree.keys,'probabilities':p.tolist()})
    selection={'policy':artifact.name,'sha256':sha,'metrics':tree.evaluate(p)}
    report,unused=immutable_json(root/'report.json',{'serving':selection,'runs':[{**selection,'variant':'main','seed':11}]})
    (root/f'strategy-{game}.json').write_text(json.dumps({'report':report.name}))

@pytest.mark.parametrize('game',['kuhn','leduc'])
def test_server_owned_simulator_roundtrip_and_restart(tmp_path,game):
    prepare(tmp_path,game)
    app=create_app(tmp_path)
    with TestClient(app,headers={'X-PokerMind-Local':'1'}) as client:
        assert client.post('/api/simulator',json={'game':game,'state':{}}).status_code==422
        m=client.post('/api/simulator',json={'game':game}).json()
        assert m==SmallGames(Store(tmp_path)).get(m['game_id'])
        assert all(k not in m for k in ['history','seed','cards','opponent_card','probabilities'])
        for _ in range(20):
            if m['completed']:break
            path=f"/api/simulator/{m['game_id']}/action"
            assert client.post(path,json={'action':9,'revision':m['revision']}).status_code==422
            assert client.post(path,json={'action':m['legal_actions'][0]['id'],'revision':m['revision']+1}).status_code==422
            body={'action':m['legal_actions'][0]['id'],'revision':m['revision']}
            response=client.post(path,json=body);assert response.status_code==200,response.text
            m=response.json();assert client.post(path,json=body).status_code==422
        assert m['completed'] and m['return_chips'] is not None
        assert client.get('/api/simulator/'+m['game_id']).json()==m
        assert client.post('/api/simulator',json={'game':'holdem-live'}).status_code==422


def test_corrupt_or_escaping_artifacts_rejected(tmp_path):
    prepare(tmp_path,'kuhn');games=SmallGames(Store(tmp_path))
    m=games.start('kuhn','baseline')
    policy=next(tmp_path.glob('policy-*.json'));policy.write_text('{}')
    with pytest.raises(ValueError,match='checksum'):games.start('kuhn','baseline')
    from services.api.artifacts import safe_path
    with pytest.raises(ValueError,match='escapes'):safe_path(tmp_path,'../outside')


def test_holdem_server_state_hidden_cards_retries_and_restart(tmp_path):
    from services.api.holdem import HoldemGames
    report,sha=immutable_json(tmp_path/'behavior-report.json',{'frequency':{'table':{},'global':[1,2,2,1,1,1,1]}})
    (tmp_path/'behavior.json').write_text(json.dumps({'report':report.name}))
    with TestClient(create_app(tmp_path),headers={'X-PokerMind-Local':'1'}) as client:
        assert client.post('/api/holdem',json={'state':{},'opponent':'baseline'}).status_code==422
        for _ in range(5):
            response=client.post('/api/holdem',json={'opponent':'baseline'});assert response.status_code==200,response.text
            m=response.json()
            while not m['completed']:
                assert not next(s for s in m['state']['seats'] if s['seat']==2)['cards']
                assert m==HoldemGames(Store(tmp_path)).get(m['game_id'])
                legal=m['state']['legal_actions'];kind='check' if 'check' in legal else 'call'
                path=f"/api/holdem/{m['game_id']}/action";body={'kind':kind,'revision':m['revision']}
                assert client.post(path,json={**body,'to':1}).status_code==422
                r=client.post(path,json=body);assert r.status_code==200,r.text
                m=r.json();assert client.post(path,json=body).status_code==422
            assert m==client.get('/api/holdem/'+m['game_id']).json()
            assert m['state']['settled'] and len(m['state']['seats'])==2


def test_backup_restores_in_progress_pinned_policy(tmp_path):
    from services.api.backup import restore
    root=tmp_path/'app';root.mkdir();prepare(root,'kuhn')
    # The production layout stores artifacts in one of the backed-up namespaces.
    target=root/'strategy';target.mkdir()
    for p in list(root.glob('policy-*.json'))+list(root.glob('report-*.json')):p.rename(target/p.name)
    pointer=json.loads((root/'strategy-kuhn.json').read_text());pointer['report']='strategy/'+pointer['report'];(root/'strategy-kuhn.json').write_text(json.dumps(pointer))
    report_path=target/Path(pointer['report']).name;r=json.loads(report_path.read_text())
    for entry in [r['serving']]+r['runs']:entry['policy']='strategy/'+entry['policy']
    report_path.write_text(json.dumps(r))
    store=Store(root);games=SmallGames(store);m=games.start('kuhn','baseline')
    store.backup(tmp_path/'backup');restore(tmp_path/'backup',tmp_path/'restored')
    other=SmallGames(Store(tmp_path/'restored'));assert other.get(m['game_id'])==m
    if not m['completed']:
        action=m['legal_actions'][0]['id']
        assert games.act(m['game_id'],action,m['revision'])==other.act(m['game_id'],action,m['revision'])
