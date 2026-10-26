// NeuroScreenMode.cs —— 全屏 / 小窗切换（只管逻辑，界面由 NeuroUI 画）
//
// 正常演示时建模画面就是全屏；点右上角「小窗」缩回 1280x800，方便和控制台并排看。
using UnityEngine;

public class NeuroScreenMode : MonoBehaviour
{
    [Tooltip("切回小窗时的尺寸")]
    public int windowWidth = 1280;
    public int windowHeight = 800;

    private bool _fullscreen;

    public bool IsFullscreen { get { return _fullscreen; } }

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    private static void Boot()
    {
        if (FindFirstObjectByType<NeuroScreenMode>() != null) return;
        var go = new GameObject("NeuroScreenMode");
        go.AddComponent<NeuroScreenMode>();
        DontDestroyOnLoad(go);
    }

    private void Awake()
    {
        // 启动模式在这里**主动定死**，不依赖 PlayerSettings 和命令行参数
        // （那两条路实测都会失效，结果就是每次启动都抢全屏、把用户桌面切走）。
        // 默认一律窗口；只有显式要求全屏（正式演示）才进全屏。
        bool wantFull = false;
        var env = System.Environment.GetEnvironmentVariable("NB_PLAYER_FULLSCREEN");
        if (env == "1") wantFull = true;
        foreach (var a in System.Environment.GetCommandLineArgs())
            if (a == "-nb-fullscreen") wantFull = true;

        _fullscreen = wantFull;
        Screen.SetResolution(
            wantFull ? Display.main.systemWidth : windowWidth,
            wantFull ? Display.main.systemHeight : windowHeight,
            wantFull ? FullScreenMode.FullScreenWindow : FullScreenMode.Windowed);
        Debug.Log($"[ScreenMode] 启动 {(wantFull ? "全屏" : $"窗口 {windowWidth}x{windowHeight}")}");
    }

    private void Start()
    {
        Invoke(nameof(Snap), 8f);   // 启动后自动存一张，方便自查
    }

    private void Update()
    {
        if (Input.GetKeyDown(KeyCode.F11)) Toggle("F11");
        if (Input.GetKeyDown(KeyCode.F12)) Snap();
    }

    private void ApplyFullscreen()
    {
        int w = Display.main.systemWidth, h = Display.main.systemHeight;
        if (w > 0 && h > 0) Screen.SetResolution(w, h, FullScreenMode.FullScreenWindow);
    }

    public void Toggle(string by = "按钮")
    {
        _fullscreen = !_fullscreen;
        if (_fullscreen) ApplyFullscreen();
        else Screen.SetResolution(windowWidth, windowHeight, FullScreenMode.Windowed);
        Debug.Log($"[ScreenMode] 切到 {(_fullscreen ? "全屏" : $"小窗 {windowWidth}x{windowHeight}")}（来源：{by}）");
    }

    /// <summary>把当前画面原样存成 PNG。</summary>
    public void Snap()
    {
        try
        {
            string p = System.IO.Path.Combine(Application.persistentDataPath, "unity_screen.png");
            ScreenCapture.CaptureScreenshot(p);
            Debug.Log($"[ScreenMode] 截图 {p}");
        }
        catch (System.Exception e) { Debug.LogWarning("[ScreenMode] 截图失败：" + e.Message); }
    }
}
