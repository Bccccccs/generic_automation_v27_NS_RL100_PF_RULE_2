"""Independent B56 probabilistic history MLP. Torch is loaded only on construction."""
from __future__ import annotations
import json
import math
import platform
from pathlib import Path
import numpy as np
from .probabilistic_data import INPUT_NAMES, OUTPUT_NAMES, sha256

VERSION='B56_probabilistic_history_MLP_v1'
INCREMENT_VERSION='B56_probabilistic_history_MLP_v2_increment'


def torch_api():
    try: import torch
    except ImportError as exc:
        raise RuntimeError('B56 requires optional dependency: pip install -r requirements-rom-neural.txt') from exc
    return torch


def write_json(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def matrix(a,name):
    a=np.asarray(a,dtype=np.float64)
    if a.ndim!=2 or a.shape[1]!=6 or not np.isfinite(a).all():
        raise ValueError(f'{name} must be finite (N,6)')
    return a


class ProbabilisticEnsembleROM:
    def __init__(self, *, input_lags=200, output_lags=10, hidden_sizes=(300,300,300),
                 member_seeds=(42,43,44,45,46), scaler=None, dt_s=.001,
                 input_names=INPUT_NAMES,output_names=OUTPUT_NAMES,prediction_mode='absolute'):
        if prediction_mode not in ('absolute','state_increment'):
            raise ValueError('unsupported prediction_mode')
        self.prediction_mode=prediction_mode
        if any(type(n) is not int or n<1 for n in (input_lags,output_lags,*hidden_sizes)) or not hidden_sizes:
            raise ValueError('invalid architecture')
        if not member_seeds or len(set(member_seeds))!=len(member_seeds) or any(type(s) is not int or s<0 for s in member_seeds):
            raise ValueError('invalid independent member seeds')
        if tuple(input_names)!=INPUT_NAMES or tuple(output_names)!=OUTPUT_NAMES: raise ValueError('channel order mismatch')
        if not math.isfinite(dt_s) or dt_s<=0: raise ValueError('invalid dt')
        if scaler is None:
            scaler={f'{k}_{s}':[v]*6 for k in ('input','output') for s,v in [('mean',0),('scale',1)]}
        for key in ('input_mean','input_scale','output_mean','output_scale'):
            a=np.asarray(scaler.get(key,[]),dtype=float)
            if a.shape!=(6,) or not np.isfinite(a).all() or ('scale' in key and np.any(a<=0)):
                raise ValueError('invalid scaler '+key)
        self.input_lags=input_lags; self.output_lags=output_lags; self.max_lag=max(input_lags,output_lags)
        self.hidden_sizes=list(hidden_sizes); self.member_seeds=list(member_seeds)
        self.scaler=json.loads(json.dumps(scaler)); self.dt_s=float(dt_s)
        self.input_names=tuple(input_names); self.output_names=tuple(output_names)
        self.provenance={}; self.members=[]
        t=torch_api()
        # fork_rng prevents construction from changing the caller's global random stream.
        for seed in self.member_seeds:
            with t.random.fork_rng(devices=[]):
                t.manual_seed(seed); layers=[]; dims=[6*(input_lags+output_lags),*hidden_sizes,12]
                for i,(a,b) in enumerate(zip(dims[:-1],dims[1:])):
                    layers.append(t.nn.Linear(a,b))
                    if i<len(dims)-2: layers.append(t.nn.SiLU())
                self.members.append(t.nn.Sequential(*layers).cpu().float())

    def scaled(self,u,y):
        return ((u-np.asarray(self.scaler['input_mean']))/np.asarray(self.scaler['input_scale']),
                (y-np.asarray(self.scaler['output_mean']))/np.asarray(self.scaler['output_scale']))

    def forward(self,member,x):
        raw=member(x); mu=raw[...,:6]
        if self.prediction_mode=='state_increment':
            # x ends with the latest standardized six-region state. The public
            # output remains y[k], not the temporal increment y[k]-y[k-1].
            mu=mu+x[...,-6:]
        lv=math.log(1e-6)+(math.log(1e2)-math.log(1e-6))*raw[...,6:].sigmoid()
        return mu,lv

    def _contract(self,dt_s,input_names,output_names):
        if not np.isfinite(dt_s) or not np.isclose(dt_s,self.dt_s,rtol=1e-6,atol=0): raise ValueError('dt mismatch')
        if tuple(input_names)!=self.input_names or tuple(output_names)!=self.output_names: raise ValueError('channel order mismatch')

    def _distribution_batch(self,u,y):
        t=torch_api(); us,ys=self.scaled(u,y)
        x=np.concatenate([us.reshape(len(u),-1),ys.reshape(len(y),-1)],axis=1)
        if not np.isfinite(x).all(): raise ValueError('nonfinite scaled history')
        means=[]; variances=[]
        with t.no_grad():
            xt=t.as_tensor(x,dtype=t.float32)
            for member in self.members:
                mu,lv=self.forward(member,xt)
                means.append(mu.numpy().astype(float)); variances.append(lv.exp().numpy().astype(float))
        means=np.array(means); variances=np.array(variances)
        if not np.isfinite(means).all() or np.any(np.abs(means)>1e12) or not np.isfinite(variances).all():
            raise ValueError('ROLLOUT_FAILED: nonfinite or standardized mean > 1e12')
        scale=np.asarray(self.scaler['output_scale']); offset=np.asarray(self.scaler['output_mean'])
        means=means*scale+offset; variances=variances*scale**2
        mean=means.mean(0); ale=variances.mean(0); epi=((means-mean)**2).mean(0)
        result=dict(mean=mean,aleatoric_var=ale,epistemic_var=epi,total_var=ale+epi,
                    member_means=means,member_vars=variances)
        if not all(np.isfinite(v).all() for v in result.values()): raise ValueError('nonfinite physical prediction')
        return result

    def predict_distribution(self,u_history,y_history,*,dt_s,input_names,output_names):
        self._contract(dt_s,input_names,output_names)
        u=matrix(u_history,'u_history'); y=matrix(y_history,'y_history')
        if u.shape!=(self.input_lags,6) or y.shape!=(self.output_lags,6): raise ValueError('history length mismatch')
        d=self._distribution_batch(u[None],y[None])
        return {k:(v[:,0] if k.startswith('member_') else v[0]) for k,v in d.items()}

    def _initial(self,inputs,initial_outputs,dt_s,input_names,output_names):
        self._contract(dt_s,input_names,output_names)
        u=matrix(inputs,'inputs'); y=matrix(initial_outputs,'initial_outputs')
        if len(u)<=self.max_lag or y.shape!=(self.max_lag,6): raise ValueError('initial outputs must contain only max_lag rows')
        return u,y.copy()

    def rollout(self,inputs,initial_outputs,*,dt_s,input_names,output_names):
        u,y=self._initial(inputs,initial_outputs,dt_s,input_names,output_names)
        history=list(y); rows=[]
        for k in range(self.max_lag,len(u)):
            try: d=self.predict_distribution(u[k-self.input_lags:k],np.asarray(history[-self.output_lags:]),
                                             dt_s=dt_s,input_names=input_names,output_names=output_names)
            except ValueError as exc: raise ValueError(f'ROLLOUT_FAILED step={k}: {exc}') from exc
            rows.append(d); history.append(d['mean'])
        return dict(mean=np.array([d['mean'] for d in rows]),
                    **{'conditional_'+key:np.array([d[key] for d in rows]) for key in ('aleatoric_var','epistemic_var','total_var')})

    def sample_rollouts(self,inputs,initial_outputs,*,particles=200,seed=42,dt_s,input_names,output_names):
        u,y=self._initial(inputs,initial_outputs,dt_s,input_names,output_names)
        if type(particles) is not int or particles<1: raise ValueError('particles must be positive')
        rng=np.random.default_rng(seed); history=np.repeat(y[None],particles,axis=0); result=[]
        for k in range(self.max_lag,len(u)):
            try:
                d=self._distribution_batch(np.repeat(u[None,k-self.input_lags:k],particles,axis=0),history[:,-self.output_lags:])
                member=rng.integers(len(self.members),size=particles); p=np.arange(particles)
                sample=d['member_means'][member,p]+np.sqrt(d['member_vars'][member,p])*rng.normal(size=(particles,6))
                scaled=(sample-np.asarray(self.scaler['output_mean']))/np.asarray(self.scaler['output_scale'])
                if not np.isfinite(sample).all() or np.any(abs(scaled)>1e12): raise ValueError('particle divergence')
            except ValueError as exc: raise ValueError(f'ROLLOUT_FAILED step={k}: {exc}') from exc
            result.append(sample); history=np.concatenate([history[:,1:],sample[:,None]],axis=1)
        return np.stack(result,axis=1)

    def metadata(self):
        t=torch_api()
        return dict(version=INCREMENT_VERSION if self.prediction_mode=='state_increment' else VERSION,
                    prediction_mode=self.prediction_mode,input_lags=self.input_lags,output_lags=self.output_lags,
                    hidden_sizes=self.hidden_sizes,member_seeds=self.member_seeds,scaler=self.scaler,dt_s=self.dt_s,
                    input_names=self.input_names,output_names=self.output_names,dtype='float32',device='cpu',
                    log_variance_bounds=[math.log(1e-6),math.log(1e2)],provenance=self.provenance,
                    environment=dict(python=platform.python_version(),numpy=np.__version__,torch=t.__version__),
                    rl_ready=False,cfd_replacement_accepted=False)

    def _write_snapshot(self,directory):
        t=torch_api(); directory=Path(directory)
        t.save({str(i):m.state_dict() for i,m in enumerate(self.members)},directory/'weights.pt')
        meta=self.metadata(); meta['weights_sha256']=sha256(directory/'weights.pt')
        write_json(directory/'model_metadata.json',meta)
        write_json(directory/'manifest.json',dict(model_metadata_sha256=sha256(directory/'model_metadata.json'),**meta))

    def save(self,directory):
        directory=Path(directory); directory.mkdir(parents=True,exist_ok=False)
        self._write_snapshot(directory)

    @classmethod
    def load(cls,directory):
        directory=Path(directory); meta=json.loads((directory/'model_metadata.json').read_text())
        mode=meta.get('prediction_mode','absolute')
        version=INCREMENT_VERSION if mode=='state_increment' else VERSION
        if mode not in ('absolute','state_increment') or meta.get('version')!=version or meta.get('dtype')!='float32' or meta.get('device')!='cpu': raise ValueError('unsupported snapshot version/dtype/device')
        if meta.get('weights_sha256')!=sha256(directory/'weights.pt'): raise ValueError('weights hash mismatch')
        manifest=json.loads((directory/'manifest.json').read_text())
        if manifest.get('model_metadata_sha256')!=sha256(directory/'model_metadata.json'): raise ValueError('metadata hash mismatch')
        if meta.get('log_variance_bounds')!=[math.log(1e-6),math.log(1e2)]: raise ValueError('variance bounds mismatch')
        m=cls(prediction_mode=mode,**{k:meta[k] for k in ('input_lags','output_lags','hidden_sizes','member_seeds','scaler','dt_s','input_names','output_names')})
        t=torch_api(); states=t.load(directory/'weights.pt',map_location='cpu',weights_only=True)
        if not isinstance(states,dict) or set(states)!={str(i) for i in range(len(m.members))}: raise ValueError('missing/extra member')
        try:
            for i,member in enumerate(m.members):
                state=states[str(i)]
                if not all(isinstance(v,t.Tensor) and v.dtype==t.float32 and t.isfinite(v).all() for v in state.values()): raise ValueError('invalid weight tensors')
                member.load_state_dict(state,strict=True)
        except (RuntimeError,TypeError,AttributeError) as exc: raise ValueError('bad state_dict') from exc
        m.provenance=meta.get('provenance',{})
        return m
