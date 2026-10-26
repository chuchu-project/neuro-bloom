// BrainBridge.cs —— Unity 端 WebSocket 客户端（零依赖，用 Unity 自带 JsonUtility）
// 订阅 bridge 的 world 通道，接收 {type,label,...,world_params,script}，在主线程回调。
using System;
using System.Collections.Concurrent;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;

/// <summary>世界观参数（0~1）</summary>
[Serializable]
public class WorldParams
{
    public float bloom;     // 花/植被绽放
    public float sunlight;  // 阳光
    public float fog;       // 雾气
    public float crystal;   // 水晶生长
}

/// <summary>bridge 下发的完整消息</summary>
[Serializable]
public class WorldMessage
{
    public string type;    // "state"
    public string label;   // relaxed / focused / anxious / unknown
    public float focus;    // 专注度 0~1
    public float calm;     // 放松度 0~1
    public WorldParams world_params;
    public string script;  // 向导引导语（可能为空）
    public string weather; // 可选：天气指令 sunny / hazy / night（空=不改）

    // type == "plant" 时用：控制台上点画面的位置（比例坐标，原点左上角）
    public float u;
    public float v;
}

public class BrainBridge : MonoBehaviour
{
    [Tooltip("桥接地址。本机 Unity 用 127.0.0.1；Quest / 另一台电脑改成 Mac 的内网 IP，如 ws://192.168.70.195:8765/ws/world")]
    public string url = "ws://127.0.0.1:8765/ws/world";

    [Tooltip("自动重连间隔（秒）")]
    public float retrySeconds = 2f;

    [Tooltip("单次连接的超时（秒）。连接卡住不返回时，靠它把重试放出来")]
    public float connectTimeout = 5f;

    [Tooltip("上一次连接尝试卡住多久算死、强制重来（秒）")]
    public float stuckTimeout = 8f;

    /// <summary>收到脑波状态时触发（已解析，在主线程）</summary>
    public event Action<WorldMessage> OnWorld;

    private ClientWebSocket _ws;
    private CancellationTokenSource _cts;
    private readonly ConcurrentQueue<string> _queue = new ConcurrentQueue<string>();

    public bool IsConnected => _ws != null && _ws.State == WebSocketState.Open;

    // 连接改由主线程的 Update() 看门狗负责拉起（见下方 KickConnect）。
    // 原来只靠 async void Start() 里跑一个大循环：脚本一重编译、Play 里发生一次域重载之后，
    // 那个循环有时再也不会回来 —— 实测就是"⌘R 之后连接数一直是 0，字牌永远停在旧句子"。
    // 交给 Update() 兜底以后，断线会在 retrySeconds 秒内自己接回来，演示途中改脚本也不怕。
    private int _connecting;      // 0/1：同一时刻只允许一个连接任务在跑
    private float _nextKick;
    private float _stuckSince;    // 连接尝试卡住的起点（0 = 没卡）

    // 说明：这里原来有一段 Awake()，会在成品里执行
    //     Screen.SetResolution(1280, 720, FullScreenMode.FullScreenWindow);
    // 本意是「观众看到的不该是一个带标题栏的小窗口」，但它是运行期强行改显示模式，
    // 会把打包设置和启动参数（-screen-fullscreen 0 / 1）一起盖掉 —— 表现就是「一打开必定全屏」。
    // 而 macOS 上退出全屏会触发显示模式切换 + 图形设备重建，推流组件和场景很容易被一起带走
    // （详见 CameraStreamer.cs 文件头），于是就成了「一退全屏就停播」。
    //
    // 所以删掉了。窗口化还是全屏，交给打包设置和启动参数决定：
    //   · 默认（窗口 + 可缩放）：双击成品，或控制台点「开始演示」
    //   · 想全屏演示：启动桥接前 export NB_PLAYER_FULLSCREEN=1
    //                 或 open -a NeuroBloom.app --args -screen-fullscreen 1
    // 编辑器里本来就不该动显示模式，现在也不必再判断 Application.isEditor 了。

    void Start()
    {
        _cts = new CancellationTokenSource();
        _nextKick = 0f;                                     // 第一帧就去连
    }

    void OnEnable()
    {
        if (_cts == null) _cts = new CancellationTokenSource();
        _nextKick = 0f;
    }

