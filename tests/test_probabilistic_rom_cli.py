import json
import os
import subprocess
import sys
import numpy as np
import pytest
import yaml
from test_probabilistic_rom_data import dataset
from flow_control.rom.train_probabilistic_rom import run
from flow_control.rom.evaluate_probabilistic_rom import run as evaluate, region_metrics


@pytest.fixture(autouse=True)
def optional_torch_tests(request):
    if request.node.name not in {'test_no_torch_workflow_help','test_metrics_degenerate',
                                 'test_gaussian_mixture_nll_physical_units','test_probability_case_summary_is_equal_weight','test_development_study_flag_in_help'}:
        pytest.importorskip('torch', exc_type=ImportError)


def config(tmp_path,empty=False):
    dataset(tmp_path/'train.csv',n=225)
    dataset(tmp_path/'val.csv',case='b',n=0 if empty else 225,shift=10)
    cfg=dict(project_root='.',training_dataset='train.csv',validation_dataset='val.csv',sample_stride=1,
             network=dict(hidden_sizes=[8],member_seeds=[1,2]),
             optimizer=dict(epochs=2,warmup_epochs=1,horizon=3,batch_size=32),
             evaluation=dict(particles=4,seed=42,mixture_samples=100))
    p=tmp_path/'config.yaml'; p.write_text(yaml.safe_dump(cfg)); return p


def test_end_to_end_and_outputs(tmp_path):
    p=config(tmp_path); out=tmp_path/'train'; r=run(p,out)
    assert r['status']=='COMPARISON_COMPLETE' and r['rl_ready'] is False
    for name in ['manifest.json','model_metadata.json','weights.pt','training_log.csv','metrics.json','predictions.csv','result.md','AGENT_HANDOFF.md','bootstrap.json']:
        assert (out/name).is_file()
    report=evaluate(out,p,tmp_path/'eval')
    assert report['validation']['cases']==r['validation']['cases']
    assert report['baselines']['B54']['status']=='BASELINE_UNAVAILABLE'
    with pytest.raises(FileExistsError): run(p,out)
    with pytest.raises(FileExistsError): evaluate(out,p,tmp_path/'eval')


def test_empty_missing_validation(tmp_path):
    p=config(tmp_path,empty=True)
    assert run(p,tmp_path/'a')['status']=='TRAINING_ONLY'
    with pytest.raises(ValueError): run(p,tmp_path/'b',require_validation=True)
    (tmp_path/'val.csv').unlink()
    with pytest.raises(FileNotFoundError): run(p,tmp_path/'c')
    assert (tmp_path/'c'/'failure.json').is_file()
    manifest=json.loads((tmp_path/'c'/'manifest.json').read_text())
    assert manifest['input_files']['training_dataset']['sha256']
    assert manifest['input_files']['validation_dataset']['sha256'] is None


def test_metrics_degenerate():
    r=region_metrics(np.ones((4,6)),np.zeros((4,6)),np.arange(4)*.001)
    assert r[0]['rmse_N']==1 and r[0]['nrmse'] is None and r[0]['correlation'] is None


def test_no_torch_workflow_help(tmp_path):
    # Fresh interpreter blocks every torch import, not just sys.modules inspection.
    (tmp_path/'sitecustomize.py').write_text('import sys\nclass Block:\n def find_spec(self,fullname,*args):\n  if fullname=="torch" or fullname.startswith("torch."): raise ImportError("blocked torch")\nsys.meta_path.insert(0,Block())\n')
    env={**os.environ,'PYTHONPATH':str(tmp_path)}
    for args in [[],['train-probabilistic-rom','--help'],['evaluate-probabilistic-rom','--help'],['train-residual-rom','--help'],['actions','--help']]:
        cp=subprocess.run([sys.executable,'scripts/workflow.py',*args],capture_output=True,text=True,env=env)
        assert cp.returncode==0,cp.stderr
    p=config(tmp_path)
    cp=subprocess.run([sys.executable,'scripts/workflow.py','train-probabilistic-rom','--config',str(p),'--output-dir',str(tmp_path/'blocked')],capture_output=True,text=True,env=env)
    assert cp.returncode!=0 and 'optional dependency' in cp.stderr


def test_external_validation_does_not_change_weights(tmp_path):
    import torch
    p=config(tmp_path); run(p,tmp_path/'a')
    dataset(tmp_path/'val.csv',case='b',n=225,shift=500)
    run(p,tmp_path/'b')
    a=torch.load(tmp_path/'a'/'weights.pt',weights_only=True)
    b=torch.load(tmp_path/'b'/'weights.pt',weights_only=True)
    for i in a:
        for key in a[i]: assert torch.equal(a[i][key],b[i][key])


