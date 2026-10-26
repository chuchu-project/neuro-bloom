// CameraStreamer.cs —— 把 Unity 的画面实时推给控制台网页。
//
// 做法：每隔一小会儿，把相机单独渲染到一张 RenderTexture 上，读回像素、
// 压成 JPEG，用 WebSocket 二进制帧发给 bridge。bridge 原样转发给浏览器。
//
// 为什么不截屏：截屏要系统录屏权限，还会把别的窗口拍进去。这样渲染只有
// 这一个相机的画面，干净、可控、也不会打扰正在用电脑的人。
//
// 不用手动挂：进入 Play 时会自动给主相机加一个（见文件末尾的 Bootstrap）。
//
// ── 2026-10-21 修：「非全屏也要能实时播」────────────────────────
// 现象：成品一打开就全屏；用户强退全屏之后，网页里的直播就停了，控制台显示
//       「Unity 停止播放了」。而全屏状态下反而是好的 —— 所以问题不在抓帧本身。
//
// 两条互相独立的成因，都修在这里：
//
//  1) 后台不跑 Update。
//     全屏时窗口永远在最前，Update 一直在跑；一退出全屏，用户往往会点到浏览器
//     去看直播，Unity 窗口就失去焦点。这时如果 runInBackground 没真正生效，
//     Unity 会停掉 Update —— 而抓帧和发送全在 Update 里，于是一帧都不再发出。
//     修法：运行期把 Application.runInBackground 钉成 true，并且每 5 秒重申一次
//     （窗口切来切去、系统省电策略都可能把它改回去）。
//     不依赖打包设置有没有勾对 —— 打包设置可能被改回、旧成品也可能没带上。
//
//  2) 切换全屏/窗口会重载场景，把推流器一起销毁。
//     macOS 上全屏↔窗口切换会触发图形设备重置，Unity 会重建渲染资源并重载场景。
//     本组件是靠 RuntimeInitializeOnLoadMethod 在启动后挂上来的，而那个入口
//     **整个进程只跑一次** —— 场景一重载，组件被销毁就再也回不来了。
//     这正是「退出全屏之后永久停掉、怎么等都好不了」的原因。
//     修法：注册 SceneManager.sceneLoaded，每次场景加载后检查一遍，缺了就补挂。
//     另外 Capture() 也做了自愈：相机被销毁就重新找，RenderTexture 丢了就重建。
//
// 注意：本组件的画面**不依赖屏幕**（渲染到离屏 RenderTexture），所以窗口被挡住、
// 被缩到一边、甚至完全在后台，推流照样是实时的。前提就是上面两条要成立。
using System;
using System.Net.WebSockets;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;
using UnityEngine.SceneManagement;

public class CameraStreamer : MonoBehaviour
{
    [Tooltip("推流地址。Unity 和 bridge 在同一台电脑就用 127.0.0.1")]
    public string url = "ws://127.0.0.1:8765/ws/video/pub";

    [Tooltip("要推哪台相机。留空 = 自动找主相机")]
    public Camera source;

    [Header("画质与流量")]
    [Tooltip("推出去的分辨率。1080p 在 Retina 上才不糊（16:9，控制台里按 cover 裁切）")]
    public int width = 1600;
    public int height = 900;

    [Tooltip("每秒推几张。18 张是流畅和 CPU 的折中；控制台端丢帧不会再拖慢它")]
    public int fps = 18;

    [Range(30, 95)]
    [Tooltip("JPEG 质量。85 在 1080p 下细节仍在，单帧约 300KB")]
    public int quality = 82;

    [Tooltip("离屏 RT 的 MSAA。1080p 下 4x 会让 ReadPixels 多一次全画面解析，实测吃掉一半帧率")]
    public int msaa = 1;

    [Header("连接")]
    public float retrySeconds = 2f;
    public float connectTimeout = 5f;

    [Header("状态")]
    [SerializeField] private bool connected;
    [SerializeField] private int framesSent;
    [SerializeField] private float lastFps;

    private RenderTexture _rt;
    private Texture2D _tex;
    private ClientWebSocket _ws;
    // 桥接每 4 秒发一次心跳。整条连接半死（对端进程被 kill、TCP 没收 RST）时，
    // ReceiveAsync 会一直挂着、SendAsync 还会"成功"，只看发送永远发现不了断线。
    private int _lastRecvMs;
    private CancellationTokenSource _cts;
    private int _connecting;
    private float _nextTry;
    private float _stuckAt;
    private int _sending;          // 0/1：同一时刻只允许一帧在路上
    private float _nextFrame;
    private float _statAt;
    private int _framesInWindow;
    private int _warnedStuck;