    private void KickConnect()
    {
        if (_cts == null || _cts.IsCancellationRequested) return;
        if (IsConnected) return;
        if (Time.time < _nextKick) return;
        if (Interlocked.CompareExchange(ref _connecting, 1, 0) != 0)
        {
            // 上一次尝试还没结束。如果它卡了（ConnectAsync 在个别网络状态下会不返回），
            // 这个标志就会永远停在 1，重连就再也不发生了 —— 实测踩到过。
            // 超过 stuckTimeout 秒就判死，放行重来。
            if (_stuckSince <= 0f) _stuckSince = Time.time;
            else if (Time.time - _stuckSince > stuckTimeout)
            {
                Debug.LogWarning($"[BrainBridge] 上次连接尝试卡住超过 {stuckTimeout}s，强制重来（_ws={( _ws == null ? "null" : _ws.State.ToString())}）");
                Interlocked.Exchange(ref _connecting, 0);
                _stuckSince = 0f;
                _nextKick = Time.time;
            }
            return;
        }
        _stuckSince = 0f;
        _nextKick = Time.time + Mathf.Max(1f, retrySeconds);
        Debug.Log($"[BrainBridge] 开始连接 {url}");
        _ = ConnectOnce(_cts.Token);
    }

    private async Task ConnectOnce(CancellationToken token)
    {
        try
        {
            var ws = new ClientWebSocket();
            // 给连接本身也加超时：不能让它无限卡住（否则看门狗的重试标志永远放不出来）
            using (var to = CancellationTokenSource.CreateLinkedTokenSource(token))
            {
                to.CancelAfter(TimeSpan.FromSeconds(connectTimeout));
                await ws.ConnectAsync(new Uri(url), to.Token);   // 主线程发起，await 回来仍在主线程
            }
            _ws = ws;
            Debug.Log($"[BrainBridge] 已连接 {url}");
            await ReceiveLoop(token);                       // 断线即返回，交给看门狗重连
        }
        catch (OperationCanceledException) { }
        catch (Exception e)
        {
            Debug.LogWarning($"[BrainBridge] 连接失败：{e.Message}，{retrySeconds}s 后重试");
        }
        finally { Interlocked.Exchange(ref _connecting, 0); }
    }

    private async Task ReceiveLoop(CancellationToken token)
    {
        var buffer = new byte[8192];
        while (_ws != null && _ws.State == WebSocketState.Open && !token.IsCancellationRequested)
        {
            var sb = new StringBuilder();
            WebSocketReceiveResult result;
            try
            {
                do
                {
                    result = await _ws.ReceiveAsync(new ArraySegment<byte>(buffer), token);
                    if (result.MessageType == WebSocketMessageType.Close)
                    {
                        await _ws.CloseAsync(WebSocketCloseStatus.NormalClosure, "bye", token);
                        return;
                    }
                    sb.Append(Encoding.UTF8.GetString(buffer, 0, result.Count));
                } while (!result.EndOfMessage);
            }
            catch (OperationCanceledException) { return; }
            catch (Exception e)
            {
                Debug.LogWarning($"[BrainBridge] 接收中断：{e.Message}");
                return;
            }
            _queue.Enqueue(sb.ToString());
        }
    }

    private NeuroGarden _garden;

    /// <summary>
    /// 控制台点了画面 → 桥接转成 {"type":"plant",u,v} 发过来 → 在这里落到草地上种一株。
    /// 这样用户点网页里的画面就行，不用去点 Unity 窗口（窗口经常在后台或被挡住）。
    /// </summary>
    private void HandlePlant(float u, float v)
    {
        if (_garden == null) _garden = UnityEngine.Object.FindAnyObjectByType<NeuroGarden>();
        if (_garden == null)
        {
            Debug.LogWarning("[BrainBridge] 收到播种指令，但场景里没有 NeuroGarden");
            return;
        }
        bool ok = _garden.PlantFromViewport(u, v);
        Debug.Log($"[BrainBridge] 播种指令 u={u:0.00} v={v:0.00} → {(ok ? "成功" : "没种下")}");
    }

    void Update()
    {
        KickConnect();          // 连接断了就自己接回来（域重载/重编译之后尤其需要）
        while (_queue.TryDequeue(out var msg))
        {
            WorldMessage parsed = null;
            try { parsed = JsonUtility.FromJson<WorldMessage>(msg); }
            catch (Exception e) { Debug.LogWarning($"[BrainBridge] JSON 解析失败：{e.Message}"); }

            if (parsed != null)
            {
                // 控制台点了一下画面：这是"手控种花"，不参与脑控状态
                if (parsed.type == "plant") { HandlePlant(parsed.u, parsed.v); continue; }

                if (parsed.world_params == null) parsed.world_params = new WorldParams();
                OnWorld?.Invoke(parsed);
            }
        }
    }

    async void OnDestroy()
    {
        try { _cts?.Cancel(); } catch { }
        if (_ws != null && _ws.State == WebSocketState.Open)
        {
            try { await _ws.CloseAsync(WebSocketCloseStatus.NormalClosure, "bye", CancellationToken.None); }
            catch { }
        }
    }
}
