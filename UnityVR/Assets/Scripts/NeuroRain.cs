// NeuroRain.cs —— 雨：雨丝 + 地面涟漪 + 地面/水面湿化
//
// 为什么要有这个：原来是"雾霾"把场景糊成一片灰，用户说很难看。
// 雨不一样 —— 雨丝、涟漪、湿地面都是**有形状**的细节，能把注意力从"模型简单"挪到氛围上。
//
// 设计约定：
// 1) 全部运行时生成（不依赖场景里预先摆好的对象、不需要 .prefab/.mat 资源），
//    这样打包/换场景都不会丢东西，AutoWire 任何一步失败也不影响主体。
// 2) 只由 NeuroWeather 调 target（0=停 1=大雨），自己平滑过渡。
//    [DefaultExecutionOrder(210)] 保证在 NeuroWeather(200) 之后跑。
// 3) 不用 Shader.Find 死磕：优先 Particles/Standard Unlit，失败就借场景里现有粒子材质的着色器
//    （那个着色器一定在包里，因为场景在用它），避免打包后变成粉色/隐形。
using UnityEngine;

[DefaultExecutionOrder(210)]
public class NeuroRain : MonoBehaviour
{
    public static NeuroRain Instance;

    [Header("雨量（由天气驱动：0=停雨 1=大雨）")]
    [Range(0f, 1f)] public float target;
    [Range(0f, 1f)] public float intensity;
    public float smoothSeconds = 2.6f;

    [Header("雨丝")]
    public int maxDrops = 2400;
    public float area = 44f;          // 覆盖边长（米）
    public float fallSpeed = 19f;

    [Header("涟漪")]
    public int maxRipples = 240;

    // ---- 内部 ----
    private ParticleSystem _rain, _ripple;
    private ParticleSystemRenderer _rainR, _rippleR;
    private Transform _cam;
    private Material _ground, _water;
    private float _gGloss0 = -1f, _wGloss0 = -1f;
    private Color _gColor0, _wColor0;
    private float _applied = -1f;
    private float _groundY;

    public static NeuroRain Ensure()
    {
        if (Instance != null) return Instance;
        var go = new GameObject("Rain");
        Instance = go.AddComponent<NeuroRain>();
        return Instance;
    }

    void Awake()
    {
        Instance = this;
        Build();
    }

    void OnDestroy()
    {
        if (Instance == this) Instance = null;
        if (_ground != null) Destroy(_ground);
        if (_water != null) Destroy(_water);
    }

    // ================================================================ 搭
    private void Build()
    {
        var cam = Camera.main;
        _cam = cam != null ? cam.transform : null;
        _groundY = 0.02f;

        BuildStreaks();
        BuildRipples();
        CacheSurfaces();
        ApplyVisual();
    }

