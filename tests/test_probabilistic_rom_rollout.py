import numpy as np
import pytest
from test_probabilistic_rom_model import small,context


def test_rollout_prefix_and_no_future_outputs():
    m=small(); u=np.zeros((12,6)); y=np.ones((3,6))
    a=m.rollout(u,y,**context())
    u[7:]=99
    b=m.rollout(u,y,**context())
    np.testing.assert_array_equal(a['mean'][:5],b['mean'][:5])
    assert a['conditional_total_var'].shape==(9,6)
    with pytest.raises(ValueError): m.rollout(u,np.zeros((12,6)),**context())


def test_particles_independent_reproducible():
    m=small(); u=np.zeros((12,6)); y=np.zeros((3,6))
    a=m.sample_rollouts(u,y,particles=10,seed=3,**context())
    b=m.sample_rollouts(u,y,particles=10,seed=3,**context())
    np.testing.assert_array_equal(a,b)
    assert a.shape==(10,9,6) and not np.array_equal(a[0],a[1])
    assert not np.array_equal(a,m.sample_rollouts(u,y,particles=10,seed=4,**context()))


def test_failure_location():
    import torch
    m=small()
    with torch.no_grad(): m.members[0][-1].bias[:6].fill_(1e13)
    with pytest.raises(ValueError,match='step=3'): m.rollout(np.zeros((9,6)),np.zeros((3,6)),**context())


def test_ts1_independent_feedback_matches_manual_sampling():
    import torch
    m=small()
    class Feedback(torch.nn.Module):
        def forward(self,x): return torch.cat([.5*x[:,-6:],torch.zeros_like(x[:,-6:])],1)
    m.members=[Feedback(),Feedback()]
    u=np.zeros((5,6)); y=np.ones((3,6)); particles=8; seed=7
    actual=m.sample_rollouts(u,y,particles=particles,seed=seed,**context())
    rng=np.random.default_rng(seed); history=np.ones((particles,6)); expected=[]
    for k in range(2):
        rng.integers(2,size=particles)
        history=.5*history+.1*rng.normal(size=(particles,6)); expected.append(history.copy())
    np.testing.assert_allclose(actual,np.stack(expected,axis=1),rtol=1e-6,atol=1e-7)
