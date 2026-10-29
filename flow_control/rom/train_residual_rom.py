"""B55：固定已有 B54 模型，训练非线性残差并报告独立验证结果。"""
from __future__ import annotations
import argparse
import csv
import json
from pathlib import Path
import numpy as np
import yaml
from .b54_from_b53 import read_dataset, check_partitions, subsample, _sha256
from .b54_real_rom import ScaledRealARXModel
from .residual_rom import ResidualROM


def run(config_path, output_dir):
    config_path = Path(config_path).resolve()
    cfg = yaml.safe_load(config_path.read_text(encoding='utf-8'))
    root = Path(cfg['project_root'])
    if not root.is_absolute():
        root = (config_path.parent / root).resolve()
    def resolve(value):
        p = Path(value)
        return p if p.is_absolute() else root / p
    train_path, val_path = resolve(cfg['training_dataset']), resolve(cfg['validation_dataset'])
    base_path = resolve(cfg['linear_model'])
    payload = json.loads(base_path.read_text(encoding='utf-8'))
    linear = ScaledRealARXModel.from_dict(payload)
    provenance = payload.get('training_data', {})
    # 禁止用来源未知或包含验证数据的线性快照形成表面独立的混合模型。
    if provenance.get('dataset_sha256') != _sha256(train_path):
        raise ValueError('linear model training dataset hash differs from training_dataset')
    jets = [int(name.rsplit('_', 1)[1]) for name in linear.arx.input_names_]
    training = read_dataset(train_path, jets)
    if not training:
        raise ValueError('training dataset is empty')
    if not val_path.is_file():
        raise ValueError('validation dataset is missing (use an existing empty B53 CSV for TRAINING_ONLY)')
    validation = read_dataset(val_path, jets)
    timing = check_partitions(training, validation, dict(representative_jets=jets,
                                                       massflow_active_threshold_kg_s=1e-8))
    if set(provenance.get('case_ids', [])) != {s.case_id for s in training}:
        raise ValueError('linear model training case provenance differs')
    stride = payload.get('sample_stride')
    if type(stride) is not int or stride < 1:
        raise ValueError('linear snapshot needs positive sample_stride')
    dt = timing['raw_dt_s']*stride
    if not np.isclose(dt, payload.get('effective_dt_s', np.nan), rtol=1e-6, atol=0):
        raise ValueError('linear snapshot effective dt differs from dataset')
    training, validation = subsample(training, stride), subsample(validation, stride)
    if any(len(s) <= linear.arx.max_lag for s in training+validation):
        raise ValueError('each segment must exceed max_lag; short segments cannot be silently dropped')
    model = ResidualROM(linear, dt_s=dt, **cfg.get('network', {}))
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=False)
    # 先落盘来源，即使训练失败也保留可排查的输入记录。
    manifest = dict(config=cfg, linear_sha256=_sha256(base_path), training_sha256=_sha256(train_path),
                    validation_sha256=_sha256(val_path), effective_dt_s=dt, sample_stride=stride)
    (out/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    losses = model.fit([(s.inputs, s.outputs) for s in training], **cfg.get('optimizer', {}))
    snapshot = model.to_dict()
    snapshot['provenance'] = manifest
    (out/'model.json').write_text(json.dumps(snapshot, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    metrics = dict(status='VALIDATED' if validation else 'TRAINING_ONLY',
                   interpretation='Evaluation performed; not an acceptance decision or CFD replacement certification.',
                   fit_rows=model.fit_rows, training_loss=losses)
    for label, segments in [('training', training), ('validation', validation)]:
        errors = {k: [] for k in ('linear_one_step', 'linear_rollout', 'hybrid_one_step', 'hybrid_rollout')}
        failures = []
        with (out/f'{label}_predictions.csv').open('w', newline='', encoding='utf-8') as f:
            names = linear.arx.output_names_
            fields = ['source_case_id', 'segment_id', 'physical_time'] + [f'{kind}_{name}' for kind in ('truth', *errors) for name in names]
            writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader()
            for s in segments:
                lag = model.max_lag
                predictions = dict(linear_one_step=linear.predict_one_step(s.inputs, s.outputs)[lag:],
                                   hybrid_one_step=model.predict_one_step(s.inputs, s.outputs)[lag:])
                for key, predictor in [('linear_rollout', ResidualROM(linear, dt_s=dt)), ('hybrid_rollout', model)]:
                    try:
                        predictions[key] = predictor.rollout(s.inputs, s.outputs[:lag], dt_s=dt)
                    except FloatingPointError as exc:
                        predictions[key] = np.full_like(s.outputs[lag:], np.nan)
                        failures.append(dict(segment_id=s.segment_id, model=key, error=str(exc)))
                for key in errors:
                    errors[key].append(predictions[key]-s.outputs[lag:])
                for i, t in enumerate(range(lag, len(s))):
                    row = dict(source_case_id=s.case_id, segment_id=s.segment_id, physical_time=float(s.time[t]))
                    for key, values in dict(truth=s.outputs[lag:], **predictions).items():
                        row.update({f'{key}_{name}': float(v) if np.isfinite(v) else '' for name, v in zip(names, values[i])})
                    writer.writerow(row)
        report = dict(segments=len(segments), rollout_failures=failures, rmse_N={})
        for key, chunks in errors.items():
            arr = np.vstack(chunks) if chunks else np.empty((0, 6))
            report['rmse_N'][key] = np.sqrt(np.mean(arr**2, axis=0)).tolist() if len(arr) and np.isfinite(arr).all() else None
        metrics[label] = report
    if any(metrics[k]['rollout_failures'] for k in ('training', 'validation')):
        metrics['status'] = 'ROLLOUT_FAILED'
    (out/'metrics.json').write_text(json.dumps(metrics, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
    return metrics


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='configs/b55/residual_rom.yaml')
    parser.add_argument('--output-dir', required=True, help='必须是不存在的新目录')
    args = parser.parse_args(argv)
    try:
        result = run(args.config, args.output_dir)
    except (ValueError, OSError, FloatingPointError) as exc:
        parser.exit(2, f'B55 failed: {exc}\n')
    print(json.dumps(dict(status=result['status'], output_dir=str(Path(args.output_dir).resolve())), ensure_ascii=False))
    return 1 if result['status'] == 'ROLLOUT_FAILED' else 0