    private void BuildStreaks()
    {
        var go = new GameObject("RainStreaks");
        go.transform.SetParent(transform, false);
        _rain = go.AddComponent<ParticleSystem>();
        _rainR = go.GetComponent<ParticleSystemRenderer>();
        if (_rainR == null) _rainR = go.AddComponent<ParticleSystemRenderer>();

        _rain.Stop(true, ParticleSystemStopBehavior.StopEmittingAndClear);

        var main = _rain.main;
        main.loop = true;
        main.duration = 1f;
        main.prewarm = true;
        main.startLifetime = new ParticleSystem.MinMaxCurve(0.75f, 1.15f);
        main.startSpeed = new ParticleSystem.MinMaxCurve(fallSpeed * 0.86f, fallSpeed * 1.18f);
        main.startSize = new ParticleSystem.MinMaxCurve(0.06f, 0.16f);
        main.startColor = new ParticleSystem.MinMaxGradient(new Color(0.82f, 0.89f, 1f, 1f));
        main.gravityModifier = 1.35f;
        main.maxParticles = maxDrops;
        // 世界坐标：雨丝落地后不会跟着发射器平移，看起来才像"雨"，不像"贴在镜头上的条纹"
        main.simulationSpace = ParticleSystemSimulationSpace.World;

        var em = _rain.emission;
        em.rateOverTime = 0f;                 // 由雨量驱动

        var shp = _rain.shape;
        shp.shapeType = ParticleSystemShapeType.Box;
        // ⚠️ Box 形状的粒子是沿**局部 +Z**方向飞的，不是沿世界 -Y。
        // 原来只把 startSpeed 设成 19 结果雨是"斜着往前冲"的（用户一眼看出来了）。
        // 把形状绕 X 轴转 90°，局部 +Z 就变成世界 -Y —— 雨垂直往下落。
        // 转完 local Y/Z 互换，所以 scale 也要跟着换（薄的那一层现在在 Y 上）。
        shp.rotation = new Vector3(90f, 0f, 0f);
        shp.scale = new Vector3(area, area, 1.2f);
        shp.position = Vector3.zero;

        _rainR.renderMode = ParticleSystemRenderMode.Stretch;
        _rainR.lengthScale = 0.55f;
        _rainR.velocityScale = 0.05f;
        _rainR.minParticleSize = 0f;
        _rainR.maxParticleSize = 1f;
        _rainR.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
        _rainR.receiveShadows = false;
        _rainR.sharedMaterial = MakeParticleMaterial(MakeStreakTex(), new Color(0.86f, 0.92f, 1f, 0.5f));

        _rain.Play();
    }

    private void BuildRipples()
    {
        var go = new GameObject("RainRipples");
        go.transform.SetParent(transform, false);
        _ripple = go.AddComponent<ParticleSystem>();
        _rippleR = go.GetComponent<ParticleSystemRenderer>();
        if (_rippleR == null) _rippleR = go.AddComponent<ParticleSystemRenderer>();

        _ripple.Stop(true, ParticleSystemStopBehavior.StopEmittingAndClear);

        var main = _ripple.main;
        main.loop = true;
        main.duration = 1f;
        main.prewarm = true;
        main.startLifetime = new ParticleSystem.MinMaxCurve(0.55f, 0.85f);
        main.startSpeed = 0f;
        main.startSize = new ParticleSystem.MinMaxCurve(0.12f, 0.22f);
        main.startColor = new ParticleSystem.MinMaxGradient(new Color(0.86f, 0.93f, 1f, 0.55f));
        main.gravityModifier = 0f;
        main.maxParticles = maxRipples;
        main.simulationSpace = ParticleSystemSimulationSpace.World;

        var em = _ripple.emission;
        em.rateOverTime = 0f;

        var shp = _ripple.shape;
        shp.shapeType = ParticleSystemShapeType.Box;
        shp.scale = new Vector3(area * 0.62f, 0.01f, area * 0.62f);

        // 越活越大（水圈扩散）、越活越淡
        var sz = _ripple.sizeOverLifetime;
        sz.enabled = true;
        sz.size = new ParticleSystem.MinMaxCurve(1f, AnimationCurve.EaseInOut(0f, 0.35f, 1f, 3.2f));

        var col = _ripple.colorOverLifetime;
        col.enabled = true;
        var g = new Gradient();
        g.SetKeys(
            new GradientColorKey[] {
                new GradientColorKey(new Color(0.88f, 0.95f, 1f), 0f),
                new GradientColorKey(new Color(0.88f, 0.95f, 1f), 1f) },
            new GradientAlphaKey[] {
                new GradientAlphaKey(0.0f, 0f),
                new GradientAlphaKey(0.55f, 0.18f),
                new GradientAlphaKey(0.0f, 1f) });
        col.color = new ParticleSystem.MinMaxGradient(g);

        _rippleR.renderMode = ParticleSystemRenderMode.HorizontalBillboard;
        _rippleR.alignment = ParticleSystemRenderSpace.World;
        _rippleR.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
        _rippleR.receiveShadows = false;
        _rippleR.sharedMaterial = MakeParticleMaterial(MakeRingTex(), Color.white);

        _ripple.Play();
    }