    // 保活与看门狗（见文件头第 1 条）
    private float _lastSentAt;      // 上一次真正送出画面（或刚连上）的时刻
    private float _nextKeepAlive;
    private int _warnedStall;
    private float _lastSendBegin;   // 这一帧是什么时候开始发的（用来发现"发送挂死"）
    private float _captureMs;       // 抓帧（渲染+读回+编码）平均耗时，用来定位帧率瓶颈

    public bool IsStreaming => connected;

    void Awake()
    {
        Application.runInBackground = true;   // 一上来就开，别等用户切窗口
        _cts = new CancellationTokenSource();
        RebuildTargets();
        if (source == null) source = ResolveCamera();
    }

    void OnEnable()
    {
        if (_cts == null || _cts.IsCancellationRequested) _cts = new CancellationTokenSource();
        _nextTry = 0f;
    }

    void Update()
    {
        // 每 5 秒重申一次后台运行。理由见文件头第 1 条：
        // 只要这一项掉了，窗口一失焦 Update 就停，直播会「静默」断掉。
        if (Time.unscaledTime >= _nextKeepAlive)
        {
            _nextKeepAlive = Time.unscaledTime + 5f;
            if (!Application.runInBackground) Application.runInBackground = true;
        }

        KickConnect();

        // ── 发送挂死自愈（2026-10-21 实测踩到：卡了 1982 秒画面不回来）──────
        // 浏览器刷新/关页之后，对端可能留下一条"半死"的 TCP：Socket 状态还是 Open，
        // 但对面永远不读。send 缓冲很快填满，_ws.SendAsync 就永远不返回 ——
        // _sending 卡在 1，之后每一帧都在下面被丢掉，而 KickConnect 看到 State==Open
        // 也不会重连。表现就是「已连接、相机和 RenderTexture 都正常，但一帧都发不出去」。
        // 所以：一帧超过 6 秒还没发完，就当连接死了，直接断掉重连。
        if (_sending != 0 && _lastSendBegin > 0f && Time.unscaledTime - _lastSendBegin > 6f)
        {
            Debug.LogWarning("[CameraStreamer] 一帧卡住超过 6 秒（对端不读），强制断开重连");
            Interlocked.Exchange(ref _sending, 0);
            _lastSendBegin = 0f;
            DropConnection();
        }

        // ── 连接半死自愈 ───────────────────────────────────────────
        // 桥接重启/被 kill 之后，这条 socket 可能既收不到关闭帧、也发不出错误：
        // 表现就是「还在 12fps 地推，但桥接那边一帧都没收到」。15 秒没收到心跳就重连。
        if (connected && Environment.TickCount - _lastRecvMs > 15000)
        {
            Debug.LogWarning("[CameraStreamer] 15 秒没收到桥接心跳（连接已半死），重连");
            DropConnection();
            return;
        }

        if (connected && Time.unscaledTime - _lastSentAt > 10f && _warnedStall++ % 3 == 0)
        {
            // 连上了却一直送不出画面 —— 把卡在哪一步打出来，省得下次再靠猜。
            Debug.LogWarning(
                $"[CameraStreamer] 已连接但 {Time.unscaledTime - _lastSentAt:0}s 没送出画面：" +
                $"相机={(source ? source.name : "丢了")}，" +
                $"RenderTexture={(_rt != null && _rt.IsCreated() ? "正常" : "丢了")}，" +
                $"后台运行={Application.runInBackground}");
        }

        if (!connected || source == null) return;
        if (Time.unscaledTime < _nextFrame) return;
        _nextFrame = Time.unscaledTime + 1f / Mathf.Max(1, fps);

        if (Interlocked.CompareExchange(ref _sending, 1, 0) != 0) return;   // 上一帧还没发完，这帧直接丢
        byte[] jpg;
        float capT0 = Time.realtimeSinceStartup;
        try { jpg = Capture(); }
        catch (Exception e)
        {
            Debug.LogWarning($"[CameraStreamer] 抓帧失败：{e.Message}");
            Interlocked.Exchange(ref _sending, 0);
            return;
        }
        if (jpg == null) { Interlocked.Exchange(ref _sending, 0); return; }
        _captureMs = Mathf.Lerp(_captureMs, (Time.realtimeSinceStartup - capT0) * 1000f, 0.25f);
        _lastSendBegin = Time.unscaledTime;          // 发送挂死自愈的起点（见上面）
        _ = SendAsync(jpg);

        _framesInWindow++;
        if (Time.unscaledTime - _statAt > 10f)
        {
            lastFps = _framesInWindow / (Time.unscaledTime - _statAt);
            _statAt = Time.unscaledTime;
            _framesInWindow = 0;
            Debug.Log($"[CameraStreamer] 推流中 {lastFps:0.0} fps，抓帧 {_captureMs:0.0} ms，累计 {framesSent} 帧");
        }
    }

