import hashlib,json
from pathlib import Path
import pytest
from packages.pokermind_core.contracts import canonical
from services.api.releases import capture,activate,inventory,promotion_check
from services.api.artifacts import report
from services.api.store import Store
from services.api.backup import restore


def artifact(root,name,value):
    path=root/'models'/name;path.parent.mkdir(exist_ok=True);path.write_bytes(value)
    return str(path.relative_to(root)),hashlib.sha256(value).hexdigest()


def pointer(root,version):
    path,sha=artifact(root,f'weights-{version}.pt',version.encode())
    obj={'features_version':'river-counts-v1','status':'research-only','promotion_gates':{'mlp':{'support':False,'quality':False}},'runs':[{'model_path':path,'sha256':sha}],'version':version}
    target=root/'models'/f'report-{version}.json';target.write_text(canonical(obj));(root/'research.json').write_text(canonical({'report':str(target.relative_to(root))}));return target


def test_coherent_activation_rollback_integrity_and_backup(tmp_path):
    store=Store(tmp_path/'app');pointer(store.root,'old');old=capture(store.root)
    pointer(store.root,'new');new=capture(store.root);activate(store.root,new['release_id'])
    assert report(store.root,'research')['version']=='new'
    activate(store.root,old['release_id']);assert report(store.root,'research')['version']=='old'
    assert promotion_check(store.root,'research')['default_promotion_allowed'] is False
    store.backup(tmp_path/'backup');restore(tmp_path/'backup',tmp_path/'restored')
    assert report(tmp_path/'restored','research')['version']=='old'
    (store.root/'models/weights-new.pt').write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='checksum'):activate(store.root,new['release_id'])
    assert inventory(store.root)['active']==old['release_id']
    assert report(store.root,'research')['version']=='old'


def test_release_rejects_schema_and_path_tampering(tmp_path):
    store=Store(tmp_path);p=pointer(store.root,'v1');r=json.loads(p.read_text());r['features_version']='unknown';p.write_text(canonical(r))
    with pytest.raises(ValueError,match='schema'):capture(store.root)
    (store.root/'research.json').write_text(canonical({'report':'../../escape'}))
    with pytest.raises(ValueError,match='escapes'):capture(store.root)


def test_published_research_is_checksummed_and_survives_backup(tmp_path):
    from fastapi.testclient import TestClient
    from services.api.app import create_app
    store=Store(tmp_path/'app');folder=store.root/'evidence';folder.mkdir()
    data=canonical({'status':'measured','cost_usd':0,'cloud_enabled':False}).encode()
    (folder/'summary.json').write_bytes(data)
    (store.root/'local-research.json').write_text(canonical({'path':'evidence/summary.json','sha256':hashlib.sha256(data).hexdigest()}))
    store.backup(tmp_path/'backup');restore(tmp_path/'backup',tmp_path/'restored')
    with TestClient(create_app(tmp_path/'restored')) as client:
        assert client.get('/api/research/local').json()['status']=='measured'
        (tmp_path/'restored/evidence/summary.json').write_bytes(b'altered')
        assert client.get('/api/research/local').status_code==422
