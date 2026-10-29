import csv
import numpy as np
import pytest
from flow_control.rom.probabilistic_data import (
    load_sequences, build_window_index, window, fit_scaler, check_isolation, INPUT_NAMES, OUTPUT_NAMES)


def dataset(path, *, case='a', n=240, dt=.001, shift=0):
    fields = ['source_case_id','segment_id','physical_time','event_id','jet_id','phase',
              'action_start_s','baseline_mode','source_timeseries_csv']
    fields += [f'actual_massflow_{j:02}' for j in range(1,25)] + list(OUTPUT_NAMES)
    with path.open('w', newline='') as f:
        w=csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for k in range(n):
            r=dict.fromkeys(fields, '0')
            r.update(source_case_id=case, segment_id='s', physical_time=k*dt,
                     baseline_mode='reference_time_aligned', source_timeseries_csv=f'/raw/{case}.csv')
            for j,name in enumerate(INPUT_NAMES): r[name]=k+j+shift
            for j,name in enumerate(OUTPUT_NAMES): r[name]=2*k+j+shift
            w.writerow(r)
    return path


def rewrite(path, fn):
    with path.open() as f:
        reader=csv.DictReader(f); fields=reader.fieldnames; rows=list(reader)
    fn(rows,fields)
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)


def load(path): return load_sequences(path, sample_stride=1, expected_dt_s=.001)


def test_causal_window_and_horizon(tmp_path):
    seg,a=load(dataset(tmp_path/'a.csv'))
    idx=build_window_index(seg,input_lags=200,output_lags=10,horizon=20)
    assert idx[0]==(0,200) and idx[-1]==(0,220)
    x,y=window(seg[0],200,input_lags=200,output_lags=10,horizon=2)
    np.testing.assert_array_equal(x[:1200].reshape(200,6),seg[0].inputs[:200])
    np.testing.assert_array_equal(x[1200:].reshape(10,6),seg[0].outputs[190:200])
    seg[0].inputs[200:]=1e9; seg[0].outputs[200:]=1e9
    np.testing.assert_array_equal(window(seg[0],200,input_lags=200,output_lags=10,horizon=2)[0],x)
    assert y.shape==(2,6)


@pytest.mark.parametrize('bad', ['gap','duplicate','reverse','nan','missing','otherjet','dt'])
def test_bad_data(tmp_path,bad):
    p=dataset(tmp_path/'a.csv')
    def change(rows, fields):
        if bad=='gap': rows[10]['physical_time']='.0105'
        if bad=='duplicate': rows[10]['physical_time']=rows[9]['physical_time']
        if bad=='reverse': rows.reverse()
        if bad=='nan': rows[10][OUTPUT_NAMES[0]]='nan'
        if bad=='missing':
            fields.remove(INPUT_NAMES[0])
            for r in rows: r.pop(INPUT_NAMES[0])
        if bad=='otherjet': rows[10]['actual_massflow_01']='.01'
        if bad=='dt':
            for k,r in enumerate(rows): r['physical_time']=k*.002
    rewrite(p,change)
    with pytest.raises(ValueError): load(p)


def test_column_reordering_short_empty_missing(tmp_path):
    p=dataset(tmp_path/'a.csv', n=10)
    rewrite(p, lambda rows,fields: fields.reverse())
    s,a=load(p)
    assert s[0].inputs[0,1]==1
    assert not build_window_index(s,input_lags=200,output_lags=10,horizon=1)
    assert load(dataset(tmp_path/'empty.csv',n=0))[0]==[]
    with pytest.raises(FileNotFoundError): load(tmp_path/'missing.csv')


def test_isolation_and_training_scaler(tmp_path):
    a,aa=load(dataset(tmp_path/'a.csv'))
    b,bb=load(dataset(tmp_path/'b.csv',case='b',shift=9000))
    scaler=fit_scaler(a)
    assert scaler['input_mean'][0]==119.5
    check_isolation(aa,bb)
    rewrite(tmp_path/'b.csv',lambda rows,fields: [r.update(source_timeseries_csv='/raw/a.csv') for r in rows])
    _,bb=load(tmp_path/'b.csv')
    with pytest.raises(ValueError): check_isolation(aa,bb)
    np.testing.assert_equal(scaler,fit_scaler(a))


def test_renamed_copy_detected(tmp_path):
    _,a=load(dataset(tmp_path/'a.csv'))
    p=dataset(tmp_path/'b.csv',case='b')
    _,b=load(p)
    with pytest.raises(ValueError): check_isolation(a,b)


def test_case_segment_isolation_and_stride(tmp_path):
    p=dataset(tmp_path/'a.csv',n=450,dt=.0001)
    rewrite(p,lambda rows,fields:[r.update(segment_id='late') for r in rows[225:]])
    s,a=load_sequences(p,sample_stride=10)
    assert len(s)==2 and len(s[0])==23
    assert not build_window_index(s,input_lags=30,output_lags=2,horizon=1)
    assert a['subsample_phase']==0 and a['raw_dt_s']==pytest.approx(.0001)


def test_raw_hash_alias_isolation(tmp_path):
    p=dataset(tmp_path/'a.csv'); q=dataset(tmp_path/'b.csv',case='b',shift=300)
    import json
    for file,c in [(p,'a'),(q,'b')]:
        file.with_suffix('.provenance.json').write_text(json.dumps({'cases':{c:{'raw_sha256':'same'}}}))
    _,a=load(p); _,b=load(q)
    with pytest.raises(ValueError,match='raw_hashes'): check_isolation(a,b)


@pytest.mark.parametrize('start,stop',[(0,120),(60,180)])
def test_renamed_partial_copy_rejected(tmp_path,start,stop):
    _,a=load(dataset(tmp_path/'a.csv'))
    q=dataset(tmp_path/'b.csv',case='b')
    rewrite(q,lambda rows,fields: rows.__setitem__(slice(None),rows[start:stop]))
    _,b=load(q)
    with pytest.raises(ValueError,match='support'): check_isolation(a,b)
