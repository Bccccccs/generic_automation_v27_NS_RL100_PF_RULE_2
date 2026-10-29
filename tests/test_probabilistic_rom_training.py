import numpy as np
import pytest
from test_probabilistic_rom_model import small
from flow_control.rom.probabilistic_rom import torch_api
from flow_control.rom.train_probabilistic_rom import multistep_loss, fit
from flow_control.rom.probabilistic_data import load_sequences
from test_probabilistic_rom_data import dataset


def test_loss_hand_calculation():
    t=torch_api(); m=small()
    with t.no_grad():
        for p in m.members[0].parameters(): p.zero_()
    u=t.zeros((1,4,6)); y=t.ones((1,3,6)); target=t.ones((1,1,6))
    loss=multistep_loss(m,m.members[0],u,y,target)
    expected=.5*(np.log(.01)+100)
    assert float(loss.detach())==pytest.approx(expected,rel=1e-5)


def test_gradient_crosses_prediction_feedback():
    t=torch_api(); m=small()
    class Feedback(t.nn.Module):
        def __init__(self): super().__init__(); self.a=t.nn.Parameter(t.tensor(.5)); self.calls=[]
        def forward(self,x):
            mu=x[:,-6:]*self.a
            mu.retain_grad(); self.calls.append(mu)
            return t.cat([mu,t.zeros_like(mu)],dim=-1)
    member=Feedback(); u=t.zeros((1,5,6)); y=t.ones((1,3,6)); target=t.zeros((1,2,6))
    loss=multistep_loss(m,member,u,y,target,loss_steps=[1]); loss.backward()
    assert member.calls[0].grad is not None and member.calls[0].grad.abs().sum()>0
    # Teacher forcing would give .5 again, feedback must give .25.
    np.testing.assert_allclose(member.calls[1].detach().numpy(),.25)


def test_short_fit_decreases_and_reproducible(tmp_path):
    s,_=load_sequences(dataset(tmp_path/'a.csv',n=40),sample_stride=1)
    m=small(); logs,boot=fit(m,s,epochs=12,warmup_epochs=2,horizon=3,batch_size=64,learning_rate=.003)
    assert np.isfinite([r['loss'] for r in logs]).all()
    assert logs[-1]['loss']<logs[0]['loss']
    other=small(); logs2,boot2=fit(other,s,epochs=12,warmup_epochs=2,horizon=3,batch_size=64,learning_rate=.003)
    assert logs==logs2 and boot==boot2
