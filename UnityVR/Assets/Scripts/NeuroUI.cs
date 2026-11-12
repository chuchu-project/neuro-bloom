// NeuroUI.cs —— 应用外壳。
//
// 打开不是一个铺满屏幕的 3D 场景，而是正常 app 的三层结构：
//    顶部品牌/问候 → 内容卡片（3D 花园框在圆角卡片里）→ 底部标签栏
// 点卡片进入沉浸全屏；沉浸态右上角「退出」回到外壳。
//
// 3D 仍由主相机直接上屏，外壳只在卡片之外盖住画面（四角遮罩挖圆角），
// 不动相机、不重建 RenderTexture，推流和帧率完全不受影响。
//
// ⚠️ 字号必须用独立 GUIStyle：同一个 style 在一帧内改 fontSize 再 Label，
//    新字号的字形图集来不及生成，字会整段不画（中文尤其明显）。
//    所以统一走 S(px, anchor, color) 缓存。
using System.Collections.Generic;
using UnityEngine;

public class NeuroUI : MonoBehaviour
{
    private enum Page { Home, Breath, About }
    private enum Mode { Shell, Immersive }

    private Page _page = Page.Home;
    private Mode _mode = Mode.Immersive;

    // 启动保护：成品以全屏启动时，macOS 在全屏切换/聚焦那一下会补发 Esc，
    // Unity 的 OnGUI 还可能把同一次按键投递两帧（实测 4.9s 和 8.3s 各一次）。
    // 不挡就是「一打开自己掉进控制台」，用户会以为程序坏了。
    private float _bootTime;
    private float _lastEscAt = -10f;

    private static readonly Color BG     = new Color(0.043f, 0.055f, 0.075f, 0.995f);
    private static readonly Color CARD   = new Color(0.086f, 0.106f, 0.137f, 0.62f);
    private static readonly Color INK    = new Color(0.93f, 0.95f, 1.00f, 0.96f);
    private static readonly Color INK2   = new Color(0.60f, 0.68f, 0.82f, 0.82f);
    private static readonly Color ACCENT = new Color(0.42f, 0.86f, 0.78f, 1.00f);

    private Font _font;
    private readonly Dictionary<string, GUIStyle> _styles = new Dictionary<string, GUIStyle>();
    private Texture2D _solid, _disc, _fadeTop, _fadeBottom;
    private Texture2D[] _corner;

    private readonly Dictionary<string, Rect> _zones = new Dictionary<string, Rect>();
    private float _readyAt;
    private bool _moved;
    private Vector2 _last, _down;
    private float _downAt;
    private string _armed;
    private Renderer _titleR;

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    private static void Boot()
    {
        if (FindFirstObjectByType<NeuroUI>() != null) return;
        var go = new GameObject("NeuroUI");
        go.AddComponent<NeuroUI>();
        DontDestroyOnLoad(go);
    }

    private void Start()
    {
        _bootTime = Time.unscaledTime;
        _mode = Mode.Immersive;          // 打开就是 3D，不继承任何残留状态
        _readyAt = Time.unscaledTime + 10f;
        _solid = Solid();
        _disc = DiscTex(128, 64);
        _fadeTop = FadeTex(0.5f, 0f);
        _fadeBottom = FadeTex(0f, 0.55f);
        _corner = new[] { CornerMask(96, 48, 0), CornerMask(96, 48, 1), CornerMask(96, 48, 2), CornerMask(96, 48, 3) };

        _font = Resources.Load<Font>("Fonts/LXGWWenKai-Regular");
        if (_font == null) Debug.LogError("[NeuroUI] 找不到 Fonts/LXGWWenKai-Regular，中文会全部不显示");
    }

    private GUIStyle S(float px, TextAnchor a, Color c)
    {
        int ipx = Mathf.RoundToInt(px);
        string k = ipx + "|" + (int)a + "|" + ColorUtility.ToHtmlStringRGBA(c);
        if (_styles.TryGetValue(k, out var st)) return st;
        st = new GUIStyle { font = _font, alignment = a, wordWrap = false, clipping = TextClipping.Clip };
        st.normal.textColor = c;
        st.fontSize = Mathf.Max(8, ipx);
        _styles[k] = st;
        return st;
    }

