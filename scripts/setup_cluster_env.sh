#!/bin/bash
# 在集群登录节点一次性创建 py3.10 环境并安装依赖。
# 用法：bash scripts/setup_cluster_env.sh   （在仓库根目录或任意位置均可）
set -euo pipefail

ENV_NAME="${CCM_CONDA_ENV:-py3.10}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

module load anaconda3/2023.09
source "$(conda info --base)/etc/profile.d/conda.sh"

if conda env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
  echo "环境 $ENV_NAME 已存在，跳过创建"
else
  conda create -n "$ENV_NAME" python=3.10 -y
fi

conda activate "$ENV_NAME"
pip install -r "$REPO_ROOT/requirements.txt"
python --version
python -c "import numpy, yaml, matplotlib; print('依赖导入正常')"

module load starccmplus-20.02.007-glibc2.28_double
echo "STAR-CCM+：$(which starccm+)"
