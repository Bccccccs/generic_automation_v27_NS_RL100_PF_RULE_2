"""B56 training-only blocked development selection, never external validation tuning."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import numpy as np

from .probabilistic_rom import matrix, torch_api, write_json


def split_development(segments, *, fraction=.25, max_lag=200, horizon=20):
    """Separate raw supports, then additionally exclude max_lag+H-1 grid rows.

    This is temporal development within existing Cases, not independent CFD
    initial conditions. Each development segment uses its own warmup rows.
    """
    if not 0 < fraction < 1 or any(type(x) is not int or x < 1 for x in (max_lag, horizon)):
        raise ValueError('invalid development split')
    training, development, audit = [], [], []
    gap = max_lag + horizon - 1
    for s in segments:
        boundary = int(len(s) * (1-fraction))
        dev_start = boundary + gap
        if boundary < max_lag+horizon or len(s)-dev_start < max_lag+horizon:
            raise ValueError(f'segment too short for isolated development: {s.case_id}/{s.segment_id}')
        training.append(s.take(slice(0,boundary)))
        development.append(s.take(slice(dev_start,None)))
        audit.append(dict(case=s.case_id,segment=s.segment_id,fit_rows=[0,boundary],
                          excluded_rows=gap,excluded_support=[boundary,dev_start],
                          development_rows=[dev_start,len(s)],
                          fit_time_s=[float(s.time[0]),float(s.time[boundary-1])],
                          development_time_s=[float(s.time[dev_start]),float(s.time[-1])],
                          max_lag=max_lag,horizon=horizon,
                          interpretation='training-internal temporal holdout; not independent physical Case'))
    if not training:
        raise ValueError('no training segments')
    return training, development, audit


def development_score(model, member, sequences):
    """Equal region/mode/segment score in fit-only standardized units.

    Modes: teacher-context one-step, 100ms free blocks, full free segment.
    Real outputs initialize each block only; future outputs are loss targets.
    Partial last blocks are retained for this internal score.
    """
    t=torch_api()
    metrics={name:[] for name in ('one_step','100ms','rollout')}
    lag=model.max_lag
    with t.no_grad():
        for s in sequences:
            if len(s)<=lag:
                raise ValueError('development history too short')
            us,ys=model.scaled(s.inputs,s.outputs)
            u=t.as_tensor(us,dtype=t.float32)
            y=t.as_tensor(ys,dtype=t.float32)
            truth=y[lag:]

            def checked(x):
                mu,_=model.forward(member,x)
                if not t.isfinite(mu).all() or (mu.abs()>1e12).any():
                    raise ValueError('ROLLOUT_FAILED in internal development')
                return mu

            features=np.stack([np.r_[us[k-model.input_lags:k].ravel(),ys[k-model.output_lags:k].ravel()]
                               for k in range(lag,len(s))])
            one=t.cat([checked(t.as_tensor(features[b:b+256],dtype=t.float32))
                       for b in range(0,len(features),256)])
            metrics['one_step'].append(float(((one-truth).square().mean(0).sqrt()).mean()))
            for name,block_size in [('100ms',max(1,round(.1/model.dt_s))),('rollout',len(s)-lag)]:
                predictions=[]
                for start in range(lag,len(s),block_size):
                    history=y[start-lag:start].clone()
                    for k in range(start,min(start+block_size,len(s))):
                        x=t.cat([u[k-model.input_lags:k].flatten(),history[-model.output_lags:].flatten()])[None]
                        mu=checked(x)[0]
                        predictions.append(mu)
                        history=t.cat([history[1:],mu[None]],dim=0)
                predicted=t.stack(predictions)
                metrics[name].append(float(((predicted-truth).square().mean(0).sqrt()).mean()))
    modes={name:float(np.mean(scores)) for name,scores in metrics.items()}
    return dict(score=float(np.mean(list(modes.values()))),modes=modes,
                interpretation='equal standardized RMSE across regions, modes and development segments')


class DevelopmentSelector:
    """Epoch callback. Keeps each member's best internal-development checkpoint."""
    def __init__(self,sequences,*,interval=10,min_epoch=1):
        if type(interval) is not int or interval<1 or type(min_epoch) is not int or min_epoch<1:
            raise ValueError('invalid selection cadence')
        self.sequences=sequences
        self.interval=interval
        self.min_epoch=min_epoch
        self.best_states={}
        self.best_scores={}
        self.best_epochs={}
        self.history=[]

    def __call__(self,model,member_id,epoch):
        record=dict(development_score=None,development_valid=None,development_error=None)
        if epoch%self.interval:
            return record
        try:
            result=development_score(model,model.members[member_id],self.sequences)
            record.update(development_score=result['score'],development_valid=True)
            if epoch>=self.min_epoch and result['score']<self.best_scores.get(member_id,math.inf):
                self.best_scores[member_id]=result['score']
                self.best_epochs[member_id]=epoch
                self.best_states[member_id]=copy.deepcopy(model.members[member_id].state_dict())
            self.history.append(dict(member=member_id,epoch=epoch,eligible=epoch>=self.min_epoch,**result))
        except ValueError as exc:
            record.update(development_valid=False,development_error=str(exc))
            self.history.append(dict(member=member_id,epoch=epoch,status='ROLLOUT_FAILED',error=str(exc)))
        return record

    def restore(self,model):
        if set(self.best_states)!=set(range(len(model.members))):
            raise ValueError('no finite eligible development checkpoint for every member')
        for i,member in enumerate(model.members):
            member.load_state_dict(self.best_states[i])


