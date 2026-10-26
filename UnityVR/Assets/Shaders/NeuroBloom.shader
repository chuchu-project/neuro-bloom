// NeuroBloom.shader —— 内置渲染管线（Built-in RP）后处理链
// 用途：真实 HDR Bloom + 色彩分级（冷暖分离）+ 暗角 + 轻微色差
// 由 Assets/Scripts/NeuroPostFX.cs 通过 OnRenderImage 驱动。
//
// Pass 0 PREFILTER  亮度阈值提取（软膝，HDR 友好，不做 clamp）
// Pass 1 BLUR       可分离高斯模糊（9 tap，方向由 _BlurDir 指定）
// Pass 2 COMPOSITE  原图 + 泛光叠加，再做饱和度/对比度/冷暖分离调色/暗角/色差
//
// 注意：本文件只使用 CGPROGRAM 与 Unity 内置 shader 库，未引用任何 URP/HDRP 符号。
Shader "Hidden/NeuroBloom"
{
    Properties
    {
        _MainTex       ("Base", 2D) = "white" {}
        _BloomTex      ("Bloom", 2D) = "black" {}
        _Threshold     ("Threshold", Range(0, 4)) = 0.82
        _SoftKnee      ("Soft Knee", Range(0, 1)) = 0.60
        _BloomIntensity("Bloom Intensity", Range(0, 4)) = 1.20
        _Exposure      ("Exposure", Range(0, 3)) = 1.12
        _TonemapKnee   ("Tonemap Knee", Range(0.3, 0.98)) = 0.80
        _Saturation    ("Saturation", Range(0, 2)) = 1.30
        _Contrast      ("Contrast", Range(0, 2)) = 1.10
        _Vignette      ("Vignette", Range(0, 1)) = 0.32
        _Chroma        ("Chromatic Aberration", Range(0, 4)) = 1.00
        _ShadowTint    ("Shadow Tint", Color) = (0.82, 0.76, 1.00, 1)
        _HighlightTint ("Highlight Tint", Color) = (1.00, 0.95, 0.80, 1)
    }

    CGINCLUDE
    #include "UnityCG.cginc"

    sampler2D _MainTex;
    float4    _MainTex_TexelSize;
    sampler2D _BloomTex;

    float  _Threshold;
    float  _SoftKnee;
    float  _BloomIntensity;
    float  _Exposure;
    float  _TonemapKnee;
    float  _Saturation;
    float  _Contrast;
    float  _Vignette;
    float  _Chroma;
    float4 _ShadowTint;
    float4 _HighlightTint;
    float4 _BlurDir;

    struct v2f
    {
        float4 pos : SV_POSITION;
        float2 uv  : TEXCOORD0;
    };

    v2f vert(appdata_img v)
    {
        v2f o;
        o.pos = UnityObjectToClipPos(v.vertex);
        o.uv  = v.texcoord;
        return o;
    }

    // ---------------------------------------------------------------- Pass 0
    // 亮度阈值提取。天空盒与自发光物体亮度可超过 1，因此全程用 float 不 clamp。
    float4 fragPrefilter(v2f i) : SV_Target
    {
        float3 c  = tex2D(_MainTex, i.uv).rgb;
        float  br = max(c.r, max(c.g, c.b));

        float knee = max(_Threshold * _SoftKnee, 1e-5);
        float soft = clamp(br - _Threshold + knee, 0.0, 2.0 * knee);
        soft = soft * soft / (4.0 * knee);

        float contrib = max(soft, br - _Threshold) / max(br, 1e-5);
        return float4(c * contrib, 1.0);
    }

    // ---------------------------------------------------------------- Pass 1
    // 可分离高斯模糊，9 tap。权重和 = 1.0，不做 clamp 以保留 HDR 强度。
    float4 fragBlur(v2f i) : SV_Target
    {
        float2 t = _BlurDir.xy * _MainTex_TexelSize.xy;

        float3 c  = tex2D(_MainTex, i.uv).rgb * 0.2270270270;
        c += (tex2D(_MainTex, i.uv + t * 1.3846153846).rgb +
              tex2D(_MainTex, i.uv - t * 1.3846153846).rgb) * 0.3162162162;
        c += (tex2D(_MainTex, i.uv + t * 3.2307692308).rgb +
              tex2D(_MainTex, i.uv - t * 3.2307692308).rgb) * 0.0702702703;

        return float4(c, 1.0);
    }

    // 软肩色调映射：膝点以下原样通过（保住中间调对比与黑位），
    // 膝点以上指数逼近 1.0 —— 只压真正溢出的高光，不会把画面压灰。
    float3 TonemapShoulder(float3 c, float knee)
    {
        knee = clamp(knee, 0.30, 0.98);
        float3 lo = min(c, knee);
        float3 hi = max(c - knee, 0.0);
        float  r  = 1.0 - knee;
        return lo + r * (1.0 - exp(-hi / max(r, 1e-4)));
    }

    // ---------------------------------------------------------------- Pass 2
    // 合成 + 调色。阴影偏蓝紫、高光偏暖金 —— 对应美术方向的冷暖分离。
    float4 fragComposite(v2f i) : SV_Target
    {
        float2 uvC = i.uv - 0.5;
        float2 off = uvC * (_Chroma * 0.004);

        float3 base = tex2D(_MainTex, i.uv).rgb;
        base.r = tex2D(_MainTex, i.uv + off).r;
        base.b = tex2D(_MainTex, i.uv - off).b;

        float3 bloom = tex2D(_BloomTex, i.uv).rgb;
        float3 col   = base + bloom * _BloomIntensity;

        // 曝光：纯增益，不改变对比
        col = max(col, 0.0) * _Exposure;

        float lum = dot(col, float3(0.2126, 0.7152, 0.0722));
        col = lerp(lum.xxx, col, _Saturation);
        col = (col - 0.5) * _Contrast + 0.5;
        col = max(col, 0.0);

        // 软肩放在分级之后：只削掉溢出的高光尖峰，中间调层级完整保留
        col = TonemapShoulder(col, _TonemapKnee);

        float l2 = saturate(dot(col, float3(0.2126, 0.7152, 0.0722)));
        col *= lerp(_ShadowTint.rgb, _HighlightTint.rgb, l2);

        float vig = 1.0 - _Vignette * dot(uvC, uvC) * 2.0;
        col *= saturate(vig);

        return float4(col, 1.0);
    }
    ENDCG

    SubShader
    {
        Cull Off
        ZWrite Off
        ZTest Always
        Fog { Mode Off }

        Pass
        {
            Name "PREFILTER"
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment fragPrefilter
            ENDCG
        }

        Pass
        {
            Name "BLUR"
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment fragBlur
            ENDCG
        }

        Pass
        {
            Name "COMPOSITE"
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment fragComposite
            ENDCG
        }
    }

    Fallback Off
}
