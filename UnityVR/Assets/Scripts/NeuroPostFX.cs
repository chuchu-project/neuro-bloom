// NeuroPostFX.cs —— 内置渲染管线（Built-in RP）后处理，挂在 Main Camera 上。
// 无需任何 Package（不使用 Post Processing Stack / URP）。
//
// 链路：OnRenderImage(src, dst)
//   1. 半分辨率 PreFilter 提取高亮区域
//   2. 逐次迭代的可分离高斯模糊（每次迭代加大半径，得到宽而柔的泛光）
//   3. 合成：原图 + 泛光，再叠加饱和度 / 对比度 / 冷暖分离 / 暗角 / 色差
//
// 所有参数都是 Inspector 可调的公开字段，方便美术方向微调。
// 若 Shader.Find("Hidden/NeuroBloom") 失败（例如着色器编译报错），
// 本组件会退化为直通（原图输出）并只警告一次，绝不黑屏。
using UnityEngine;

[ExecuteAlways]
[RequireComponent(typeof(Camera))]
[AddComponentMenu("NeuroBloom/Post FX")]
public class NeuroPostFX : MonoBehaviour
{
    [Header("Bloom 泛光")]
    [Range(0f, 4f)] public float threshold = 1.10f;
    [Range(0f, 1f)] public float softKnee = 0.55f;
    [Range(0f, 4f)] public float bloomIntensity = 0.70f;
    [Range(1, 6)] public int blurIterations = 3;
    [Range(0.5f, 4f)] public float blurSpread = 1.00f;
    [Range(0.1f, 3f)] public float exposure = 1.12f;
    [Range(0.3f, 0.98f)] public float tonemapKnee = 0.80f;

    [Header("Color Grading 调色")]
    [Range(0f, 2f)] public float saturation = 1.38f;
    [Range(0f, 2f)] public float contrast = 1.08f;
    public Color shadowTint = new Color(0.82f, 0.76f, 1.00f, 1f);
    public Color highlightTint = new Color(1.00f, 0.96f, 0.86f, 1f);

    [Header("Lens 镜头")]
    [Range(0f, 1f)] public float vignette = 0.30f;
    [Range(0f, 4f)] public float chromaticAberration = 0.80f;

    private Material _mat;
    private bool _warned;

    private static readonly int IdThreshold   = Shader.PropertyToID("_Threshold");
    private static readonly int IdSoftKnee    = Shader.PropertyToID("_SoftKnee");
    private static readonly int IdIntensity   = Shader.PropertyToID("_BloomIntensity");
    private static readonly int IdSaturation  = Shader.PropertyToID("_Saturation");
    private static readonly int IdContrast    = Shader.PropertyToID("_Contrast");
    private static readonly int IdVignette    = Shader.PropertyToID("_Vignette");
    private static readonly int IdChroma      = Shader.PropertyToID("_Chroma");
    private static readonly int IdShadowTint  = Shader.PropertyToID("_ShadowTint");
    private static readonly int IdHighTint    = Shader.PropertyToID("_HighlightTint");
    private static readonly int IdBlurDir     = Shader.PropertyToID("_BlurDir");
    private static readonly int IdBloomTex    = Shader.PropertyToID("_BloomTex");
    private static readonly int IdExposure    = Shader.PropertyToID("_Exposure");
    private static readonly int IdTonemapKnee = Shader.PropertyToID("_TonemapKnee");

    private Material GetMaterial()
    {
        if (_mat != null) return _mat;

        Shader sh = Shader.Find("Hidden/NeuroBloom");
        // 打包版里 Hidden/ 前缀的 shader 如果没进 Resources / AlwaysIncluded，
        // Shader.Find 会返回 null，OnRenderImage 就整段直通（曝光、泛光、调色全失效）。
        // 用 Resources 里的材质资产兜底，把它钉进包里。
        if (sh == null)
        {
            var fallback = Resources.Load<Material>("NeuroPostFX");
            if (fallback != null) sh = fallback.shader;
        }
        if (sh == null || !sh.isSupported)
        {
            if (!_warned)
            {
                _warned = true;
                Debug.LogWarning("[NeuroPostFX] 找不到或无法编译 Hidden/NeuroBloom，本次直通输出。");
            }
            return null;
        }

        _mat = new Material(sh);
        _mat.hideFlags = HideFlags.HideAndDontSave;
        return _mat;
    }

    private void OnDisable()
    {
        if (_mat != null)
        {
            if (Application.isPlaying) Destroy(_mat);
            else DestroyImmediate(_mat);
            _mat = null;
        }
    }

    private void OnRenderImage(RenderTexture src, RenderTexture dst)
    {
        Material mat = GetMaterial();
        if (mat == null || src == null)
        {
            Graphics.Blit(src, dst);
            return;
        }

        mat.SetFloat(IdThreshold, Mathf.Max(0f, threshold));
        mat.SetFloat(IdSoftKnee, Mathf.Clamp01(softKnee));
        mat.SetFloat(IdIntensity, Mathf.Max(0f, bloomIntensity));
        mat.SetFloat(IdExposure, Mathf.Max(0.01f, exposure));
        mat.SetFloat(IdTonemapKnee, Mathf.Clamp(tonemapKnee, 0.3f, 0.98f));
        mat.SetFloat(IdSaturation, saturation);
        mat.SetFloat(IdContrast, contrast);
        mat.SetFloat(IdVignette, Mathf.Clamp01(vignette));
        mat.SetFloat(IdChroma, Mathf.Max(0f, chromaticAberration));
        mat.SetColor(IdShadowTint, shadowTint);
        mat.SetColor(IdHighTint, highlightTint);

        int w = Mathf.Max(2, src.width / 2);
        int h = Mathf.Max(2, src.height / 2);
        RenderTextureFormat fmt = RenderTextureFormat.DefaultHDR;

        RenderTexture pre = RenderTexture.GetTemporary(w, h, 0, fmt);
        pre.filterMode = FilterMode.Bilinear;

        RenderTexture tmp = RenderTexture.GetTemporary(w, h, 0, fmt);
        tmp.filterMode = FilterMode.Bilinear;

        Graphics.Blit(src, pre, mat, 0);   // Pass 0: PreFilter

        int iter = Mathf.Clamp(blurIterations, 1, 6);
        for (int i = 0; i < iter; i++)
        {
            float r = blurSpread * (1f + i * 0.75f);

            mat.SetVector(IdBlurDir, new Vector4(r, 0f, 0f, 0f));
            Graphics.Blit(pre, tmp, mat, 1);   // Pass 1: 横向

            mat.SetVector(IdBlurDir, new Vector4(0f, r, 0f, 0f));
            Graphics.Blit(tmp, pre, mat, 1);   // Pass 1: 纵向
        }

        mat.SetTexture(IdBloomTex, pre);
        Graphics.Blit(src, dst, mat, 2);       // Pass 2: Composite

        RenderTexture.ReleaseTemporary(pre);
        RenderTexture.ReleaseTemporary(tmp);
    }
}