    /// <summary>按像素宽度断行（中文逐字断），每行都是一条独立的单行字符串，永远不会被裁。</summary>
    private static List<string> WrapCjk(string text, GUIStyle st, float maxW)
    {
        var outp = new List<string>();
        if (string.IsNullOrEmpty(text)) return outp;
        var sb = new System.Text.StringBuilder();
        float w = 0f;
        foreach (char ch in text)
        {
            if (ch == '\n') { outp.Add(sb.ToString()); sb.Length = 0; w = 0f; continue; }
            float cw = st.CalcSize(new GUIContent(ch.ToString())).x;
            if (w + cw > maxW && sb.Length > 0)
            {
                // 行首不收标点
                string cur = sb.ToString();
                int cut = ("，。；：、！？）》」』".IndexOf(ch) >= 0 && cur.Length > 1) ? 1 : 0;
                if (cut == 1) { outp.Add(cur); sb.Length = 0; w = 0f; sb.Append(ch); w = cw; }
                else { outp.Add(cur); sb.Length = 0; w = 0f; sb.Append(ch); w = cw; }
                continue;
            }
            sb.Append(ch); w += cw;
        }
        if (sb.Length > 0) outp.Add(sb.ToString());
        return outp;
    }

    /// <summary>正文样式：自动换行、不裁剪。字幕和长句必须用它，否则尾巴会被切掉。</summary>
    private GUIStyle SW(float px, Color c)
    {
        int ipx = Mathf.RoundToInt(px);
        string k = "w" + ipx + "|" + ColorUtility.ToHtmlStringRGBA(c);
        if (_styles.TryGetValue(k, out var st)) return st;
        st = new GUIStyle { font = _font, alignment = TextAnchor.LowerCenter, wordWrap = true,
                            clipping = TextClipping.Overflow };
        st.normal.textColor = c;
        st.fontSize = Mathf.Max(8, ipx);
        st.padding = new RectOffset(8, 8, 0, 0);
        _styles[k] = st;
        return st;
    }

    // ---------- 程序化贴图 ----------
    private static Texture2D Solid()
    {
        var t = new Texture2D(1, 1, TextureFormat.RGBA32, false) { hideFlags = HideFlags.DontSave };
        t.SetPixel(0, 0, Color.white); t.Apply(); return t;
    }

    private static Texture2D DiscTex(int size, int radius)
    {
        var t = new Texture2D(size, size, TextureFormat.RGBA32, false)
        { wrapMode = TextureWrapMode.Clamp, hideFlags = HideFlags.DontSave };
        var px = new Color32[size * size];
        float half = size * 0.5f, r = radius;
        for (int y = 0; y < size; y++)
            for (int x = 0; x < size; x++)
            {
                float cx = Mathf.Abs(x + 0.5f - half) - (half - r);
                float cy = Mathf.Abs(y + 0.5f - half) - (half - r);
                float dx = Mathf.Max(cx, 0f), dy = Mathf.Max(cy, 0f);
                float d = Mathf.Sqrt(dx * dx + dy * dy) + Mathf.Min(Mathf.Max(cx, cy), 0f) - r;
                px[y * size + x] = new Color32(255, 255, 255, (byte)(Mathf.Clamp01(0.5f - d) * 255f));
            }
        t.SetPixels32(px); t.Apply(false, true); return t;
    }

    /// <summary>角落遮罩：圆角弧线外侧不透明，用来把方角"切"成圆角。</summary>
    private static Texture2D CornerMask(int size, int radius, int corner)
    {
        var t = new Texture2D(size, size, TextureFormat.RGBA32, false)
        { wrapMode = TextureWrapMode.Clamp, hideFlags = HideFlags.DontSave };
        var px = new Color32[size * size];
        for (int y = 0; y < size; y++)
            for (int x = 0; x < size; x++)
            {
                float px_ = (corner == 1 || corner == 3) ? size - 1 - x : x;
                float py_ = (corner == 2 || corner == 3) ? size - 1 - y : y;
                float d = Mathf.Sqrt((px_ - radius) * (px_ - radius) + (py_ - radius) * (py_ - radius));
                px[y * size + x] = new Color32(255, 255, 255, (byte)(Mathf.Clamp01(d - radius + 0.5f) * 255f));
            }
        t.SetPixels32(px); t.Apply(false, true); return t;
    }

