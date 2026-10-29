"""B56 B53 audit and causal windows. No torch import; no interpolation or sorting."""
from __future__ import annotations
import csv
import hashlib
import json
from pathlib import Path
import numpy as np
from .b54_from_b53 import read_dataset, BASE_COLUMNS

JETS = (2, 3, 9, 11, 17, 19)
INPUT_NAMES = tuple(f'actual_massflow_{j:02d}' for j in JETS)
OUTPUT_NAMES = tuple(f'delta_Fz_{r}' for r in ('S1L','S1R','S2L','S2R','S3L','S3R'))


def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''): h.update(block)
    return h.hexdigest()


def load_sequences(dataset_path, *, jets=JETS, sample_stride=10, expected_dt_s=.001):
    path=Path(dataset_path).resolve()
    if not path.is_file(): raise FileNotFoundError(path)
    if tuple(jets)!=JETS: raise ValueError('B56 requires the six representative jets in fixed order')
    if type(sample_stride) is not int or sample_stride<1: raise ValueError('invalid stride')
    if not np.isfinite(expected_dt_s) or expected_dt_s<=0: raise ValueError('invalid dt')
    with path.open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f); headers=reader.fieldnames or []; rows=list(reader)
    if len(headers)!=len(set(headers)): raise ValueError('duplicate columns')
    missing=set((*BASE_COLUMNS,*OUTPUT_NAMES))-set(headers)
    if missing: raise ValueError(f'missing columns: {sorted(missing)}')
    # Reuse B54 numeric/column/baseline parser only after checking original order.
    times={}
    sources=set(); fingerprints=set(); support_fingerprints=set(); seen=set()
    for r in rows:
        key=(r['source_case_id'],r['segment_id'])
        times.setdefault(key,[]).append(float(r['physical_time']))
    raw_dt=expected_dt_s/sample_stride
    observed_dts=[]
    for key,t in times.items():
        if not np.isfinite(t).all(): raise ValueError(f'nonfinite time {key}')
        if len(t)>1: observed_dts.append(float(np.median(np.diff(t))))
        if len(t)>1 and not np.allclose(np.diff(t),raw_dt,rtol=1e-6,atol=raw_dt*1e-8):
            raise ValueError(f'time gap, duplicate, order or dt mismatch: {key}')
    segments=read_dataset(path,jets)
    for s in segments:
        if not s.case_id or not s.segment_id: raise ValueError('empty case/segment')
        nonrep=[j-1 for j in range(1,25) if j not in JETS]
        if np.any(np.abs(s.all_actual[:,nonrep])>1e-8): raise ValueError('nonrepresentative jet active')
        # Detect renamed copies by physical content, independent of labels and filenames.
        content=np.column_stack([s.time,s.all_actual,s.outputs]).astype('<f8').tobytes()
        fingerprints.add(hashlib.sha256(content).hexdigest())
        # Sliding 16-row supports detect cropped/resegmented renamed copies.
        # Ignore uninformative all-zero supports shared by independent initializations.
        physical=np.column_stack([s.time,s.all_actual,s.outputs]).astype('<f8')
        row_digests=[hashlib.sha256(row.tobytes()).digest() for row in physical]
        active=np.any(physical[:,1:]!=0,axis=1).astype(int)
        cs=np.r_[0,np.cumsum(active)]
        for k in range(len(s)-15):
            if cs[k+16]>cs[k]:
                support_fingerprints.add(hashlib.sha256(b''.join(row_digests[k:k+16])).hexdigest())
    for r in rows:
        t=float(r['physical_time']); case=r['source_case_id']
        source=r.get('source_timeseries_csv') or r.get('source_case_dir')
        if source: sources.add(str(Path(source).expanduser().resolve()))
        key=(source or case,t)
        if key in seen: raise ValueError('overlapping segments / duplicate source time')
        seen.add(key)
    source_hashes={source:sha256(source) for source in sources if Path(source).is_file()}
    provenance_path=path.with_suffix('.provenance.json')
    provenance=json.loads(provenance_path.read_text()) if provenance_path.exists() else {}
    cases=provenance.get('cases',{})
    raw_hashes={p['raw_sha256'] for p in cases.values() if p.get('raw_sha256')} | set(source_hashes.values())
    for p in cases.values():
        if p.get('source_path'): sources.add(str(Path(p['source_path']).resolve()))
    required=('raw_sha256','reference','template','initial_condition','sample_ownership_rule')
    complete=bool(rows) and all(all(cases.get(c,{}).get(k) for k in required)
                                    for c in {s.case_id for s in segments})
    sampled=[s.take(slice(None,None,sample_stride)) for s in segments]
    audit=dict(path=str(path),sha256=sha256(path),case_ids=sorted({s.case_id for s in segments}),
               source_paths=sorted(sources),source_file_hashes=source_hashes,raw_hashes=sorted(raw_hashes),
               observed_raw_dt_s=observed_dts,
               source_identifiers={key:sorted({r[key] for r in rows if r.get(key)})
                  for key in ('source_star_sim_file','source_star_sim_hash_sha256','source_git_commit','source_case_dir','source_raw_star_dir')},
               content_fingerprints=sorted(fingerprints),content_support_fingerprints=sorted(support_fingerprints),
               source_independence_verified=complete,source_independence_note='Missing provenance cannot prove independence of disjoint renamed sources.',raw_dt_s=raw_dt,effective_dt_s=expected_dt_s,
               sample_stride=sample_stride,subsample_phase=0,provenance=provenance,
               provenance_complete=complete,provenance_sha256=sha256(provenance_path) if provenance_path.exists() else None,
               input_names=INPUT_NAMES,output_names=OUTPUT_NAMES,
               segments=[dict(case=s.case_id,segment=s.segment_id,raw_rows=len(r),rows=len(s),
                              start_s=float(s.time[0]),end_s=float(s.time[-1]))
                         for r,s in zip(segments,sampled)],
               sampling_note='No filtering/interpolation; causal one-sample actuation lag. Edge alignment requires independent verification.')
    return sampled,audit


def check_isolation(a,b):
    for name in ('case_ids','source_paths','raw_hashes','content_fingerprints','content_support_fingerprints'):
        if set(a[name]) & set(b[name]): raise ValueError(f'split source overlap: {name}')
    if a['sha256']==b['sha256'] and a['case_ids']: raise ValueError('same dataset across splits')


def build_window_index(segments, *, input_lags=200, output_lags=10, horizon=20):
    if any(type(v) is not int or v<1 for v in (input_lags,output_lags,horizon)):
        raise ValueError('lags/horizon must be positive integers')
    lag=max(input_lags,output_lags)
    return [(i,k) for i,s in enumerate(segments) for k in range(lag,len(s)-horizon+1)]


def window(segment,k,*,input_lags=200,output_lags=10,horizon=1):
    if k<max(input_lags,output_lags) or k+horizon>len(segment): raise ValueError('window out of range')
    return (np.concatenate([segment.inputs[k-input_lags:k].ravel(),segment.outputs[k-output_lags:k].ravel()]),
            segment.outputs[k:k+horizon].copy())


def fit_scaler(segments):
    # Each source time appears only once (loader rejects overlaps); never weight by window count.
    result={}; constants={}
    for label,attr in [('input','inputs'),('output','outputs')]:
        x=np.vstack([getattr(s,attr) for s in segments])
        mean=x.mean(0); scale=x.std(0); constant=scale<1e-12; scale[constant]=1.
        result[label+'_mean']=mean.tolist(); result[label+'_scale']=scale.tolist()
        constants[label]=np.flatnonzero(constant).tolist()
    result['constant_columns']=constants
    return result
