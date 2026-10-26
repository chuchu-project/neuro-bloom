// EnsureRunInBackground.cs —— 把「后台继续跑 + 窗口化」这两个打包设置钉死
//
// 起因一（后台冻结）：runInBackground=0 时，Unity 窗口一被切到后台，Play 的 Update
//   就停了 → 桥接不重连、天气/大模型指令都堆着不处理，推流也一起停。
//   演示时切到 PPT/浏览器、或者用户点到控制台网页看直播，都会中招。
//
// 起因二（全屏抢屏）：fullscreenMode 是全屏时，成品一打开就整个屏都是 Unity，
//   用户想一边看控制台一边看画面就做不到；而且 macOS 上退出全屏会触发图形设备
//   重置 + 场景重载，很容易把推流组件一起带走（详见 CameraStreamer.cs 文件头）。
//   所以成品默认走「窗口 + 可缩放」，要全屏时按用户自己的意愿去切。
//
// 这里在编辑器打开工程时自动纠正一次，避免这两个值被别的操作改回去。
// 注意：运行期还有一道保险（CameraStreamer 里每 5 秒重申 runInBackground），
// 所以就算打包设置漏了、或者用的是旧成品，也不会再出现「一切窗口直播就停」。
using UnityEditor;
using UnityEngine;

[InitializeOnLoad]
public static class EnsureRunInBackground
{
    static EnsureRunInBackground()
    {
        bool changed = false;

        if (!PlayerSettings.runInBackground)
        {
            PlayerSettings.runInBackground = true;
            changed = true;
        }

        // 3 = FullScreenMode.Windowed（0 独占全屏 / 1 无边框全屏 / 2 最大化 / 3 窗口）
        if (PlayerSettings.fullScreenMode != FullScreenMode.Windowed)
        {
            PlayerSettings.fullScreenMode = FullScreenMode.Windowed;
            changed = true;
        }

        if (!PlayerSettings.resizableWindow)
        {
            PlayerSettings.resizableWindow = true;
            changed = true;
        }

        if (changed)
        {
            Debug.Log("[SETTINGS] 已纠正打包设置：Run In Background=开，" +
                      "启动=窗口(可缩放)。切到别的窗口、或退出全屏，演示都不会冻结。");
        }
    }
}
