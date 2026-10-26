// RainAudioCheck.cs —— 把程序化雨声导出成 wav，方便用数值检查音色
// 用法：Unity -batchmode -quit -projectPath UnityVR -executeMethod RainAudioCheck.Run
using System.IO;
using UnityEditor;
using UnityEngine;

public static class RainAudioCheck
{
    [MenuItem("NeuroBloom/导出雨声 wav（检查用）")]
    public static void Run()
    {
        const int sr = 44100;
        const float sec = 8f;
        int ch;
        var data = NeuroRainNoise.BuildSamples(sr, sec, 20261023, out ch);

        string dir = Path.GetFullPath(Path.Combine(Application.dataPath, "..", ".deepworks", "tmp"));
        Directory.CreateDirectory(dir);
        string path = Path.Combine(dir, "rain_audio.wav");

        int frames = data.Length / ch;
        using (var fs = new FileStream(path, FileMode.Create))
        using (var bw = new BinaryWriter(fs))
        {
            int dataBytes = frames * ch * 2;
            bw.Write(new[] { 'R', 'I', 'F', 'F' });
            bw.Write(36 + dataBytes);
            bw.Write(new[] { 'W', 'A', 'V', 'E', 'f', 'm', 't', ' ' });
            bw.Write(16);
            bw.Write((short)1);          // PCM
            bw.Write((short)ch);
            bw.Write(sr);
            bw.Write(sr * ch * 2);       // byte rate
            bw.Write((short)(ch * 2));   // block align
            bw.Write((short)16);         // bits
            bw.Write(new[] { 'd', 'a', 't', 'a' });
            bw.Write(dataBytes);
            for (int i = 0; i < data.Length; i++)
            {
                int v = Mathf.Clamp(Mathf.RoundToInt(data[i] * 32767f), -32768, 32767);
                bw.Write((short)v);
            }
        }
        Debug.Log("[RainAudio] 已导出 " + path + "（" + frames + " 帧 / " + ch + " 声道 / " + sr + "Hz）");
    }
}
