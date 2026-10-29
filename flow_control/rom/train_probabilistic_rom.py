"""B56 mean-feedback multistep Gaussian training; no external validation fitting."""
from __future__ import annotations
import argparse
import csv
import subprocess
import sys
import time
from pathlib import Path
import numpy as np
import yaml
from .probabilistic_data import (load_sequences,check_isolation,fit_scaler,build_window_index,sha256)
from .probabilistic_rom import ProbabilisticEnsembleROM,torch_api,write_json


def multistep_loss(model,member,u,initial_y,targets,*,loss_steps=None):
    """All tensors standardized. No detach: gradients propagate through predicted histories."""
    t=torch_api(); history=initial_y; losses=[]
    start=model.max_lag
    for h in range(targets.shape[1]):
        x=t.cat([u[:,start+h-model.input_lags:start+h].flatten(1),history[:,-model.output_lags:].flatten(1)],dim=1)
        mu,lv=model.forward(member,x)
        if loss_steps is None or h in loss_steps:
            losses.append(.5*(lv+(targets[:,h]-mu).square()*(-lv).exp()).mean())
        history=t.cat([history[:,1:],mu[:,None]],dim=1)
    return t.stack(losses).mean()


def fit(model,segments,*,epochs=200,warmup_epochs=20,horizon=20,batch_size=64,learning_rate=2.8e-4,weight_decay=1e-4,gradient_clip=1.,output_dir=None,epoch_callback=None,window_index_horizon=None):
    if any(type(v) is not int or v<1 for v in (epochs,horizon,batch_size)) or type(warmup_epochs) is not int or warmup_epochs<0:
        raise ValueError('invalid training schedule')
    if not np.isfinite([learning_rate,weight_decay,gradient_clip]).all() or learning_rate<=0 or weight_decay<0 or gradient_clip<=0:
        raise ValueError('invalid optimizer parameters')
    t=torch_api(); logs=[]; bootstrap={}
    # Fixed bootstrap per member AND horizon pool, not resampled every epoch.
    horizons={1 if e<warmup_epochs else horizon for e in range(epochs)}
    if window_index_horizon is not None and (type(window_index_horizon) is not int or window_index_horizon<max(horizons)):
        raise ValueError('window_index_horizon must cover every scheduled horizon')
    pools={h:build_window_index(segments,input_lags=model.input_lags,output_lags=model.output_lags,horizon=window_index_horizon or h) for h in horizons}
    if any(not p for p in pools.values()): raise ValueError('no training windows for scheduled horizon')
    scaled=[model.scaled(s.inputs,s.outputs) for s in segments]
    for member_id,(seed,member) in enumerate(zip(model.member_seeds,model.members)):
        rng=np.random.default_rng(seed)
        if window_index_horizon is None:
            selections={h:rng.integers(len(p),size=len(p)) for h,p in sorted(pools.items())}
        else:
            common=rng.integers(len(next(iter(pools.values()))),size=len(next(iter(pools.values()))))
            selections={h:common for h in pools}
        bootstrap[str(member_id)]={str(h):dict(window_index=[list(x) for x in pools[h]],sampled_indices=selections[h].tolist(),seed=seed) for h in sorted(pools)}
        if output_dir is not None: write_json(Path(output_dir)/'bootstrap.json',bootstrap)
        optimizer=t.optim.Adam(member.parameters(),lr=learning_rate,weight_decay=weight_decay)
        for epoch in range(epochs):
            h=1 if epoch<warmup_epochs else horizon; order=rng.permutation(selections[h]); total=0.; count=0
            saturation=0; variance_count=0
            for b in range(0,len(order),batch_size):
                windows=[pools[h][i] for i in order[b:b+batch_size]]
                u=t.tensor(np.stack([scaled[i][0][k-model.max_lag:k+h] for i,k in windows]),dtype=t.float32)
                y=t.tensor(np.stack([scaled[i][1][k-model.max_lag:k] for i,k in windows]),dtype=t.float32)
                targets=t.tensor(np.stack([scaled[i][1][k:k+h] for i,k in windows]),dtype=t.float32)
                optimizer.zero_grad(); loss=multistep_loss(model,member,u,y,targets)
                if not t.isfinite(loss): raise ValueError(f'nonfinite loss member={member_id} epoch={epoch}')
                loss.backward(); norm=t.nn.utils.clip_grad_norm_(member.parameters(),gradient_clip,error_if_nonfinite=True)
                optimizer.step(); total+=float(loss.detach())*len(windows); count+=len(windows)
                with t.no_grad():
                    x=t.cat([u[:,:model.max_lag][:,-model.input_lags:].flatten(1),y[:,-model.output_lags:].flatten(1)],dim=1)
                    _,lv=model.forward(member,x)
                    frac=(lv-np.log(1e-6))/(np.log(1e2)-np.log(1e-6))
                    saturation+=int(((frac<.01)|(frac>.99)).sum()); variance_count+=frac.numel()
            logs.append(dict(member=member_id,epoch=epoch+1,horizon=h,loss=total/count,
                             variance_boundary_fraction=saturation/variance_count,variance_boundary_scope='first-step batch contexts',last_gradient_norm=float(norm)))
            if epoch_callback is not None:
                logs[-1].update(epoch_callback(model,member_id,epoch+1) or {})
            if output_dir is not None:
                with (Path(output_dir)/'training_log.csv').open('w',newline='') as f:
                    w=csv.DictWriter(f,fieldnames=list(logs[0])); w.writeheader(); w.writerows(logs)
    return logs,bootstrap


