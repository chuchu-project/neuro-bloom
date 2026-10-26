// NeuroBloomBuild.cs —— 命令行构建 macOS 成品（只在带 -nbBuild 时生效）
//
// 为什么要它：控制台要能「一键开始演示」，就不该让用户先打开 Unity 编辑器再按 Play。
// 这里直接产出一个双击即跑、自动全屏、自动连桥接的 NeuroBloom.app，
// 之后控制台只负责启动/关闭这个 app，用户全程不碰 Unity。
//
// 用法（终端）：
//   TMPDIR=/tmp open -a "/Applications/Unity/Hub/Editor/6000.0.22f1/Unity.app" \
//     --args -projectPath <工程绝对路径> -nbBuild
// 产物：UnityVR/Build/NeuroBloom.app
using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;

[InitializeOnLoad]
public static class NeuroBloomBuild
{
    const string Flag = "-nbBuild";
    const string Scene = "Assets/Scenes/NeuroBloom.unity";
    static bool started;

    static NeuroBloomBuild()
    {
        if (started || !HasFlag(Flag)) return;
        started = true;
        EditorApplication.delayCall += Run;
    }

    static bool HasFlag(string flag)
    {
        foreach (var a in Environment.GetCommandLineArgs())
            if (string.Equals(a, flag, StringComparison.OrdinalIgnoreCase)) return true;
        return false;
    }

    public static void Run()
    {
        string root = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
        string outDir = Path.Combine(root, "Build");
        string logPath = Path.Combine(root, ".deepworks/tmp/build_player.log");
        try
        {
            // Apple Silicon 原生，避免成品跑在 Rosetta 里掉帧
            try
            {
                UnityEditor.OSXStandalone.UserBuildSettings.architecture =
                    UnityEditor.Build.OSArchitecture.ARM64;
            }
            catch (Exception) { /* 老版本 API 不存在就按默认架构构建 */ }

            Directory.CreateDirectory(outDir);
            Directory.CreateDirectory(Path.GetDirectoryName(logPath));

            // 成品名就是观众在 Dock 里看到的名字。工程内部叫 UnityVR 无所谓，
            // 但 .app 的二进制名跟着 productName 走，桥接靠它判断「成品在不在跑」。
            PlayerSettings.productName = "NeuroBloom";
            PlayerSettings.companyName = "NeuroBloom";

            // 关掉 Retina 2x 渲染：窗口 1280×800 时，2x 会让 Unity 每帧渲染
            // 2560×1600 ≈ 4MP，只为了一个用户根本不看的窗口；推流另有离屏 RT。
            // 实测这就是推流只有 12–15fps 的真凶（CPU 被重复渲染吃满）。
            try { PlayerSettings.macRetinaSupport = false; }
            catch (Exception) { }

            var scenes = EditorBuildSettings.scenes
                .Where(s => s.enabled)
                .Select(s => s.path)
                .ToArray();
            if (scenes.Length == 0) scenes = new[] { Scene };

            var opts = new BuildPlayerOptions
            {
                scenes = scenes,
                locationPathName = Path.Combine(outDir, "NeuroBloom.app"),
                target = BuildTarget.StandaloneOSX,
                options = BuildOptions.None,
            };

            BuildReport report = BuildPipeline.BuildPlayer(opts);
            var s = report.summary;
            string line = $"{s.result} size={s.totalSize} errors={s.totalErrors} " +
                          $"time={s.totalTime} out={opts.locationPathName}";
            File.WriteAllText(logPath, line + "\n");
            Debug.Log("[NeuroBloomBuild] " + line);
            EditorApplication.Exit(s.result == BuildResult.Succeeded ? 0 : 1);
        }
        catch (Exception e)
        {
            File.WriteAllText(logPath, "fail " + e + "\n");
            Debug.LogError("[NeuroBloomBuild] " + e);
            EditorApplication.Exit(1);
        }
    }
}
