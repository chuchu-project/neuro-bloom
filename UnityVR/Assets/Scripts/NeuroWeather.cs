// NeuroWeather.cs —— 天气系统：晴天 / 雨天 / 黑天
//
// 2026-10-23 改：中间的「雾霾」换成「雨」。用户原话是雾把场景糊得很难看，
// 雨有形状（雨丝/涟漪/湿地反光），既好看又和「紧张→放松」的叙事对得上（越紧张雨越大）。
//
// 设计要点（很重要，别改坏）：
// 1) 这一层只做「叠加」，不做「取代」。
//    NeuroWorldController 每帧都会用 world_params(bloom/sunlight/fog/crystal)
//    重新写一遍阳光/雾/环境光/天空盒，**它是唯一的基底来源**。
//    天气层在它之后执行，在它写好的值上做乘法/混色 → 四键契约完全不受影响。
//    （控制器每帧重算、不累加，所以我们每帧乘一次也不会滚雪球）
// 2) 因为有执行顺序要求，必须 [DefaultExecutionOrder(200)]（控制器是默认 0）。
// 3) [ExecuteAlways] 让编辑器里也能直接看到三套天气 —— 出对比图靠它。
//    但编辑器里没人写基底，所以编辑器下**只在被显式调用时套用一次**，不每帧套。
using UnityEngine;

[DefaultExecutionOrder(200)]
[ExecuteAlways]
public class NeuroWeather : MonoBehaviour
{
    public enum WeatherType { Sunny = 0, Hazy = 1, Night = 2 }

    [Header("引用（留空会自动按名字找）")]
    public NeuroWorldController world;
    public Light sun;              // Sun
    public Light fill;             // FillLight
    public Material skyMaterial;   // NB2_Sky
    public Transform fireflies;    // Fireflies
    public Transform critters;     // Critters
    public Transform nightSky;     // NightSky（星星 + 月亮）
    public Camera cam;             // 场景相机：clearFlags 是 SolidColor，背景色就是天花板
    public Material starsMaterial;
    public Material moonMaterial;

    [Header("天气")]
    public WeatherType current = WeatherType.Sunny;
    [Tooltip("切换一次的过渡时长（秒）。需求是 2~3 秒")]
    [Range(0.2f, 8f)] public float transitionSeconds = 2.6f;
    [Tooltip("演示时用键盘 1/2/3 切换天气")]
    public bool keyboardSwitch = true;

    [Tooltip("越放松越晴朗：放松度 ≥66% 晴天、35~65% 雨天、<34% 黑天（带滞后，不会来回横跳）")]
    public bool driveByRelax = true;

    [Tooltip("手动/指令切换后，暂停自动联动多少秒（人工优先）")]
    public float manualHoldSeconds = 25f;

    [Tooltip("自动联动最短停留：切完一次至少这么多秒内不再切，避免脑电一抖就来回跳")]
    public float minAutoHoldSeconds = 22f;

    private float _manualUntil;
    private float _nextAuto;

    // ---------------------------------------------------------------- 一套天气的全部叠加量
    private struct Preset
    {
        public float sunMul;         // 阳光强度倍数
        public Color sunColor;       // 阳光往这个颜色靠
        public float sunColorMix;
        public bool  noShadow;       // 阴雨天：无锐利阴影
        public float ambientMul;
        public Color ambientColor;
        public float ambientMix;
        public float fogMul;         // 雾浓度倍数
        public Color fogColor;
        public float fogColorMix;
        public Color bgColor;        // 相机背景色＝真正的"天色"（本场景相机是 SolidColor，不画天空盒）
        public float bgMix;
        public Color skyTint;
        public float skyTintMix;
        public Color skyGround;
        public float skyGroundMix;
        public float skyExposure;    // 天空盒曝光倍数
        public float sunDisk;        // 天空盒里的太阳圆盘：0=藏起来（黑夜）
        public float sunSize;
        public float bloomMul;       // 后期泛光倍数
        public float satMul;         // 饱和度倍数（饱和拉满 / 低饱和）
        public float expMul;         // 曝光倍数
        public float fireflyMul;     // 萤火虫数量倍数
        public float fireflySizeMul;
        public float critterMul;     // 小鸟可见比例（蝴蝶不动）
        public float critterHeight;  // 小鸟飞行高度倍数
        public float starAlpha;
        public float moonAlpha;
        public float rain;           // 雨量 0~1（交给 NeuroRain）
    }