    private static Texture2D FadeTex(float top, float bottom)
    {
        const int H = 64;
        var t = new Texture2D(1, H, TextureFormat.RGBA32, false)
        { wrapMode = TextureWrapMode.Clamp, hideFlags = HideFlags.DontSave };
        var px = new Color32[H];
        for (int y = 0; y < H; y++)
            px[y] = new Color32(255, 255, 255, (byte)(Mathf.Lerp(bottom, top, y / (H - 1f)) * 255f));
        t.SetPixels32(px); t.Apply(false, true); return t;
    }

    private void Fill(Rect r, Color c)
    {
        var old = GUI.color; GUI.color = c;
        GUI.DrawTexture(r, _solid, ScaleMode.StretchToFill, true);
        GUI.color = old;
    }

    private void Round(Rect r, Color c)
    {
        var old = GUI.color; GUI.color = c;
        GUI.DrawTexture(r, _disc, ScaleMode.StretchToFill, true);
        GUI.color = old;
    }

    private void OnGUI()
    {
        float s = Mathf.Clamp(Screen.height / 1080f, 0.62f, 3f);
        float W = Screen.width, H = Screen.height;
        _zones.Clear();

        if (_titleR == null)
        {
            var gt = FindFirstObjectByType<NeuroGuideTitle>();
            if (gt != null) _titleR = gt.GetComponent<Renderer>();
        }

        if (_mode == Mode.Immersive) DrawImmersive(W, H, s);
        else DrawShell(W, H, s);

        // 世界空间那条大字牌整体停用：全屏演示只用底部一行小字幕，
        // 屏幕空间渲染，永远在安全区内，也不会挡住花园。
        if (_titleR != null) _titleR.enabled = false;

        HandleClicks();
    }

    // ===== App 控制台（极简）=====
    // 用户要求：这个界面只要两样东西 —— 全屏的 3D 场景，和 App 控制台。
    // 问候语、插画横幅、光束统计、底部「花园/呼吸/关于」全部删掉。
    [Tooltip("App 控制台地址（桥接起的网页端）")]
    public string consoleUrl = "http://127.0.0.1:8899/app.html";

    private void DrawShell(float W, float H, float s)
    {
        Fill(new Rect(0, 0, W, H), BG);

        GUI.Label(new Rect(0, H * 0.5f - 190 * s, W, 34 * s), "NeuroBloom",
                  S(22 * s, TextAnchor.MiddleCenter, INK));
        GUI.Label(new Rect(0, H * 0.5f - 152 * s, W, 22 * s), "脑境共生 · 呼吸花园",
                  S(11.5f * s, TextAnchor.MiddleCenter, INK2));

        float cw = Mathf.Min(W - 80 * s, 520 * s);
        float x = (W - cw) * 0.5f, y = H * 0.5f - 100 * s, rh = 68 * s, gap = 14 * s;

        var full = new Rect(x, y, cw, rh);
        Round(full, new Color(0.30f, 0.86f, 0.78f, 0.94f));
        GUI.Label(new Rect(full.x + 24 * s, full.y, cw - 48 * s, rh), "全屏 3D 花园",
                  S(16 * s, TextAnchor.MiddleLeft, new Color(0.03f, 0.12f, 0.11f)));
        GUI.Label(new Rect(full.xMax - 120 * s, full.y, 96 * s, rh), "进入 →",
                  S(13 * s, TextAnchor.MiddleRight, new Color(0.03f, 0.12f, 0.11f)));
        _zones["enter"] = full;

        var con = new Rect(x, y + rh + gap, cw, rh);
        Round(con, CARD);
        GUI.Label(new Rect(con.x + 24 * s, con.y, cw - 48 * s, rh), "App 控制台",
                  S(16 * s, TextAnchor.MiddleLeft, INK));
        GUI.Label(new Rect(con.xMax - 120 * s, con.y, 96 * s, rh), "打开 →",
                  S(13 * s, TextAnchor.MiddleRight, INK2));
        _zones["console"] = con;

        GUI.Label(new Rect(0, con.yMax + 26 * s, W, 22 * s), "Esc 在 3D 与这里之间切换",
                  S(11 * s, TextAnchor.MiddleCenter, new Color(0.55f, 0.62f, 0.72f, 0.75f)));

        _zones["back"] = new Rect(-10, -10, 1, 1);
    }

    private void OpenConsole()
    {
        try { Application.OpenURL(consoleUrl); }
        catch (System.Exception e) { Debug.LogWarning("[NeuroUI] 打开控制台失败：" + e.Message); }
    }

