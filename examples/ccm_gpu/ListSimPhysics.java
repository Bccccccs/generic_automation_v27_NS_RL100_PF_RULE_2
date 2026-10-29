// 只读列出 .sim 里实际启用的物理模型与求解器，作为 GPU 资格文件 sim_review.items 的核对清单。
// 不修改、不保存 .sim，不推进求解。用法见 docs/GPU.md 4.4：
//   cd /tmp && "$STARCCM_BIN" -batch "$REPO/examples/ccm_gpu/ListSimPhysics.java" -np 1 /abs/path/cifu0.sim > sim_physics.log 2>&1
//   grep '^SIM_' sim_physics.log
// 输出行：SIM_CONTINUUM <物理连续体>、SIM_MODEL <类名> <显示名>、SIM_SOLVER <类名> <显示名>。
// 用反射调用以兼容不同 STAR 版本（2026-10-20 在 STAR 2606 / 21.04.007 上实测可用，20.02 未测）。
import star.common.*;

public class ListSimPhysics extends StarMacro {
  public void execute() {
    Simulation sim = getActiveSimulation();
    for (Object continuum : sim.getContinuumManager().getObjects()) {
      sim.println("SIM_CONTINUUM " + continuum.toString());
      try {
        Object manager = continuum.getClass().getMethod("getModelManager").invoke(continuum);
        Object models = manager.getClass().getMethod("getObjects").invoke(manager);
        for (Object model : (java.util.Collection<?>) models) {
          sim.println("SIM_MODEL " + model.getClass().getSimpleName() + " " + model.toString());
        }
      } catch (Exception e) {
        sim.println("SIM_MODELS_ERROR " + e.toString());
      }
    }
    for (Object solver : sim.getSolverManager().getObjects()) {
      sim.println("SIM_SOLVER " + solver.getClass().getSimpleName() + " " + solver.toString());
    }
  }
}
