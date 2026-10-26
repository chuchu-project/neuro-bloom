// NeuroGuideTitle.shader —— 引导文字牌的梦幻花体表现
//
// 这个着色器只负责"好看"，不参与任何判定。做法：
//   · 字号形状仍然用真字体（组件里优先挑手写/花体），这里只改颜色与光
//   · 竖直渐变：薰衣草紫 → 薄荷绿 → 樱粉 → 淡金 → 奶白（极光调子）
//   · 外发光：把字图集往外围取样一圈，得到柔和的蓝紫光晕（偏 HDR，交给场景 Bloom）
//   · 逐字淡入：用"文字块从左到右"的进度裁掉还没亮起的字
//   · 微风摆动 + 上浮：整体轻微绕 Y 偏转、整体上移，不改物体自身的 Transform
Shader "NeuroBloom/GuideTitle"
{
    Properties
    {
        _MainTex      ("Font Atlas", 2D) = "white" {}
        _GlowColor    ("Glow Color", Color) = (1.15, 0.98, 1.55, 1)
        _GlowStrength ("Glow Strength", Range(0, 4)) = 1.15
        _GlowRadius   ("Glow Radius", Range(0, 8)) = 2.2
        _BodyAlpha    ("Body Alpha", Range(0, 1)) = 0.50
        _BodyGain     ("Body Gain (brightness)", Range(0, 3)) = 0.95
        _InkLo        ("Ink Thin Lo", Range(0, 1)) = 0.08
        _InkHi        ("Ink Thin Hi", Range(0, 1)) = 0.34
        _Shimmer      ("Aurora Shimmer", Range(0, 2)) = 0.55
        _GlowWide     ("Wide Aura", Range(0, 2)) = 0.45
        _Breathe      ("Breathing", Range(0, 0.3)) = 0.07
        _Drift        ("Aurora Drift", Range(0, 0.4)) = 0.035
        _Reveal       ("Reveal 0..1", Range(0, 1)) = 1
        _CharCount    ("Chars Per Line", Float) = 11
        _LineCount    ("Lines", Float) = 2
        _Stagger      ("Stagger Seconds", Float) = 0.05
        _PopSeconds   ("Pop Seconds", Float) = 0.55
        _PopOvershoot ("Pop Overshoot", Range(0, 1)) = 0.42
        _Wipe         ("Wipe", Range(0, 1)) = 1
        _Softness     ("Wipe Softness", Range(0.01, 1)) = 0.30
        _Rise         ("Rise", Float) = 0
        _Sway         ("Sway", Float) = 0
        _LocalMin     ("Local Min", Vector) = (-1, -1, 0, 0)
        _LocalMax     ("Local Max", Vector) = (1, 1, 0, 0)
        _ColTop       ("Colour Top", Color) = (1.00, 0.97, 0.92, 1)
        _ColGold      ("Colour Gold", Color) = (1.00, 0.88, 0.58, 1)
        _ColPink      ("Colour Pink", Color) = (1.00, 0.72, 0.86, 1)
        _ColMint      ("Colour Mint", Color) = (0.64, 0.95, 0.80, 1)
        _ColViolet    ("Colour Violet", Color) = (0.78, 0.68, 1.00, 1)
    }

    SubShader
    {
        Tags { "Queue" = "Transparent+30" "RenderType" = "Transparent" "IgnoreProjector" = "True" }
        ZWrite Off
        Cull Off

        Pass
        {
            Blend SrcAlpha OneMinusSrcAlpha

            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #pragma target 3.0
            #include "UnityCG.cginc"

            struct appdata
            {
                float4 vertex : POSITION;
                float2 uv     : TEXCOORD0;
            };

            struct v2f
            {
                float4 pos    : SV_POSITION;
                float2 uv     : TEXCOORD0;
                float3 objPos : TEXCOORD1;
                float  fade   : TEXCOORD2;
            };

            sampler2D _MainTex;
            half4  _GlowColor;
            float  _GlowStrength;
            float  _GlowRadius;
            float  _BodyAlpha;
            float  _BodyGain;
            float  _InkLo;
            float  _InkHi;
            float  _Shimmer;
            float  _GlowWide;
            float  _Breathe;
            float  _Drift;
            float  _Reveal;
            float  _CharCount;
            float  _LineCount;
            float  _Stagger;
            float  _PopSeconds;
            float  _PopOvershoot;
            float  _Wipe;
            float  _Softness;
            float  _Rise;
            float  _Sway;
            float4 _LocalMin;
            float4 _LocalMax;
            half4  _ColTop;
            half4  _ColGold;
            half4  _ColPink;
            half4  _ColMint;
            half4  _ColViolet;

            v2f vert (appdata v)
            {
                v2f o;
                o.objPos = v.vertex.xyz;          // 渐变/裁切用"没动过"的坐标，字移了颜色不跟着跑

                // 逐字弹入：把整块文字按"每行几个字、共几行"切成格子，
                // 每个字绕自己的格子中心从 0 放大到 1（带轻微回弹），相邻字错开 _Stagger 秒。
                // 这样不需要协程，也不会因掉帧而错乱。
                float spanX = max(_LocalMax.x - _LocalMin.x, 0.0001);
                float spanY = max(_LocalMax.y - _LocalMin.y, 0.0001);
                float cellW = spanX / max(_CharCount, 1.0);
                float lineH = spanY / max(_LineCount, 1.0);
                float ix    = floor(clamp((v.vertex.x - _LocalMin.x) / cellW, 0.0, _CharCount - 1.0));
                float iy    = floor(clamp((v.vertex.y - _LocalMin.y) / lineH, 0.0, _LineCount - 1.0));
                float t     = saturate((_Reveal - ix * _Stagger) / max(_PopSeconds, 0.001));
                float ease  = 1.0 - pow(1.0 - t, 3.0);                                    // 缓出
                float s     = ease * (1.0 + _PopOvershoot * sin(3.14159265 * t) * (1.0 - t)); // 轻微弹性
                float cx    = _LocalMin.x + (ix + 0.5) * cellW;
                float cy    = _LocalMin.y + (iy + 0.5) * lineH;

                float3 p = v.vertex.xyz;
                p.x = cx + (p.x - cx) * s;
                p.y = cy + (p.y - cy) * s;

                float ca = cos(_Sway);
                float sa = sin(_Sway);
                float3 r = float3(p.x * ca - p.z * sa, p.y + _Rise, p.x * sa + p.z * ca);

                o.pos = UnityObjectToClipPos(float4(r, 1.0));
                o.uv  = v.uv;
                o.fade = saturate(t * 1.7);       // 弹入的前 60% 里同步淡入
                return o;
            }

            half3 Gradient (float t)
            {
                t = saturate(t);
                float s = t * 4.0;
                half3 c;
                if      (s < 1.0) c = lerp(_ColViolet.rgb, _ColMint.rgb, s);
                else if (s < 2.0) c = lerp(_ColMint.rgb,   _ColPink.rgb, s - 1.0);
                else if (s < 3.0) c = lerp(_ColPink.rgb,   _ColGold.rgb, s - 2.0);
                else              c = lerp(_ColGold.rgb,   _ColTop.rgb,  s - 3.0);
                return c;
            }

            fixed4 frag (v2f i) : SV_Target
            {
                // Unity 字体的字图集把"此处有没有笔画"放在 alpha 通道
                // 笔画"削细 + 发绒"：把字图集的 alpha 重新映射。
                // 抬高阈值→笔画变细；两端用 smoothstep 过渡→边缘像薄水晶一样柔。
                // 想让字更细就把 _InkHi 往大调，想更实就往小调（0.35 左右最实）。
                float rawA = tex2D(_MainTex, i.uv).a;
                float a    = smoothstep(_InkLo, _InkHi, rawA);

                float2 span = max(_LocalMax.xy - _LocalMin.xy, float2(0.0001, 0.0001));
                float2 q    = (i.objPos.xy - _LocalMin.xy) / span;

                // 逐字亮起：进度左边全亮，前沿一小段柔化
                float wipe = 1.0 - smoothstep(_Wipe, _Wipe + _Softness, q.x);

                // 外发光：周围八个方向取样。
                // 半径按“屏幕像素”算（用 uv 的屏幕导数），这样换字体、换图集大小光晕都不会变粗变没有
                float2 rad = float2(abs(ddx(i.uv.x)), abs(ddy(i.uv.y))) * _GlowRadius;
                float g = 0;
                g += tex2D(_MainTex, i.uv + float2( rad.x,  0)).a;
                g += tex2D(_MainTex, i.uv + float2(-rad.x,  0)).a;
                g += tex2D(_MainTex, i.uv + float2( 0,  rad.y)).a;
                g += tex2D(_MainTex, i.uv + float2( 0, -rad.y)).a;
                g += tex2D(_MainTex, i.uv + float2( rad.x,  rad.y) * 0.71).a;
                g += tex2D(_MainTex, i.uv + float2(-rad.x,  rad.y) * 0.71).a;
                g += tex2D(_MainTex, i.uv + float2( rad.x, -rad.y) * 0.71).a;
                g += tex2D(_MainTex, i.uv + float2(-rad.x, -rad.y) * 0.71).a;
                g *= 0.125;

                // 第二圈更远的取样：做"雾气"般的宽晕（4 个方向够用）
                float2 radW = rad * 3.0;
                float gW = 0;
                gW += tex2D(_MainTex, i.uv + float2( 0,  radW.y)).a;
                gW += tex2D(_MainTex, i.uv + float2( 0, -radW.y)).a;
                gW += tex2D(_MainTex, i.uv + float2( radW.x,  0) * 1.4).a;
                gW += tex2D(_MainTex, i.uv + float2(-radW.x,  0) * 1.4).a;
                gW *= 0.25;

                // 紧光晕：故意做得很亮（HDR，超过场景泛光阈值 1.10），
                // 由后期泛光把它散成一团柔软的光雾——这才是"仙气"的来源，
                // 而不是在着色器里硬画一个大光斑（那会糊成一块白色底衬）。
                float halo = saturate((g - a) * 2.0) * wipe;
                float wide = saturate((gW - a) * 1.1);

                // 极光波：一道柔亮的光非常慢地横向流过整行字（周期约 40 秒）
                float band = 0.5 + 0.5 * sin((q.x * 1.15 - _Time.y * 0.025) * 6.2831853);
                band = pow(band, 3.0) * _Shimmer;

                // 极光漂移：渐变的取样纵坐标自己缓慢上下游走 + 逐字起伏，
                // 于是整行字的颜色像极光一样在流动，而不是钉死的五段渐变。
                float drift = _Drift * sin(_Time.y * 0.13 + q.x * 4.2)
                            + _Drift * 0.62 * sin(_Time.y * 0.21 + q.x * 1.3);
                half3 gradV = Gradient(saturate(q.y + drift));    // 纵向极光（保留一点流动感）
                // 横向主渐变：中间暖金，两侧樱粉 -> 最边上薰衣草紫
                float dc = saturate(abs(q.x - 0.5) * 2.0);
                half3 gradH = lerp(_ColGold, _ColPink, smoothstep(0.18, 0.78, dc));
                gradH = lerp(gradH, _ColViolet, smoothstep(0.70, 1.0, dc));
                half3 grad = lerp(gradV, gradH, 0.68);

                // 极缓慢的整体呼吸（像灯在轻轻起伏）
                float breathe = 1.0 + _Breathe * sin(_Time.y * 0.9);

                half3 col  = grad * a * _BodyGain * (1.0 + band * 0.55) * breathe
                           + _GlowColor.rgb * halo * _GlowStrength
                           + _GlowColor.rgb * wide * _GlowWide * 1.1
                           + _GlowColor.rgb * band * 0.20;
                float alpha = saturate(a * _BodyAlpha + halo * 0.18 + wide * 0.02) * wipe * i.fade;

                return fixed4(col, alpha);
            }
            ENDCG
        }
    }
    Fallback "Unlit/Transparent"
}