    // ================================================================ 每帧
    void LateUpdate()
    {
        float dt = Time.unscaledDeltaTime;
        float step = dt / Mathf.Max(0.05f, smoothSeconds);
        if (intensity < target) intensity = Mathf.Min(target, intensity + step);
        else if (intensity > target) intensity = Mathf.Max(target, intensity - step);

        if (_cam != null)
        {
            // 雨幕跟着镜头走（只在水平面 + 抬高），垂直方向给足空间：
            // 相机 y + 9 起落，寿命 0.75~1.15 秒、速度约 19 → 落到地面附近正好消失
            transform.position = new Vector3(_cam.position.x, _cam.position.y + 9f, _cam.position.z);
        }

        if (Mathf.Abs(intensity - _applied) > 0.002f) ApplyVisual();
    }

    private void ApplyVisual()
    {
        _applied = intensity;

        if (_rainR != null) _rainR.enabled = intensity > 0.015f;
        if (_rippleR != null) _rippleR.enabled = intensity > 0.05f;

        if (_rain != null)
        {
            var em = _rain.emission;
            em.rateOverTime = maxDrops * 0.95f * Mathf.Pow(intensity, 1.25f);
        }
        if (_ripple != null)
        {
            var em = _ripple.emission;
            em.rateOverTime = maxRipples * Mathf.Pow(intensity, 1.6f);
            // 涟漪贴地：跟着镜头走，高度取这块地面的高度
            Vector3 p = transform.position;
            _ripple.transform.position = new Vector3(p.x, _groundY + 0.03f, p.z);
        }

        // ---- 地面湿化：越下越亮（反光）越暗（吸水）----
        if (_ground != null && _gGloss0 >= 0f)
        {
            if (_ground.HasProperty("_Glossiness"))
                _ground.SetFloat("_Glossiness", Mathf.Lerp(_gGloss0, 0.74f, intensity));
            if (_ground.HasProperty("_Color"))
                _ground.SetColor("_Color", Color.Lerp(_gColor0, _gColor0 * 0.74f, intensity * 0.9f));
        }
        if (_water != null && _wGloss0 >= 0f)
        {
            if (_water.HasProperty("_Glossiness"))
                _water.SetFloat("_Glossiness", Mathf.Lerp(_wGloss0, 0.99f, intensity));
            if (_water.HasProperty("_Color"))
            {
                Color c = _wColor0;
                c.a = _wColor0.a;
                _water.SetColor("_Color", Color.Lerp(_wColor0, new Color(c.r * 0.92f, c.g * 0.96f, c.b, c.a), intensity));
            }
        }
    }

    private void CacheSurfaces()
    {
        _ground = FindMaterial("Ground", out _gGloss0, out _gColor0);
        _water = FindMaterial("Stream", out _wGloss0, out _wColor0);

        // 地面高度：从空中往下打一条射线，决定涟漪贴在哪儿
        RaycastHit hit;
        if (Physics.Raycast(new Vector3(0f, 60f, 0f), Vector3.down, out hit, 120f))
            _groundY = hit.point.y;
        else
            _groundY = 0.02f;
    }

    private static Material FindMaterial(string objectName, out float gloss, out Color color)
    {
        gloss = -1f;
        color = Color.white;
        var go = GameObject.Find(objectName);
        if (go == null) return null;
        var r = go.GetComponent<Renderer>();
        if (r == null) return null;
        Material m = r.material;      // 实例化副本，只改这一个地面/水面
        if (m != null && m.HasProperty("_Glossiness")) gloss = m.GetFloat("_Glossiness");
        if (m != null && m.HasProperty("_Color")) color = m.GetColor("_Color");
        return m;
    }