    // 晴天：暖金强光、锐利阴影、极低雾、饱和拉满
    private static readonly Preset P_Sunny = new Preset
    {
        sunMul = 1.02f,            // 1.15 过曝、0.90 偏暗 → 取中间
        sunColor = new Color(1.00f, 0.95f, 0.82f), sunColorMix = 0.35f,
        noShadow = false,
        ambientMul = 1.00f,
        ambientColor = new Color(0.44f, 0.48f, 0.54f), ambientMix = 0.40f,
        fogMul = 0.28f,                                  // 极低雾
        fogColor = new Color(0.80f, 0.88f, 0.96f), fogColorMix = 0.45f,
        bgColor = new Color(0.40f, 0.56f, 0.78f), bgMix = 0.90f,         // 晴：浅蓝天
        skyTint = new Color(0.58f, 0.76f, 0.96f), skyTintMix = 0.45f,
        skyGround = new Color(0.88f, 0.84f, 0.72f), skyGroundMix = 0.45f,
        skyExposure = 1.10f, sunDisk = 2f, sunSize = 0.028f,
        bloomMul = 0.90f, satMul = 1.06f, expMul = 1.00f,   // 饱和拉满但不过曝
        fireflyMul = 0.50f, fireflySizeMul = 1.0f,
        critterMul = 1.00f, critterHeight = 1.00f,
        starAlpha = 0f, moonAlpha = 0f,
        rain = 0f                                  // 晴天不下雨
    };

    // 雨天：阴雨森林。
    // 关键不是"铺灰"，而是（1）光变柔变冷（2）饱和降一档但不是黑白（3）雾中等偏淡、偏蓝灰
    // （4）雨丝/涟漪/湿地反光提供视觉焦点。萤火虫基本关掉（雨天没有），鸟也躲起来。
    private static readonly Preset P_Hazy = new Preset
    {
        sunMul = 0.40f,
        sunColor = new Color(0.80f, 0.88f, 1.00f), sunColorMix = 0.85f,   // 阴天的冷白日光
        noShadow = true,                                 // 阴天没有锐利阴影 —— 这一条对"高级感"贡献最大
        ambientMul = 1.00f,
        ambientColor = new Color(0.38f, 0.50f, 0.68f), ambientMix = 0.82f, // 冷蓝环境光
        fogMul = 0.72f,                                  // 比晴天多，但远不到"雾墙"
        fogColor = new Color(0.50f, 0.60f, 0.72f), fogColorMix = 0.76f,   // 雨雾：蓝灰
        bgColor = new Color(0.14f, 0.16f, 0.20f), bgMix = 0.92f,         // 雨：压低的阴云
        skyTint = new Color(0.44f, 0.52f, 0.66f), skyTintMix = 0.90f,    // 铅灰蓝天空
        skyGround = new Color(0.36f, 0.40f, 0.44f), skyGroundMix = 0.88f,
        skyExposure = 0.74f, sunDisk = 0f, sunSize = 0.050f,            // 太阳完全藏掉
        bloomMul = 0.48f, satMul = 0.60f, expMul = 0.82f,               // 降饱和但不致死灰
        fireflyMul = 0.04f, fireflySizeMul = 0.80f,                     // 雨天没有萤火虫
        critterMul = 0.22f, critterHeight = 0.35f,                      // 鸟躲起来、飞得低
        starAlpha = 0f, moonAlpha = 0f,
        rain = 1f                                        // ← 大雨
    };

