// NeuroGuideTitle.cs —— 引导文字牌：手写花体 + 极光渐变 + 柔光外发光 + 逐字淡入
//
// 它只管"好不好看"，不参与任何判定，也不读脑电数据：
//   · 字体优先挑系统里的手写/花体（行楷、翩翩体、娃娃体、圆体…），挑不到就退回内置字体
//   · 颜色交给 NeuroBloom/GuideTitle 着色器算（薰衣草紫 → 薄荷绿 → 樱粉 → 淡金 → 奶白）
//   · 外发光偏蓝紫，和场景里的发光蘑菇、萤火虫是同一套调子
//   · 逐字亮起（左 → 右），亮起时整块轻轻上浮，之后像被微风吹着轻微摆动
//
// 铁律：不出现任何数字、分数、百分比、诊断词汇。
using UnityEngine;

[ExecuteAlways]
[RequireComponent(typeof(TextMesh))]
public class NeuroGuideTitle : MonoBehaviour
{
    [Tooltip("逐字淡入用时（秒）")]
    public float wipeSeconds = 1.8f;
    public float revealSeconds = 1.25f;   // 逐字弹入的总时长（最后一个字在这之后刚好弹完）
    [Tooltip("淡入时整块文字从下方浮上来的距离（世界单位）")]
    public float riseAmount = 0.055f;
    [Tooltip("微风吹动的最大偏转角度")]
    public float swayDegrees = 1.6f;
    [Tooltip("一次摆动来回用时（秒）")]
    public float swaySeconds = 8f;
    [Tooltip("编辑器出图用：>= 0 时强制当作“淡入已完成到该进度”；-1 = 走正常动画")]
    public float previewWipe = -1f;

    public string guideText = "点一下地面，种下一束光\n慢慢呼吸，花会跟着你开";

    // 手写/花体优先，其次圆润秀气，最后退回内置。逐个用 HasCharacter 筛掉不含中文字形的。
    private static readonly string[] OsFontPriority =
    {
        "STXingkai", "华文行楷",
        "Hannotate SC", "翩翩体-简",
        "Wawati SC", "娃娃体-简",
        "Yuanti SC", "圆体-简",
        "Kaiti SC", "楷体-简",
        "Songti SC", "PingFang SC"
    };

    private TextMesh _tm;
    private MeshRenderer _mr;
    private MaterialPropertyBlock _mpb;
    private Material _wanted;          // 换字体会让 TextMesh 抢回自己的材质，这里记住我们想要的那个
    private float _startTime = -1f;

    void OnEnable()
    {
        _tm = GetComponent<TextMesh>();
        _mr = GetComponent<MeshRenderer>();
        _mpb = new MaterialPropertyBlock();
        _wanted = (_mr != null) ? _mr.sharedMaterial : null;
        _startTime = -1f;
        Style();
    }

    /// <summary>换一段引导语（运行时可调）。空串会被忽略。</summary>
    public void SetText(string s)
    {
        if (string.IsNullOrEmpty(s)) return;
        guideText = s;
        Style();
    }

    /// <summary>把字体重挑一遍、材质纠正回来，并重新开始淡入。</summary>
    private void Style()
    {
        if (_tm == null) return;

        _tm.text = guideText;
        _tm.anchor = TextAnchor.MiddleCenter;
        _tm.alignment = TextAlignment.Center;
        _tm.color = Color.white;                       // 真正的颜色在着色器里算
        _tm.richText = false;

        Font f = PickFont(guideText);
        if (f != null && _tm.font != f) _tm.font = f;

        if (_mr != null && _wanted != null && _mr.sharedMaterial != _wanted)
            _mr.sharedMaterial = _wanted;

        _startTime = -1f;                              // 下一帧从头淡入
    }

    // 项目自带的手写楷体（霞鹜文楷，OFL 开源）。放在 Assets/Resources/Fonts/ 下
    private static readonly string[] ProjectFontPaths = { "Fonts/LXGWWenKai-Regular" };

    private static Font PickFont(string s)
    {
        // 1) 项目自带手写楷体 —— 最稳，换台电脑也长一样
        for (int i = 0; i < ProjectFontPaths.Length; i++)
        {
            Font f = Resources.Load<Font>(ProjectFontPaths[i]);
            if (f != null && HasAllGlyphs(f, s)) { Report(f, "项目自带手写楷体"); return f; }
        }

        // 2) 系统里的手写/圆润字体（这台机器没装，别的机器可能有）
        string[] installed = null;
        try { installed = Font.GetOSInstalledFontNames(); } catch { installed = null; }
        for (int i = 0; i < OsFontPriority.Length; i++)
        {
            if (installed != null && !ContainsName(installed, OsFontPriority[i])) continue;
            Font f = null;
            try { f = Font.CreateDynamicFontFromOSFont(OsFontPriority[i], 64); }
            catch { f = null; }
            if (f == null || !HasAllGlyphs(f, s)) continue;
            Report(f, "系统字体");
            return f;
        }

        // 3) 兜底：内置字体
        Font builtin = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
        if (builtin == null) builtin = Resources.GetBuiltinResource<Font>("Arial.ttf");
        Report(builtin, "内置兜底（会偏商务）");
        return builtin;
    }

