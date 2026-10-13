#!/bin/bash
#SBATCH -J ccm-geometry
#SBATCH -N 1
#SBATCH --ntasks-per-node=16
#SBATCH --cpus-per-task=1
#SBATCH -p hx1hdnormal01
#SBATCH --mem=128G
# 集群限制每 CPU 内存 ≤ DefMemPerCPU（约 8G）；16 核 × 8G = 128G，与 run_python.slurm 一致。STAR 仍只用 --np 8。
#SBATCH --output=ccm-geometry-%j.out
#SBATCH --error=ccm-geometry-%j.err

# 只读模板：导出喷口几何、六区力 report 组成、来流与密度；不求解、不保存 .sim。
#   sbatch scripts/inspect_ccm_geometry.slurm
#   CCM_SIM=/path/other.sim sbatch scripts/inspect_ccm_geometry.slurm
set -e
module load anaconda3/2023.09
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate py3.10

PROJECT_DIR="/public/home/yanbochao/bc/generic_automation_v27_NS_RL100_PF_RULE_2"
SIM_PATH="${CCM_SIM:-$PROJECT_DIR/cifu0.sim}"
STARCCM_BIN="/public/home/yanbochao/apprepo/starccmplus/20.02.007-glibc2.28_double/app/20.02.007-R8/STAR-CCM+20.02.007-R8/star/bin/starccm+"
cd "$PROJECT_DIR"
OUT_DIR="runs/_geometry/$(basename "$SIM_PATH" .sim)_${SLURM_JOB_ID}"

python -u scripts/inspect_ccm_geometry.py run \
  --sim "$SIM_PATH" --out-dir "$OUT_DIR" --starccm "$STARCCM_BIN" --np 8
python -u scripts/inspect_ccm_geometry.py analyze "$OUT_DIR/ccm_geometry_inspection.yaml" \
  --output "$OUT_DIR/geometry_report.md"
echo "结果：$OUT_DIR/ccm_geometry_inspection.yaml 和 geometry_report.md"