    // 黑天：暗紫/暗蓝/暗青的夜色（不是纯黑！用户原话"夜间太暗了"）+ 星空明月 + 自发光全开 + Bloom 更强
    private static readonly Preset P_Night = new Preset
    {
        sunMul = 0.11f,                                                  // 0.22 时主光还有 0.06，配合亮天空等于没天黑
        sunColor = new Color(0.52f, 0.62f, 1.00f), sunColorMix = 0.90f,  // 月光：偏蓝青，压得住暗紫
        noShadow = false,
        ambientMul = 0.40f,                                              // 环境光压到四成，草和树仍看得出轮廓
        ambientColor = new Color(0.13f, 0.11f, 0.26f), ambientMix = 0.94f,  // 暗紫环境光
        fogMul = 1.10f,
        // ⚠️ 雾色是"天黑"的命门：远处地形几乎全被雾盖住，天空区看到的其实是雾不是背景。
        // 线性 0.146 → 屏幕上约 107 灰（还是白天）；要成夜色必须压到 0.01 量级，
        // 而且 mix 要接近 1，否则每帧被控制器写回的亮基准 (0.61,0.68,0.75) 会渗回来。
        fogColor = new Color(0.012f, 0.014f, 0.040f), fogColorMix = 0.97f,
        bgColor = new Color(0.020f, 0.026f, 0.070f), bgMix = 0.94f,      // 夜：近黑的靛蓝天
        skyTint = new Color(0.11f, 0.10f, 0.26f), skyTintMix = 0.94f,    // 暗紫蓝夜空
        skyGround = new Color(0.06f, 0.07f, 0.16f), skyGroundMix = 0.92f,
        // ⚠️ skyExposure 是**乘**在 NeuroWorldController 每帧写回的 0.42~0.85 上，
        // 所以 0.62 实际只到 0.26~0.53 —— Procedural 天空在这个曝光下仍然是亮的，
        // 这就是"切到夜也不黑"的真正原因。要天真的黑，乘数必须压到 0.15 以下。
        skyExposure = 0.13f, sunDisk = 0f, sunSize = 0.020f,             // 藏掉太阳，只留月光
        bloomMul = 1.20f, satMul = 1.35f, expMul = 0.60f,                // 自发光更彩更亮 → 梦幻炫彩
        fireflyMul = 3.00f, fireflySizeMul = 1.70f,                      // 萤火虫"加满"
        critterMul = 0.45f, critterHeight = 0.80f,
        starAlpha = 2.30f, moonAlpha = 1.90f,                            // 星空/月亮加满（shader 上限 3）
        rain = 0.32f                                                     // 夜里下点小雨：湿地上反着萤火虫的光
    };

    // ---------------------------------------------------------------- 运行时状态
    private Preset _from, _to;
    private float _t = 1f;
    private bool _dirty;

    // 原始值（只存一次，绝不从"已经乘过的值"里再取基底）
    private float _baseBloom = -1f, _baseSat, _baseExp;
    private ParticleSystem[] _flyPs;
    private float[] _flyRate, _flySizeLo, _flySizeHi;
    private NeuroCritters[] _birds;
    private float[] _birdHeight;
    private bool _wired;
    private BrainBridge _bridge;      // 可选：让桥接/大模型也能切天气
    private bool _subscribed;

    // ================================================================ 生命周期
    void OnEnable()
    {
        AutoWire();
        if (!Application.isPlaying) { _from = _to = PresetOf(current); _t = 1f; }
    }

    void OnDisable()
    {
        if (_bridge != null && _subscribed) { _bridge.OnWorld -= OnWorldMessage; _subscribed = false; }
    }

    /// <summary>桥接下发了 weather 字段 → 切换天气（空值忽略）</summary>
    private void OnWorldMessage(WorldMessage m)
    {
        if (m == null || string.IsNullOrEmpty(m.weather)) return;
        SetWeatherByName(m.weather);
    }