    /// <summary>把离屏渲染目标建回来。图形设备重置后必须重建，否则读不到像素。</summary>
    private void RebuildTargets()
    {
        if (_rt != null && _rt.IsCreated()) return;
        if (_rt != null) { _rt.Release(); Destroy(_rt); }
        // 必须显式 sRGB：默认 Default 在 Gamma 项目里会把线性值原样留在 RT 上，
        // ReadPixels 读出来再 JPEG 编码，画面就整体被提亮、对比被压平（控制台里那种"发灰发糊"）。
        _rt = new RenderTexture(width, height, 24, RenderTextureFormat.ARGB32, RenderTextureReadWrite.sRGB)
        { name = "NeuroBloomStream" };
        _rt.antiAliasing = Mathf.Max(1, msaa);
        _rt.Create();
        if (_tex == null) _tex = new Texture2D(width, height, TextureFormat.RGB24, false);
    }

    /// <summary>找一台能用的相机。场景重载后原来的引用会被销毁，得重新找。</summary>
    private static Camera ResolveCamera()
    {
        var cam = Camera.main;
        if (cam == null)
        {
            var all = Camera.allCameras;
            if (all != null && all.Length > 0) cam = all[0];
        }
        return cam;
    }

    private byte[] Capture()
    {
        // 自愈：设备重置 / 场景重载之后，相机和 RenderTexture 都可能已经没了。
        // 不补这两下，Capture() 会一直抛异常，看起来就是「直播停了」。
        if (source == null) source = ResolveCamera();
        if (source == null) return null;
        if (_rt == null || !_rt.IsCreated()) RebuildTargets();

        var prevTarget = source.targetTexture;
        var prevActive = RenderTexture.active;

        source.targetTexture = _rt;
        source.Render();                       // 只渲染这一台相机到 _rt，不碰屏幕
        source.targetTexture = prevTarget;

        RenderTexture.active = _rt;
        _tex.ReadPixels(new Rect(0, 0, width, height), 0, 0, false);
        RenderTexture.active = prevActive;

        return ImageConversion.EncodeToJPG(_tex, quality);
    }

    private async Task SendAsync(byte[] payload)
    {
        var ws = _ws;
        if (ws == null || ws.State != WebSocketState.Open)
        {
            Interlocked.Exchange(ref _sending, 0);
            _lastSendBegin = 0f;
            return;
        }
        try
        {
            // 必须带超时：对端不读的时候 SendAsync 会永远挂着（见 Update 里的说明）。
            // 6 秒发不出去一帧 = 这条连接已经废了，直接断掉让 KickConnect 重连。
            using (var to = CancellationTokenSource.CreateLinkedTokenSource(_cts.Token))
            {
                to.CancelAfter(TimeSpan.FromSeconds(6));
                await ws.SendAsync(new ArraySegment<byte>(payload), WebSocketMessageType.Binary, true, to.Token);
            }
            framesSent++;
            _lastSentAt = Time.unscaledTime;      // 真的送出去了才算（看门狗看这个）
        }
        catch (OperationCanceledException)
        {
            if (!_cts.IsCancellationRequested)
            {
                Debug.LogWarning("[CameraStreamer] 发送超时（6 秒），断开重连");
                DropConnection();
            }
        }
        catch (Exception e)
        {
            Debug.LogWarning($"[CameraStreamer] 发送中断：{e.Message}");
            DropConnection();
        }
        finally
        {
            _lastSendBegin = 0f;
            Interlocked.Exchange(ref _sending, 0);
        }
    }

