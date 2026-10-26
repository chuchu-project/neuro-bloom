// NeuroRainAudio.cs —— 把程序化雨声挂到场景上，音量跟着雨量走
//
// 和 NeuroRain 一样是自举的：谁需要谁 Ensure()，不需要在场景里预摆对象。
using UnityEngine;

[DefaultExecutionOrder(215)]
public class NeuroRainAudio : MonoBehaviour
{
    public static NeuroRainAudio Instance { get; private set; }

    public float maxVolume = 0.75f;     // 雨最大时的音量（0~1）。再大就盖住引导语音了
    public float smoothSeconds = 2.6f;  // 和天气过渡同速，避免"突然开始下雨"

    private AudioSource _src;
    private float _vol;

    public static NeuroRainAudio Ensure()
    {
        if (Instance != null) return Instance;
        var go = new GameObject("NB_RainAudio");
        Instance = go.AddComponent<NeuroRainAudio>();
        return Instance;
    }

    private void Awake()
    {
        Instance = this;
        _src = gameObject.AddComponent<AudioSource>();
        _src.clip = NeuroRainNoise.Build();
        _src.loop = true;
        _src.playOnAwake = false;
        _src.spatialBlend = 0f;         // 2D：雨声铺满整个场景，不需要空间化
        _src.volume = 0f;
        _src.priority = 128;
    }

    private void Update()
    {
        var rain = NeuroRain.Instance;
        float target = rain != null ? Mathf.Clamp01(rain.intensity) * maxVolume : 0f;

        // 线性平滑就够（时间常数 2.6 秒 → 每秒改动的幅度很小，听不出台阶）
        _vol = Mathf.MoveTowards(_vol, target, Time.deltaTime / Mathf.Max(0.1f, smoothSeconds) * maxVolume);
        _src.volume = _vol;

        if (_vol > 0.002f)
        {
            if (!_src.isPlaying)
            {
                _src.Play();
                Debug.Log("[RainAudio] 雨声起播（雨量 " + (rain != null ? rain.intensity.ToString("F3") : "-") + "，从 0 渐强）");
            }
        }
        else if (_src.isPlaying)
        {
            _src.Stop();     // 完全不下雨时停掉，省一点 CPU
            Debug.Log("[RainAudio] 雨停了，静音");
        }
    }
}
