// NeuroBloomDiagnose.cs —— 诊断脚本：打印场景内物体与材质、字体可用性
// 用法：Unity -batchmode -executeMethod NeuroBloomDiagnose.Run -quit
using System.Text;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class NeuroBloomDiagnose
{
    [MenuItem("NeuroBloom/诊断场景")]
    public static void Run()
    {
        var sb = new StringBuilder();
        sb.AppendLine("===== NeuroBloom 诊断 =====");

        // 1. 内置字体
        Font f1 = null, f2 = null;
        try { f1 = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf"); } catch (System.Exception e) { sb.AppendLine("LegacyRuntime 异常: " + e.Message); }
        try { f2 = Resources.GetBuiltinResource<Font>("Arial.ttf"); } catch { }
        sb.AppendLine($"字体 LegacyRuntime.ttf: {(f1 == null ? "❌ 空" : "✅ " + f1.name)}");
        sb.AppendLine($"字体 Arial.ttf: {(f2 == null ? "❌ 空(已废弃)" : "✅ " + f2.name)}");

        // 2. 打开我们的场景
        EditorSceneManager.OpenScene("Assets/Scenes/NeuroBloom.unity", OpenSceneMode.Single);
        var scene = EditorSceneManager.GetActiveScene();
        sb.AppendLine($"当前场景: {scene.path} (已加载={scene.isLoaded})");

        // 3. 遍历所有根对象
        foreach (var go in scene.GetRootGameObjects())
        {
            Dump(go, "", sb);
        }

        Debug.Log(sb.ToString());
    }

    private static void Dump(GameObject go, string indent, StringBuilder sb)
    {
        var comps = go.GetComponents<Component>();
        string extra = "";
        var mr = go.GetComponent<MeshRenderer>();
        if (mr != null && mr.sharedMaterial != null)
        {
            var sh = mr.sharedMaterial.shader;
            extra += $" | 材质={mr.sharedMaterial.name} 着色器={(sh == null ? "❌空" : sh.name)}";
        }
        var tm = go.GetComponent<TextMesh>();
        if (tm != null)
            extra += $" | TextMesh 字体={(tm.font == null ? "❌空" : tm.font.name)} 文字=\"{tm.text}\"";
        var cam = go.GetComponent<Camera>();
        if (cam != null)
            extra += $" | 相机";

        sb.AppendLine($"{indent}- {go.name} [缩放={go.transform.localScale}] {extra}");

        foreach (Transform c in go.transform)
            Dump(c.gameObject, indent + "   ", sb);
    }
}
