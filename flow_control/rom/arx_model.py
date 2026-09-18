"""最小化多输出 ARX 模型 —— 用于输入-输出 ROM 辨识。

ARX（Auto-Regressive with eXogenous inputs）模型的数学形式：

    y[t] = c + A_1 * y[t-1] + ... + A_na * y[t-na]
           + B_0 * u[t] + ... + B_nb * u[t-nb+1]

其中：
  - y[t] 是当前时刻的输出（载荷），维度 n_outputs
  - y[t-k] 是 k 步前的输出（自回归部分）
  - u[t-k] 是 k 步前的输入（喷气指令，外生部分）
  - c 是常数偏置项
  - A_k 和 B_k 是待拟合的系数矩阵

使用岭回归（ridge-regularized least squares）求解。

递推预测（recursive prediction）：
  在验证阶段，前 max_lag 行使用真实输出作为历史；
  之后的预测完全使用模型自身的输出作为反馈，不使用真实值。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class ARXModel:
    """小型多输出 ARX 模型，使用岭回归最小二乘法拟合。

    输入维度：n_input_features（如 JET_01..JET_24 + cmd_massflow_01..24）
    输出维度：n_output_features（如 Fz_S1L..Fz_S3R + Fz_Total）
    特征维度：1（偏置） + n_outputs * output_lags + n_inputs * input_lags
    """

    # --- 模型超参数 ---
    input_lags: int = 4           # 输入滞后步数（含当前步：u[t], u[t-1], ...）
    output_lags: int = 3          # 输出滞后步数（y[t-1], y[t-2], ...）
    include_current_input: bool = True  # 是否包含当前输入 u[t]
    ridge_alpha: float = 1.0e-8   # 岭回归正则化系数，防止过拟合
    coefficients_: np.ndarray | None = field(default=None, init=False, repr=False)
    feature_names_: list[str] = field(default_factory=list, init=False)
    input_names_: list[str] = field(default_factory=list, init=False)
    output_names_: list[str] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        """初始化后验证超参数合法性。"""
        if self.input_lags < 1:
            raise ValueError("input_lags must be at least 1")
        if self.output_lags < 1:
            raise ValueError("output_lags must be at least 1")
        if self.ridge_alpha < 0:
            raise ValueError("ridge_alpha must be non-negative")

    @property
    def max_lag(self) -> int:
        """返回模型所需的最大历史步数。

        设计矩阵需要至少 max_lag 行历史数据才能构造第一行有效特征。
        如果 include_current_input=True（默认），
          输入历史滞后 = input_lags - 1
        否则
          输入历史滞后 = input_lags
        """
        input_history_lag = self.input_lags - 1 if self.include_current_input else self.input_lags
        return max(self.output_lags, input_history_lag)

    def fit(
        self,
        inputs: np.ndarray,
        outputs: np.ndarray,
        *,
        input_names: list[str] | tuple[str, ...] | None = None,
        output_names: list[str] | tuple[str, ...] | None = None,
    ) -> "ARXModel":
        """在显式提供的全部可用行上拟合模型。

        使用带岭正则化的最小二乘法求解：
          coefficients = (X^T X + λI)^{-1} X^T y

        Args:
            inputs: [n_rows × n_inputs] 输入数组。
            outputs: [n_rows × n_outputs] 输出数组。
            input_names: 输入列名列表。
            output_names: 输出列名列表。

        Returns:
            已拟合的 ARXModel 实例。
        """
        inputs = _as_2d_float(inputs, "inputs")
        outputs = _as_2d_float(outputs, "outputs")
        if inputs.shape[0] != outputs.shape[0]:
            raise ValueError("inputs and outputs must have the same row count")

        if inputs.shape[0] <= self.max_lag:
            raise ValueError(
                "training data is too short: "
                f"need more than max_lag={self.max_lag}, got {inputs.shape[0]}"
            )

        self.input_names_ = list(input_names or [f"u{idx}" for idx in range(inputs.shape[1])])
        self.output_names_ = list(output_names or [f"y{idx}" for idx in range(outputs.shape[1])])
        x_train, y_train = self._design_matrix(inputs, outputs, self.max_lag, inputs.shape[0])
        # 构造岭回归正规方程 (X^T X + λI) * w = X^T y
        penalty = self.ridge_alpha * np.eye(x_train.shape[1], dtype=float)
        penalty[0, 0] = 0.0  # 不对偏置项（intercept）做正则化
        lhs = x_train.T @ x_train + penalty
        rhs = x_train.T @ y_train
        try:
            self.coefficients_ = np.linalg.solve(lhs, rhs)
        except np.linalg.LinAlgError:
            # 矩阵奇异时使用伪逆
            self.coefficients_ = np.linalg.pinv(lhs) @ rhs
        return self

    def predict_one_step(self, inputs: np.ndarray, outputs: np.ndarray) -> np.ndarray:
        """使用真实历史输出进行单步预测（非递推）。

        每一行的预测仅使用该行之前 max_lag 步的真实输出。
        结果的前 max_lag 行为 NaN（没有足够历史数据）。

        Args:
            inputs: [n_rows × n_inputs] 输入。
            outputs: [n_rows × n_outputs] 真实输出（用作历史）。

        Returns:
            [n_rows × n_outputs] 预测值，前 max_lag 行为 NaN。
        """
        self._require_fitted()
        inputs = _as_2d_float(inputs, "inputs")
        outputs = _as_2d_float(outputs, "outputs")
        predictions = np.full_like(outputs, np.nan, dtype=float)
        x_all, _ = self._design_matrix(inputs, outputs, self.max_lag, inputs.shape[0])
        predictions[self.max_lag :] = x_all @ self.coefficients_
        return predictions

    def predict_recursive(
        self,
        inputs: np.ndarray,
        observed_outputs: np.ndarray,
        *,
        start_index: int,
        end_index: int | None = None,
    ) -> np.ndarray:
        """从 start_index 开始递推预测（自举模式）。

        递推预测的关键行为：
          - start_index 之前的行使用 observed_outputs 中的真实值
          - start_index 开始后的行，使用模型自身上一步的输出作为下一步的历史
          - 也就是说，模型不依赖后续的真实测量值，"自举"地向前推进

        Args:
            inputs: [n_rows × n_inputs] 外部输入。
            observed_outputs: [n_rows × n_outputs] 真实输出（用于历史初始化）。
            start_index: 开始递推的行索引（必须 >= max_lag）。
            end_index: 结束索引（不含，默认到末尾）。

        Returns:
            [(end - start) × n_outputs] 递推预测结果。
        """
        self._require_fitted()
        inputs = _as_2d_float(inputs, "inputs")
        observed_outputs = _as_2d_float(observed_outputs, "observed_outputs")
        if inputs.shape[0] != observed_outputs.shape[0]:
            raise ValueError("inputs and observed_outputs must have the same row count")
        start = int(start_index)
        end = inputs.shape[0] if end_index is None else int(end_index)
        if start < self.max_lag:
            raise ValueError(f"start_index must be >= max_lag={self.max_lag}")
        if end < start or end > inputs.shape[0]:
            raise ValueError("end_index must satisfy start_index <= end_index <= row count")

        # work_outputs 是工作副本：前半段保持真实值，后半段逐步被预测值覆盖
        work_outputs = np.asarray(observed_outputs, dtype=float).copy()
        predictions = np.full((end - start, observed_outputs.shape[1]), np.nan, dtype=float)
        for row_idx, t in enumerate(range(start, end)):
            feature = self._feature_at(t, inputs, work_outputs)
            y_hat = feature @ self.coefficients_
            work_outputs[t] = y_hat
            predictions[row_idx] = y_hat
        return predictions

    def to_dict(self) -> dict[str, Any]:
        """将模型序列化为 JSON 兼容的字典。

        Returns:
            包含模型参数、超参数、系数矩阵的字典。
        """
        self._require_fitted()
        return {
            "model_type": "ARX",
            "input_lags": self.input_lags,
            "output_lags": self.output_lags,
            "include_current_input": self.include_current_input,
            "ridge_alpha": self.ridge_alpha,
            "input_names": self.input_names_,
            "output_names": self.output_names_,
            "feature_names": self.feature_names_,
            "coefficients": self.coefficients_.tolist(),
            "formula": (
                "y[t] = c + sum_i A_i*y[t-i] + "
                "sum_j B_j*u[t-j], with recursive validation feedback"
            ),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ARXModel":
        """从 to_dict() 的序列化输出恢复模型。

        Args:
            payload: to_dict() 输出的字典。

        Returns:
            恢复的 ARXModel 实例。
        """
        if payload.get("model_type") != "ARX":
            raise ValueError("model snapshot must have model_type='ARX'")
        model = cls(
            input_lags=int(payload["input_lags"]),
            output_lags=int(payload["output_lags"]),
            include_current_input=bool(payload.get("include_current_input", True)),
            ridge_alpha=float(payload.get("ridge_alpha", 0.0)),
        )
        model.input_names_ = list(payload.get("input_names", []))
        model.output_names_ = list(payload.get("output_names", []))
        model.feature_names_ = list(payload.get("feature_names", []))
        model.coefficients_ = np.asarray(payload["coefficients"], dtype=float)
        return model

    def _design_matrix(
        self,
        inputs: np.ndarray,
        outputs: np.ndarray,
        start: int,
        end: int,
    ) -> tuple[np.ndarray, np.ndarray]:
        """构造设计矩阵 X 和目标向量 y。

        对从 start 到 end 的每个时间步 t，构造特征向量：
          [1, y[t-1], ..., y[t-na], u[t], u[t-1], ..., u[t-nb]]

        Args:
            inputs: [n_rows × n_inputs] 输入序列。
            outputs: [n_rows × n_outputs] 输出序列。
            start: 起始行（包含）。
            end: 结束行（不含）。

        Returns:
            (X, y) 元组。
        """
        rows = [self._feature_at(t, inputs, outputs) for t in range(start, end)]
        return np.vstack(rows), outputs[start:end]

    def _feature_at(self, t: int, inputs: np.ndarray, outputs: np.ndarray) -> np.ndarray:
        """构造第 t 行的特征向量。

        特征顺序：
          1. 偏置项（intercept = 1.0）
          2. 输出滞后：y[t-1], y[t-2], ..., y[t-na]
          3. 输入滞后：u[t], u[t-1], ..., u[t-nb]

        Args:
            t: 当前时间步。
            inputs: 输入数组。
            outputs: 输出数组。

        Returns:
            特征向量。
        """
        values: list[float] = [1.0]
        names: list[str] = ["intercept"]

        # 输出滞后项：y[t-1], y[t-2], ...
        for lag in range(1, self.output_lags + 1):
            for col_idx, name in enumerate(self.output_names_ or [f"y{idx}" for idx in range(outputs.shape[1])]):
                values.append(float(outputs[t - lag, col_idx]))
                names.append(f"{name}(t-{lag})")

        # 输入滞后项：u[t], u[t-1], ...（可选择是否包含 u[t]）
        start_lag = 0 if self.include_current_input else 1
        stop_lag = start_lag + self.input_lags
        for lag in range(start_lag, stop_lag):
            suffix = "t" if lag == 0 else f"t-{lag}"
            for col_idx, name in enumerate(self.input_names_ or [f"u{idx}" for idx in range(inputs.shape[1])]):
                values.append(float(inputs[t - lag, col_idx]))
                names.append(f"{name}({suffix})")

        if not self.feature_names_:
            self.feature_names_ = names
        return np.asarray(values, dtype=float)

    def _require_fitted(self) -> None:
        """检查模型是否已拟合，否则抛出异常。"""
        if self.coefficients_ is None:
            raise RuntimeError("ARXModel has not been fitted")


def _as_2d_float(values: np.ndarray, name: str) -> np.ndarray:
    """验证输入并转换为 2D float64 数组。

    Args:
        values: 输入数组。
        name: 变量名（用于错误消息中的标识）。

    Returns:
        验证通过的 2D float64 数组。

    Raises:
        ValueError: 如果不是 2D 或含有 NaN/Inf。
    """
    array = np.asarray(values, dtype=float)
    if array.ndim != 2:
        raise ValueError(f"{name} must be a 2-D array")
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{name} contains NaN or infinite values")
    return array
