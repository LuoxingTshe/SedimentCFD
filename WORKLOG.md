# 工作日志

按日期记录做了什么、为什么这样做、结果怎么验证。代码结构以 README 为准，这里只记录过程和决策。

## 2026-11-09 · 含沙量配色、石笼线型、视频导出

分支 `cage-dxf`，基于 `outputs/cage`（Cage.dxf 算例，133 × 115 m，右侧来流，耦合 600 s）。

### 网页预览（`sedimentcfd/web/preview.html`）

- 含沙量色带由黑 → 绿改为黑 → 红 `#FF2C2C`（新增常量 `RED`，`color()` 中为 `concentration` 单独加分支）。流速、水深仍用 `GREEN`，床面变化仍为橙 / 蓝。图例自动跟随。
- 示踪粒子颜色不变：粒子由流速场驱动，不是泥沙颗粒，不算含沙量相关颜色。
- 石笼轮廓由 2 px 白色虚线改为 1 px 白色细实线；图注文字同步。
- 已有的 `outputs/cage`、`outputs/preview` 页面用各自的 `preview_data.json` 重新拼接模板，没有重新求解。

### 结果数据的存储方式（核查结论）

- 耦合阶段按模拟时间等分导出：`simulation_export_time = duration / 40`，共 41 帧；cage 算例每 15 s 一帧，求解步长 0.5 s。spin-up 阶段不进 JSON。
- 每帧存 DG0 单元平均：`uv`（唯一的矢量）、`concentration`、`bed`、`depth`，保留 4 位有效数字；cage 算例 30 590 个三角形，`preview_data.json` 约 57 MB。
- 网页在快照之间线性插值播放（约 4.5 快照/s），canvas 光栅填色；15 s 以内的变化不可见。

### 视频导出（新增 `sedimentcfd/video.py`，命令 `python -m sedimentcfd video <run>`）

- 流程：读 `preview_data.json` → 逐帧渲染 PNG（`<run>/frames_<field>/frame_%05d.png`）→ FFmpeg 合成 MP4（libx264，CRF 16，yuv420p，BT.709 矩阵与标签，faststart）。
- 默认 2560 × 1440、60 fps、20 s：600 s 模拟均匀铺满 1200 帧，每帧 0.5 s 模拟时间（与求解步长相同）。场在快照间线性插值，与网页一致。
- 渲染方法：预先算出每个像素对应的三角形（重心坐标检测），之后每帧只做查表上色，速度很快；石笼、坐标轴、色标和面板文字作为静态叠加层，按 2 倍尺寸绘制后用 box 滤波缩小，实现抗锯齿。
- 示踪粒子：2400 个，RK2 中点法，与网页规则相同（入流侧补充、最大寿命、每帧淡出 7%）；但按模拟时间与场同步推进，所以屏幕上的速度与时间标签一致。网页里粒子按 3× 实时播放，与场的时间不同步。
- 右侧面板全部为英文，字体 Helvetica：
  - 标题、场名、未标定声明；
  - 时间、悬沙质量、进度条；
  - 横向色标；
  - 模型框架（Thetis 2D / Firedrake、DG P1–P1、隐式 Euler、Δt、网格、spin-up）和主要方程：浅水方程（含水平黏性、Nikuradse 二次底摩擦、石笼线性阻力 K_g）、守恒型悬沙方程、Exner 方程；符号与参数取自本算例配置。公式按 Thetis 源码（`shallowwater_eq.py`、`sediment_eq_2d.py`、`exner_eq.py`）核对。
- 字体处理：
  - matplotlib 只读取 `.ttc` 的第一个字体，所以用 fontTools 把 Helvetica 的常规、粗体、斜体拆到临时目录再注册，公式缺字回退 STIX Sans。
  - Helvetica 没有箭头字形，来流方向用文字说明。
  - 早先的中文版本里，冬青黑体缺 `³`，现已不用中文字体。
- 环境：本机原先没有 FFmpeg，用 Homebrew 安装了 ffmpeg 9.0.2（含 libx264）。Pillow、matplotlib 来自 `.venv-firedrake`；`pyproject.toml` 增加可选依赖 `video`。

### 产出与验证

- `outputs/cage/concentration_2560x1440_60fps.mp4`：ffprobe 显示 h264、2560 × 1440、60/1 fps、1200 帧、20.0 s、yuv420p、bt709，约 57 MB；渲染加编码约 3.5 分钟。序列帧约 1.6 GB。两者都在 `outputs/` 下，不进 git。
- 抽帧目检：t ≈ 300 s、360 s 画面中，色带、细实线石笼、面板排版、单位 kg/m³ 显示正常。
- 测试：新增 `tests/test_video.py`（帧数与配色、像素全覆盖、奇数尺寸报错、ffprobe 校验 MP4）；全部测试通过。

### 已知局限 / 后续可做

- 时间分辨率受 41 个快照限制（cage 每 15 s 一个）；要更细需改 `model.py` 的导出间隔并重新求解，JSON 体积大约按比例增长。
- 色标取全时段极值（0–1 kg/m³）；高浓度只出现在入流附近，大部分区域偏暗。可考虑固定范围或做非线性拉伸。
- Helvetica 斜体的 ν 与 v 形近，面板下方用参数说明区分。
- 结果仍是未标定的定性预览，只能用于在固定参数下比较不同布局。
