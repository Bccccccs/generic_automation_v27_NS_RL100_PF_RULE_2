#!/usr/bin/env bash
# CCM GPU 只读环境取证（计划任务 0 / 任务 4）。
#
# 用法： bash examples/ccm_gpu/collect_environment_evidence.sh /absolute/path/to/starccm+
#
# 约束：
#   * 只做只读查询：不安装驱动或 CUDA，不修改环境，不启动 STAR 求解，不申请资源。
#   * 必须在已经分配给本次任务的计算节点、且处于实际 STAR 启动上下文里执行
#     （原生安装或站点已核验的容器 / launcher wrapper）。宿主机可见不等于容器可见。
#   * 只打印白名单内的资源字段，不打印完整环境变量，不打印许可证或 PoD 密钥。
#   * 输出需要人工脱敏后归档到 docs/gpu/20.02-evidence-register.md 对应的 BLOCKED 项。

set -uo pipefail

CCM_BIN="${1:-}"

section() {
    printf '\n===== %s =====\n' "$1"
}

run_readonly() {
    # 逐条记录命令、返回码和输出；失败也继续，便于一次采集全部证据。
    printf '\n$ %s\n' "$*"
    "$@" 2>&1
    printf '[returncode=%s]\n' "$?"
}

section "采集上下文"
printf 'collected_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf 'hostname=%s\n' "$(hostname)"
printf 'user=%s\n' "${USER:-unset}"
printf 'pwd=%s\n' "$(pwd)"
printf 'container_markers: '
for marker in /.dockerenv /run/.containerenv; do
    if [ -e "$marker" ]; then
        printf '%s=present ' "$marker"
    fi
done
printf '\n'

section "STAR-CCM+ 可执行文件身份"
if [ -z "$CCM_BIN" ]; then
    printf 'BLOCKED: 未提供 CCM_BIN。请传入实际可执行文件的绝对路径，不要复制计划里的虚构安装目录。\n'
else
    printf 'CCM_BIN=%s\n' "$CCM_BIN"
    if [ -x "$CCM_BIN" ]; then
        run_readonly ls -l "$CCM_BIN"
        if command -v sha256sum >/dev/null 2>&1; then
            run_readonly sha256sum "$CCM_BIN"
        elif command -v shasum >/dev/null 2>&1; then
            run_readonly shasum -a 256 "$CCM_BIN"
        else
            printf 'sha256 工具缺失，未记录可执行文件 hash\n'
        fi
        run_readonly "$CCM_BIN" -version
        run_readonly "$CCM_BIN" -help
        # B-01：确认 -gpgpu 选择器、严格兼容参数和 :nomps 后缀的实际语法。
        # 若该 build 不接受 -phelp:gpgpu，保存返回结果并改用 -help 指示的详细帮助入口。
        run_readonly "$CCM_BIN" -phelp:gpgpu
    else
        printf 'BLOCKED: %s 不存在或不可执行；不要在宿主机上猜测容器内路径。\n' "$CCM_BIN"
    fi
fi

section "操作系统与架构"
run_readonly uname -srm
if [ -r /etc/os-release ]; then
    run_readonly cat /etc/os-release
else
    printf '/etc/os-release 不可读（非 Linux 或受限容器）\n'
fi

section "GPU 设备与拓扑"
if command -v nvidia-smi >/dev/null 2>&1; then
    run_readonly nvidia-smi -L
    run_readonly nvidia-smi \
        --query-gpu=index,uuid,name,pci.bus_id,memory.total,driver_version \
        --format=csv,noheader
    run_readonly nvidia-smi topo -m
    printf '\n注意：nvidia-smi 显示的 CUDA 兼容版本不等于系统已安装 CUDA toolkit。\n'
else
    printf 'nvidia-smi 不可用。AMD 平台当前 BLOCKED（B-03）：\n'
    printf '需要先用目标驱动自带工具的 --help 确认只读等效命令，再单独实现 vendor 分支。\n'
    printf '不得无条件调用 NVIDIA 工具，也不得自动安装驱动。\n'
fi

section "GPU 可见性（只打印，不修改）"
printf 'CUDA_VISIBLE_DEVICES=%s\n' "${CUDA_VISIBLE_DEVICES-unset}"
printf 'NVIDIA_VISIBLE_DEVICES=%s\n' "${NVIDIA_VISIBLE_DEVICES-unset}"
printf 'SLURM_JOB_ID=%s\n' "${SLURM_JOB_ID-unset}"
printf 'SLURM_JOB_NODELIST=%s\n' "${SLURM_JOB_NODELIST-unset}"
printf 'SLURM_STEP_ID=%s\n' "${SLURM_STEP_ID-unset}"

section "Slurm 作业分配"
if command -v scontrol >/dev/null 2>&1; then
    if [ -n "${SLURM_JOB_ID:-}" ]; then
        run_readonly scontrol show job -o "$SLURM_JOB_ID"
        run_readonly scontrol show hostnames "${SLURM_JOB_NODELIST:-$SLURM_JOB_ID}"
    else
        printf 'SLURM_JOB_ID 未设置：当前不在作业上下文内，GPU 分配证据无法采集。\n'
        printf '本项目只消费已存在的分配，不申请资源。\n'
    fi
else
    printf 'scontrol 不可用：manual 模式必须确认本机是保留给本次任务的独占服务器。\n'
fi

section "离线文档检索"
if [ -n "$CCM_BIN" ] && [ -x "$CCM_BIN" ]; then
    INSTALL_ROOT="$(dirname "$(dirname "$CCM_BIN")")"
    printf 'install_root_guess=%s\n' "$INSTALL_ROOT"
    if command -v rg >/dev/null 2>&1; then
        # 只在安装根目录下检索，不扫描用户所有目录。
        run_readonly rg --files "$INSTALL_ROOT" -g '*doc*' -g '*UserGuide*' \
            -g '*Installation*' -g '*Release*'
    else
        printf 'rg 不可用，改用： find "%s" -maxdepth 4 \\( -iname "*userguide*" -o -iname "*release*" \\)\n' "$INSTALL_ROOT"
    fi
else
    printf '未提供可执行的 CCM_BIN，跳过安装文档检索（B-02）。\n'
fi

printf '\n===== 采集结束 =====\n'
printf '下一步：人工核对每项输出，把结论写入 docs/gpu/20.02-evidence-register.md，\n'
printf '并据此填写资格文件。本脚本的输出本身不构成 GPU 求解验收证据。\n'
