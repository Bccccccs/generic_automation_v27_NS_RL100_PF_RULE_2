#!/bin/bash
#SBATCH -J nojet-ccm
#SBATCH -N 1
#SBATCH --ntasks-per-node=16
#SBATCH --cpus-per-task=1
#SBATCH -p hx1hdnormal01
#SBATCH --gres=dcu:8
#SBATCH --mem=128G
#SBATCH --output=nojet-ccm-%j.out
#SBATCH --error=nojet-ccm-%j.err

# --mem 为每节点主机 RAM，独立于 8 张 DCU 的显存。
# 先以 128G 验证启动和持续步进；是否增大应依据实际内存峰值与 OOM 记录。
# allocation 用于取得内存额度；STAR 下方固定 --np 8，不跟随任务数增加。
set -e
set -o pipefail
trap 'echo "作业失败：第 ${LINENO} 行，请检查作业日志。" >&2' ERR

source /public/home/acn6k38urd/miniconda3/etc/profile.d/conda.sh
conda activate py3.10

PROJECT_DIR="/public/home/acn6k38urd/bc/generic_automation_v27_NS_RL100_PF_RULE_2"
SIM_PATH="$PROJECT_DIR/cifutest.sim"
STARCCM_BIN="/public/home/acn6k38urd/apprepo/starccmplus/20.02.007-glibc2.28_double/app/20.02.007-R8/STAR-CCM+20.02.007-R8/star/bin/starccm+"
# 必须与 cifutest.sim 中包含 J01～J24 的区域名称一致；可通过环境变量覆盖。
REGION="${CCM_REGION:-Region}"

: "${SLURM_JOB_ID:?请使用 sbatch 提交此脚本}"
: "${SLURM_NTASKS:?未获取到 Slurm 任务数}"
cd "$PROJECT_DIR"
test -f scripts/workflow.py
test -f "$SIM_PATH"
test -x "$STARCCM_BIN"

CASE_DIR="$PROJECT_DIR/runs/nojet_3s_${SLURM_JOB_ID}"
# 拒绝覆盖同一作业 ID 的已有结果。
mkdir -p "$PROJECT_DIR/runs"
mkdir "$CASE_DIR"

echo "作业 ID：$SLURM_JOB_ID"
echo "节点：${SLURM_JOB_NODELIST}；Slurm 任务数：$SLURM_NTASKS；STAR MPI 进程数：8"
echo "模型：$SIM_PATH；区域：$REGION"
echo "输出：$CASE_DIR"
echo "每节点主机内存申请：128G（--mem，独立于 DCU 显存）"
printf '%s\n' "$SLURM_JOB_ID" > "$CASE_DIR/job_id.txt"
# 保存查询命令，在作业结束后执行，避免把运行中的会计快照当作最终结果。
printf 'sacct -j %s --format=JobID,State,ExitCode,Elapsed,ReqMem,MaxRSS,AllocCPUS,AllocTRES%%80\n' \
  "$SLURM_JOB_ID" > "$CASE_DIR/check_accounting.sh"
echo "作业结束后查询资源和退出状态：bash $CASE_DIR/check_accounting.sh"
python --version

# 生成 3 秒、步长 1e-4 秒、24 路喷口全零的动作表。
python -u scripts/workflow.py actions \
  --config configs/actions/temp_no_jet_3s_dt1e-4.yaml \
  --output-dir "$CASE_DIR"

# 注意：申请 DCU 不会自动启用 STAR GPU；以下命令当前仍为 CPU 求解。
# DCU 型号、STAR 支持和项目 GPU 预检适配确认后，才能改成 GPU 启动。
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