def read_config(path):
    path=Path(path).resolve(); cfg=yaml.safe_load(path.read_text(encoding='utf-8'))
    root=Path(cfg.get('project_root','.'))
    if not root.is_absolute(): root=(path.parent/root).resolve()
    def resolve(value):
        p=Path(value); return p if p.is_absolute() else root/p
    return cfg,resolve


def datasets(cfg,resolve):
    args=dict(sample_stride=cfg.get('sample_stride',10),expected_dt_s=cfg.get('expected_dt_s',.001))
    train,ta=load_sequences(resolve(cfg['training_dataset']),**args)
    val,va=load_sequences(resolve(cfg['validation_dataset']),**args)
    check_isolation(ta,va)
    if not train: raise ValueError('empty training dataset')
    if np.any(np.max(np.abs(np.vstack([s.inputs for s in train])),axis=0)<=1e-8): raise ValueError('representative training jet unexcited')
    return train,ta,val,va


def input_files(cfg,resolve):
    paths={key:resolve(cfg[key]) for key in ('training_dataset','validation_dataset','linear_model','residual_model') if cfg.get(key)}
    paths.update({f"evaluation:{item['name']}":resolve(item['path']) for item in cfg.get('evaluation_datasets',[])})
    return {key:dict(path=str(p),sha256=sha256(p) if p.is_file() else None) for key,p in paths.items()}


def source_version():
    def git(*args): return subprocess.check_output(['git',*args],text=True).strip()
    return dict(git_head=git('rev-parse','HEAD'),git_status=git('status','--short'),
                tracked_diff_sha256=__import__('hashlib').sha256(subprocess.check_output(['git','diff','HEAD'])).hexdigest(),
                b56_source_hashes={p.name:sha256(p) for p in Path(__file__).parent.glob('*probabilistic*.py')})


def failure(out,exc):
    write_json(out/'failure.json',dict(status='FAILED',error_type=type(exc).__name__,error=str(exc),rl_ready=False,cfd_replacement_accepted=False))
    (out/'AGENT_HANDOFF.md').write_text(f'# B56 本地运行失败\n\n{type(exc).__name__}: {exc}\n未启动CFD；保留证据，新目录重试。\n',encoding='utf-8')


def run(config_path,output_dir,*,require_validation=False):
    out=Path(output_dir).resolve(); out.mkdir(parents=True,exist_ok=False)
    try:
        cfg,resolve=read_config(config_path)
        write_json(out/'manifest.json',dict(config=cfg,config_sha256=sha256(config_path),version=source_version(),input_files=input_files(cfg,resolve),rl_ready=False,cfd_replacement_accepted=False))
        (out/'config.yaml').write_text(Path(config_path).read_text(),encoding='utf-8')
        train,ta,val,va=datasets(cfg,resolve)
        if require_validation and not val: raise ValueError('validation required but empty')
        model=ProbabilisticEnsembleROM(scaler=fit_scaler(train),dt_s=cfg.get('expected_dt_s',.001),**cfg.get('network',{}))
        model.provenance=dict(config=cfg,config_sha256=sha256(config_path),version=source_version(),training=ta,validation=va)
        write_json(out/'data_audit.json',model.provenance)
        started=time.perf_counter()
        logs,bootstrap=fit(model,train,output_dir=out,**cfg.get('optimizer',{}))
        model.provenance['training_elapsed_s']=time.perf_counter()-started
        write_json(out/'bootstrap.json',bootstrap)
        with (out/'training_log.csv').open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(logs[0])); w.writeheader(); w.writerows(logs)
        model._write_snapshot(out)
        from .evaluate_probabilistic_rom import evaluate_into
        report=evaluate_into(model,cfg,resolve,out,train,ta,val,va)
        return report
    except Exception as exc:
        failure(out,exc); raise


def main(argv=None):
    p=argparse.ArgumentParser(description='B56 local probabilistic ensemble training (optional torch)')
    p.add_argument('--config',required=True); p.add_argument('--output-dir',required=True); p.add_argument('--require-validation',action='store_true')
    p.add_argument('--development-study',action='store_true',help='select only on blocked training development, then refit once')
    a=p.parse_args(argv)
    try:
        if a.development_study:
            from .probabilistic_development import run_study
            r=run_study(a.config,a.output_dir,require_validation=a.require_validation)
        else:
            r=run(a.config,a.output_dir,require_validation=a.require_validation)
    except Exception as exc: print(f'B56: {exc}',file=sys.stderr); return 1
    return 1 if r['status'] in ('ROLLOUT_FAILED','INSUFFICIENT_DATA') else 0
