"""B56 offline comparison. Conditional mixture moments are not trajectory uncertainty."""
from __future__ import annotations
import argparse
import csv
import json
import math
import sys
import time
from pathlib import Path
import numpy as np
from .probabilistic_data import INPUT_NAMES,OUTPUT_NAMES,load_sequences,check_isolation,sha256
from .probabilistic_rom import ProbabilisticEnsembleROM,write_json


def region_metrics(truth,predicted,times):
    result=[]
    for j,name in enumerate(OUTPUT_NAMES):
        y=truth[:,j]; p=predicted[:,j]; e=p-y; span=float(np.ptp(y))
        corr=float(np.corrcoef(y,p)[0,1]) if np.std(y)>1e-12 and np.std(p)>1e-12 else None
        result.append(dict(region=name,samples=len(y),rmse_N=float(np.sqrt(np.mean(e**2))),
                           mae_N=float(np.mean(abs(e))),nrmse=float(np.sqrt(np.mean(e**2))/span) if span>=1e-12 else None,
                           nrmse_denominator_N=span,nrmse_null_reason='truth range below 1e-12 N' if span<1e-12 else None,
                           correlation=corr,correlation_null_reason='zero variance' if corr is None else None,
                           peak_N=float(p[np.argmax(abs(p))]),truth_peak_N=float(y[np.argmax(abs(y))]),
                           peak_error_N=float(p[np.argmax(abs(p))]-y[np.argmax(abs(y))]),
                           peak_time_error_s=float(times[np.argmax(abs(p))]-times[np.argmax(abs(y))])))
    return result


def mixture_diagnostics(truth,distributions,*,samples,seed):
    rng=np.random.default_rng(seed); means=np.stack([d['member_means'] for d in distributions],axis=1)
    var=np.stack([d['member_vars'] for d in distributions],axis=1)
    # Mixture marginal NLL in physical N; diagonal within each member, mixing jointly over six regions.
    logp=-.5*(np.log(2*np.pi*var)+(truth[None]-means)**2/var)
    joint=logp.sum(-1); maxlog=joint.max(0)
    joint_nll=-(maxlog+np.log(np.exp(joint-maxlog).mean(0)))
    maxlog=logp.max(0); marginal_nll=-(maxlog+np.log(np.exp(logp-maxlog).mean(0)))
    # Same member index must supply BOTH mean and variance.
    idx=rng.integers(len(means),size=(samples,len(truth)))
    draws=means[idx,np.arange(len(truth))]+np.sqrt(var[idx,np.arange(len(truth))])*rng.normal(size=(samples,len(truth),6))
    regions=[]
    for j,name in enumerate(OUTPUT_NAMES):
        r=dict(region=name,mixture_nll_N=float(marginal_nll[:,j].mean()),samples=len(truth),intervals={})
        for level in (.5,.9,.95):
            lo,hi=np.quantile(draws[:,:,j],[(1-level)/2,(1+level)/2],axis=0)
            r['intervals'][str(level)]=dict(coverage=float(np.mean((truth[:,j]>=lo)&(truth[:,j]<=hi))),mean_width_N=float(np.mean(hi-lo)))
        regions.append(r)
    return dict(joint_mixture_nll=float(joint_nll.mean()),regions=regions,quantile_samples=samples,seed=seed,
                note='Single-step conditional mixture intervals; Monte Carlo quantiles, not trajectory intervals.')


def _equal_summary(cases):
    """Equal Case weighting. Failed/missing cases are explicitly retained outside aggregates."""
    result={}
    modes=sorted({mode for c in cases for mode in c.get('modes',{})})
    for mode in modes:
        result[mode]=[]
        for j,name in enumerate(OUTPUT_NAMES):
            valid=[c['modes'][mode]['regions'][j] for c in cases if c.get('modes',{}).get(mode,{}).get('status')=='OK']
            row=dict(region=name,valid_cases=len(valid),total_cases=len(cases))
            for metric in ('rmse_N','mae_N','nrmse','correlation','peak_error_N','peak_time_error_s'):
                vals=[r[metric] for r in valid if r[metric] is not None]
                row[metric]=float(np.mean(vals)) if vals else None
            result[mode].append(row)
    return result