    /// <summary>按名字切天气：sunny/rain/night、晴/雨/夜、1/2/3 都认</summary>
    public void SetWeatherByName(string name)
    {
        if (string.IsNullOrEmpty(name)) return;
        string k = name.Trim().ToLowerInvariant();
        if (k == "sunny" || k == "sun" || k == "clear" || k == "晴" || k == "晴天" || k == "1") SetWeather(WeatherType.Sunny);
        // "hazy/haze/fog/雾/雾霾" 是旧名字，保留做兼容：外部脚本/旧指令仍能触发中间这一档
        else if (k == "hazy" || k == "haze" || k == "fog" || k == "foggy" || k == "雾" || k == "雾霾"
              || k == "rain" || k == "rainy" || k == "雨" || k == "雨天" || k == "下雨" || k == "2") SetWeather(WeatherType.Hazy);
        else if (k == "night" || k == "dark" || k == "夜" || k == "黑夜" || k == "黑天" || k == "3") SetWeather(WeatherType.Night);
        else Debug.LogWarning("[Weather] 认不出的天气名：" + name);
    }

    void Start()
    {
        AutoWire();
        _from = _to = PresetOf(current);
        _t = 1f;
    }

    void Update()
    {
        if (Application.isPlaying && keyboardSwitch)
        {
            if (Input.GetKeyDown(KeyCode.Alpha1)) SetWeather(WeatherType.Sunny);
            else if (Input.GetKeyDown(KeyCode.Alpha2)) SetWeather(WeatherType.Hazy);
            else if (Input.GetKeyDown(KeyCode.Alpha3)) SetWeather(WeatherType.Night);
        }

        // 越放松越晴朗（手动/指令切换后 manualHoldSeconds 秒内不插手；
        // 两次自动切换之间至少隔 minAutoHoldSeconds 秒，免得脑电一抖就来回跳）
        if (Application.isPlaying && driveByRelax && world != null
            && Time.time >= _manualUntil && Time.time >= _nextAuto)
        {
            WeatherType want = AutoTargetByRelax(world.Relax);
            if (want != current) Switch(want, false);
        }

        if (_t < 1f)
        {
            _t = Mathf.Min(1f, _t + Time.deltaTime / Mathf.Max(0.05f, transitionSeconds));
            Apply();
            return;
        }

        // 编辑器里：基底没人写，每帧套用会滚雪球 → 只在被显式要求时套一次
        if (_dirty)
        {
            _dirty = false;
            Apply();
            return;
        }
        if (Application.isPlaying) Apply();
    }

    // ================================================================ 对外接口
    /// <summary>平滑切换天气（默认 2.6 秒过渡）。手动/指令调用会让自动联动暂停一会儿。</summary>
    public void SetWeather(WeatherType t) { Switch(t, true); }

    private void Switch(WeatherType t, bool manual)
    {
        AutoWire();
        _from = LivePreset();
        _to = PresetOf(t);
        _t = 0f;
        current = t;
        _dirty = true;
        if (manual) _manualUntil = Time.time + manualHoldSeconds;
        _nextAuto = Time.time + minAutoHoldSeconds;
        Debug.Log(string.Format("[Weather] 切换到 {0}（{1:0.0} 秒过渡，{2}）", t, transitionSeconds,
            manual ? "手动" : "随放松度自动"));
    }

    /// <summary>放松度 → 该哪套天气。带 ±0.04 滞后，避免在阈值附近反复切。</summary>
    private WeatherType AutoTargetByRelax(float r)
    {
        switch (current)
        {
            case WeatherType.Sunny: return r < 0.58f ? (r < 0.34f ? WeatherType.Night : WeatherType.Hazy) : WeatherType.Sunny;
            case WeatherType.Night: return r >= 0.38f ? (r >= 0.66f ? WeatherType.Sunny : WeatherType.Hazy) : WeatherType.Night;
            default:                return r >= 0.66f ? WeatherType.Sunny : (r < 0.34f ? WeatherType.Night : WeatherType.Hazy);
        }
    }

    /// <summary>瞬切（出对比图用，没有过渡）。</summary>
    public void SetWeatherImmediate(WeatherType t)
    {
        AutoWire();
        current = t;
        _from = _to = PresetOf(t);
        _t = 1f;
        _dirty = false;
        Apply();
    }

    [ContextMenu("切换：晴天（Sunny）")] private void CtxSunny() { SetWeatherImmediate(WeatherType.Sunny); }
    [ContextMenu("切换：雨天（Rainy）")] private void CtxRain()  { SetWeatherImmediate(WeatherType.Hazy); }
    [ContextMenu("切换：黑天（Night）")] private void CtxNight() { SetWeatherImmediate(WeatherType.Night); }