    // ===== 首页 =====
    private void DrawHome(float W, float H, float s)
    {
        float m = 34 * s;
        var card = new Rect(m, 176 * s, W - 2 * m, H * 0.38f);

        Fill(new Rect(0, 0, W, card.y), BG);
        Fill(new Rect(0, card.yMax, W, H - card.yMax), BG);
        Fill(new Rect(0, card.y, card.x, card.height), BG);
        Fill(new Rect(card.xMax, card.y, W - card.xMax, card.height), BG);

        float r = 26 * s;
        GUI.color = BG;
        GUI.DrawTexture(new Rect(card.x, card.y, r, r), _corner[0], ScaleMode.StretchToFill, true);
        GUI.DrawTexture(new Rect(card.xMax - r, card.y, r, r), _corner[1], ScaleMode.StretchToFill, true);
        GUI.DrawTexture(new Rect(card.x, card.yMax - r, r, r), _corner[2], ScaleMode.StretchToFill, true);
        GUI.DrawTexture(new Rect(card.xMax - r, card.yMax - r, r, r), _corner[3], ScaleMode.StretchToFill, true);
        GUI.color = Color.white;

        var pill = new Rect(card.x + 16 * s, card.y + 14 * s, 132 * s, 32 * s);
        Round(pill, new Color(0.02f, 0.05f, 0.07f, 0.58f));
        GUI.Label(pill, "花园 · 夜里", S(13 * s, TextAnchor.MiddleCenter, INK));

        var enter = new Rect(card.xMax - 16 * s - 108 * s, card.yMax - 14 * s - 36 * s, 108 * s, 36 * s);
        Round(enter, new Color(0.30f, 0.86f, 0.78f, 0.94f));
        GUI.Label(enter, "进入全屏", S(14 * s, TextAnchor.MiddleCenter, new Color(0.03f, 0.12f, 0.11f)));
        _zones["enter"] = enter;

        GUI.Label(new Rect(m, 30 * s, 460 * s, 26 * s), "NeuroBloom", S(18 * s, TextAnchor.UpperLeft, INK));
        GUI.Label(new Rect(m, 56 * s, 460 * s, 22 * s), "脑境共生 · 呼吸花园", S(11.5f * s, TextAnchor.UpperLeft, INK2));

        GUI.Label(new Rect(W - m - 260 * s, 34 * s, 260 * s, 24 * s),
                  System.DateTime.Now.ToString("M月d日") + "  " + WeekdayCn(), S(12 * s, TextAnchor.UpperRight, INK2));

        GUI.Label(new Rect(m, 100 * s, 620 * s, 40 * s), "晚上好", S(27 * s, TextAnchor.UpperLeft, INK));
        GUI.Label(new Rect(m, 142 * s, 620 * s, 24 * s), "今天也慢慢呼吸，花园会跟着你开", S(13 * s, TextAnchor.UpperLeft, INK2));

        var g = FindFirstObjectByType<NeuroGarden>();
        int n = g != null ? g.PlantCount : 0;
        var info = new Rect(m, card.yMax + 24 * s, W - 2 * m, 96 * s);
        Round(info, CARD);
        GUI.Label(new Rect(info.x + 26 * s, info.y + 12 * s, 240 * s, 42 * s), n.ToString(), S(31 * s, TextAnchor.UpperLeft, ACCENT));
        GUI.Label(new Rect(info.x + 26 * s, info.y + 54 * s, 280 * s, 24 * s), "花园里的光束", S(12 * s, TextAnchor.MiddleLeft, INK));
        GUI.Label(new Rect(info.xMax - 220 * s, info.y + 40 * s, 196 * s, 24 * s),
                  n > 0 ? "越长越亮" : "点一下地面开始", S(12 * s, TextAnchor.MiddleRight, INK2));
    }

    private static string WeekdayCn()
    {
        return "星期" + "日一二三四五六"[(int)System.DateTime.Now.DayOfWeek];
    }