def probability_case_summary(cases):
    valid=[c['conditional_probability'] for c in cases if c.get('conditional_probability')]
    if not valid: return dict(valid_cases=0,regions=[])
    result=dict(valid_cases=len(valid),joint_mixture_nll=float(np.mean([c['joint_mixture_nll'] for c in valid])),regions=[])
    for j in range(len(valid[0]['regions'])):
        rows=[c['regions'][j] for c in valid]
        result['regions'].append(dict(region=rows[0]['region'],
            mixture_nll_N=float(np.mean([r['mixture_nll_N'] for r in rows])),
            intervals={str(level):{key:float(np.mean([r['intervals'][str(level)][key] for r in rows]))
                for key in ('coverage','mean_width_N')} for level in (.5,.9,.95)}))
    return result


def load_baselines(cfg,resolve,model):
    from .b54_real_rom import ScaledRealARXModel
    from .residual_rom import ResidualROM
    result={}; info={}
    for label,key in [('B54','linear_model'),('B55','residual_model')]:
        if not cfg.get(key):
            info[label]=dict(status='BASELINE_UNAVAILABLE',reason='not configured'); continue
        p=resolve(cfg[key]); payload=json.loads(p.read_text())
        b=ScaledRealARXModel.from_dict(payload) if label=='B54' else ResidualROM.from_dict(payload)
        linear=b if label=='B54' else b.linear
        dt=payload.get('effective_dt_s') if label=='B54' else b.dt_s
        if (linear.arx.max_lag!=model.max_lag or linear.arx.include_current_input or
            tuple(linear.arx.input_names_)!=INPUT_NAMES or tuple(linear.arx.output_names_)!=OUTPUT_NAMES or
            not np.isclose(dt,model.dt_s,rtol=1e-6,atol=0)):
            raise ValueError(f'{label} incompatible channels/lag/dt/current input')
        training=payload.get('training_data',{}) if label=='B54' else payload.get('provenance',{})
        train_hash=training.get('dataset_sha256',training.get('training_sha256'))
        info[label]=dict(status='AVAILABLE',path=str(p),sha256=sha256(p),training_provenance=training,
                         training_dataset_matches=train_hash==model.provenance.get('training',{}).get('sha256'))
        result[label]=b
    return result,info


def _baseline_predictions(b,label,s,lag,dt):
    if label=='B54':
        from .b54_from_b53 import predict
        one,recursive=predict(b,s)
        return one[lag:],recursive[lag:]
    return b.predict_one_step(s.inputs,s.outputs)[lag:],b.rollout(s.inputs,s.outputs[:lag],dt_s=dt)


