"""合成系统检查；不代表 CFD 精度验证。"""
import json
import numpy as np
import pytest
from flow_control.rom.arx_model import ARXModel
from flow_control.rom.b54_real_rom import ScaledRealARXModel, REGION_FORCE_COLUMNS
from flow_control.rom.residual_rom import ResidualROM


def base():
    arx = ARXModel(input_lags=3, output_lags=1, include_current_input=False)
    arx.input_names_ = [f'actual_massflow_{j:02d}' for j in (2, 3, 9, 11, 17, 19)]
    arx.output_names_ = [f'delta_{s}' for s in REGION_FORCE_COLUMNS]
    arx._feature_at(3, np.zeros((4, 6)), np.zeros((4, 6)))
    arx.coefficients_ = np.zeros((25, 6))
    arx.coefficients_[1:7] = 0.4 * np.eye(6)
    return ScaledRealARXModel(arx, np.zeros(6), np.ones(6), np.zeros(6), np.ones(6))


def sequence(seed, n=400):
    u = np.random.default_rng(seed).uniform(-1, 1, (n, 6))
    y = np.zeros_like(u)
    for t in range(1, n):
        y[t] = .4*y[t-1] + .3*u[t-1]**2 + .1*u[t-1]*np.roll(u[t-1], 1)
    return u, y


def test_zero_branch_matches_arx_and_roundtrip():
    b = base(); m = ResidualROM(b, dt_s=.001)
    u, y = sequence(1)
    expected = b.predict_rolling(u, y)
    np.testing.assert_allclose(m.rollout(u, y[:3], dt_s=.001), expected[3:], atol=1e-12)
    restored = ResidualROM.from_dict(json.loads(json.dumps(m.to_dict())))
    np.testing.assert_allclose(m.rollout(u, y[:3]), restored.rollout(u, y[:3]))
    with pytest.raises(ValueError, match='dt'):
        m.rollout(u, y[:3], dt_s=.002)
    with pytest.raises(ValueError):
        m.rollout(u, y[:2])


def test_residual_learns_and_base_is_frozen():
    m = ResidualROM(base(), dt_s=.001, hidden=24)
    before = m.linear.arx.coefficients_.copy()
    u, y = sequence(3, 800)
    losses = m.fit([(u, y)], epochs=100, batch_size=128, learning_rate=.01)
    assert losses[-1] < losses[0] * .2
    np.testing.assert_array_equal(before, m.linear.arx.coefficients_)
    uv, yv = sequence(4)
    pred = m.rollout(uv, yv[:3])
    baseline = m.linear.predict_rolling(uv, yv)[3:]
    assert np.mean((pred-yv[3:])**2) < .2*np.mean((baseline-yv[3:])**2)
    restored = ResidualROM.from_dict(m.to_dict())
    np.testing.assert_allclose(pred, restored.rollout(uv, yv[:3]))


def test_causality_and_segment_boundaries():
    m = ResidualROM(base(), dt_s=.001)
    u, y = sequence(2, 30)
    m.fit([(u[:15], y[:15]), (u[15:], y[15:])], epochs=1)
    assert m.fit_rows == 24
    one = m.predict_one_step(u, y)
    changed_u = u.copy(); changed_u[20:] += 100
    changed_y = y.copy(); changed_y[20:] += 100
    np.testing.assert_allclose(one[3:21], m.predict_one_step(changed_u, changed_y)[3:21])
    with pytest.raises(ValueError):
        m.fit([(u[:3], y[:3])])


def test_workflow_roundtrip_and_provenance_gates(tmp_path):
    import csv
    import hashlib
    import yaml
    from flow_control.rom.b54_from_b53 import REQUIRED_COLUMNS
    from flow_control.rom.train_residual_rom import run
    for label, seed in [('training', 5), ('validation', 6)]:
        u, y = sequence(seed, 50)
        with (tmp_path/f'{label}.csv').open('w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=REQUIRED_COLUMNS); w.writeheader()
            for t in range(len(u)):
                row = dict.fromkeys(REQUIRED_COLUMNS, 0)
                row.update(source_case_id=label, segment_id=label, physical_time=(t+1)*.001,
                           event_id='event', jet_id='J02', phase='actuation', action_start_s=0,
                           baseline_mode='reference_time_aligned')
                row.update(dict(zip(base().arx.input_names_, u[t])))
                row.update(dict(zip(base().arx.output_names_, y[t])))
                w.writerow(row)
    snap = base().to_dict()
    snap.update(sample_stride=1, effective_dt_s=.001, training_data=dict(case_ids=['training'],
                dataset_sha256=hashlib.sha256((tmp_path/'training.csv').read_bytes()).hexdigest()))
    (tmp_path/'base.json').write_text(json.dumps(snap))
    cfg = dict(project_root=str(tmp_path), linear_model='base.json', training_dataset='training.csv',
               validation_dataset='validation.csv', optimizer=dict(epochs=2))
    path = tmp_path/'config.yaml'; path.write_text(yaml.safe_dump(cfg))
    result = run(path, tmp_path/'out')
    assert result['status'] == 'VALIDATED'
    assert len(result['validation']['rmse_N']['hybrid_rollout']) == 6
    model = ResidualROM.from_dict(json.loads((tmp_path/'out/model.json').read_text()))
    assert model.fit_rows == 47
    with pytest.raises(FileExistsError):
        run(path, tmp_path/'out')
    # 验证标签改变不能改变训练权重。
    val = tmp_path/'validation.csv'
    rows = list(csv.DictReader(val.open()))
    for row in rows:
        for name in base().arx.output_names_:
            row[name] = str(float(row[name])+5)
    with val.open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=REQUIRED_COLUMNS); w.writeheader(); w.writerows(rows)
    run(path, tmp_path/'out2')
    snapshot2 = json.loads((tmp_path/'out2/model.json').read_text())
    np.testing.assert_array_equal(model.w1, snapshot2['w1'])
    np.testing.assert_array_equal(model.w2, snapshot2['w2'])
    snap['training_data']['dataset_sha256'] = 'wrong'
    (tmp_path/'base.json').write_text(json.dumps(snap))
    with pytest.raises(ValueError, match='hash'):
        run(path, tmp_path/'out3')
    assert not (tmp_path/'out3').exists()
