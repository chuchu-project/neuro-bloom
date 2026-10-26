# VR 开发机（Windows）配置说明

> 这台机器负责 Unity VR 的实时渲染与串流，是"世界"的所在地。

## 1. 软件安装

1. **Unity Hub** → 安装 **Unity 2022 LTS**，勾选：
   - Android Build Support（Meta Quest / Pico）
   - Windows Build Support (IL2CPP)
2. 新建项目时选择 **Universal Render Pipeline (URP)** 模板。

## 2. 必备 Package

Package Manager 安装：

| 包 | 用途 |
| --- | --- |
| XR Interaction Toolkit | 手柄/手部交互 |
| OpenXR Plugin | 跨头显标准 |
| Input System | 输入统一管理 |
| TextMeshPro | 引导语显示 |
| Newtonsoft Json | 解析桥接 JSON |

## 3. XR 设置

1. `Project Settings → XR Plug-in Management` 启用 **OpenXR**。
2. Quest：切到 Android 平台，OpenXR 里启用 **Meta Quest Support**。
3. 图形 API：Quest 用 **Vulkan**；PC VR 用 **D3D11/12**。
4. 关闭多视图以外的多余后处理以保帧率。

## 4. 接入脑波桥

1. 把 `unity/Scripts/BrainBridge.cs` 与 `NeuroWorldController.cs` 拷进 `Assets/Scripts`。
2. 新建空物体 `BrainBridge`，挂 `BrainBridge.cs`；场景里放 `NeuroWorldController` 并挂上。
3. `BrainBridge` 默认连接 `ws://127.0.0.1:8765/ws/world`；若 Unity 在另一台机器，改成 Mac 的内网 IP。
4. 场景准备三套可视化：
   - 放松：花朵绽放、蝴蝶、阳光
   - 专注：水晶生长、能量流动
   - 焦虑：乌云、雾气，触发深呼吸引导

## 5. 网络

- 用 Wi-Fi 6E 路由器专供头显串流，PC 与头显同网段。
- 串流优先 **Quest Link / Virtual Desktop**，延迟目标 < 40ms。

## 6. 录制演示

- Elgato 4K60 采集卡录 VR 画面；或头显内置录屏。