    // ===== 呼吸页 =====
    private void DrawBreath(float W, float H, float s)
    {
        Fill(new Rect(0, 0, W, H), BG);
        float cx = W * 0.5f, cy = H * 0.44f;

        float t = Time.time % 19f;          // 吸 4 → 屏 7 → 呼 8
        string phase; float k;
        if (t < 4f)       { phase = "吸气"; k = t / 4f; }
        else if (t < 11f) { phase = "屏住"; k = 1f; }
        else              { phase = "呼气"; k = 1f - (t - 11f) / 8f; }

        float R = (110 + 46 * Mathf.SmoothStep(0f, 1f, k)) * s;
        Round(new Rect(cx - R * 1.34f, cy - R * 1.34f, R * 2.68f, R * 2.68f), new Color(0.14f, 0.52f, 0.48f, 0.10f));
        Round(new Rect(cx - R, cy - R, R * 2, R * 2), new Color(0.20f, 0.72f, 0.66f, 0.22f));
        Round(new Rect(cx - R * 0.62f, cy - R * 0.62f, R * 1.24f, R * 1.24f), new Color(0.34f, 0.88f, 0.80f, 0.34f));

        GUI.Label(new Rect(0, cy - 20 * s, W, 40 * s), phase, S(30 * s, TextAnchor.MiddleCenter, INK));
        GUI.Label(new Rect(0, 120 * s, W, 36 * s), "呼吸练习", S(24 * s, TextAnchor.UpperCenter, INK));
        GUI.Label(new Rect(0, 158 * s, W, 24 * s), "吸气 4 拍 · 屏住 7 拍 · 呼气 8 拍", S(12 * s, TextAnchor.UpperCenter, INK2));
    }

    // ===== 关于页 =====
    private void DrawAbout(float W, float H, float s)
    {
        Fill(new Rect(0, 0, W, H), BG);
        GUI.Label(new Rect(0, 120 * s, W, 36 * s), "关于", S(24 * s, TextAnchor.UpperCenter, INK));

        float m = 34 * s, y = 186 * s;
        var box = new Rect(m, y, W - 2 * m, 168 * s);
        Round(box, CARD);
        GUI.Label(new Rect(box.x + 24 * s, box.y + 20 * s, box.width - 48 * s, 26 * s), "NeuroBloom · 脑境共生", S(13 * s, TextAnchor.MiddleLeft, INK));
        GUI.Label(new Rect(box.x + 24 * s, box.y + 56 * s, box.width - 48 * s, 24 * s), "一个跟着你呼吸生长的花园。", S(12 * s, TextAnchor.MiddleLeft, INK2));
        GUI.Label(new Rect(box.x + 24 * s, box.y + 86 * s, box.width - 48 * s, 24 * s), "心率越平稳，夜里的花就开得越多。", S(12 * s, TextAnchor.MiddleLeft, INK2));

        var mode = FindFirstObjectByType<NeuroScreenMode>();
        var win = new Rect(m, y + 190 * s, W - 2 * m, 56 * s);
        Round(win, CARD);
        GUI.Label(new Rect(win.x + 24 * s, win.y, win.width * 0.6f, win.height),
                  mode != null && mode.IsFullscreen ? "小窗查看（Esc 也能回）" : "回到全屏 3D", S(13 * s, TextAnchor.MiddleLeft, INK));
        GUI.Label(new Rect(win.xMax - 150 * s, win.y, 126 * s, win.height), "点这里切换", S(12 * s, TextAnchor.MiddleRight, INK2));
        _zones["window"] = win;
    }

    // ===== 底部标签栏 =====
    private void DrawTabs(float W, float H, float s)
    {
        float th = 86 * s;
        var bar = new Rect(0, H - th, W, th);
        Fill(bar, new Color(0.055f, 0.069f, 0.090f, 0.995f));
        Fill(new Rect(0, bar.y, W, 1 * s), new Color(1, 1, 1, 0.06f));

        string[] names = { "花园", "呼吸", "关于" };
        for (int i = 0; i < 3; i++)
        {
            bool on = (int)_page == i;
            float w = W / 3f;
            var cell = new Rect(i * w, bar.y, w, th);
            float dot = 5.5f * s;
            Round(new Rect(cell.x + cell.width * 0.5f - dot, bar.y + 16 * s, dot * 2, dot * 2),
                  on ? ACCENT : new Color(1, 1, 1, 0.26f));
            GUI.Label(new Rect(cell.x, bar.y + 34 * s, cell.width, 26 * s), names[i],
                      S(12.5f * s, TextAnchor.MiddleCenter, on ? ACCENT : INK2));
            _zones["tab" + i] = cell;
        }
    }

