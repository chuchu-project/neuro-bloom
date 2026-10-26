// PlayStateProbe.cs —— 仅调试用：Play 状态一变就往 Editor.log 打一行。
// 为什么需要它：从外面（脚本/AppleScript）没法可靠判断编辑器到底在不在 Play。
//   - "8765 连接数"会骗人（脚本重编译后连接不回来，但仍在 Play）
//   - "[GARDEN] 心跳"会骗人（那个脚本是 [ExecuteAlways]，编辑态也刷）
//   - 日志里 "Reloading assemblies for play mode" 只记录"进入"，不记录"退出"
// 有了这个探针，每次改动脚本后，只要 grep PlayStateProbe 就能拿到权威答案。
using UnityEditor;
using UnityEngine;

[InitializeOnLoad]
public static class PlayStateProbe
{
    private static bool _last;
    private static bool _wasCompiling;

    static PlayStateProbe()
    {
        _last = EditorApplication.isPlaying;
        _wasCompiling = EditorApplication.isCompiling;
        EditorApplication.update += Tick;
        Debug.Log($"[PLAYPROBE] 初始化：isPlaying={_last}（编译中={_wasCompiling}）");
    }

    private static void Tick()
    {
        bool now = EditorApplication.isPlaying;
        if (now != _last)
        {
            _last = now;
            Debug.Log($"[PLAYPROBE] isPlaying -> {now}");
        }

        bool compiling = EditorApplication.isCompiling;
        if (compiling != _wasCompiling)
        {
            _wasCompiling = compiling;
            Debug.Log($"[PLAYPROBE] 编译中 -> {compiling}");
        }
    }

    // 为什么加菜单入口：从外面发快捷键（⌘P）有时进不了 Unity 的窗口，但"菜单点击"永远有效。
    // 这样自动化就能稳定地进出 Play。
    [MenuItem("NeuroBloom/切换 Play 状态 %#p")]
    private static void TogglePlay()
    {
        bool want = !EditorApplication.isPlaying;
        EditorApplication.isPlaying = want;
        Debug.Log($"[PLAYPROBE] 菜单请求切换 Play -> {want}");
    }

    [MenuItem("NeuroBloom/确保已退出 Play")]
    private static void EnsureStopped()
    {
        if (EditorApplication.isPlaying) EditorApplication.isPlaying = false;
        Debug.Log("[PLAYPROBE] 菜单请求：确保退出 Play（当前 isPlaying=" + EditorApplication.isPlaying + "）");
    }
}
