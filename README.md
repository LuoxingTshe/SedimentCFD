# SedimentCFD

新增实验性 Thetis 后端：`python3 -m sedimentcfd thetis --check`。接入状态、依赖与运行方式见 [THETIS.md](THETIS.md)。新后端已通过本机 Firedrake 环境检查和短时耦合冒烟测试；下面的结果和网页仍属于原始轻量模型。

二维俯视平面水流—悬沙沉积计算原型。Python + NumPy + SciPy；无 FEniCS、GPU、外部网页资源依赖。附本地交互界面、CLI、离线结果预览与守恒诊断。

## 启动

在项目目录运行（Python 3.10+）：

```bash
python3 -m pip install -e .
python3 -m sedimentcfd serve
```

浏览器打开 http://127.0.0.1:8765 。可修改河段尺寸、水深、参考流速、泥沙参数、石笼中心/长宽/角度/阻力，切换流速、悬沙浓度、水头和沉积厚度，播放沉积过程。默认自动计算双石笼示例。

当前开发机器已有 NumPy 与 SciPy，可直接运行 `python3 -m sedimentcfd serve`。本次受限执行环境不允许绑定本地端口，因此服务器没有持续运行，也未完成真实浏览器端联调。Python 数值测试、无网络 HTTP 处理器测试和 JavaScript 语法检查已执行。离线 HTML 已生成，但浏览器安全策略不允许工具打开 file://，其视觉布局未经过截图验证。

批量计算与导出：

```bash
python3 -m sedimentcfd run --config examples/two_gabions.json --output outputs/demo
python3 -m sedimentcfd config
python3 -m unittest discover -s tests -v
python3 examples/grid_study.py
```

无需服务器查看已算结果：用浏览器打开 `outputs/demo/preview.html`。离线预览支持场量切换与时间回放；参数编辑和重新计算须启动服务。CLI 输出：

- `result.json`：配置、节点、单元、场量、时间帧、质量预算与警告。
- `preview.html`：嵌入全部结果的独立网页，无网络资源依赖。
- `nodes.csv`：节点坐标、流速向量、水头、最终浓度、最终沉积厚度。
- `history.csv`：累计输入/输出、悬浮质量、沉积质量、质量残差。
- `fields.npz`：供后续 Python、GIS 或网格处理使用的数组。

## 模型选择

这是**恒定水深、阻力主导、单一悬沙组分、单向耦合**的最简模型。水流用真正的 P1 三角形有限元；泥沙采用基于水力 FEM 刚度边通量的守恒迎风离散，扩散项用 P1 FEM，时间用后向欧拉。它不是完整浅水方程或 Navier–Stokes 求解器，也不是纯 Galerkin 悬沙离散。

### 水流

忽略水流惯性和动量扩散，以线性阻力与水头梯度平衡：

\[
\mathbf u=-\frac{g}{r(x,y)}\nabla\eta,
\qquad \nabla\cdot(H\mathbf u)=0.
\]

因此求解

\[
\nabla\cdot(T\nabla\eta)=0,
\qquad T=\frac{Hg}{r}.
\]

- H：恒定水深，m；η：相对水力水头，m；u：深度平均流速，m/s。
- r：线性阻力系数，s⁻¹。背景 r₀ 默认 0.03；石笼内 r = Rr₀。
- R：阻力倍数，≥1。它不是孔隙率，也不直接等于真实石笼渗透率。
- 每个三角形以重心判定是否落入旋转矩形石笼；重叠石笼取最大阻力倍数。
- 石笼允许穿透、绕流与偏转。岸边石笼超出域的部分自然裁切。

弱形式为 `∫ T ∇Nᵢ·∇Nⱼ dA`。线性三角形梯度为常数，逐单元显式组装稀疏矩阵；固定边界自由度消元后求解。

左边界 η = U₀Lr₀/g，右边界 η = 0，上下岸线为自然零法向通量。U₀ 是**相同水头差、无石笼条件的均匀流速**；加石笼后总流量通常降低。当前没有固定总流量模式。

界面水头不是水深；H 在整个计算中保持不变。图中速度是单元速度的节点面积加权投影，不能用投影速度重算质量预算。

### 悬沙输运与沉积

\[
H\partial_t C+\nabla\cdot(H\mathbf uC-HD\nabla C)=-J_d,
\]

\[
J_d=w_s C\max\left(0,1-\frac{|\mathbf u|^2}{U_c^2}\right),
\qquad
\partial_t z_b=\frac{J_d}{\rho_s(1-p)}.
\]

C 单位 kg/m³，D 为 m²/s，wₛ 为 m/s，Jd 为 kg/(m²·s)，zb 为 m。ρs 是颗粒密度，p 是沉积床孔隙率。C 是包含石笼区域在内的等效体积浓度；没有另外求解笼内孔隙储水量与过滤截留。

沉降闭合借用细颗粒沉积随剪切增大而受抑制的思路，假设床剪切与速度平方成正比，将阈值写为可标定的临界流速 Uc。这是本原型的经验闭合，**不能直接作为通用沙砾沉降规律**。默认 wₛ、Uc、R 均为演示值，尚未根据粒径、絮凝、床面条件或石笼实验标定。直接输入 wₛ，不从粒径自动推算。

节点沉降概率采用邻近单元 `|u|²` 的面积加权平均，避免相反方向速度平均抵消而制造虚假的低剪切区。没有侵蚀、再悬浮、推移质或颗粒碰撞。

### 离散守恒

采用结构化直角三角形保证标量水力/扩散刚度矩阵非对角项非正。水力刚度矩阵 K 给出边 i→j 的体积通量：

