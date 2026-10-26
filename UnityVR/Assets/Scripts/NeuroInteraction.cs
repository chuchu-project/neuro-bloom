// NeuroInteraction.cs —— B 阶段核心交互：光纹汇聚 → 绽放
//
// 设计要点（对应 PRD 的 F5 与铁律）：
//   1. 只读 NeuroWorldController 的平滑值（world.Relax），绝不自己再算一套判断；
//   2. 用「个人基线」当参照：基线是放松度的慢速滑动平均，只看相对自己高了多少；
//   3. 失败不惩罚：低于基线时只是缓慢回落，不扣分、不变暗、不给负面提示；
//   4. 画面上永远不出现数字、分数、百分比；
//   5. NeuroInteraction.Active = false 可全局关掉全部互动（P8 对照轮的前提）。
using UnityEngine;

[ExecuteAlways]
public class NeuroInteraction : MonoBehaviour
{
    [Header("引用（由 NeuroBloom/一键搭建场景 v2 自动接好）")]
    public NeuroWorldController world;   // 只读它的 Relax
    public MeshRenderer runeRing;        // 光纹环
    public MeshRenderer burstRing;       // 绽放脉冲（平时隐藏）
    public AudioSource chime;            // 绽放音效（没有 clip 时静默）

    [Header("蓄能")]
    public float chargeSeconds = 75f;    // 持续高于个人基线时，攒满所需秒数
    public float baselineTau   = 45f;    // 个人基线的时间常数（秒）
    public float gain          = 10f;    // 基线富余量的放大倍数
    public float retreatFactor = 0.5f;   // 回落系数（< 1 = 回落比蓄能更慢、更温和）
    public float burstSeconds  = 4f;     // 绽放持续时间

    [Header("外观")]
    public float runeBaseSize = 3.0f;
    public float runeMaxSize  = 4.8f;
    public Color runeCold = new Color(0.30f, 0.55f, 0.90f);   // charge = 0
    public Color runeWarm = new Color(0.55f, 1.00f, 0.88f);   // charge = 1

    [Header("手动预览（仅出图用；运行时保持关闭）")]
    public bool previewMode = false;
    [Range(0f, 1f)] public float previewCharge = 0f;
    [Range(0f, 1f)] public float previewBurst  = 0f;

    // ================= P8 对照轮的全局开关 =================
    // Active = false 时：不蓄能、不绽放、不往画面注入任何东西。
    // 这是「什么都不干预」对照轮成立的前提，不能删、不能挪作他用。
    public static bool Active = true;
    public bool enabledInScene = true;

    /// <summary>当前蓄能进度 0~1（只读，供调试/日志用，不显示给使用者）。</summary>
    public float Charge { get; private set; }
    /// <summary>已完成多少次绽放（只读，供调试/日志用）。</summary>
    public int BurstCount { get; private set; }

    private float _baseline = 0.5f;
    private bool  _baselineReady = false;
    private float _burstT = -1f;   // < 0 表示不在绽放中
    private float _pulse  = 0f;    // 0~1 当前脉冲强度
    private MaterialPropertyBlock _mpb;

    private static readonly int IdTint  = Shader.PropertyToID("_TintColor");
    private static readonly int IdColor = Shader.PropertyToID("_Color");

    private bool Effective { get { return Active && enabledInScene && isActiveAndEnabled; } }

    void OnEnable()
    {
        _baselineReady = false;
        Charge = 0f;
        _burstT = -1f;
        _pulse = 0f;
        PushPulse(0f);
        ApplyVisual();
    }

    void OnDisable()
    {
        PushPulse(0f);   // 组件被关掉时，绝不把脉冲留在画面上
    }

