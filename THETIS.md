# Thetis 后端：本机安装与耦合冒烟测试通过

2026-11-05：ARM macOS / Python 3.13 环境已完成 PETSc、MPI、并行 HDF5、Firedrake 和 Thetis 安装。Firedrake 官方串行及 2、3 进程检查通过；项目 19 项单元测试通过。256 单元石笼算例完成 1 s 水动力预热与 1 s 悬沙—床面耦合，最小水深 0.999194 m，最小悬沙 q=8.66564e-6 m，最大绝对床面变化 0.000118816 m。

这证明程序能够执行，不代表物理标定、网格收敛或完整质量收支验证。网页仍使用原始轻量模型。

## 接入内容

入口 `python3 -m sedimentcfd thetis`，实现位于 `sedimentcfd/thetis_backend.py`。

1. P1DG–P1DG 非线性浅水方程，保留惯性、自由水面和水平黏性。
2. 旋转矩形石笼转换为 DG0 额外线性阻力场 `drag * (resistance - 1)`；背景底摩擦单独用 Nikuradse 颗粒粗糙度。该阻力仍需标定，不表示真实石块孔隙。
3. 固定入口体积流量 `velocity * initial_depth * width`，固定出口水位。**与原型的固定水头差工况不同，不能直接比较流量或宣称误差减少。**
4. 先运行水动力预热，再将速度与水面传递给耦合算例。默认 120 s 只是预热时间，不是稳态收敛判据。
5. 开启守恒悬沙与 Exner 床面更新；推移质可通过 `--bedload` 开启，默认关闭。
6. Thetis 原生泥沙变量为 `q = H * C_volume`；项目输入 `C_mass [kg/m³]` 转为 `q = H * C_mass / rho_s`。入口与初始悬沙分别取 `inlet_concentration`、`initial_concentration` 并显式转换，H 随水位/河床变化。远高于输沙能力的含沙量需配合细颗粒：0.2 mm 砂的过饱和入流会全部沉积在入口一列单元内；预览默认采用高含沙水体 1 kg/m³、0.063 mm。
7. 分离 `molecular_viscosity=1e-6`（颗粒沉降、D*）与 `horizontal_viscosity=0.01`（流场混合）。后者是待标定示例参数。
8. 形态加速系数固定为 1。原参数 `critical_velocity`、`settling_velocity`、`dt` 不控制本后端；沉降由上游粒径闭合计算，步长用 `--dt`。

## 本机原生安装脚本

在 Mac 的普通终端执行：

```bash
cd /Users/xie/code/SedimentCFD
python3 scripts/install_thetis.py
```

安装脚本固定兼容的 Firedrake 提交与关键 Python 依赖。当前验证组合：

| 组件 | 版本 / 提交 |
| --- | --- |
| Thetis | `162c605079611dfce69b4d1182181a421116b141` |
| Firedrake | `2093d46b499ae31e816b4e651f68e3189f4eeb1b` |
| PETSc | `fba289ebbbcfb2ac322cc38ce51dea125b19e7b3` |
| loopy / islpy / petsctools | `2025.2` / `2026.1` / `2026.1` |

完整依赖快照见 `.setup/thetis/pip-freeze.txt`。PETSc 新安装仍从 main 获取，因此跨机器重装不能仅凭脚本保证完全复现上述组合。

修复包括：为 h5py 提供 PETSc 的并行 HDF5 与 MPI 编译器；使用兼容的 Firedrake/loopy；隔离旧编译缓存；设置单线程及可用的 MPI 启动参数。适配层通过上游 `sediment_model_class` 扩展点，将关闭速度修正时的整数 1 改为 `Constant(1)`，避免 CrankNicolson 丢失旧时刻字段；不改变修正因子数值。

已有 PETSc 编译结果时可 `python3 scripts/install_thetis.py --resume-python`；依赖已经装好时可 `--resume-verify`。后者执行官方检查与耦合冒烟测试，成功后写安装清单。构建日志和缓存均在 `.setup/thetis/`。

## 运行

```bash
cd /Users/xie/code/SedimentCFD
source activate_thetis.sh
python scripts/smoke_thetis.py
python -m sedimentcfd thetis --config examples/two_gabions.json --output outputs/thetis --grain-size 0.00016 --dt 0.25 --spinup 120
```

小算例结果在 `outputs/thetis_smoke/`，含 `smoke_summary.json` 与 VTK。较大的 two_gabions 示例尚未作为本次安装验收运行。

`python -m sedimentcfd thetis --check` 仅报告依赖可发现性和源码版本；其中 `runtime_validated: false` 不动态读取本机测试报告，不代表此次冒烟失败。实际验收以安装日志及 `smoke_summary.json` 为准。

容器脚本仍可作为另一条路线，但本次没有验证 Docker 镜像；勿将可变的 dev-main 标签当作已验证环境。

## 输出与边界

- `spinup/`、`morphology/`：Thetis 原生 VTK 结果，可在 ParaView 查看速度、水面、河床和泥沙。
- `inventory.json`：悬浮泥沙总质量和床面净质量变化。后者允许为负，不能等同于累计沉积。
- `run_config.json`：参数、源码检查信息及实际导入模块路径。

原网页仍调用旧模型，尚未消费 Thetis 的 DG 网格与 VTK 输出。没有将上游结果假装转换为旧模型的 JSON 质量预算。当前 inventory **不包含严格离散边界累计通量，因此不是完整闭合守恒验证**；上游诊断输出另行保留。

本实现使用全域可冲刷床，没有有限泥沙储量、石笼基础防冲或不透蚀区域；石笼位置固定，不能表示结构失稳。需要后续加入可蚀层与石笼基础约束再用于工程评估。二维浅水能包含平面惯性效应，不能解析三维石缝流和垂向尾涡。

## 后续物理验收

1. 已通过上游 `test/sediment/test_migrating_trench.py` 的守恒/非守恒两项回归（95 s）。上游出现 PyOP2.Global 通信器警告，因此仅确认单进程 Thetis 耦合；Firedrake MPI 检查不能替代 Thetis MPI 验收。
2. 已完成小网格短时冒烟；后续扩展计算时长与多进程适配层测试。
3. 空河道稳态、石笼阻力减速偏转、悬沙/床面交换，并对齐边界通量做质量收支。
4. 空间与时间收敛、预热长度敏感性、阻力/混合参数敏感性。
5. 接入网页前，明确 DG 节点、体积浓度与正负床面变化的展示规则。

## 对照源码

- `external/thetis/examples/sediment_trench_2d/trench_hydro.py`
- `external/thetis/examples/sediment_trench_2d/trench_example.py`
- `external/thetis/thetis/sediment_model.py`
- `external/thetis/thetis/sediment_eq_2d.py`
- `external/thetis/thetis/exner_eq.py`
- `external/thetis/thetis/solver2d.py`

Thetis 为 MIT 许可。本适配层调用其公开接口，没有拷贝或修改上游求解器。归档不包含 external 仓库，需单独克隆指定版本。
