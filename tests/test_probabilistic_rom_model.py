import json
import numpy as np
import pytest
pytest.importorskip("torch", exc_type=ImportError)
from flow_control.rom.probabilistic_rom import ProbabilisticEnsembleROM, torch_api
from flow_control.rom.probabilistic_data import INPUT_NAMES,OUTPUT_NAMES


def small(**kw):
    return ProbabilisticEnsembleROM(input_lags=3,output_lags=2,hidden_sizes=[8],member_seeds=[1,2],**kw)


def context(): return dict(dt_s=.001,input_names=INPUT_NAMES,output_names=OUTPUT_NAMES)


def test_architecture_independence():
    m=ProbabilisticEnsembleROM(); t=torch_api()
    assert len(m.members)==5
    layers=[x for x in m.members[0] if isinstance(x,t.nn.Linear)]
    assert [(x.in_features,x.out_features) for x in layers]==[(1260,300),(300,300),(300,300),(300,12)]
    assert not t.equal(layers[0].weight,m.members[1][0].weight)


def test_moments_and_units():
    t=torch_api(); scaler=dict(input_mean=[0]*6,input_scale=[1]*6,output_mean=[10]*6,output_scale=[2]*6)
    m=small(scaler=scaler)
    with t.no_grad():
        for i,member in enumerate(m.members):
            for p in member.parameters(): p.zero_()
            member[-1].bias[:6].fill_(i*2)
    d=m.predict_distribution(np.zeros((3,6)),np.zeros((2,6)),**context())
    np.testing.assert_allclose(d['mean'],12)
    np.testing.assert_allclose(d['epistemic_var'],4)
    np.testing.assert_allclose(d['aleatoric_var'],.04,rtol=1e-5)
    np.testing.assert_allclose(d['total_var'],4.04,rtol=1e-5)


@pytest.mark.parametrize('bad',['dt','columns','nan','shape'])
def test_inference_contract(bad):
    m=small(); c=context(); u=np.zeros((3,6)); y=np.zeros((2,6))
    if bad=='dt': c['dt_s']=.01
    if bad=='columns': c['input_names']=INPUT_NAMES[::-1]
    if bad=='nan': u[0,0]=np.nan
    if bad=='shape': y=y[:1]
    with pytest.raises(ValueError): m.predict_distribution(u,y,**c)


def test_snapshot(tmp_path):
    m=small(); p=tmp_path/'model'; m.save(p)
    a=m.predict_distribution(np.ones((3,6)),np.ones((2,6)),**context())
    b=m.load(p).predict_distribution(np.ones((3,6)),np.ones((2,6)),**context())
    np.testing.assert_array_equal(a['mean'],b['mean'])
    with pytest.raises(FileExistsError): m.save(p)
    with (p/'weights.pt').open('ab') as f: f.write(b'bad')
    with pytest.raises(ValueError,match='hash'): m.load(p)


@pytest.mark.parametrize('field,value',[('version','bad'),('input_names',[]),('member_seeds',[]),('dt_s',-.1),('scaler',{})])
def test_bad_metadata(tmp_path,field,value):
    m=small(); p=tmp_path/'m'; m.save(p)
    meta=json.loads((p/'model_metadata.json').read_text()); meta[field]=value
    (p/'model_metadata.json').write_text(json.dumps(meta))
    with pytest.raises(ValueError): m.load(p)


@pytest.mark.parametrize('damage',['member','shape','nonfinite'])
def test_bad_tensor_snapshot_even_with_updated_hash(tmp_path,damage):
    from flow_control.rom.probabilistic_data import sha256
    t=torch_api(); p=tmp_path/'m'; small().save(p)
    states=t.load(p/'weights.pt',weights_only=True)
    if damage=='member': states.pop('0')
    if damage=='shape': states['0']['0.weight']=t.zeros((2,2))
    if damage=='nonfinite': states['0']['0.weight'][0,0]=float('nan')
    t.save(states,p/'weights.pt')
    meta=json.loads((p/'model_metadata.json').read_text()); meta['weights_sha256']=sha256(p/'weights.pt')
    (p/'model_metadata.json').write_text(json.dumps(meta))
    manifest=json.loads((p/'manifest.json').read_text()); manifest['model_metadata_sha256']=sha256(p/'model_metadata.json')
    (p/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError): ProbabilisticEnsembleROM.load(p)


def test_large_finite_means_are_not_clipped():
    m=small();t=torch_api()
    with t.no_grad():
        for member in m.members:
            for p in member.parameters(): p.zero_()
            member[-1].bias[:6].fill_(1e5)
    d=m.predict_distribution(np.zeros((3,6)),np.zeros((2,6)),**context())
    np.testing.assert_allclose(d['mean'],1e5)


def test_state_increment_mean_variance_and_old_snapshot(tmp_path):
    t=torch_api(); m=small(prediction_mode='state_increment')
    with t.no_grad():
        for member in m.members:
            for p in member.parameters(): p.zero_()
    y=np.arange(12,dtype=float).reshape(2,6)
    d=m.predict_distribution(np.zeros((3,6)),y,**context())
    np.testing.assert_array_equal(d['mean'],y[-1])
    np.testing.assert_allclose(d['aleatoric_var'],.01,rtol=1e-5)
    m.save(tmp_path/'increment'); restored=m.load(tmp_path/'increment')
    assert restored.prediction_mode=='state_increment'
    np.testing.assert_array_equal(restored.predict_distribution(np.zeros((3,6)),y,**context())['mean'],y[-1])
    old=small(); old.save(tmp_path/'absolute')
    assert old.load(tmp_path/'absolute').prediction_mode=='absolute'


def test_increment_nonunit_physical_units():
    t=torch_api()
    scale=np.arange(2.,8.)
    m=small(prediction_mode='state_increment',scaler=dict(input_mean=[0]*6,input_scale=[1]*6,
            output_mean=[100]*6,output_scale=scale.tolist()))
    with t.no_grad():
        for member in m.members:
            for p in member.parameters(): p.zero_()
            member[-1].bias[:6].fill_(2)
    y=np.arange(12,dtype=float).reshape(2,6)
    d=m.predict_distribution(np.zeros((3,6)),y,**context())
    np.testing.assert_allclose(d['mean'],y[-1]+2*scale,atol=1e-5)
    np.testing.assert_allclose(d['aleatoric_var'],.01*scale**2,rtol=1e-5)


def test_legacy_snapshot_without_prediction_mode(tmp_path):
    from flow_control.rom.probabilistic_data import sha256
    p=tmp_path/'legacy'; small().save(p)
    meta=json.loads((p/'model_metadata.json').read_text()); meta.pop('prediction_mode')
    (p/'model_metadata.json').write_text(json.dumps(meta))
    manifest=json.loads((p/'manifest.json').read_text())
    manifest['model_metadata_sha256']=sha256(p/'model_metadata.json')
    (p/'manifest.json').write_text(json.dumps(manifest))
    assert ProbabilisticEnsembleROM.load(p).prediction_mode=='absolute'