    void OnValidate()
    {
        if (!Application.isPlaying) { _from = _to = PresetOf(current); _t = 1f; _dirty = true; }
    }

    private static Preset PresetOf(WeatherType t)
    {
        if (t == WeatherType.Hazy) return P_Hazy;
        if (t == WeatherType.Night) return P_Night;
        return P_Sunny;
    }

    /// <summary>当前这一刻生效的叠加量（过渡中就是插值后的结果）。</summary>
    private Preset LivePreset()
    {
        if (_t >= 1f) return PresetOf(current);
        float s = _t * _t * (3f - 2f * _t);      // SmoothStep，两端更柔和
        return Blend(_from, _to, s);
    }

    // ================================================================ 找引用
    private void AutoWire()
    {
        if (world == null) world = FindFirstObjectByType<NeuroWorldController>();

        if (sun == null) { var g = GameObject.Find("Sun");       if (g != null) sun = g.GetComponent<Light>(); }
        if (fill == null) { var g = GameObject.Find("FillLight"); if (g != null) fill = g.GetComponent<Light>(); }
        if (skyMaterial == null) skyMaterial = RenderSettings.skybox;
        if (cam == null) cam = Camera.main;

        if (fireflies == null) { var g = GameObject.Find("Fireflies"); if (g != null) fireflies = g.transform; }
        if (critters == null)  { var g = GameObject.Find("Critters");  if (g != null) critters = g.transform; }
        if (nightSky == null)  { var g = GameObject.Find("NightSky");  if (g != null) nightSky = g.transform; }

        if (nightSky != null)
        {
            if (starsMaterial == null)
            {
                var t = nightSky.Find("Stars");
                if (t != null) { var r = t.GetComponent<Renderer>(); if (r != null) starsMaterial = r.sharedMaterial; }
            }
            if (moonMaterial == null)
            {
                var t = nightSky.Find("Moon");
                if (t != null) { var r = t.GetComponent<Renderer>(); if (r != null) moonMaterial = r.sharedMaterial; }
            }
        }

        if (world != null && _baseBloom < 0f)
        {
            var fx = world.GetComponentInChildren<NeuroPostFX>(true);
            if (fx == null) fx = FindFirstObjectByType<NeuroPostFX>();
            if (fx != null) { _baseBloom = fx.bloomIntensity; _baseSat = fx.saturation; _baseExp = fx.exposure; }
        }

        // 萤火虫：起飞前记下每套粒子系统原本的发射率和尺寸
        if (fireflies != null && (_flyPs == null || _flyPs.Length == 0))
        {
            _flyPs = fireflies.GetComponentsInChildren<ParticleSystem>(true);
            if (_flyPs != null && _flyPs.Length > 0)
            {
                _flyRate = new float[_flyPs.Length];
                _flySizeLo = new float[_flyPs.Length];
                _flySizeHi = new float[_flyPs.Length];
                for (int i = 0; i < _flyPs.Length; i++)
                {
                    _flyRate[i] = _flyPs[i].emission.rateOverTime.constant;
                    var sz = _flyPs[i].main.startSize;
                    _flySizeLo[i] = sz.constantMin;
                    _flySizeHi[i] = sz.constantMax;
                }
            }
        }

        // 小鸟：只有 mode == Bird 的才受"小鸟多/少/飞低"影响，蝴蝶不动
        if (critters != null && (_birds == null || _birds.Length == 0))
        {
            var all = critters.GetComponentsInChildren<NeuroCritters>(true);
            var list = new System.Collections.Generic.List<NeuroCritters>();
            for (int i = 0; i < all.Length; i++) if (all[i] != null && all[i].mode == NeuroCritters.Mode.Bird) list.Add(all[i]);
            _birds = list.ToArray();
            _birdHeight = new float[_birds.Length];
            for (int i = 0; i < _birds.Length; i++) _birdHeight[i] = _birds[i].height;
        }

        if (_bridge == null) _bridge = FindFirstObjectByType<BrainBridge>();
        if (_bridge != null && !_subscribed) { _bridge.OnWorld += OnWorldMessage; _subscribed = true; }
        _wired = true;
    }