    // ===== 沉浸全屏 =====
    private void DrawImmersive(float W, float H, float s)
    {
        // 全屏演示只有一个界面：场景 + 底部一行字幕。
        // 无品牌条、无标签栏、无按钮、无渐变遮罩。
        var world = FindFirstObjectByType<NeuroWorldController>();
        string line = (world != null && !string.IsNullOrEmpty(world.currentScript))
            ? world.currentScript
            : "点一下地面，种下一束光";

        // 手动按字宽断行：中文没有空格，靠 wordWrap 会整段拉成一行再被截掉。
        float capW = Mathf.Min(W - 100 * s, 720 * s);
        var stCap = SW(18 * s, new Color(0.92f, 0.95f, 1f, 0.90f));
        var lines = WrapCjk(line, stCap, capW);
        float lh = stCap.fontSize * 1.5f;
        for (int i = 0; i < lines.Count; i++)
        {
            var r = new Rect((W - capW) * 0.5f, H - 96 * s - lh * (lines.Count - i), capW, lh);
            GUI.Label(r, lines[i], stCap);
        }

        float a = Mathf.Clamp01((6f - (Time.unscaledTime - _readyAt + 10f)) / 2f);
        if (a > 0.01f)
            GUI.Label(new Rect(0, H - 40 * s, W, 22 * s),
                      "点地面种花　·　Esc 回控制台",
                      S(11 * s, TextAnchor.MiddleCenter, new Color(0.72f, 0.80f, 0.92f, 0.50f * a)));

        _zones["back"] = new Rect(-10, -10, 1, 1);
    }

    // ===== 点击 =====
    private void HandleClicks()
    {
        var e = Event.current;
        // Esc 一个键来回切：3D 全屏 ⇄ 软件自己的控制台。
        // 不进浏览器、不发系统快捷键，纯粹本窗口内切换。
        if (e.type == EventType.KeyDown && e.keyCode == KeyCode.Escape)
        {
            // ① 启动期的系统补发：直接丢掉，连日志都不刷
            if (Time.unscaledTime - _bootTime < 12f) return;
            // ② 同一次按键被投递两帧：1.5 秒内的第二个 Esc 不算新操作
            if (Time.unscaledTime - _lastEscAt < 1.5f) return;
            _lastEscAt = Time.unscaledTime;
            if (_mode == Mode.Immersive)
            {
                _mode = Mode.Shell;
                _readyAt = Time.unscaledTime + 10f;
            }
            else
            {
                var sm = FindFirstObjectByType<NeuroScreenMode>();
                if (sm != null && !sm.IsFullscreen) sm.SetFull(true, "Esc 回 3D");
                _mode = Mode.Immersive;
                _readyAt = Time.unscaledTime + 10f;
            }
        }
        if ((e.mousePosition - _last).sqrMagnitude > 9f) _moved = true;
        _last = e.mousePosition;

        if (Time.unscaledTime < _readyAt || !_moved) { _armed = null; return; }

        if (e.type == EventType.MouseDown)
        {
            _armed = null;
            foreach (var kv in _zones)
                if (kv.Value.Contains(e.mousePosition)) { _armed = kv.Key; break; }
            _down = e.mousePosition; _downAt = Time.unscaledTime;
        }
        else if (e.type == EventType.MouseUp)
        {
            // 真人点按从按下到抬起 ≥60ms；系统重放那一对间隔接近 0，靠时间下限挡掉
            bool real = _armed != null && Time.unscaledTime - _downAt >= 0.06f
                        && (e.mousePosition - _down).magnitude <= 10f;
            string hit = _armed;
            _armed = null;
            if (!real) return;

            switch (hit)
            {
                case "enter":
                {
                    var sm = FindFirstObjectByType<NeuroScreenMode>();
                    if (sm != null && !sm.IsFullscreen) sm.SetFull(true, "控制台进入全屏");
                    _mode = Mode.Immersive;
                    _readyAt = Time.unscaledTime + 10f;
                    break;
                }
                case "console": OpenConsole(); break;
                case "back":   _mode = Mode.Shell; break;
                case "plant":  DoPlant(); break;
                case "tab0":   _page = Page.Home; break;
                case "tab1":   _page = Page.Breath; break;
                case "tab2":   _page = Page.About; break;
                case "window": FindFirstObjectByType<NeuroScreenMode>()?.Toggle("按钮"); break;
            }
        }
    }

    private void DoPlant()
    {
        var g = FindFirstObjectByType<NeuroGarden>();
        if (g != null) g.PlantFromViewport(Random.Range(0.34f, 0.66f), Random.Range(0.30f, 0.44f));
    }
}
