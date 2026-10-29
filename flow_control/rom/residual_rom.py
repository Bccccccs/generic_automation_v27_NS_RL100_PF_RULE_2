"""固定 B54 ARX + 输入驱动非线性残差；纯 NumPy，无额外深度学习依赖。"""
from __future__ import annotations

import numpy as np
from .b54_real_rom import ScaledRealARXModel
from .b54_from_b53 import Hyper, _design


def _matrix(value, name):
    a = np.asarray(value, dtype=float)
    if a.ndim != 2 or a.shape[1] != 6 or not np.isfinite(a).all():
        raise ValueError(f'{name} must be a finite (N, 6) matrix')
    return a


class ResidualROM:
    """y_hat = ARX(history) + output_scale * MLP(input_history).

    非线性分支不读取 y，保持原 ARX 的反馈极点；这不保证原 ARX 稳定。
    rollout 只接收初始化输出，不需要未来真实输出。所有数组单位沿用 B53。
    """
    def __init__(self, linear: ScaledRealARXModel, *, dt_s: float, hidden: int = 32, seed: int = 42):
        if not np.isfinite(dt_s) or dt_s <= 0 or type(hidden) is not int or hidden < 1:
            raise ValueError('dt_s must be positive; hidden must be a positive integer')
        # 独立副本，训练不会修改调用方的线性模型。
        self.linear = ScaledRealARXModel.from_dict(linear.to_dict())
        self.dt_s, self.hidden, self.seed = float(dt_s), hidden, seed
        n = linear.arx.input_lags
        self.windows = sorted(set([1, n] + [k for k in (5, 20, 50, 100) if k <= n]))
        # 多尺度平均 + 最近输入平方 + 15 个两两乘积。
        d = len(self.windows)*6 + 6 + 15
        rng = np.random.default_rng(seed)
        self.w1 = rng.normal(0, 1/np.sqrt(d), (d, hidden))
        self.b1 = np.zeros(hidden)
        self.w2 = np.zeros((hidden, 6))  # 初始化严格退化为原 ARX
        self.b2 = np.zeros(6)
        self.feature_mean = np.zeros(d)
        self.feature_scale = np.ones(d)
        self.fit_rows = 0

    @property
    def max_lag(self):
        return self.linear.arx.max_lag

    def _features(self, u):
        first = 0 if self.linear.arx.include_current_input else 1
        end = np.arange(self.max_lag, len(u)) - first + 1
        cs = np.vstack([np.zeros((1, 6)), np.cumsum(u, axis=0)])
        recent = u[end-1]
        pairs = np.column_stack([recent[:, i]*recent[:, j] for i in range(6) for j in range(i+1, 6)])
        return np.hstack([(cs[end]-cs[end-k])/k for k in self.windows] + [recent**2, pairs])

    def _prepare(self, inputs, outputs):
        u, y = _matrix(inputs, 'inputs'), _matrix(outputs, 'outputs')
        if len(u) != len(y) or len(u) <= self.max_lag:
            raise ValueError('sequence must have matching rows and exceed max_lag')
        b = self.linear
        return (u-b.input_mean)/b.input_scale, (y-b.output_mean)/b.output_scale

    def _design(self, u, y):
        a = self.linear.arx
        return _design(u, y, Hyper(a.input_lags, a.output_lags, a.ridge_alpha), a.include_current_input)

    def _forward(self, features):
        x = (features-self.feature_mean)/self.feature_scale
        return np.tanh(x @ self.w1 + self.b1) @ self.w2 + self.b2

    def fit(self, sequences, *, epochs=200, batch_size=256, learning_rate=.001, weight_decay=1e-4):
        """只传训练片段；固定 epoch，不利用外部验证集选择权重或标准化。"""
        if type(epochs) is not int or epochs < 1 or type(batch_size) is not int or batch_size < 1:
            raise ValueError('epochs and batch_size must be positive integers')
        if not np.isfinite([learning_rate, weight_decay]).all() or learning_rate <= 0 or weight_decay < 0:
            raise ValueError('invalid optimizer settings')
        features, targets = [], []
        for inputs, outputs in sequences:
            u, y = self._prepare(inputs, outputs)
            x, target = self._design(u, y)
            features.append(self._features(u))
            targets.append(target-x @ self.linear.arx.coefficients_)
        if not features:
            raise ValueError('no training segments')
        x, target = np.vstack(features), np.vstack(targets)
        self.feature_mean = x.mean(axis=0)
        self.feature_scale = x.std(axis=0)
        self.feature_scale[self.feature_scale < 1e-12] = 1
        x = (x-self.feature_mean)/self.feature_scale
        self.fit_rows = len(x)
        params = [self.w1, self.b1, self.w2, self.b2]
        m, v = [np.zeros_like(p) for p in params], [np.zeros_like(p) for p in params]
        rng, step, losses = np.random.default_rng(self.seed), 0, []
        for _ in range(epochs):
            order = rng.permutation(len(x))
            for start in range(0, len(x), batch_size):
                idx = order[start:start+batch_size]
                xb, yb = x[idx], target[idx]
                h = np.tanh(xb @ self.w1 + self.b1)
                error = h @ self.w2 + self.b2 - yb
                g = 2*error/error.size
                dh = (g @ self.w2.T)*(1-h*h)
                grads = [xb.T @ dh + weight_decay*self.w1, dh.sum(0),
                         h.T @ g + weight_decay*self.w2, g.sum(0)]
                step += 1
                for i, (p, grad) in enumerate(zip(params, grads)):
                    m[i] = .9*m[i]+.1*grad
                    v[i] = .999*v[i]+.001*grad*grad
                    p -= learning_rate*(m[i]/(1-.9**step))/(np.sqrt(v[i]/(1-.999**step))+1e-8)
            loss = float(np.mean((np.tanh(x @ self.w1+self.b1) @ self.w2+self.b2-target)**2))
            if not np.isfinite(loss):
                raise FloatingPointError('non-finite residual training loss')
            losses.append(loss)
        return losses

    def predict_one_step(self, inputs, outputs):
        u, y = self._prepare(inputs, outputs)
        x, _ = self._design(u, y)
        result = np.full_like(y, np.nan)
        result[self.max_lag:] = (x @ self.linear.arx.coefficients_ + self._forward(self._features(u)))*self.linear.output_scale+self.linear.output_mean
        return result

    def rollout(self, inputs, initial_outputs, *, dt_s=None):
        """inputs 含初始历史与未来流量；initial_outputs 必须恰为 max_lag 行。

        返回历史之后的 (N-max_lag, 6) ΔFz。输入必须按快照列序、采样间隔排列。
        """
        if dt_s is not None and not np.isclose(dt_s, self.dt_s, rtol=1e-6, atol=0):
            raise ValueError('dt_s differs from trained effective dt')
        u, history = _matrix(inputs, 'inputs'), _matrix(initial_outputs, 'initial_outputs')
        if len(history) != self.max_lag or len(u) <= self.max_lag:
            raise ValueError('initial_outputs must have max_lag rows; inputs must be longer')
        b, a = self.linear, self.linear.arx
        u = (u-b.input_mean)/b.input_scale
        work = np.zeros((len(u), 6))
        work[:self.max_lag] = (history-b.output_mean)/b.output_scale
        correction = self._forward(self._features(u))
        for i, t in enumerate(range(self.max_lag, len(u))):
            work[t] = a._feature_at(t, u, work) @ a.coefficients_ + correction[i]
            if not np.isfinite(work[t]).all() or np.max(np.abs(work[t])) > 1e12:
                raise FloatingPointError(f'recursive prediction diverged at row {t}')
        return work[self.max_lag:]*b.output_scale+b.output_mean

    def to_dict(self):
        return dict(model_type='B55_input_residual_ARX_v1', linear=self.linear.to_dict(),
                    dt_s=self.dt_s, hidden=self.hidden, seed=self.seed, fit_rows=self.fit_rows,
                    **{k: getattr(self, k).tolist() for k in ('w1', 'b1', 'w2', 'b2', 'feature_mean', 'feature_scale')})

    @classmethod
    def from_dict(cls, payload):
        if payload.get('model_type') != 'B55_input_residual_ARX_v1':
            raise ValueError('unsupported residual model')
        model = cls(ScaledRealARXModel.from_dict(payload['linear']), dt_s=payload['dt_s'],
                    hidden=payload['hidden'], seed=payload['seed'])
        for name in ('w1', 'b1', 'w2', 'b2', 'feature_mean', 'feature_scale'):
            value = np.asarray(payload[name], dtype=float)
            if value.shape != getattr(model, name).shape or not np.isfinite(value).all():
                raise ValueError(f'invalid {name}')
            setattr(model, name, value.copy())
        if np.any(model.feature_scale <= 0):
            raise ValueError('feature_scale must be positive')
        model.fit_rows = int(payload['fit_rows'])
        return model
