#!/bin/bash
#SBATCH -J b52-ccm
#SBATCH -N 1
#SBATCH --ntasks-per-node=16
#SBATCH --cpus-per-task=1
#SBATCH -p hx1hdnormal01
#SBATCH --mem=128G
#SBATCH --output=b52-ccm-%j.out
#SBATCH --error=b52-ccm-%j.err

# --mem 为每节点主机 RAM。
# 先以 128G 验证启动和持续步进；是否增大应依据实际内存峰值与 OOM 记录。
# allocation 用于取得内存额度；STAR 下方固定 --np 8，不跟随任务数增加。
set -e
set -o pipefail
trap 'echo "作业失败：第 ${LINENO} 行，请检查作业日志。" >&2' ERR

# 集群通过 module 提供 conda；py3.10 环境需事先创建（见 AGENTS.md 5.1：bash scripts/setup_cluster_env.sh）。
module load anaconda3/2023.09
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate py3.10

PROJECT_DIR="/public/home/yanbochao/bc/generic_automation_v27_NS_RL100_PF_RULE_2"
SIM_PATH="$PROJECT_DIR/cifu0.sim"
STARCCM_BIN="/public/home/yanbochao/apprepo/starccmplus/20.02.007-glibc2.28_double/app/20.02.007-R8/STAR-CCM+20.02.007-R8/star/bin/starccm+"
# 动作配置：默认训练集；跑验证集时用
#   CCM_ACTION_CONFIG=configs/b52/validation_dt1e-4.yaml sbatch run_python.slurm
ACTION_CONFIG="${CCM_ACTION_CONFIG:-configs/b52/training_dt1e-4.yaml}"
CASE_TAG="$(basename "$ACTION_CONFIG" .yaml)"
# 必须与 cifu0.sim 中包含 J01～J24 的区域名称一致；可通过环境变量覆盖。
REGION="${CCM_REGION:-Region}"

: "${SLURM_JOB_ID:?请使用 sbatch 提交此脚本}"
: "${SLURM_NTASKS:?未获取到 Slurm 任务数}"
cd "$PROJECT_DIR"
test -f scripts/workflow.py
test -f "$ACTION_CONFIG"
test -f "$SIM_PATH"
test -x "$STARCCM_BIN"

CASE_DIR="$PROJECT_DIR/runs/b52_v2/${CASE_TAG}_${SLURM_JOB_ID}"
# 拒绝覆盖同一作业 ID 的已有结果。
mkdir -p "$PROJECT_DIR/runs/b52_v2"
mkdir "$CASE_DIR"

echo "作业 ID：$SLURM_JOB_ID"
echo "节点：${SLURM_JOB_NODELIST}；Slurm 任务数：$SLURM_NTASKS；STAR MPI 进程数：8"
echo "模型：$SIM_PATH；区域：$REGION；动作配置：$ACTION_CONFIG"
echo "输出：$CASE_DIR"
echo "每节点主机内存申请：128G（--mem）"
printf '%s\n' "$SLURM_JOB_ID" > "$CASE_DIR/job_id.txt"
# 保存查询命令，在作业结束后执行，避免把运行中的会计快照当作最终结果。
printf 'sacct -j %s --format=JobID,State,ExitCode,Elapsed,ReqMem,MaxRSS,AllocCPUS,AllocTRES%%80\n' \
  "$SLURM_JOB_ID" > "$CASE_DIR/check_accounting.sh"
echo "作业结束后查询资源和退出状态：bash $CASE_DIR/check_accounting.sh"
python --version

# 按动作配置生成动作表（求解时间步 1e-4 s，与下方 --time-step 一致）。
python -u scripts/workflow.py actions \
  --config "$ACTION_CONFIG" \
  --output-dir "$CASE_DIR"

# 以下命令为 CPU 求解。
# Python 只启动一次，由项目 runner 启动 STAR MPI 进程。
python -u scripts/workflow.py ccm \
  --schedule "$CASE_DIR/input/actuation_schedule.csv" \
  --sim "$SIM_PATH" \
  --out "$CASE_DIR/raw_star" \
  --starccm-path "$STARCCM_BIN" \
  --region "$REGION" \
  --time-step 1e-4 \
  --np 8 \
  --scheduler slurm \
  --slurm-job-id "$SLURM_JOB_ID" \
  --execution-mode run

echo "流程完成：$CASE_DIR"
