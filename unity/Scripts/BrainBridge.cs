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
}

public class BrainBridge : MonoBehaviour
{
    [Tooltip("桥接地址。本机 Unity 用 127.0.0.1；Quest / 另一台电脑改成 Mac 的内网 IP，如 ws://192.168.70.195:8765/ws/world")]
    public string url = "ws://127.0.0.1:8765/ws/world";

    [Tooltip("自动重连间隔（秒）")]
    public float retrySeconds = 2f;

    /// <summary>收到脑波状态时触发（已解析，在主线程）</summary>
    public event Action<WorldMessage> OnWorld;

    private ClientWebSocket _ws;
    private CancellationTokenSource _cts;
    private readonly ConcurrentQueue<string> _queue = new ConcurrentQueue<string>();

    public bool IsConnected => _ws != null && _ws.State == WebSocketState.Open;

    async void Start()
    {
        _cts = new CancellationTokenSource();
        await ConnectLoop(_cts.Token);
    }

    private async Task ConnectLoop(CancellationToken token)
    {
        while (!token.IsCancellationRequested)
        {
            try
            {
                _ws = new ClientWebSocket();
                await _ws.ConnectAsync(new Uri(url), token);
                Debug.Log($"[BrainBridge] 已连接 {url}");
                await ReceiveLoop(token);
            }
            catch (OperationCanceledException) { break; }
            catch (Exception e)
            {
                Debug.LogWarning($"[BrainBridge] 连接失败：{e.Message}，{retrySeconds}s 后重试");
            }

            try { await Task.Delay(TimeSpan.FromSeconds(retrySeconds), token); }
            catch (OperationCanceledException) { break; }
        }
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

    void Update()
    {
        while (_queue.TryDequeue(out var msg))
        {
            WorldMessage parsed = null;
            try { parsed = JsonUtility.FromJson<WorldMessage>(msg); }
            catch (Exception e) { Debug.LogWarning($"[BrainBridge] JSON 解析失败：{e.Message}"); }

            if (parsed != null)
            {
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
