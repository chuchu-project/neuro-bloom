// NeuroBloomRender.cs —— 把场景渲染成 PNG（含"雾气开启"验证图），便于排查画面问题
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class NeuroBloomRender
{
    [MenuItem("NeuroBloom/渲染预览图")]
    public static void Run()
    {
        EditorSceneManager.OpenScene("Assets/Scenes/NeuroBloom.unity", OpenSceneMode.Single);

        var cam = Camera.main;
        if (cam == null) { Debug.LogError("[Render] 找不到 Main Camera"); return; }
        Debug.Log($"[Render] 相机位置={cam.transform.position} 旋转={cam.transform.eulerAngles}");

        Render(cam, "render_preview.png");

        // 模拟"焦虑"状态：原生体积雾 + 天色转阴，检查观感
        RenderSettings.fog = true;
        RenderSettings.fogMode = FogMode.ExponentialSquared;
        RenderSettings.fogDensity = 0.055f;
        RenderSettings.fogColor = new Color(0.36f, 0.37f, 0.43f);
        RenderSettings.ambientLight = new Color(0.16f, 0.18f, 0.24f);
        if (RenderSettings.skybox != null)
        {
            var sb = RenderSettings.skybox;
            if (sb.HasProperty("_SkyTint")) sb.SetColor("_SkyTint", new Color(0.16f, 0.17f, 0.24f));
            if (sb.HasProperty("_GroundColor")) sb.SetColor("_GroundColor", new Color(0.34f, 0.34f, 0.40f));
            if (sb.HasProperty("_Exposure")) sb.SetFloat("_Exposure", 0.55f);
        }
        var sunGO = GameObject.Find("Sun");
        if (sunGO != null)
        {
            var l = sunGO.GetComponent<Light>();
            if (l != null) { l.intensity = 0.4f; l.color = new Color(0.55f, 0.62f, 0.78f); }
        }
        // 萤火虫预演
        var fog = GameObject.Find("Fog");
        if (fog != null)
        {
            var ps = fog.GetComponent<ParticleSystem>();
            if (ps != null) { var em = ps.emission; em.rateOverTime = 12f; ps.Simulate(4f, true, true); ps.Play(); }
        }
        Render(cam, "render_preview_fog.png");
    }

    private static void Render(Camera cam, string fileName)
    {
        int w = 960, h = 540;
        var rt = new RenderTexture(w, h, 24, RenderTextureFormat.ARGB32);
        rt.Create();
        var old = cam.targetTexture;
        cam.targetTexture = rt;
        cam.Render();
        cam.targetTexture = old;

        RenderTexture.active = rt;
        var tex = new Texture2D(w, h, TextureFormat.RGB24, false);
        tex.ReadPixels(new Rect(0, 0, w, h), 0, 0);
        tex.Apply();
        RenderTexture.active = null;

        string outPath = Path.Combine(Application.dataPath, "../" + fileName);
        File.WriteAllBytes(outPath, tex.EncodeToPNG());
        Debug.Log($"[Render] 已保存：{Path.GetFullPath(outPath)}");
    }
}
