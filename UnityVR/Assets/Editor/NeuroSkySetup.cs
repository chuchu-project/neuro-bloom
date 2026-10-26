// NeuroSkySetup.cs —— 把程序化天空盒换成 CC0 的森林 HDRI（Skybox/Panoramic）
//
// 为什么要换：程序化天空只有一个渐变 + 一颗糊太阳，远看像"塑料棚"。
// 一张真实的阴天森林 HDRI 能把远处的树林/雾气层次带进来，是整个画面最便宜的一次提升。
//
// 素材：Assets/Sky/rain_forest_2k.hdr —— Poly Haven 的 autumn_forest_02（CC0 公有领域，商用免署名）。
//
// 用法（batchmode）：
//   Unity -batchmode -quit -projectPath UnityVR -executeMethod NeuroSkySetup.Run
//
// 注意：这个脚本会**打开并保存** Assets/Scenes/NeuroBloom.unity，把 RenderSettings.skybox
// 以及 NeuroWorldController / NeuroWeather 上的 skyMaterial 引用换掉。
// 换回来只需把 NeuroSkySetup.Revert 跑一次（或把 _MainTex 换成 NB2_Sky.mat）。
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;

public static class NeuroSkySetup
{
    private const string HdrPath   = "Assets/Sky/rain_forest_2k.hdr";
    private const string MatPath   = "Assets/Materials/NB2_SkyRain.mat";
    private const string ScenePath = "Assets/Scenes/NeuroBloom.unity";

    [MenuItem("NeuroBloom/换 HDRI 天空盒（雨天森林）")]
    public static void Run()
    {
        // ---------- 1) 导入设置：这颗 HDR 只当 2D 全景图用 ----------
        var ti = AssetImporter.GetAtPath(HdrPath) as TextureImporter;
        if (ti == null)
        {
            Debug.LogError("[SkySetup] 找不到 " + HdrPath);
            return;
        }
        ti.textureType = TextureImporterType.Default;
        ti.textureShape = TextureImporterShape.Texture2D;
        ti.sRGBTexture = true;                              // 全景图是颜色数据
        ti.maxTextureSize = 2048;
        ti.textureCompression = TextureImporterCompression.Uncompressed;   // 压了会有色块
        ti.wrapMode = TextureWrapMode.Clamp;                // 免得接缝处出现一条竖线
        ti.mipmapEnabled = false;
        ti.SaveAndReimport();

        var tex = AssetDatabase.LoadAssetAtPath<Texture2D>(HdrPath);
        if (tex == null)
        {
            Debug.LogError("[SkySetup] HDR 导入失败：" + HdrPath);
            return;
        }

        // ---------- 2) 天空盒材质 ----------
        Shader sh = Shader.Find("Skybox/Panoramic");
        if (sh == null)
        {
            Debug.LogError("[SkySetup] 找不到 Skybox/Panoramic");
            return;
        }
        var mat = AssetDatabase.LoadAssetAtPath<Material>(MatPath);
        if (mat == null)
        {
            mat = new Material(sh) { name = "NB2_SkyRain" };
            AssetDatabase.CreateAsset(mat, MatPath);
        }
        mat.shader = sh;
        mat.SetTexture("_MainTex", tex);
        mat.SetFloat("_Mapping", 1f);        // 1 = Latitude Longitude（全景等距柱状投影）
        mat.SetFloat("_ImageType", 0f);      // 0 = 360 度全景
        mat.SetFloat("_Exposure", 0.95f);
        mat.SetFloat("_Rotation", 0f);
        mat.SetColor("_Tint", new Color(0.5f, 0.5f, 0.5f, 0.5f));   // 0.5 = 不染色（shader 里乘 2）
        EditorUtility.SetDirty(mat);
        AssetDatabase.SaveAssets();

        // ---------- 3) 写进场景 ----------
        var scene = EditorSceneManager.OpenScene(ScenePath, OpenSceneMode.Single);
        RenderSettings.skybox = mat;
        RenderSettings.defaultReflectionMode = DefaultReflectionMode.Skybox;   // 湿地面要靠天空反射才亮
        RenderSettings.ambientMode = AmbientMode.Flat;                         // 环境光仍由控制器写，别让 HDRI 抢

        var wc = Object.FindFirstObjectByType<NeuroWorldController>();
        if (wc != null) { wc.skyMaterial = mat; EditorUtility.SetDirty(wc); }

        var nw = Object.FindFirstObjectByType<NeuroWeather>();
        if (nw != null) { nw.skyMaterial = mat; EditorUtility.SetDirty(nw); }

        EditorSceneManager.MarkSceneDirty(scene);
        EditorSceneManager.SaveScene(scene);
        AssetDatabase.SaveAssets();

        Debug.Log("[SkySetup] 完成：天空盒 → " + MatPath + "（" + tex.width + "x" + tex.height + "）");
    }

    /// <summary>换回原来的程序化天空（出问题时的退路）。</summary>
    [MenuItem("NeuroBloom/还原程序化天空盒")]
    public static void Revert()
    {
        var old = AssetDatabase.LoadAssetAtPath<Material>("Assets/Materials/NB2_Sky.mat");
        if (old == null) { Debug.LogError("[SkySetup] 找不到 NB2_Sky.mat"); return; }

        var scene = EditorSceneManager.OpenScene(ScenePath, OpenSceneMode.Single);
        RenderSettings.skybox = old;
        var wc = Object.FindFirstObjectByType<NeuroWorldController>();
        if (wc != null) { wc.skyMaterial = old; EditorUtility.SetDirty(wc); }
        var nw = Object.FindFirstObjectByType<NeuroWeather>();
        if (nw != null) { nw.skyMaterial = old; EditorUtility.SetDirty(nw); }
        EditorSceneManager.MarkSceneDirty(scene);
        EditorSceneManager.SaveScene(scene);
        Debug.Log("[SkySetup] 已还原程序化天空盒");
    }
}
