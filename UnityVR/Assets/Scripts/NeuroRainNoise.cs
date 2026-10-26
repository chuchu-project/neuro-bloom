// NeuroRainNoise.cs —— 程序化生成雨声（不依赖任何音频素材）
//
// 为什么不用现成音效：CC0 的雨声素材要么大（几 MB），要么循环接缝明显；
// 而"雨声"本质就是带限噪声 + 缓慢起伏，二十行代码就能生成，还能随雨量实时改音量。
//
// 声音设计：
//   白噪声 → 高通 400Hz（去掉像卡车经过的低频轰鸣）
//           → 两级低通 6kHz（把"嘶——"的刺耳高频磨掉，听着像雨不是像电流）
//           → 两条超低频起伏（阵风感，频率取 1/时长 的整数倍，保证循环无缝）
// 左右声道用两路独立噪声，听起来有宽度（单声道雨声会很"薄"）。
using UnityEngine;

public static class NeuroRainNoise
{
    /// <summary>生成一段可无缝循环的雨声。返回 [-1,1] 的交叉交织采样（L,R,L,R...）。</summary>
    public static float[] BuildSamples(int sampleRate, float seconds, int seed, out int channels)
    {
        channels = 2;
        int frames = Mathf.Max(1, Mathf.RoundToInt(sampleRate * seconds));
        int fade = Mathf.RoundToInt(sampleRate * 0.25f);   // 接缝交叉淡入淡出长度
        int total = frames + fade;
        var buf = new float[frames * channels];
        var tmp = new float[total];

        // 阵风：周期必须整除 seconds，否则循环点会"咔"一下
        float gust1 = 1f / seconds * 1f;      // 8 秒 1 次
        float gust2 = 1f / seconds * 3f;      // 8 秒 3 次

        for (int ch = 0; ch < channels; ch++)
        {
            // 每个声道独立种子 → 左右不相关 → 听感变宽
            uint s = (uint)(seed + ch * 7919);
            if (s == 0) s = 1;

            double lpLow = 0;    // 低通用的状态
            double lp1 = 0, lp2 = 0, lp3 = 0;

            double aLow = Coef(400.0, sampleRate);      // 400Hz 低通（用来做高通：x - lp）
            double aHi = Coef(4000.0, sampleRate);      // 三级 4kHz 低通 → 滚降够陡，去掉"电流味"

            for (int i = 0; i < total; i++)
            {
                // 快速 xorshift32：比 System.Random 快一个数量级
                s ^= s << 13; s ^= s >> 17; s ^= s << 5;
                double w = (s & 0xFFFFFF) / 8388608.0 - 1.0;     // 白噪声 [-1,1)

                // 高通：把 400Hz 以下的轰鸣扣掉
                lpLow += aLow * (w - lpLow);
                double hp = w - lpLow;

                // 三级低通：单级太缓，高频还剩一大截，听起来像电流
                lp1 += aHi * (hp - lp1);
                lp2 += aHi * (lp1 - lp2);
                lp3 += aHi * (lp2 - lp3);

                // 阵风起伏（0.82 ~ 1.0，不要让声音忽大忽小到出戏）
                double t = i / (double)sampleRate;
                double gust = 1.0
                    - 0.10 * (0.5 + 0.5 * System.Math.Sin(2 * System.Math.PI * gust1 * t + ch * 1.7))
                    - 0.08 * (0.5 + 0.5 * System.Math.Sin(2 * System.Math.PI * gust2 * t + ch * 0.9));

                tmp[i] = (float)(lp3 * gust);
            }

            // 交叉淡化：把尾巴折回开头，循环点就没有台阶（否则每 8 秒"咔"一下）
            for (int i = 0; i < frames; i++)
            {
                float v;
                if (i < fade)
                {
                    float k = i / (float)fade;                 // 0 → 1
                    v = tmp[i] * k + tmp[frames + i] * (1f - k);
                }
                else
                {
                    v = tmp[i];
                }
                buf[i * channels + ch] = v;
            }
        }

        // 归一化到 0.65 峰值：留足余量，运行时再乘音量，避免爆音
        float peak = 0f;
        for (int i = 0; i < buf.Length; i++) peak = Mathf.Max(peak, Mathf.Abs(buf[i]));
        if (peak > 1e-6f)
        {
            float k = 0.65f / peak;
            for (int i = 0; i < buf.Length; i++) buf[i] *= k;
        }
        return buf;
    }

    public static AudioClip Build(int sampleRate = 44100, float seconds = 8f, int seed = 20261023)
    {
        int channels;
        var data = BuildSamples(sampleRate, seconds, seed, out channels);
        var clip = AudioClip.Create("NB_RainLoop", data.Length / channels, channels, sampleRate, false);
        clip.SetData(data, 0);
        return clip;
    }

    // 单极点低通系数：给定截止频率和采样率
    private static double Coef(double hz, int sampleRate)
    {
        return 1.0 - System.Math.Exp(-2.0 * System.Math.PI * hz / sampleRate);
    }
}