def test_baseline_comparison_uses_matching_intervals(tmp_path):
    from flow_control.rom.arx_model import ARXModel
    from flow_control.rom.b54_real_rom import ScaledRealARXModel
    from flow_control.rom.residual_rom import ResidualROM
    from flow_control.rom.probabilistic_data import INPUT_NAMES,OUTPUT_NAMES,sha256
    p=config(tmp_path); cfg=yaml.safe_load(p.read_text())
    cfg['network'].update(input_lags=3,output_lags=2)
    arx=ARXModel(input_lags=3,output_lags=2,include_current_input=False)
    arx.input_names_=list(INPUT_NAMES); arx.output_names_=list(OUTPUT_NAMES)
    arx._feature_at(3,np.zeros((4,6)),np.zeros((4,6)))
    arx.coefficients_=np.zeros((31,6)); linear=ScaledRealARXModel(arx,np.zeros(6),np.ones(6),np.zeros(6),np.ones(6))
    snap=linear.to_dict(); snap.update(effective_dt_s=.001,sample_stride=1,training_data={'dataset_sha256':sha256(tmp_path/'train.csv')})
    (tmp_path/'linear.json').write_text(json.dumps(snap))
    (tmp_path/'residual.json').write_text(json.dumps(ResidualROM(linear,dt_s=.001).to_dict()))
    cfg.update(linear_model='linear.json',residual_model='residual.json'); p.write_text(yaml.safe_dump(cfg))
    r=run(p,tmp_path/'with_baselines')
    modes=r['validation']['cases'][0]['modes']
    for mode in ('one_step','rollout','20ms','100ms'):
        np.testing.assert_allclose([x['rmse_N'] for x in modes['B54_'+mode]['regions']],
                                   [x['rmse_N'] for x in modes['B55_'+mode]['regions']])
        assert modes['B54_'+mode]['regions'][0]['samples']==modes['B56_'+mode]['regions'][0]['samples']
    assert r['baselines']['B54']['training_dataset_matches']


def test_short_validation_not_comparison_complete(tmp_path):
    p=config(tmp_path); dataset(tmp_path/'val.csv',case='b',n=30,shift=10)
    r=run(p,tmp_path/'short')
    assert r['status']=='INSUFFICIENT_DATA'
    assert r['validation']['short_segments']


def test_failure_case_retained_and_not_clipped(tmp_path):
    from flow_control.rom.probabilistic_rom import ProbabilisticEnsembleROM
    from flow_control.rom.train_probabilistic_rom import read_config,datasets
    from flow_control.rom.evaluate_probabilistic_rom import evaluate_into
    import torch
    p=config(tmp_path); run(p,tmp_path/'train')
    m=ProbabilisticEnsembleROM.load(tmp_path/'train')
    with torch.no_grad(): m.members[0][-1].bias[:6].fill_(1e13)
    cfg,resolve=read_config(p); train,ta,val,va=datasets(cfg,resolve)
    out=tmp_path/'failure_eval';out.mkdir()
    report=evaluate_into(m,cfg,resolve,out,train,ta,val,va)
    assert report['status']=='ROLLOUT_FAILED' and len(report['failures'])==2
    assert report['validation']['cases'][0]['status']=='ROLLOUT_FAILED'
    assert report['validation']['cases'][0]['segments'][0]['error']


def test_gaussian_mixture_nll_physical_units():
    from flow_control.rom.evaluate_probabilistic_rom import mixture_diagnostics
    # Two identical N(0, 4) members: scalar NLL at zero = log(sqrt(8*pi)).
    d=dict(member_means=np.zeros((2,6)),member_vars=np.full((2,6),4.))
    r=mixture_diagnostics(np.zeros((2,6)),[d,d],samples=200,seed=1)
    assert r['joint_mixture_nll']==pytest.approx(6*.5*np.log(8*np.pi))
    assert r['regions'][0]['mixture_nll_N']==pytest.approx(.5*np.log(8*np.pi))


@pytest.mark.parametrize('stride,dt',[(2,.002),(2,.001)])
def test_evaluation_sampling_contract_rejects_same_csv(tmp_path,stride,dt):
    p=config(tmp_path);run(p,tmp_path/'train')
    cfg=yaml.safe_load(p.read_text());cfg.update(sample_stride=stride,expected_dt_s=dt);p.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError,match='sampling contract'): evaluate(tmp_path/'train',p,tmp_path/'bad')


@pytest.mark.parametrize('alias',[False,True])
def test_known_validation_cannot_be_relabelled_blind(tmp_path,alias):
    p=config(tmp_path); run(p,tmp_path/'train')
    cfg=yaml.safe_load(p.read_text()); path='val.csv'
    if alias:
        dataset(tmp_path/'alias.csv',case='renamed',n=225,shift=10);path='alias.csv'
    cfg['evaluation_datasets']=[dict(name='blind',path=path,role='blind_test')];p.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError,match='overlap'): evaluate(tmp_path/'train',p,tmp_path/'bad')


def test_probability_case_summary_is_equal_weight():
    from flow_control.rom.evaluate_probabilistic_rom import probability_case_summary
    cases=[]
    for nll,n in [(2.,1),(6.,100)]:
        cases.append(dict(conditional_probability=dict(joint_mixture_nll=nll,
            regions=[dict(region='x',samples=n,mixture_nll_N=nll,
                intervals={str(level):dict(coverage=.5,mean_width_N=3.) for level in (.5,.9,.95)})])))
    r=probability_case_summary(cases)
    assert r['joint_mixture_nll']==4 and r['regions'][0]['mixture_nll_N']==4


def test_development_study_flag_in_help():
    cp=subprocess.run([sys.executable,'scripts/workflow.py','train-probabilistic-rom','--help'],capture_output=True,text=True)
    assert cp.returncode==0 and '--development-study' in cp.stdout