    // ================================================================ 套用
    private void Apply()
    {
        // ⚠️ AutoWire 是幂等的（每个字段都 if (x == null) 才找），所以可以反复调。
        // 只调一次是坑：如果第一次跑的时候 world/后期还没就绪，_baseBloom 会永久停在 -1，
        // 之后 bloomMul / satMul / expMul 全部空转 —— 表现就是"调夜景参数一点不变"。
        if (!_wired || world == null || _baseBloom < 0f) AutoWire();
        var p = LivePreset();

        // ---- 主光：强度相乘、颜色靠近 ----
        if (sun != null)
        {
            sun.intensity *= p.sunMul;
            sun.color = Color.Lerp(sun.color, p.sunColor, p.sunColorMix);
            sun.shadows = p.noShadow ? LightShadows.None : LightShadows.Soft;
        }
        if (fill != null && p.noShadow) fill.intensity = Mathf.Max(fill.intensity, 0.10f);   // 无阴影时补一点漫射

        // ---- 环境光 ----
        RenderSettings.ambientLight = Color.Lerp(RenderSettings.ambientLight, p.ambientColor, p.ambientMix) * p.ambientMul;

        // ---- 雾 ----
        RenderSettings.fogDensity *= p.fogMul;
        RenderSettings.fogColor = Color.Lerp(RenderSettings.fogColor, p.fogColor, p.fogColorMix);
        RenderSettings.fog = RenderSettings.fogDensity > 0.002f;

        // ---- 天色（相机背景色）----
        // 本场景相机是 clearFlags=SolidColor，天空盒压根没被画出来，
        // 所以调 _SkyTint/_Exposure 全是空转 —— 真正决定天是什么颜色的是这里。
        if (cam != null) cam.backgroundColor = Color.Lerp(cam.backgroundColor, p.bgColor, p.bgMix);

        // ---- 天空盒（万一以后改成 Skybox 清屏，这些参数才是有效的）----
        if (skyMaterial != null)
        {
            if (skyMaterial.HasProperty("_SkyTint"))
                skyMaterial.SetColor("_SkyTint", Color.Lerp(skyMaterial.GetColor("_SkyTint"), p.skyTint, p.skyTintMix));
            if (skyMaterial.HasProperty("_GroundColor"))
                skyMaterial.SetColor("_GroundColor", Color.Lerp(skyMaterial.GetColor("_GroundColor"), p.skyGround, p.skyGroundMix));
            if (skyMaterial.HasProperty("_Exposure"))
                skyMaterial.SetFloat("_Exposure", skyMaterial.GetFloat("_Exposure") * p.skyExposure);
            if (skyMaterial.HasProperty("_SunDisk")) skyMaterial.SetFloat("_SunDisk", p.sunDisk);
            if (skyMaterial.HasProperty("_SunSize")) skyMaterial.SetFloat("_SunSize", p.sunSize);
        }

        // ---- 后期：泛光 / 饱和 / 曝光（用启动时存的原始值算，不滚雪球）----
        if (_baseBloom >= 0f && world != null)
        {
            var fx = world.GetComponentInChildren<NeuroPostFX>(true);
            if (fx == null) fx = FindFirstObjectByType<NeuroPostFX>();
            if (fx != null)
            {
                fx.bloomIntensity = Mathf.Clamp(_baseBloom * p.bloomMul, 0f, 4f);
                fx.saturation = Mathf.Clamp(_baseSat * p.satMul, 0f, 2f);
                fx.exposure = Mathf.Clamp(_baseExp * p.expMul, 0.1f, 3f);
            }
        }

        // ---- 萤火虫 ----
        if (_flyPs != null)
        {
            for (int i = 0; i < _flyPs.Length; i++)
            {
                if (_flyPs[i] == null) continue;
                var em = _flyPs[i].emission;
                em.rateOverTime = _flyRate[i] * p.fireflyMul;
                var main = _flyPs[i].main;
                main.startSize = new ParticleSystem.MinMaxCurve(_flySizeLo[i] * p.fireflySizeMul,
                                                                _flySizeHi[i] * p.fireflySizeMul);
            }
        }

        // ---- 小鸟：数量 + 高度 ----
        if (_birds != null && _birds.Length > 0)
        {
            int keep = Mathf.Clamp(Mathf.RoundToInt(_birds.Length * p.critterMul), 0, _birds.Length);
            for (int i = 0; i < _birds.Length; i++)
            {
                if (_birds[i] == null) continue;
                // 打散着留，别只留前面几只
                bool on = ((i * 7) % _birds.Length) < keep ? true : (i < keep);
                _birds[i].gameObject.SetActive(on);
                _birds[i].height = _birdHeight[i] * p.critterHeight;
            }
        }

        // ---- 雨：（雨量由天气预设决定，NeuroRain 自己平滑过渡）----
        var nr = NeuroRain.Ensure();
        if (nr != null) nr.target = p.rain;
        NeuroRainAudio.Ensure();     // 雨声：音量自己跟着 NeuroRain.intensity 走

        // ---- 星空 / 月亮 ----
        if (starsMaterial != null && starsMaterial.HasProperty("_Alpha")) starsMaterial.SetFloat("_Alpha", p.starAlpha);
        if (moonMaterial != null && moonMaterial.HasProperty("_Alpha")) moonMaterial.SetFloat("_Alpha", p.moonAlpha);

        // 刻意不 SetActive(false)：一旦"隐藏"了它，GameObject.Find 就再也找不到，
        // 而 Alpha=0 的加法着色器本来就完全不可见，没必要冒这个险。
    }