    // ================================================================ 贴图/材质（程序生成）
    private static Texture2D MakeStreakTex()
    {
        const int w = 16, h = 64;
        var t = new Texture2D(w, h, TextureFormat.RGBA32, false);
        t.name = "NB_RainStreak";
        t.wrapMode = TextureWrapMode.Clamp;
        var px = new Color32[w * h];
        for (int y = 0; y < h; y++)
        {
            float ty = y / (float)(h - 1);
            float along = Mathf.Sin(ty * Mathf.PI);                  // 上下两端淡
            for (int x = 0; x < w; x++)
            {
                float tx = Mathf.Abs(x - (w - 1) * 0.5f) / ((w - 1) * 0.5f);
                float across = Mathf.Pow(Mathf.Clamp01(1f - tx), 2.4f); // 中间一条细线
                byte a = (byte)(255f * along * across);
                px[y * w + x] = new Color32(255, 255, 255, a);
            }
        }
        t.SetPixels32(px);
        t.Apply(false, false);
        return t;
    }

    private static Texture2D MakeRingTex()
    {
        const int n = 64;
        var t = new Texture2D(n, n, TextureFormat.RGBA32, false);
        t.name = "NB_RainRing";
        t.wrapMode = TextureWrapMode.Clamp;
        var px = new Color32[n * n];
        for (int y = 0; y < n; y++)
        {
            for (int x = 0; x < n; x++)
            {
                float dx = (x + 0.5f) / n * 2f - 1f;
                float dy = (y + 0.5f) / n * 2f - 1f;
                float d = Mathf.Sqrt(dx * dx + dy * dy);
                // 一圈圆环，内外都淡出
                float ring = Mathf.Clamp01(1f - Mathf.Abs(d - 0.72f) / 0.26f);
                float fade = Mathf.Clamp01(1f - Mathf.Abs(d - 0.72f) / 0.26f) * Mathf.Clamp01((1f - d) / 0.18f);
                byte a = (byte)(255f * Mathf.Min(ring, fade) * (d < 1f ? 1f : 0f));
                px[y * n + x] = new Color32(255, 255, 255, a);
            }
        }
        t.SetPixels32(px);
        t.Apply(false, false);
        return t;
    }

    private static Material MakeParticleMaterial(Texture2D tex, Color tint)
    {
        Shader sh = Shader.Find("Particles/Standard Unlit");
        if (sh == null) sh = Shader.Find("Legacy Shaders/Particles/Alpha Blended");
        if (sh == null) sh = SceneParticleShader();
        if (sh == null) sh = Shader.Find("Sprites/Default");

        Material m;
        if (sh != null)
        {
            m = new Material(sh);
            m.name = "NB_RainParticle";
            if (m.HasProperty("_MainTex")) m.SetTexture("_MainTex", tex);
            if (m.HasProperty("_Color")) m.SetColor("_Color", tint);
            if (m.HasProperty("_TintColor")) m.SetColor("_TintColor", tint);
            if (sh.name.IndexOf("Standard Unlit") >= 0)
            {
                m.SetFloat("_Mode", 2f);
                m.SetOverrideTag("RenderType", "Transparent");
                m.SetInt("_SrcBlend", (int)UnityEngine.Rendering.BlendMode.SrcAlpha);
                m.SetInt("_DstBlend", (int)UnityEngine.Rendering.BlendMode.OneMinusSrcAlpha);
                m.SetInt("_ZWrite", 0);
                m.EnableKeyword("_ALPHABLEND_ON");
                m.DisableKeyword("_ALPHAPREMULTIPLY_ON");
                m.renderQueue = 3000;
            }
        }
        else
        {
            // 最后的兜底：至少别是粉色
            m = new Material(Shader.Find("Unlit/Transparent") ?? Shader.Find("Sprites/Default"));
        }
        m.mainTexture = tex;
        return m;
    }

    /// <summary>借场景里现有粒子材质的着色器 —— 它一定被打进包里，比 Shader.Find 稳。</summary>
    private static Shader SceneParticleShader()
    {
        var all = FindObjectsByType<ParticleSystemRenderer>(FindObjectsSortMode.None);
        for (int i = 0; i < all.Length; i++)
        {
            if (all[i] == null || all[i].sharedMaterial == null) continue;
            return all[i].sharedMaterial.shader;
        }
        return null;
    }
}
