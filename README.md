# SedimentCFD

二维平面河段中石笼对水流与悬沙沉积影响的**定性**预览工具。物理计算全部由 [Thetis](https://thetisproject.org/)（基于 Firedrake）完成，本项目只负责参数、石笼阻力场、结果导出和一个可交互的动画网页。

> 模型未经实测标定。石笼阻力、水平黏性、泥沙扩散、粒径和含沙量都是假设值。结果只用于比较不同布局的相对规律（石笼后方淤积更多、束窄处更少、沿程含沙量下降等），不能用来预测真实的淤积厚度。

## 快速开始

需要先装好 Firedrake + Thetis 环境（见[安装](#安装)）。

```bash
source activate_thetis.sh
python -m sedimentcfd run      # 默认场景，约 1.5 分钟，结果写入 outputs/preview/
python -m sedimentcfd serve    # 打开 http://127.0.0.1:8767/
```

在网页中可以：

- 切换流速、床面变化、含沙量和水深；
- 连续播放时间序列；
- 叠加流向箭头和示踪粒子；
- 编辑石笼布局后点「应用布局并重新计算」。新结果写入 `outputs/preview/jobs/<id>/`，同一时间只运行一个计算任务。

其他命令：

```bash
python -m sedimentcfd config > my.json            # 导出默认参数
python -m sedimentcfd run --config my.json --output outputs/my_case
python -m sedimentcfd check                       # 检查 Firedrake/Thetis 是否可用
```

配置文件中可以只写需要修改的字段，其余字段取默认值。

## 从 DXF 导入石笼

```bash
python -m sedimentcfd dxf Cage.dxf --inflow right > cases/cage.json
python -m sedimentcfd run --config cases/cage.json --output outputs/cage
```

- DXF 单位按米处理。每个石笼是一个矩形：4 条首尾相连的 LINE，或一条闭合 LWPOLYLINE；不构成矩形的线（中心线、轴线标记）会被忽略。
- 河段取石笼外包框再加边距：两岸各 `--bank-margin`（默认 10 m），来流侧 `--upstream`（20 m），出口侧 `--downstream`（30 m），向外取整到整米。用 `origin_x/origin_y` 保留 CAD 坐标。
- `--cell`（默认 1 m）设定网格尺寸，`--resistance`（默认 10）设定阻力倍数；时间参数沿用默认值，大河段需要手动加长（见 `cases/cage.json`）。

`cases/cage.json` 是 `Cage.dxf` 的算例：5 个 36 × 1.8 m 石笼，右侧来流，河段 133 × 115 m，1 m 网格，300 s 预热 + 600 s 耦合，时间步 0.5 s，单核约 31 分钟（网页约 57 MB）。

## 默认场景与参数

参数集中在 `sedimentcfd/config.py` 的 `Config` 中（国际单位制）。

| 参数 | 默认值 | 说明 |
|---|---|---|
| `length` × `width` | 16 × 8 m | 矩形河段 |
| `origin_x`, `origin_y` | 0, 0 m | 河段左下角坐标（用于保留 CAD 坐标） |
| `inflow` | `left` | 来流侧：`left`（向 +x 流）或 `right`（向 −x 流） |
| `nx` × `ny` | 32 × 16 | 每个矩形格分成 2 个三角形，约 0.5 m |
| `depth` | 0.6 m | 初始水深，河床平整 |
| `velocity` | 0.35 m/s | 来流流速，固定流量 = 流速 × 水深 × 河宽 |
| `drag` | 0.03 s⁻¹ | 基准线性阻力；石笼内额外阻力 = `drag × (resistance − 1)` |
| `viscosity` / `diffusivity` | 0.01 / 0.01 m²/s | 水平涡黏性 / 泥沙水平扩散（待标定） |
| `grain_size` | 0.063 mm | 单一粒径；闭合要求 D\* ≥ 1，即粒径不小于约 0.04 mm |
| `sediment_density` / `bed_porosity` | 2650 kg/m³ / 0.4 | |
| `inlet_concentration` / `initial_concentration` | 1 / 1 kg/m³ | 高含沙来流和初始水体（场景假设） |
| `reference_height` | 0.025 m | 近底参考高度，需满足 3 × 粒径 < 该值 < 水深 |
| `spinup` / `duration` / `timestep` | 60 / 60 / 0.1 s | 先只算水动力预热，再进行泥沙与床面耦合计算 |
| `gabions` | 两个 3 × 1.5 m 斜置石笼 | 中心 `x, y`，尺寸 `length, width`，角度 `angle`（度，逆时针），阻力倍数 `resistance` |

网页提交的计算任务限制在 nx × ny ≤ 16000、耦合时长 ≤ 600 s。命令行运行没有这项限制。

## 模型

- **水流**：P1DG–P1DG 非线性浅水方程，包含惯性、自由水面和水平黏性，时间推进用隐式 CrankNicolson（θ = 1）。底摩擦采用 Nikuradse 粗糙度 3 × 粒径。
- **石笼**：在旋转矩形范围内施加 DG0 额外线性阻力。水流可以穿过和绕过石笼，模型不解析石缝。
- **泥沙**：守恒型悬沙方程，Thetis 求解的变量是 q = H·C / ρs。冲刷与沉积采用上游的 van Rijn 型闭合（Rouse 积分），沉降速度由粒径计算。床面用 Exner 方程更新，没有推移质，形态加速系数为 1。
- **边界**：
  - 来流侧（默认左侧，`inflow` 可改为右侧）全宽均匀来流，流量固定，并携带入流含沙量；
  - 对侧出口水位为 0；
  - 两岸不透水。
- **对上游的一处修补**：在当前 Thetis 版本中，关闭速度修正时修正因子是整数 1，CrankNicolson 会因此丢掉该项。`model.py` 通过 `sediment_model_class` 把它换成 `Constant(1)`，数值不变。
- **导出与检查**：每次导出时把场投影到 DG0 单元平均。如果出现非正水深，或负浓度超过峰值的 0.1%，计算直接报错。

**已知局限**：

- 全域河床都可冲刷，没有石笼基础防冲，也没有有限的可蚀层；
- 单一粒径，没有絮凝；
- 只算二维水深平均，没有三维尾涡；
- 没有做网格和时间步收敛研究；
- `inventory.json` 中的悬沙量与床面质量变化不是严格的边界通量收支。

## 输出

`python -m sedimentcfd run` 的输出目录中有：

| 文件 | 内容 |
|---|---|
| `index.html` | 内嵌全部数据的独立网页，可以直接用浏览器打开；重新计算需要通过 `serve` 访问 |
| `preview_data.json` | 网格、41 帧单元平均场、闭合参数、数值检查结果、质量库存 |
| `inventory.json` | 每帧的悬沙总质量与床面净质量变化（kg） |
| `run_config.json` | 实际使用的参数和环境信息 |
| `spinup/`, `morphology/` | Thetis 原生 VTK 输出，可用 ParaView 打开 |

## 安装

```bash
python3 scripts/install_thetis.py      # 从源码编译 PETSc / Firedrake，安装 Thetis 和本项目
source activate_thetis.sh
```

已验证的组合（ARM macOS，Python 3.13）：

| 组件 | 版本 / 提交 |
|---|---|
| Thetis（`external/thetis`） | `162c605079611dfce69b4d1182181a421116b141` |
| Firedrake | `2093d46b499ae31e816b4e651f68e3189f4eeb1b` |
| PETSc | `fba289ebbbcfb2ac322cc38ce51dea125b19e7b3` |
| loopy / islpy / petsctools | 2025.2 / 2026.1 / 2026.1 |

- 构建日志、缓存和 `pip-freeze.txt` 在 `.setup/thetis/`。
- PETSc 已经编译过时，可以加 `--resume-python` 跳过编译。
- 依赖已经装好时，可以用 `--resume-verify` 只运行检查。
- PETSc 每次从 main 分支获取，所以换一台机器重装时，不能保证得到完全相同的组合。

## 目录

```
sedimentcfd/
  config.py        Config / Gabion：参数、默认场景、校验
  model.py         Thetis 运行（水动力预热 + 泥沙床面耦合）
  preview.py       逐帧导出单元平均场，生成 preview_data.json 与 index.html
  server.py        本地服务：静态文件 + 单任务求解接口
  __main__.py      命令行：run / serve / check / config
  web/preview.html 网页模板
scripts/install_thetis.py
tests/             参数、服务器和安装脚本测试；test_thetis.py 为真实求解冒烟测试（无 Thetis 时跳过）
```

## 测试

```bash
source activate_thetis.sh
python -m unittest discover -s tests -v   # 约 1 分钟，其中包括一次 4 × 2 m 的真实 Thetis 短算
```
