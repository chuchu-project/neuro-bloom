// NeuroWorldController.cs —— 世界状态机（零依赖）
// 把脑波状态平滑映射到 VR 世界的视觉参数：花/水晶生长、阳光强弱、雾气浓度、天空与雾色。
using System;
using UnityEngine;

public class NeuroWorldController : MonoBehaviour
{
    [Header("引用")]
    public BrainBridge bridge;

    [Header("世界元素（可留空；用 NeuroBloom/一键搭建场景 会自动填）")]
    public Transform[] flowers;      // 花朵
    public Transform[] crystals;     // 水晶
    public Light sunLight;           // 主光源
    public Light crystalLight;       // 水晶辉光
    public ParticleSystem fog;       // 环境粒子（萤火虫/浮尘）
    public AudioSource ambient;      // 环境音
    public AudioSource voice;        // 引导语音
    public Material skyMaterial;     // 程序化天空盒材质

    [Header("平滑与缩放")]
    [Range(1f, 12f)] public float lerpSpeed = 3f;
    public float minScale = 0.25f;
    public float maxScale = 1.0f;

    [Header("运行时状态（只读）")]
    public string currentLabel = "unknown";
    public string currentScript = "";

    [Header("B 阶段交互（由 NeuroInteraction 写入；对照轮保持 0）")]
    [Range(0f, 1f)] public float bloomPulse = 0f;   // 绽放脉冲：短暂放大花朵/水晶并提亮水晶辉光

    [Header("专注度（第二轴：只作用在水晶上，不改阳光/雾/花）")]
    [Tooltip("专注度越高，水晶棱面越亮越清晰；体积只加一点点，避免和放松度抢同一件事")]
    public float focusScaleBonus = 0.12f;   // 最多再加 12% 体积
    public float focusGlowBonus = 0.9f;     // 最多再加 90% 辉光
    public float focusCrispness = 0.45f;    // 光色向「冷白」收敛的程度
    [Range(0f, 1f)] public float currentFocus = 0f;   // 运行时状态（只读）

    // 当前值与目标值
    private float _bloom, _sun, _fog, _crystal, _focus;
    private float _tBloom, _tSun, _tFog, _tCrystal, _tFocus;

    /// <summary>
    /// 平滑后的放松度（0~1）。只读 —— B 阶段交互唯一允许的数据来源，
    /// 交互层不得自己重算判断，也不得写回 _bloom。
    /// </summary>
    public float Relax { get { return Mathf.Clamp01(_bloom); } }

    private static readonly Color SkyTopCalm = new Color(0.22f, 0.48f, 0.90f);
    private static readonly Color SkyTopStorm = new Color(0.16f, 0.17f, 0.24f);
    private static readonly Color SkyHorizonCalm = new Color(0.80f, 0.88f, 1.00f);
    private static readonly Color SkyHorizonStorm = new Color(0.34f, 0.34f, 0.40f);
    private static readonly Color FogCalm = new Color(0.74f, 0.84f, 0.92f);
    private static readonly Color FogStorm = new Color(0.36f, 0.37f, 0.43f);
    private static readonly Color SunCalm = new Color(1.00f, 0.96f, 0.85f);
    private static readonly Color SunStorm = new Color(0.55f, 0.62f, 0.78f);

    void OnEnable()
    {
        if (bridge != null) bridge.OnWorld += HandleWorld;
        RenderSettings.fogMode = FogMode.ExponentialSquared;
        RenderSettings.fog = true;
    }

    void OnDisable()
    {
        if (bridge != null) bridge.OnWorld -= HandleWorld;
    }

    private void HandleWorld(WorldMessage m)
    {
        currentLabel = string.IsNullOrEmpty(m.label) ? currentLabel : m.label;

        var p = m.world_params;
        if (p != null)
        {
            _tBloom = p.bloom;
            _tSun = p.sunlight;
            _tFog = p.fog;
            _tCrystal = p.crystal;
        }

        // 专注度是独立的一轴：服务端不发（老版本）时按 0 处理，画面回到原来的样子
        _tFocus = Mathf.Clamp01(m.focus);

        if (!string.IsNullOrEmpty(m.script))
        {
            currentScript = m.script;
            // 这里可对接 TTS（如书生语音合成），把引导语播出来
            if (voice != null && voice.clip != null && !voice.isPlaying)
                voice.Play();
        }
    }