def run_study(config_path,output_dir,*,require_validation=False):
    """Select only on internal development, freeze, then refit the full Case once.

    The external CSV is not read as observations until selection.json exists.
    Screened single-member runs are diagnostics; final training uses the configured
    full ensemble. All failed candidates/seeds remain in the study artifacts.
    """
    import csv
    import datetime
    import time
    from zoneinfo import ZoneInfo
    import yaml
    from .probabilistic_data import load_sequences,fit_scaler,sha256
    from .probabilistic_rom import ProbabilisticEnsembleROM
    from .train_probabilistic_rom import read_config,fit,run,failure,source_version,input_files

    out=Path(output_dir).resolve()
    out.mkdir(parents=True,exist_ok=False)
    try:
        cfg,resolve=read_config(config_path)
        study=cfg['study']
        epochs=study.get('epochs',80)
        interval=study.get('interval',10)
        seeds=study.get('member_seeds',[42,43,44])
        candidates=study['candidates']
        if not candidates or not seeds or len(set(seeds))!=len(seeds):
            raise ValueError('nonempty unique study seeds and candidates required')
        if type(epochs) is not int or epochs<interval or epochs%interval:
            raise ValueError('study epochs must be a positive multiple of interval')
        names=[c['name'] for c in candidates]
        if len(set(names))!=len(names) or any(not name or not all(ch.isalnum() or ch in '-_' for ch in name) for name in names):
            raise ValueError('invalid candidate names')
        maximum_horizon=max(c['horizon'] for c in candidates)
        common_horizon=study.get('window_horizon',maximum_horizon)
        if common_horizon<maximum_horizon:
            raise ValueError('common window must cover every candidate horizon')
        manifest=dict(config=cfg,config_sha256=sha256(config_path),version=source_version(),
                      input_files=input_files(cfg,resolve),external_data_used_for_selection=False,
                      rl_ready=False,cfd_replacement_accepted=False)
        write_json(out/'study_manifest.json',manifest)
        training,audit=load_sequences(resolve(cfg['training_dataset']),sample_stride=cfg.get('sample_stride',10),
                                      expected_dt_s=cfg.get('expected_dt_s',.001))
        # Existence is checked without reading external target observations.
        if not resolve(cfg['validation_dataset']).is_file():
            raise FileNotFoundError(resolve(cfg['validation_dataset']))
        network=dict(cfg.get('network',{}))
        lag=max(network.get('input_lags',200),network.get('output_lags',10))
        fit_sequences,development,support=split_development(training,fraction=study.get('development_fraction',.25),
                                                           max_lag=lag,horizon=common_horizon)
        scaler=fit_scaler(fit_sequences)
        write_json(out/'development_split.json',dict(source_audit=audit,support=support,scaler=scaler,
                    selection_score='equal mean of standardized region RMSE for one-step, 100ms blocks, full rollout'))
        ranking=[]
        for candidate in candidates:
            candidate_dir=out/'candidates'/candidate['name']
            candidate_dir.mkdir(parents=True,exist_ok=False)
            runs=[]
            for seed in seeds:
                seed_dir=candidate_dir/f'seed_{seed}'
                seed_dir.mkdir(exist_ok=False)
                started=time.perf_counter()
                print(f"B56 study: {candidate['name']} seed={seed} starting",flush=True)
                try:
                    net=dict(network)
                    net.update(hidden_sizes=candidate['hidden_sizes'],member_seeds=[seed],
                               prediction_mode=candidate['prediction_mode'])
                    model=ProbabilisticEnsembleROM(scaler=scaler,dt_s=cfg.get('expected_dt_s',.001),**net)
                    warmup=candidate.get('warmup_epochs',0)
                    selector=DevelopmentSelector(development,interval=interval,
                                min_epoch=warmup+1 if candidate['horizon']>1 else 1)
                    optimizer=dict(cfg.get('optimizer',{}))
                    optimizer.update(epochs=epochs,warmup_epochs=warmup,horizon=candidate['horizon'])
                    logs,bootstrap=fit(model,fit_sequences,epoch_callback=selector,
                                       window_index_horizon=common_horizon,output_dir=seed_dir,**optimizer)
                    write_json(seed_dir/'bootstrap.json',bootstrap)
                    write_json(seed_dir/'development_history.json',selector.history)
                    selector.restore(model)
                    model.provenance=dict(study=manifest,fit_support=support,source_dataset_sha256=audit['sha256'],
                                          role='training-internal screening; not full-Case final model')
                    model.save(seed_dir/'best_model')
                    record=dict(seed=seed,status='OK',score=selector.best_scores[0],
                                best_epoch=selector.best_epochs[0],elapsed_s=time.perf_counter()-started)
                    print(f"B56 study: {candidate['name']} seed={seed} score={record['score']:.6g} best_epoch={record['best_epoch']}",flush=True)
                except Exception as exc:
                    failure(seed_dir,exc)
                    record=dict(seed=seed,status='FAILED',error=str(exc),elapsed_s=time.perf_counter()-started)
                    print(f"B56 study: {candidate['name']} seed={seed} FAILED: {exc}",flush=True)
                write_json(seed_dir/'score.json',record)
                runs.append(record)
            ok=all(r['status']=='OK' for r in runs)
            row=dict(name=candidate['name'],candidate=candidate,runs=runs,status='OK' if ok else 'FAILED',
                     mean_score=float(np.mean([r['score'] for r in runs])) if ok else None,
                     score_std=float(np.std([r['score'] for r in runs],ddof=1)) if ok and len(runs)>1 else None)
            ranking.append(row)
            write_json(candidate_dir/'summary.json',row)
            write_json(out/'ranking_progress.json',ranking)
        valid=[r for r in ranking if r['status']=='OK']
        if not valid:
            raise ValueError('all candidates failed; no model selected')
        best=min(valid,key=lambda r:r['mean_score'])
        selected=dict(cfg)
        selected.pop('study',None)
        selected['project_root']=str(resolve('.').resolve())
        selected['network']=dict(network)
        selected['network'].update(hidden_sizes=best['candidate']['hidden_sizes'],prediction_mode=best['candidate']['prediction_mode'])
        selected['optimizer']=dict(cfg.get('optimizer',{}))
        selected['optimizer'].update(epochs=int(np.median([r['best_epoch'] for r in best['runs']])),
                                    horizon=best['candidate']['horizon'],warmup_epochs=best['candidate'].get('warmup_epochs',0))
        decision=dict(selected_candidate=best['name'],ranking=ranking,selected_config=selected,
                      criterion='lowest mean internal-development score across predeclared seeds',
                      external_data_used_for_selection=False,
                      frozen_at=datetime.datetime.now(ZoneInfo('Asia/Singapore')).isoformat(),
                      rl_ready=False,cfd_replacement_accepted=False)
        write_json(out/'selection.json',decision)
        selected_path=out/'selected_config.yaml'
        selected_path.write_text(yaml.safe_dump(selected,allow_unicode=True,sort_keys=False),encoding='utf-8')
        print(f"B56 selection FROZEN: {best['name']}; full-Case ensemble refit begins",flush=True)
        result=run(selected_path,out/'final_model',require_validation=require_validation)
        write_json(out/'study_summary.json',dict(selected_candidate=best['name'],final_model=str(out/'final_model'),
                    external_status=result['status'],external_data_used_for_selection=False,
                    rl_ready=False,cfd_replacement_accepted=False))
        (out/'AGENT_HANDOFF.md').write_text('# B56 development study\n\nSelection frozen in selection.json before external evaluation.\n'
            f"Selected: {best['name']}; final result: {result['status']}.\n"
            'All candidate failures retained. Internal holdout is within one physical Case.\n'
            'Final model and metrics: final_model/. No CFD/RL acceptance.\n',encoding='utf-8')
        return result
    except Exception as exc:
        failure(out,exc)
        raise
