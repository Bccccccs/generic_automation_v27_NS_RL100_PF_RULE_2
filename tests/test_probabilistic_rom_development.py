import json
import numpy as np
import pytest
pytest.importorskip('torch',exc_type=ImportError)
from flow_control.rom.probabilistic_data import load_sequences,fit_scaler
from flow_control.rom.probabilistic_development import split_development, development_score, DevelopmentSelector
from test_probabilistic_rom_data import dataset
from test_probabilistic_rom_model import small
from flow_control.rom.train_probabilistic_rom import fit


def test_development_gap_unique_support_and_scaler(tmp_path):
    s,_=load_sequences(dataset(tmp_path/'a.csv',n=100),sample_stride=1)
    train,dev,audit=split_development(s,fraction=.25,max_lag=3,horizon=2)
    assert len(train[0])==75 and len(dev[0])==21
    assert audit[0]['excluded_rows']==4
    assert train[0].time[-1]<dev[0].time[0]
    scaler=fit_scaler(train); dev[0].outputs[:]=99999
    assert fit_scaler(train)==scaler
    with pytest.raises(ValueError): split_development(s,fraction=.8,max_lag=50,horizon=20)


def test_development_score_no_future_outputs_and_callback(tmp_path):
    s,_=load_sequences(dataset(tmp_path/'a.csv',n=100),sample_stride=1)
    m=small(prediction_mode='state_increment');m.scaler=fit_scaler(s)
    selector=DevelopmentSelector(s,interval=1,min_epoch=1)
    logs,boot=fit(m,s,epochs=2,warmup_epochs=0,horizon=1,batch_size=128,epoch_callback=selector,window_index_horizon=3)
    assert all('development_score' in row for row in logs)
    assert set(selector.best_epochs)=={0,1}
    assert len(boot['0']['1']['window_index'])==len(s[0])-3-3+1
    selector.restore(m)
    r=development_score(m,m.members[0],s)
    assert np.isfinite(r['score']) and set(r['modes'])=={'one_step','100ms','rollout'}


def test_increment_multistep_feedback_gradient():
    import torch
    from flow_control.rom.train_probabilistic_rom import multistep_loss
    m=small(prediction_mode='state_increment')
    u=torch.zeros(1,5,6); y=torch.ones(1,3,6); targets=torch.zeros(1,2,6)
    with torch.no_grad():
        for p in m.members[0].parameters(): p.zero_()
    loss=multistep_loss(m,m.members[0],u,y,targets,loss_steps=[1]); loss.backward()
    assert m.members[0][-1].bias.grad[:6].abs().sum()>0


def test_development_free_rollout_uses_predicted_history(tmp_path):
    import torch
    sequences,_=load_sequences(dataset(tmp_path/'a.csv',n=12),sample_stride=1)
    sequences[0].outputs[:]=np.arange(12)[:,None]+10
    m=small()
    class TraceMember(torch.nn.Module):
        def __init__(self):
            super().__init__(); self.seen=[]
        def forward(self,x):
            self.seen.append(x.detach().clone())
            return torch.cat([.5*x[:,-6:],torch.zeros_like(x[:,-6:])],dim=-1)
    member=TraceMember()
    development_score(m,member,sequences)
    steps=len(sequences[0])-m.max_lag
    rollout_contexts=member.seen[-steps:]
    initial=sequences[0].outputs[m.max_lag-1]
    for step,x in enumerate(rollout_contexts):
        np.testing.assert_allclose(x[0,-6:].numpy(),initial*.5**step)


def test_study_freezes_selection_before_external_evaluation(tmp_path):
    import yaml
    from flow_control.rom.probabilistic_development import run_study
    dataset(tmp_path/'train.csv',n=120)
    dataset(tmp_path/'val.csv',case='v',n=120,shift=20)
    cfg=dict(project_root='.',training_dataset='train.csv',validation_dataset='val.csv',sample_stride=1,
             network=dict(input_lags=3,output_lags=2,hidden_sizes=[8],member_seeds=[4,5]),
             optimizer=dict(batch_size=128,learning_rate=.001),evaluation=dict(particles=0,mixture_samples=50),
             study=dict(epochs=2,interval=1,member_seeds=[1,2],development_fraction=.25,window_horizon=3,
                        candidates=[dict(name='absolute',prediction_mode='absolute',hidden_sizes=[8],horizon=1,warmup_epochs=0),
                                    dict(name='increment',prediction_mode='state_increment',hidden_sizes=[8],horizon=1,warmup_epochs=0)]))
    p=tmp_path/'cfg.yaml';p.write_text(yaml.safe_dump(cfg))
    result=run_study(p,tmp_path/'study',require_validation=True)
    decision=json.loads((tmp_path/'study'/'selection.json').read_text())
    assert decision['external_data_used_for_selection'] is False
    assert result['status']=='COMPARISON_COMPLETE'
    assert (tmp_path/'study'/'selection.json').stat().st_mtime_ns < (tmp_path/'study'/'final_model'/'manifest.json').stat().st_mtime_ns
    dataset(tmp_path/'val.csv',case='v',n=120,shift=500)
    run_study(p,tmp_path/'other',require_validation=True)
    other=json.loads((tmp_path/'other'/'selection.json').read_text())
    def without_timings(ranking):
        return [{**row,'runs':[{k:v for k,v in run.items() if k!='elapsed_s'} for run in row['runs']]} for row in ranking]
    assert without_timings(decision['ranking'])==without_timings(other['ranking'])
    assert decision['selected_candidate']==other['selected_candidate']
    import torch
    a=torch.load(tmp_path/'study'/'final_model'/'weights.pt',weights_only=True)
    b=torch.load(tmp_path/'other'/'final_model'/'weights.pt',weights_only=True)
    for i in a:
        for name in a[i]: assert torch.equal(a[i][name],b[i][name])
    with pytest.raises(FileExistsError): run_study(p,tmp_path/'study')