    private void KickConnect()
    {
        if (_cts == null || _cts.IsCancellationRequested) return;
        if (_ws != null && _ws.State == WebSocketState.Open) return;
        if (Time.unscaledTime < _nextTry) return;

        if (Interlocked.CompareExchange(ref _connecting, 1, 0) != 0)
        {
            // 上一次连接卡住了（ConnectAsync 在个别网络状态下不返回）→ 超时后放行
            if (_stuckAt <= 0f) _stuckAt = Time.unscaledTime;
            else if (Time.unscaledTime - _stuckAt > 8f)
            {
                if (_warnedStuck++ % 5 == 0)
                    Debug.LogWarning("[CameraStreamer] 上次连接卡住，强制重来");
                Interlocked.Exchange(ref _connecting, 0);
                _stuckAt = 0f;
                _nextTry = Time.unscaledTime;
            }
            return;
        }

        _stuckAt = 0f;
        _nextTry = Time.unscaledTime + Mathf.Max(1f, retrySeconds);
        _ = ConnectAsync();
    }

    private async Task ConnectAsync()
    {
        try
        {
            var ws = new ClientWebSocket();
            using (var to = CancellationTokenSource.CreateLinkedTokenSource(_cts.Token))
            {
                to.CancelAfter(TimeSpan.FromSeconds(connectTimeout));
                await ws.ConnectAsync(new Uri(url), to.Token);
            }
            _ws = ws;
            connected = true;
            _statAt = Time.unscaledTime;
            _framesInWindow = 0;
            _lastSentAt = Time.unscaledTime;   // 刚连上，看门狗从现在开始计时
            _lastRecvMs = Environment.TickCount;
            _warnedStall = 0;
            Debug.Log($"[CameraStreamer] 已连接 {url}");
            await ReceiveLoop();
        }
        catch (OperationCanceledException) { }
        catch (Exception e)
        {
            if (_warnedStuck++ % 5 == 0)
                Debug.LogWarning($"[CameraStreamer] 连接失败：{e.Message}（{retrySeconds}s 后重试）");
        }
        finally { Interlocked.Exchange(ref _connecting, 0); }
    }

    private async Task ReceiveLoop()
    {
        var buf = new byte[256];
        try
        {
            while (_ws != null && _ws.State == WebSocketState.Open)
            {
                var r = await _ws.ReceiveAsync(new ArraySegment<byte>(buf), _cts.Token);
                _lastRecvMs = Environment.TickCount;   // 心跳/任何字节都算活着
                if (r.MessageType == WebSocketMessageType.Close) break;
            }
        }
        catch { }
        finally { DropConnection(); }
    }

    private void DropConnection()
    {
        connected = false;
        try { _ws?.Dispose(); } catch { }
        _ws = null;
    }

    void OnDestroy()
    {
        try { _cts?.Cancel(); } catch { }
        DropConnection();
        if (_rt != null) { _rt.Release(); Destroy(_rt); _rt = null; }
        if (_tex != null) { Destroy(_tex); _tex = null; }
    }
}

/// <summary>
/// 进入 Play / 每次场景加载时，确保主相机上挂着一个推流器。
/// 这样不用改场景、不用手动拖组件 —— 搭建好的场景被重建也不会丢。
/// 不想要推流时，把自己的 CameraStreamer 挂上并把 autoStart 关掉即可。
///
/// 为什么要盯 sceneLoaded：全屏↔窗口切换会让 Unity 重载场景，把这里挂上去的
/// 组件销毁掉。只挂一次（RuntimeInitializeOnLoadMethod）的话，销毁后就再也回不来，
/// 表现就是「退出全屏后直播永久停掉」。见文件头第 2 条。
/// </summary>
public static class CameraStreamerBootstrap
{
    public static bool autoStart = true;
    private static bool _hooked;

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    private static void Boot()
    {
        if (!autoStart) return;

        // 双保险：打包设置里 runInBackground 关过、旧成品没带上，都在这里补回来。
        Application.runInBackground = true;

        if (!_hooked)
        {
            _hooked = true;
            SceneManager.sceneLoaded += OnSceneLoaded;
        }
        Attach();
    }

    private static void OnSceneLoaded(Scene scene, LoadSceneMode mode)
    {
        if (!autoStart) return;
        Attach();
    }

    private static void Attach()
    {
        if (UnityEngine.Object.FindAnyObjectByType<CameraStreamer>() != null) return;

        var cam = Camera.main;
        if (cam == null)
        {
            var all = Camera.allCameras;
            if (all != null && all.Length > 0) cam = all[0];
        }
        if (cam == null) return;

        var s = cam.gameObject.AddComponent<CameraStreamer>();
        s.source = cam;
        Debug.Log($"[CameraStreamer] 已挂到相机 {cam.name}，开始向控制台推流");
    }
}