def evaluate_into(model,cfg,resolve,out,train,ta,val,va):
    for audit in (ta,va):
        if not np.isclose(audit['effective_dt_s'],model.dt_s,rtol=1e-6,atol=0):
            raise ValueError('evaluation sampling contract dt mismatch')
    baselines,baseline_info=load_baselines(cfg,resolve,model)
    settings=cfg.get('evaluation',{}); particles=settings.get('particles',200)
    samples=settings.get('mixture_samples',2000); seed=settings.get('seed',42)
    if type(particles) is not int or particles<0 or type(samples) is not int or samples<1: raise ValueError('invalid evaluation sample counts')
    groups=[('training',train,ta,'training_diagnostic'),('validation',val,va,'known_evaluation')]
    # Every evaluation file is explicit, never discovered by scanning runs.
    names={'training','validation'}
    for item in cfg.get('evaluation_datasets',[]):
        if item['name'] in names: raise ValueError('duplicate evaluation name')
        names.add(item['name'])
        if item['role'] not in ('known_evaluation','blind_test'): raise ValueError('invalid evaluation role')
        seq,audit=load_sequences(resolve(item['path']),sample_stride=cfg.get('sample_stride',10),expected_dt_s=model.dt_s)
        for _,_,previous,_ in groups: check_isolation(previous,audit)
        groups.append((item['name'],seq,audit,item['role']))
    context=dict(dt_s=model.dt_s,input_names=INPUT_NAMES,output_names=OUTPUT_NAMES)
    report=dict(status='COMPARISON_COMPLETE' if val else 'TRAINING_ONLY',rl_ready=False,cfd_replacement_accepted=False,
                baselines=baseline_info,failures=[],evaluation_settings=dict(particles=particles,seed=seed,mixture_samples=samples,
                    trajectory_tail_probability_mc_se=math.sqrt(.025*.975/particles) if particles else None,
                    monte_carlo_note='Quantile probability-rank SE only; physical quantile precision not certified'),
                peak_definition='Signed value at maximum absolute magnitude; peak timing uses the same convention',
                uncertainty_note='Conditional variances are N^2; trajectory intervals use TS1 particles only.',
                timing=dict(device='cpu',dtype='float32',batch=1,warmup_calls=2),
                interpretation='Software/offline diagnostics, no real CFD accuracy or closed-loop acceptance.')
    fields=['dataset','case','segment','time','region','truth_N','mean_N','conditional_aleatoric_var_N2',
            'conditional_epistemic_var_N2','trajectory_q025_N','trajectory_q975_N','mode','horizon_steps']
    latency=[]
    with (out/'predictions.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields); writer.writeheader()
        for label,segments,audit,role in groups:
            grouped={}; short=[]
            for s in segments: grouped.setdefault(s.case_id,[]).append(s)
            case_results=[]
            for case,parts in grouped.items():
                case_record=dict(case=case,segments=[],modes={},status='OK'); case_results.append(case_record)
                # Collect per-case rows before measuring, so Case weighting is independent of segment count.
                case_arrays={}; case_prob=[]; case_truth=[]
                for s in parts:
                    lag=model.max_lag
                    sr=dict(segment=s.segment_id,initialization_rows=min(lag,len(s)),
                            initialization_start_s=float(s.time[0]),initialization_end_s=float(s.time[min(lag,len(s))-1]),
                            first_action_s=float(np.min(s.action_start)),evaluation_start_s=float(s.time[lag]) if len(s)>lag else None,
                            warmup_covers_action=bool(np.min(s.action_start)<s.time[min(lag,len(s))-1]),
                            rows=len(s),status='OK')
                    case_record['segments'].append(sr)
                    if len(s)<=lag:
                        sr['status']='INSUFFICIENT_HISTORY'; short.append(dict(case=case,segment=s.segment_id,rows=len(s)))
                        case_record['status']='INSUFFICIENT_HISTORY'; continue
                    truth=s.outputs[lag:]; times=s.time[lag:]; pred={}; conditional={}; intervals={}
                    try:
                        d=[]
                        for k in range(lag,len(s)):
                            if not latency:
                                for _ in range(2): model.predict_distribution(s.inputs[k-model.input_lags:k],s.outputs[k-model.output_lags:k],**context)
                            started=time.perf_counter()
                            d.append(model.predict_distribution(s.inputs[k-model.input_lags:k],s.outputs[k-model.output_lags:k],**context))
                            latency.append(time.perf_counter()-started)
                        pred['B56_one_step']=np.stack([a['mean'] for a in d]); conditional['B56_one_step']=d
                        case_prob.extend(d); case_truth.extend(truth)
                        rec=model.rollout(s.inputs,s.outputs[:lag],**context); pred['B56_rollout']=rec['mean']
                        conditional['B56_rollout']=[dict(aleatoric_var=a,epistemic_var=e) for a,e in zip(rec['conditional_aleatoric_var'],rec['conditional_epistemic_var'])]
                        # Fixed-horizon free runs restarted from real histories; targets never fed during each run.
                        for horizon in (20,100,500):
                            n=int(round(horizon/1000/model.dt_s)); horizon_name=f'B56_{horizon}ms'
                            if len(truth)<n:
                                sr[horizon_name]='INSUFFICIENT_LENGTH'; continue
                            chunks=[]
                            for start in range(lag,len(s)-n+1,n):
                                uu=s.inputs[start-lag:start+n]; yy=s.outputs[start-lag:start]
                                chunks.append(model.rollout(uu,yy,**context)['mean'])
                            pred[horizon_name]=np.vstack(chunks)
                        if particles:
                            draws=model.sample_rollouts(s.inputs,s.outputs[:lag],particles=particles,seed=seed,**context)
                            lo,hi=np.quantile(draws,[.025,.975],axis=0); intervals['B56_rollout']=(lo,hi)
                            sr['trajectory_95']=[dict(region=name,coverage=float(np.mean((truth[:,j]>=lo[:,j])&(truth[:,j]<=hi[:,j]))),
                                                     mean_width_N=float(np.mean(hi[:,j]-lo[:,j]))) for j,name in enumerate(OUTPUT_NAMES)]
                        for bname,b in baselines.items():
                            one,recursive=_baseline_predictions(b,bname,s,lag,model.dt_s)
                            pred[bname+'_one_step']=one; pred[bname+'_rollout']=recursive
                            for horizon in (20,100,500):
                                n=int(round(horizon/1000/model.dt_s))
                                if len(truth)<n: sr[f'{bname}_{horizon}ms']='INSUFFICIENT_LENGTH'; continue
                                pieces=[]
                                for start in range(lag,len(s)-n+1,n):
                                    piece=s.take(slice(start-lag,start+n))
                                    pieces.append(_baseline_predictions(b,bname,piece,lag,model.dt_s)[1])
                                pred[f'{bname}_{horizon}ms']=np.vstack(pieces)
                        for mode,p in pred.items():
                            if not np.isfinite(p).all(): raise ValueError(f'{mode} nonfinite prediction')
                            yy=(p-np.asarray(model.scaler['output_mean']))/np.asarray(model.scaler['output_scale'])
                            if np.any(abs(yy)>1e12): raise ValueError(f'{mode} standardized magnitude >1e12')
                    except (ValueError,FloatingPointError,OverflowError) as exc:
                        sr['status']='ROLLOUT_FAILED'; sr['error']=str(exc); case_record['status']='ROLLOUT_FAILED'
                        report['failures'].append(dict(dataset=label,case=case,segment=s.segment_id,error=str(exc)))
                        # Preserve successful modes above, but never include a failed Case in success-only overall claim.
                    for mode,p in pred.items():
                        if not np.isfinite(p).all(): continue
                        length=len(p); case_arrays.setdefault(mode,[]).append((truth[:length],p,times[:length]))
                        for i in range(length):
                            for j,name in enumerate(OUTPUT_NAMES):
                                row=dict(dataset=label,case=case,segment=s.segment_id,time=float(times[i]),region=name,
                                         truth_N=float(truth[i,j]),mean_N=float(p[i,j]),mode=mode,
                                         horizon_steps=1 if mode.endswith('one_step') else (int(round(int(mode.split('_')[-1][:-2])/1000/model.dt_s)) if mode.endswith('ms') else length))
                                if mode in conditional:
                                    row.update(conditional_aleatoric_var_N2=float(conditional[mode][i]['aleatoric_var'][j]),
                                               conditional_epistemic_var_N2=float(conditional[mode][i]['epistemic_var'][j]))
                                if mode in intervals:
                                    row.update(trajectory_q025_N=float(intervals[mode][0][i,j]),trajectory_q975_N=float(intervals[mode][1][i,j]))
                                writer.writerow(row)
                for mode,arrays in case_arrays.items():
                    truth=np.vstack([a[0] for a in arrays]); p=np.vstack([a[1] for a in arrays]); times=np.concatenate([a[2] for a in arrays])
                    case_record['modes'][mode]=dict(status='OK' if case_record['status']=='OK' else case_record['status'],regions=region_metrics(truth,p,times))
                if case_prob:
                    case_record['conditional_probability']=mixture_diagnostics(np.array(case_truth),case_prob,samples=samples,seed=seed)
            report[label]=dict(role=role,role_evidence='user-declared; historical blind freeze not verified',audit=audit,cases=case_results,short_segments=short,equal_case_summary=_equal_summary(case_results),equal_case_conditional_probability=probability_case_summary(case_results),
                               evaluation_scope='B53 segments; no claim of complete unfiltered CFD trajectory')
    if latency: report['timing'].update(single_step_mean_s=float(np.mean(latency)),measured_calls=len(latency),batch_throughput='not measured')
    if any(group[1] and report[group[0]]['short_segments'] for group in groups):
        report['status']='INSUFFICIENT_DATA'
    if report['failures']: report['status']='ROLLOUT_FAILED'
    write_json(out/'metrics.json',report)
    (out/'result.md').write_text(f"# B56 离线软件报告\n\n状态：{report['status']}。\n\n六代表口实际流量 → 六区 ΔFz；单步条件方差不能作为轨迹置信区间。\n本次报告不构成真实CFD精度或CCM闭环验收。rl_ready=false；cfd_replacement_accepted=false。\n详见 metrics.json 与 predictions.csv；失败Case保留。\n",encoding='utf-8')
    (out/'AGENT_HANDOFF.md').write_text('# B56 本地交接\n\n授权：本地网络训练/评估；未启动STAR或集群作业。\n状态：'+report['status']+'\n配置、版本、哈希见manifest.json / data_audit.json；指标见metrics.json。\n独立测试AGENT请复核因果、来源、概率单位、失败表和预热覆盖动作提示。真实精度和闭环均未验收。\n',encoding='utf-8')
    return report


def run(model_dir,config_path,output_dir,*,require_validation=False):
    from .train_probabilistic_rom import read_config,datasets,failure,source_version,input_files
    out=Path(output_dir).resolve(); out.mkdir(parents=True,exist_ok=False)
    try:
        cfg,resolve=read_config(config_path)
        write_json(out/'manifest.json',dict(config=cfg,config_sha256=sha256(config_path),version=source_version(),input_files=input_files(cfg,resolve),
                                            model_dir=str(Path(model_dir).resolve()),model_metadata_sha256=sha256(Path(model_dir)/'model_metadata.json'),rl_ready=False,cfd_replacement_accepted=False))
        model=ProbabilisticEnsembleROM.load(model_dir)
        stored=model.provenance.get('training',{})
        if (not np.isclose(cfg.get('expected_dt_s',.001),model.dt_s,rtol=1e-6,atol=0) or
            cfg.get('sample_stride',10)!=stored.get('sample_stride') or stored.get('subsample_phase')!=0):
            raise ValueError('evaluation sampling contract differs from model dt/stride/phase')
        train,ta,val,va=datasets(cfg,resolve)
        if model.provenance.get('training',{}).get('sha256')!=ta['sha256']: raise ValueError('evaluation training dataset differs from model provenance')
        if require_validation and not val: raise ValueError('validation required but empty')
        return evaluate_into(model,cfg,resolve,out,train,ta,val,va)
    except Exception as exc:
        failure(out,exc); raise


def main(argv=None):
    p=argparse.ArgumentParser(description='B56 local offline evaluation')
    p.add_argument('--model-dir',required=True); p.add_argument('--config',required=True); p.add_argument('--output-dir',required=True); p.add_argument('--require-validation',action='store_true')
    a=p.parse_args(argv)
    try: r=run(a.model_dir,a.config,a.output_dir,require_validation=a.require_validation)
    except Exception as exc: print(f'B56: {exc}',file=sys.stderr); return 1
    return 1 if r['status'] in ('ROLLOUT_FAILED','INSUFFICIENT_DATA') else 0