\[
F_{ij}=K_{ij}(\eta_j-\eta_i),\qquad F_{ji}=-F_{ij}.
\]

边上的泥沙通量为 `Fij × 上游浓度`，出入节点成对抵消。节点控制面积为 FEM 集中质量权重 `aᵢ = Σ Aₑ/3`。这是基于 FEM 连接关系的低阶守恒通量格式，有迎风数值扩散，不是未稳定化的中心 Galerkin 对流。

边界供排水量取固定水头节点上的 `(Kη)ᵢ`。正流入乘以指定入口浓度进入右端项；负流出乘以节点未知浓度进入隐式出流项。扩散项在全部边界为零法向通量；入口采用指定输入泥沙通量，而非强制浓度 Dirichlet 条件。

每步解：

\[
(M/\Delta t+A_{up}+K_D+Q_{out}+S_d)C^{n+1}
=M C^n/\Delta t+Q_{in}C_{in}.
\]

M = diag(Haᵢ)。同一步使用相同 `Cⁿ⁺¹` 累计出口质量和沉积质量，不对负浓度做破坏守恒的静默截断。床厚由沉积质量/节点面积/干体积密度计算。实际步长取 `duration/ceil(duration/dt)`，严格抵达终止时刻。隐式稳定不代表粗时间步具有足够精度。

每帧核查：

`初始悬沙 + 累计流入 − 累计流出 − 当前悬沙 − 累计沉积 ≈ 0`。

## 已验证的范围

数值测试包括均匀流解析解、串联阻力解析流量、静水沉降的后向欧拉解与时间加密、零沉降均匀浓度保持、斜石笼减速偏转、瞬态非负性/质量守恒、零泥沙、不合法参数与网格遗漏石笼警告。HTTP 测试通过内存请求模拟，不开放网络端口。

默认示例：40×16 m、H=1 m、参考流速 0.45 m/s、两道斜石笼、入口浓度 0.15 kg/m³、600 s；80×32 矩形网格分为 5120 个三角形，2673 节点，120 步。

| 矩形网格 | 通过流量 m³/s | 累积沉积 kg | 最大床厚 mm |
|---|---:|---:|---:|
| 40×16 | 6.652269 | 2.333085 | 0.147382 |
| 80×32（默认） | 6.516742 | 4.171999 | 0.145556 |
| 160×64 | 6.520480 | 4.774643 | 0.153732 |

默认案例悬浮 95.522239 kg，累计输入 586.506820 kg、输出 486.812582 kg，质量相对误差约 2.33×10⁻¹⁵。此误差表示离散收支闭合，不表示物理预测精度。

从默认网格加密到 160×64，累计沉积增加约 14.45%；流量变化约 0.057%。沉积尚不能认定网格收敛：石笼几何阶梯化、临界速度截断与迎风扩散都会影响结果。需要继续做空间与时间收敛检查，尤其不要用单次默认结果决定工程尺寸。

## 明确的适用边界

适用于观察“阻力区改变流向和局部速度，悬沙因此改变沉积位置”的早期概念实验。当前没有自由水面演化、变水深、干湿转换、回流尾涡、湍流、冲刷、级配、孔隙淤堵、地形反馈及现场标定。**不能预测石笼背后的惯性尾流沉积，也不能表示石笼表面机械截留。**沉积区域可能主要位于石笼内与邻近减速区域，而非自然形成尾涡后的下游沙洲。

积厚超过初始水深 10% 时会提示固定地形假设不适用。下一阶段若关心背水侧沉积/冲刷，应替换为含惯性的浅水或 Brinkman/流体动量求解器，并增加床面更新和侵蚀闭合；可保留现有配置、结果和守恒检查接口。

## 文件结构

- `sedimentcfd/engine.py`：网格、FEM 组装、水流、输运和床面累积。
- `sedimentcfd/server.py`：仅监听 127.0.0.1 的标准库 HTTP 服务。
- `sedimentcfd/web/index.html`：无外部依赖的中文交互界面。
- `sedimentcfd/__main__.py`：CLI、CSV/JSON/NPZ/HTML 导出。
- `examples/two_gabions.json`：完整示例参数。
- `examples/grid_study.py`：复现上表。
- `tests/`：物理/数值验证与无网络 HTTP 测试。

## 方法参考

以下资料用于核对弱形式与沉积建模思路；本实现为独立简化模型，不是对应软件的复制或等效替代。

- [FEniCS/DOLFINx：Poisson equation](https://docs.fenicsproject.org/dolfinx/main/python/demos/demo_poisson.html)：弱形式、P1 Lagrange 三角形与混合 Dirichlet/Neumann 边界。
- [USACE：EM 1110-2-1607](https://www.publications.usace.army.mil/Portals/76/Publications/EngineerManuals/EM_1110-2-1607.pdf)：沉降速度、悬沙浓度、临界沉积剪切和沉积率的关系。
- [HEC-RAS 2D Sediment Technical Reference Manual v6.4.1](https://www.hec.usace.army.mil/software/hec-ras/documentation/HEC-RAS%202D%20Sediment%20Technical%20Reference%20Manual-v6.4.1.pdf)：完整二维泥沙模型的对照参考。

## Thetis 实算预览

`source activate_thetis.sh` 后运行 `python scripts/physical_preview.py`，再用 `python -m http.server 8766 --bind 127.0.0.1 --directory outputs/physical_preview` 查看 http://127.0.0.1:8766/ 。配置依据见 [物理参数说明](examples/physical_preview_parameters.md)。这是求解结果回放；不连接旧网页的计算按钮。页面显示数值负值诊断，未经过场地实测标定。