    private static bool ContainsName(string[] arr, string name)
    {
        for (int i = 0; i < arr.Length; i++)
            if (string.Equals(arr[i], name, System.StringComparison.OrdinalIgnoreCase)) return true;
        return false;
    }

    private static void Report(Font f, string how)
    {
        Debug.Log("[GUIDETITLE] 引导语字体 = " + (f == null ? "(空)" : f.name) + " 来源：" + how);
    }

    private static bool HasAllGlyphs(Font f, string s)
    {
        for (int i = 0; i < s.Length; i++)
        {
            char c = s[i];
            if (c == '\n' || c == '\r' || c == ' ') continue;
            if (!f.HasCharacter(c)) return false;
        }
        return true;
    }

    void Update()
    {
        Apply();
    }

    /// <summary>把当前状态写给渲染器。出图前会被显式调一次，保证第一帧就有正确外观。</summary>
    public void Apply()
    {
        if (_tm == null)
        {
            _tm = GetComponent<TextMesh>();
            if (_tm == null) return;
        }
        if (_mr == null)
        {
            _mr = GetComponent<MeshRenderer>();
            if (_mr == null) return;
        }
        if (_mpb == null) _mpb = new MaterialPropertyBlock();

        Material mat = _mr.sharedMaterial;
        if (_wanted != null && mat != _wanted) { _mr.sharedMaterial = _wanted; mat = _wanted; }
        if (mat == null || !mat.HasProperty("_Wipe")) return;

        if (_startTime < 0f) _startTime = Time.realtimeSinceStartup;
        float now = Time.realtimeSinceStartup;
        float raw = Mathf.Clamp01((now - _startTime) / Mathf.Max(0.05f, wipeSeconds));
        float wipe = (previewWipe >= 0f) ? Mathf.Clamp01(previewWipe) : (raw * (2f - raw));  // 先快后慢

        // 逐字弹入的进度：线性走，0.05s 一个字的错峰由着色器负责。
        // 出图时 previewWipe=1 -> reveal=1，画面定格在"全部弹完"的状态，保证每张图一致。
        float reveal = (previewWipe >= 0f)
                       ? Mathf.Clamp01(previewWipe)
                       : Mathf.Clamp01((now - _startTime) / Mathf.Max(0.05f, revealSeconds));

        // 定格出图时锁死摆动，否则每次出图姿态都不同、图和图之间没法比
        float wob = (previewWipe >= 0f)
                    ? 0f
                    : Mathf.Sin(now * Mathf.PI * 2f / Mathf.Max(0.5f, swaySeconds))
                      * 0.5f * Mathf.Deg2Rad * swayDegrees;

        // TextMesh 没有 MeshFilter，字块范围只能从渲染器包围盒反推到局部坐标
        Bounds b = _mr.bounds;
        Matrix4x4 w2l = transform.worldToLocalMatrix;
        Vector3 mn = new Vector3(float.MaxValue, float.MaxValue, float.MaxValue);
        Vector3 mx = new Vector3(float.MinValue, float.MinValue, float.MinValue);
        for (int i = 0; i < 8; i++)
        {
            Vector3 c = new Vector3(((i & 1) == 0) ? b.min.x : b.max.x,
                                    ((i & 2) == 0) ? b.min.y : b.max.y,
                                    ((i & 4) == 0) ? b.min.z : b.max.z);
            Vector3 l = w2l.MultiplyPoint3x4(c);
            mn = Vector3.Min(mn, l);
            mx = Vector3.Max(mx, l);
        }

        Texture tex = null;
        if (_tm.font != null && _tm.font.material != null) tex = _tm.font.material.mainTexture;

        // 逐字动画的格子必须跟**当前真正显示的那段文字**对齐。
        // 原来 _CharCount/_LineCount 在材质上是写死的 11 字 x 2 行，
        // 大模型话术长短不一，格子会错位、弹入看着乱。这里按实际文本算。
        string shown = (_tm != null && !string.IsNullOrEmpty(_tm.text)) ? _tm.text : guideText;
        int nLines = 1, cur = 0, widest = 0;
        for (int i = 0; i < shown.Length; i++)
        {
            char c = shown[i];
            if (c == '\n') { if (cur > widest) widest = cur; cur = 0; nLines++; }
            else if (c != '\r') cur++;
        }
        if (cur > widest) widest = cur;
        if (widest < 1) widest = 1;
        if (nLines < 1) nLines = 1;

        _mpb.Clear();
        if (tex != null) _mpb.SetTexture("_MainTex", tex);
        _mpb.SetVector("_LocalMin", new Vector4(mn.x, mn.y, mn.z, 0f));
        _mpb.SetVector("_LocalMax", new Vector4(mx.x, mx.y, mx.z, 0f));
        _mpb.SetFloat("_Wipe", wipe);
        _mpb.SetFloat("_Reveal", reveal);
        _mpb.SetFloat("_CharCount", widest);
        _mpb.SetFloat("_LineCount", nLines);
        _mpb.SetFloat("_Sway", wob);
        _mpb.SetFloat("_Rise", -riseAmount * (1f - wipe));
        _mr.SetPropertyBlock(_mpb);
    }
}
