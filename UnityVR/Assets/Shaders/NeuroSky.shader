// NeuroSky.shader —— 星空 / 月亮用的"不受雾影响"的自发光加色着色器
// 为什么单独写一个：地形雾对 45~120 单位外的物体是致命的
//（雾密度 0.025 时，exp(-(0.025*120)^2) ≈ 0，星星会被雾吃光），
// 所以这里刻意 **不带 fog 关键字**，让星星和月亮永远按原始亮度画出来。
Shader "NeuroBloom/Sky"
{
    Properties
    {
        _MainTex ("贴图", 2D) = "white" {}
        _Tint    ("颜色", Color) = (1,1,1,1)
        _Alpha   ("强度", Range(0,3)) = 1
    }

    SubShader
    {
        // Background 队列 = 在天空盒之后、其他不透明物体之前画
        Tags { "Queue" = "Background" "RenderType" = "Background" "IgnoreProjector" = "True" }

        Cull Off          // 星空是从球内部看的，必须关背面剔除
        ZWrite Off
        ZTest LEqual      // 仍然接受深度遮挡：地面/树会把地平线以下的星星挡住

        Blend SrcAlpha One

        Pass
        {
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"

            struct appdata { float4 vertex : POSITION; float2 uv : TEXCOORD0; };
            struct v2f     { float4 pos : SV_POSITION; float2 uv : TEXCOORD0; };

            sampler2D _MainTex;
            float4 _MainTex_ST;
            fixed4 _Tint;
            float _Alpha;

            v2f vert (appdata v)
            {
                v2f o;
                o.pos = UnityObjectToClipPos(v.vertex);
                o.uv = TRANSFORM_TEX(v.uv, _MainTex);
                return o;
            }

            fixed4 frag (v2f i) : SV_Target
            {
                fixed4 c = tex2D(_MainTex, i.uv);
                // 用贴图的 alpha 当亮度权重 → 黑底完全不亮，星点才亮
                return fixed4(c.rgb * _Tint.rgb * _Alpha * c.a, 1);
            }
            ENDCG
        }
    }

    Fallback Off
}