    // ================================================================ 结构体插值
    private static float L(float a, float b, float t) { return a + (b - a) * t; }
    private static Color L(Color a, Color b, float t) { return Color.Lerp(a, b, t); }

    private static Preset Blend(Preset a, Preset b, float t)
    {
        Preset r = default(Preset);
        r.sunMul = L(a.sunMul, b.sunMul, t);
        r.sunColor = L(a.sunColor, b.sunColor, t);
        r.sunColorMix = L(a.sunColorMix, b.sunColorMix, t);
        r.noShadow = t < 0.5f ? a.noShadow : b.noShadow;
        r.ambientMul = L(a.ambientMul, b.ambientMul, t);
        r.ambientColor = L(a.ambientColor, b.ambientColor, t);
        r.ambientMix = L(a.ambientMix, b.ambientMix, t);
        r.fogMul = L(a.fogMul, b.fogMul, t);
        r.fogColor = L(a.fogColor, b.fogColor, t);
        r.fogColorMix = L(a.fogColorMix, b.fogColorMix, t);
        r.skyTint = L(a.skyTint, b.skyTint, t);
        r.skyTintMix = L(a.skyTintMix, b.skyTintMix, t);
        r.skyGround = L(a.skyGround, b.skyGround, t);
        r.skyGroundMix = L(a.skyGroundMix, b.skyGroundMix, t);
        r.skyExposure = L(a.skyExposure, b.skyExposure, t);
        r.sunDisk = L(a.sunDisk, b.sunDisk, t);
        r.sunSize = L(a.sunSize, b.sunSize, t);
        r.bloomMul = L(a.bloomMul, b.bloomMul, t);
        r.satMul = L(a.satMul, b.satMul, t);
        r.expMul = L(a.expMul, b.expMul, t);
        r.fireflyMul = L(a.fireflyMul, b.fireflyMul, t);
        r.fireflySizeMul = L(a.fireflySizeMul, b.fireflySizeMul, t);
        r.critterMul = L(a.critterMul, b.critterMul, t);
        r.critterHeight = L(a.critterHeight, b.critterHeight, t);
        r.starAlpha = L(a.starAlpha, b.starAlpha, t);
        r.moonAlpha = L(a.moonAlpha, b.moonAlpha, t);
        r.rain = L(a.rain, b.rain, t);
        return r;
    }
}
