// NightRenderAuto.cs —— 夜间自动出图入口（只在命令行带 -nbRender 时生效）
// 为什么需要它：这台机器上 System Events 点 Unity 菜单点了不执行，
// 所以改用「启动参数 + InitializeOnLoad」触发渲染，渲染完自动退出。
// 平时正常打开 Unity（不带 -nbRender）不会执行任何代码。
using System;
using System.IO;
using UnityEditor;
using UnityEngine;

[InitializeOnLoad]
public static class NightRenderAuto
{
    const string Flag = "-nbRender";
    static bool started;

    static NightRenderAuto()
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

    static void Run()
    {
        string dir = Path.Combine(Application.dataPath, "../.deepworks/tmp");
        string log = Path.Combine(dir, "render_auto.log");
        try
        {
            Directory.CreateDirectory(dir);
            NeuroBloomRender.Run();
            File.WriteAllText(log, "ok " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "\n");
        }
        catch (Exception e)
        {
            File.WriteAllText(log, "fail " + e + "\n");
        }
        EditorApplication.Exit(0);
    }
}