    void Update()
    {
        // ---- 编辑器（未播放）时只响应手动预览，不碰任何运行时状态 ----
        if (!Application.isPlaying)
        {
            if (previewMode)
            {
                Charge = Mathf.Clamp01(previewCharge);
                _pulse = Mathf.Clamp01(previewBurst);
                ApplyVisual();
            }
            return;
        }

        float dt = Time.deltaTime;
        if (dt <= 0f) { ApplyVisual(); return; }

        // ---- P8 对照轮：互动完全停摆 ----
        if (!Effective)
        {
            Charge = 0f;
            _burstT = -1f;
            _pulse = Mathf.MoveTowards(_pulse, 0f, dt * 3f);
            PushPulse(0f);
            ApplyVisual();
            return;
        }

        float relax = (world != null) ? world.Relax : 0f;

        // ---- 个人基线：指数滑动平均（每个人拿自己的水平当参照）----
        if (!_baselineReady) { _baseline = relax; _baselineReady = true; }
        float k = 1f - Mathf.Exp(-dt / Mathf.Max(1f, baselineTau));
        _baseline = Mathf.Lerp(_baseline, relax, k);

        if (_burstT >= 0f)
        {
            // ---- 绽放阶段：脉冲衰减 ----
            _burstT += dt;
            if (_burstT >= burstSeconds) { _burstT = -1f; _pulse = 0f; }
            else { _pulse = 1f - (_burstT / burstSeconds); }
        }
        else
        {
            // ---- 蓄能 / 回落 ----
            float surplus = relax - _baseline;
            float step = surplus * dt * gain / Mathf.Max(1f, chargeSeconds);
            if (step < 0f) step *= Mathf.Max(0.05f, retreatFactor);   // 回落更慢、更温和

            Charge = Mathf.Clamp01(Charge + step);

            if (Charge >= 1f) StartBurst();
        }

        if (_burstT < 0f) _pulse = Mathf.MoveTowards(_pulse, 0f, dt * 2f);

        // 低幅呼吸感：让人看得出"它在等我"，而不是一块死掉的光
        float breath = 0.5f + 0.5f * Mathf.Sin(Time.time * 1.5f);
        _pulse = Mathf.Max(_pulse, Charge * 0.16f * breath);

        PushPulse(_pulse);
        ApplyVisual();
    }

    private void StartBurst()
    {
        Charge = 0f;
        BurstCount++;
        _burstT = 0f;
        _pulse = 1f;
        if (chime != null && chime.clip != null) chime.Play();
    }

    private void PushPulse(float p)
    {
        if (world != null) world.bloomPulse = Mathf.Clamp01(p);
    }

    /// <summary>
    /// 把当前蓄能进度与脉冲强度画到光纹环和绽放环上。
    /// 只用"尺寸 + 亮度"表达，不出现任何数字。
    /// </summary>
    public void ApplyVisual()
    {
        // 编辑器预览时 Update() 不会跑，必须直接读预览值，否则三张蓄能图会长得一模一样
        bool previewing = previewMode && !Application.isPlaying;
        float c = Mathf.Clamp01(previewing ? previewCharge : Charge);
        float p = Mathf.Clamp01(previewing ? previewBurst  : _pulse);

        if (runeRing != null)
        {
            float size = Mathf.Lerp(runeBaseSize, runeMaxSize, c) * (1f + 0.22f * p);
            runeRing.transform.localScale = new Vector3(size, size, 1f);
            if (Application.isPlaying)
                runeRing.transform.Rotate(0f, 0f, (6f + 14f * c) * Time.deltaTime, Space.Self);

            runeRing.enabled = true;
            SetAdditiveColor(runeRing, Color.Lerp(runeCold, runeWarm, c),
                             Mathf.Lerp(0.35f, 1.00f, c) * (1f + 0.5f * p));
        }

        if (burstRing != null)
        {
            float size = Mathf.Lerp(0.8f, 9.0f, p);
            burstRing.transform.localScale = new Vector3(size, size, 1f);
            burstRing.enabled = p > 0.004f;
            SetAdditiveColor(burstRing, new Color(0.78f, 1.00f, 0.96f), p * p * 0.9f);
        }
    }

    /// <summary>用 MaterialPropertyBlock 改色：不产生材质实例，也不会污染 .mat 资产。</summary>
    private void SetAdditiveColor(MeshRenderer r, Color rgb, float brightness)
    {
        if (_mpb == null) _mpb = new MaterialPropertyBlock();
        r.GetPropertyBlock(_mpb);
        var col = new Color(rgb.r, rgb.g, rgb.b, Mathf.Clamp01(brightness));
        _mpb.SetColor(IdTint, col);
        _mpb.SetColor(IdColor, col);
        r.SetPropertyBlock(_mpb);
    }

    void OnValidate()
    {
        if (previewMode) ApplyVisual();
    }
}
