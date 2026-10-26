// GuideTextUpdater.cs —— 把"脑境向导"的引导语显示在 VR 世界里的文字牌上
using UnityEngine;

[RequireComponent(typeof(TextMesh))]
public class GuideTextUpdater : MonoBehaviour
{
    public NeuroWorldController world;

    [Tooltip("播种组件。种下第一株之前，字牌留给开场那句操作提示，不让大模型话术顶掉")]
    public NeuroGarden garden;

    [Tooltip("开场那句操作提示最多显示多少秒；到时间就算没点过，也让大模型接管")]
    public float introSeconds = 22f;

    private TextMesh _tm;
    private NeuroGuideTitle _title;
    private string _last;
    private bool _tookOver;
    private float _bornAt;
    private float _charSizeBase;   // 构建脚本设的原始字号（0.048），自适应缩字号时以它为基准

    void Awake()
    {
        _tm = GetComponent<TextMesh>();
        _title = GetComponent<NeuroGuideTitle>();   // 有它就让花体那套接管（换字体、重挑材质、重新淡入）
        // 场景里的 world 字段没被接线过（构建脚本只 AddComponent，没赋值）——
        // 少了这一句，Update 第一行就 return，字牌永远停在开场那句，
        // 看起来就像"大模型根本没接进来"。自己找一次兜住。
        if (world == null) world = FindFirstObjectByType<NeuroWorldController>();
        if (garden == null) garden = FindFirstObjectByType<NeuroGarden>();
        _bornAt = Time.time;
    }

    void Update()
    {
        if (world == null || _tm == null) return;

        // 开场那句「点一下地面，种下一束光」是屏幕上唯一教人怎么操作的话。
        // 大模型的陪伴话术一上来就把它顶掉的话，用户就不知道要点哪儿了 ——
        // 所以等用户真的种下第一株，字牌才交给大模型接管。
        if (!_tookOver)
        {
            if (garden == null) garden = FindFirstObjectByType<NeuroGarden>();
            // 种下第一株就交棒；要是一直没人点，最多等 introSeconds 秒 ——
            // 否则演示时字牌会一直卡在开场那句，看不到大模型的话术。
            bool planted = garden != null && garden.PlantCount > 0;
            if (!planted && Time.time - _bornAt < introSeconds) return;
            _tookOver = true;
        }

        var s = world.currentScript;
        if (string.IsNullOrEmpty(s) || s == _last) return;
        _last = s;

        // —— 排版自适应（大模型的句子长短差很多，十几字到六十几字都有）——
        // 原来固定"每行 18 字"，实测会横向冲出画面：字号 0.048 × 18 字 ≈ 13.9 个世界单位，
        // 而那个距离上屏幕只有 ~11 个单位宽 → 左右两边的字直接看不见（缺字）。
        // 规则：先按"摊成 3 行"算每行字数；每行超过 softPerLine 字，就把字号等比缩小，
        //       让"每行字数 × 字号"始终不超过 softPerLine 个字的宽度 → 永远整句可见。
        const int softPerLine = 11;   // 一屏舒服放得下的字数（开场那句就是 11 字/行）
        const int hardPerLine = 12;   // 单行字数上限。17 字实测会横向冲出屏幕两侧，别再放大
        if (_charSizeBase <= 0f) _charSizeBase = _tm.characterSize;   // 记住构建时的原始字号
        int perLine = Mathf.Clamp(Mathf.CeilToInt(s.Length / 3f), softPerLine, hardPerLine);
        _tm.characterSize = _charSizeBase * (perLine > softPerLine ? softPerLine / (float)perLine : 1f);

        var sb = new System.Text.StringBuilder();
        for (int i = 0; i < s.Length; i += perLine)
        {
            sb.Append(s.Substring(i, Mathf.Min(perLine, s.Length - i)));
            sb.Append('\n');
        }
        if (_title != null) _title.SetText(sb.ToString());
        else _tm.text = sb.ToString();
        // 朝向这件事很容易记反，记牢：**TextMesh 可读的一面是 -Z**。
        // （新建一个 3D Text，默认 rotation=(0,0,0)，相机在 (0,1,-10) 也是默认朝向，
        //   这时候字是能正常读的 —— 说明面向相机的那面是 -Z，不是 +Z。）
        // 所以要 readable 就得让 forward(+Z) 背着相机 = 文字位置 - 相机位置。
        // 上一版写成了 相机位置 - 文字位置，屏幕上就成了左右镜像的字。
        Camera cam = Camera.main;
        if (cam != null)
        {
            Vector3 fwd = transform.position - cam.transform.position;
            if (fwd.sqrMagnitude > 1e-4f)
                transform.rotation = Quaternion.LookRotation(fwd) * Quaternion.Euler(8f, 0f, -3f);
        }
    }
}