    void Update()
    {
        float t = Mathf.Clamp01(Time.deltaTime * lerpSpeed);
        _bloom = Mathf.Lerp(_bloom, _tBloom, t);
        _sun = Mathf.Lerp(_sun, _tSun, t);
        _fog = Mathf.Lerp(_fog, _tFog, t);
        _crystal = Mathf.Lerp(_crystal, _tCrystal, t);
        _focus = Mathf.Lerp(_focus, _tFocus, t);
        currentFocus = _focus;

        float storm = Mathf.Clamp01(_fog);   // 焦虑程度

        // ---- 阳光 / 环境色 ----
        if (sunLight != null)
        {
            sunLight.intensity = 0.20f + 0.90f * _sun;
            sunLight.color = Color.Lerp(SunStorm, SunCalm, _sun);
        }
        RenderSettings.ambientLight = Color.Lerp(
            new Color(0.16f, 0.18f, 0.24f), new Color(0.42f, 0.46f, 0.52f), _sun);
        RenderSettings.ambientIntensity = 0.42f + 0.22f * _sun;

        // ---- 体积雾（Unity 原生，非粒子）----
        RenderSettings.fog = _fog > 0.02f;
        RenderSettings.fogDensity = 0.006f + 0.055f * _fog;
        RenderSettings.fogColor = Color.Lerp(FogCalm, FogStorm, storm);

        // ---- 天空盒变色 ----
        if (skyMaterial != null)
        {
            if (skyMaterial.HasProperty("_SkyTint"))
                skyMaterial.SetColor("_SkyTint", Color.Lerp(SkyTopCalm, SkyTopStorm, storm));
            if (skyMaterial.HasProperty("_GroundColor"))
                skyMaterial.SetColor("_GroundColor", Color.Lerp(SkyHorizonCalm, SkyHorizonStorm, storm));
            if (skyMaterial.HasProperty("_AtmosphereThickness"))
                skyMaterial.SetFloat("_AtmosphereThickness", Mathf.Lerp(0.85f, 2.2f, storm));
            if (skyMaterial.HasProperty("_Exposure"))
                skyMaterial.SetFloat("_Exposure", Mathf.Lerp(0.85f, 0.42f, storm));
        }

        // ---- 萤火虫 / 浮尘（细微，仅氛围）----
        if (fog != null)
        {
            var em = fog.emission;
            em.rateOverTime = 3f + 4f * _sun;
        }

        // ---- 水晶辉光：放松度决定「亮不亮」，专注度决定「清不清」 ----
        // 一条轴只讲一件事：放松 → 体积和暖光；专注 → 棱面锐度、冷白光、边缘更收束。
        // 所以这里不是把 focus 混进 crystal，而是让它单独乘一个系数。
        if (crystalLight != null)
        {
            float glow = (0.4f + 3.0f * _crystal) * (1f + 0.7f * Mathf.Clamp01(bloomPulse));
            glow *= (1f + focusGlowBonus * _focus);
            crystalLight.intensity = glow;
            Color softRelaxed = new Color(0.35f, 0.85f, 1f);
            Color warmRelaxed = new Color(0.6f, 1f, 0.9f);
            Color crispFocus = new Color(0.86f, 1f, 1f);
            crystalLight.color = Color.Lerp(
                Color.Lerp(softRelaxed, warmRelaxed, _crystal),
                crispFocus, focusCrispness * _focus);
        }

        ApplyGrowth(flowers, _bloom);
        ApplyGrowth(crystals, _crystal, 1f + focusScaleBonus * _focus);
        if (ambient != null) ambient.volume = Mathf.Lerp(0.05f, 0.5f, _sun);
    }

    private void ApplyGrowth(Transform[] items, float factor, float extraScale = 1f)
    {
        if (items == null) return;
        // 绽放脉冲：短暂把花/水晶再放大最多 35%，随后平滑回落
        // extraScale：专注度给水晶的额外体积（花传 1，不受影响）
        float s = Mathf.Lerp(minScale, maxScale, Mathf.Clamp01(factor))
                  * (1f + 0.35f * Mathf.Clamp01(bloomPulse))
                  * Mathf.Max(0.1f, extraScale);
        for (int i = 0; i < items.Length; i++)
        {
            if (items[i] == null) continue;
            items[i].localScale = Vector3.Lerp(items[i].localScale,
                Vector3.one * s, Time.deltaTime * lerpSpeed);
        }
    }
}
